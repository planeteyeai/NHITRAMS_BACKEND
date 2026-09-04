"""Distress PDF async export: job creation, processing, status serialization."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from distress.models import DistressPdfExportJob, DistressReport
from pdf.builders import DistressReportBuilder
from pdf.s3 import generate_presigned_get_url, upload_local_file_to_s3
from pdf.utils import download_image, get_owned_reports

logger = logging.getLogger(__name__)

ASYNC_PDF_FILENAME = 'AIS_Identifier_Distress_Reports.pdf'
JOB_EXPIRES_AFTER = timedelta(days=7)
TEMP_EXPORT_ROOT_NAME = 'distress_export'


def job_progress(job: DistressPdfExportJob) -> int:
    return job.progress


def serialize_job(
    job: DistressPdfExportJob,
    *,
    include_download_url: bool = True,
) -> dict[str, Any]:
    download_url = None
    terminal = job.status in (
        DistressPdfExportJob.STATUS_COMPLETED,
        DistressPdfExportJob.STATUS_COMPLETED_WITH_ERRORS,
    )
    if include_download_url and terminal and job.output_s3_key:
        try:
            download_url = generate_presigned_get_url(
                job.output_s3_key,
                download_filename=ASYNC_PDF_FILENAME,
            )
        except Exception:
            logger.exception('Failed to presign export job %s', job.id)
            download_url = None

    payload: dict[str, Any] = {
        'job_id': str(job.id),
        'status': job.status,
        'total_reports': job.total_reports,
        'completed_reports': job.completed_reports,
        'failed_reports': job.failed_reports,
        'progress': job_progress(job),
        'download_url': download_url,
    }
    if job.failure_details:
        payload['failure_details'] = job.failure_details
    if job.error_message and job.status == DistressPdfExportJob.STATUS_FAILED:
        payload['error_message'] = job.error_message
    return payload


def create_export_job(*, username: str, ids: list[int]) -> DistressPdfExportJob:
    if not isinstance(ids, list) or not ids:
        raise ValidationError({'ids': 'Must contain at least one integer id.'})

    cleaned: list[int] = []
    for raw in ids:
        try:
            cleaned.append(int(raw))
        except (TypeError, ValueError) as exc:
            raise ValidationError({'ids': 'All ids must be integers.'}) from exc

    # Same ownership / dedupe / max rules as GET export.
    records = get_owned_reports(DistressReport, username, cleaned)
    if hasattr(records, 'all'):
        records = list(records)
    ordered_ids = [int(r.id) for r in records]

    return DistressPdfExportJob.objects.create(
        username=username,
        status=DistressPdfExportJob.STATUS_QUEUED,
        report_ids=ordered_ids,
        total_reports=len(ordered_ids),
        completed_reports=0,
        failed_reports=0,
        failure_details=[],
    )


def get_owned_job(*, username: str, job_id) -> DistressPdfExportJob:
    try:
        job = DistressPdfExportJob.objects.get(pk=job_id)
    except (DistressPdfExportJob.DoesNotExist, ValueError, TypeError) as exc:
        raise NotFound('Export job not found.') from exc
    if job.username != username:
        raise PermissionDenied('You do not have permission to view this export job.')
    return job


def claim_next_job() -> DistressPdfExportJob | None:
    """Atomically claim one queued job for processing (concurrency=1 safe)."""
    from django.db import connection

    lock_kwargs = {}
    if getattr(connection.features, 'has_select_for_update_skip_locked', False):
        lock_kwargs['skip_locked'] = True

    with transaction.atomic():
        job = (
            DistressPdfExportJob.objects
            .select_for_update(**lock_kwargs)
            .filter(status=DistressPdfExportJob.STATUS_QUEUED)
            .order_by('created_at')
            .first()
        )
        if not job:
            return None
        job.status = DistressPdfExportJob.STATUS_PROCESSING
        job.started_at = timezone.now()
        job.completed_reports = 0
        job.failed_reports = 0
        job.failure_details = []
        job.error_message = ''
        job.save(
            update_fields=[
                'status',
                'started_at',
                'completed_reports',
                'failed_reports',
                'failure_details',
                'error_message',
            ]
        )
        return job


def _job_temp_dir(job_id) -> str:
    root = os.path.join(tempfile.gettempdir(), TEMP_EXPORT_ROOT_NAME)
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, str(job_id))
    os.makedirs(path, exist_ok=True)
    return path


def _cleanup_dir(path: str | None) -> None:
    if not path:
        return
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        logger.warning('Failed to clean export temp dir %s', path, exc_info=True)


def process_export_job(job: DistressPdfExportJob) -> DistressPdfExportJob:
    """Prepare images per report, build one PDF, upload to S3, update job."""
    work_dir = _job_temp_dir(job.id)
    image_cache: dict[str, str | None] = {}
    successful: list[DistressReport] = []
    failures: list[dict[str, Any]] = []

    try:
        report_ids = list(job.report_ids or [])
        by_id = {
            r.id: r
            for r in DistressReport.objects.filter(
                username=job.username,
                pk__in=report_ids,
            )
        }

        for report_id in report_ids:
            try:
                report = by_id.get(int(report_id))
                if report is None:
                    raise ValueError('Report not found or not owned.')

                url = (report.Image_URL or '').strip()
                if url and url not in image_cache:
                    # One image at a time — bounded memory for large batches.
                    image_cache[url] = download_image(url, work_dir, optimize=True)

                successful.append(report)
                job.completed_reports = len(successful)
                job.failed_reports = len(failures)
                job.failure_details = list(failures)
                job.save(
                    update_fields=[
                        'completed_reports',
                        'failed_reports',
                        'failure_details',
                    ]
                )
            except Exception as exc:
                logger.warning(
                    'Export job %s skipped report %s: %s',
                    job.id,
                    report_id,
                    exc,
                )
                failures.append({
                    'report_id': int(report_id) if str(report_id).isdigit() else report_id,
                    'reason': 'Report could not be prepared for PDF export.',
                })
                job.completed_reports = len(successful)
                job.failed_reports = len(failures)
                job.failure_details = list(failures)
                job.save(
                    update_fields=[
                        'completed_reports',
                        'failed_reports',
                        'failure_details',
                    ]
                )

        if not successful:
            job.status = DistressPdfExportJob.STATUS_FAILED
            job.error_message = 'No reports could be prepared for PDF export.'
            job.completed_at = timezone.now()
            job.expires_at = job.completed_at + JOB_EXPIRES_AFTER
            job.save(
                update_fields=[
                    'status',
                    'error_message',
                    'completed_at',
                    'expires_at',
                    'completed_reports',
                    'failed_reports',
                    'failure_details',
                ]
            )
            return job

        pdf_path = os.path.join(work_dir, 'final.pdf')
        DistressReportBuilder(
            successful,
            temp_dir=work_dir,
            prefetch=False,
            prefetch_workers=1,
            image_cache=image_cache,
        ).build(pdf_path)

        s3_key = upload_local_file_to_s3(
            pdf_path,
            filename=ASYNC_PDF_FILENAME,
        )
        job.output_s3_key = s3_key
        job.completed_at = timezone.now()
        job.expires_at = job.completed_at + JOB_EXPIRES_AFTER
        job.status = (
            DistressPdfExportJob.STATUS_COMPLETED_WITH_ERRORS
            if failures
            else DistressPdfExportJob.STATUS_COMPLETED
        )
        job.error_message = ''
        job.save(
            update_fields=[
                'output_s3_key',
                'completed_at',
                'expires_at',
                'status',
                'error_message',
                'completed_reports',
                'failed_reports',
                'failure_details',
            ]
        )
        return job
    except Exception as exc:
        logger.exception('Export job %s failed', job.id)
        job.status = DistressPdfExportJob.STATUS_FAILED
        job.error_message = 'PDF export failed.'
        job.completed_at = timezone.now()
        job.expires_at = job.completed_at + JOB_EXPIRES_AFTER
        job.save(
            update_fields=[
                'status',
                'error_message',
                'completed_at',
                'expires_at',
            ]
        )
        return job
    finally:
        _cleanup_dir(work_dir)
