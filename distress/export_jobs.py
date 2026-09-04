"""Distress PDF async export: job creation, processing, status serialization."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from distress.models import DistressPdfExportJob, DistressReport
from pdf.builders import DistressReportBuilder
from pdf.http_client import ImageHttpClient
from pdf.s3 import generate_presigned_get_url, upload_local_file_to_s3
from pdf.utils import IMAGE_MAX_BYTES, IMAGE_TIMEOUT_SECONDS, download_image, get_owned_reports

logger = logging.getLogger(__name__)

ASYNC_PDF_FILENAME = 'AIS_Identifier_Distress_Reports.pdf'
JOB_EXPIRES_AFTER = timedelta(days=7)
TEMP_EXPORT_ROOT_NAME = 'distress_export'


def _image_prep_concurrency() -> int:
    return max(1, int(getattr(settings, 'DISTRESS_PDF_IMAGE_PREP_CONCURRENCY', 2)))


def _progress_save_every() -> int:
    return max(1, int(getattr(settings, 'DISTRESS_PDF_PROGRESS_SAVE_EVERY', 4)))


def _stale_processing_seconds() -> int:
    return max(60, int(getattr(settings, 'DISTRESS_PDF_STALE_PROCESSING_SECONDS', 30 * 60)))


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

    # Same ownership / dedupe / max rules as GET export (batched queries).
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


def requeue_stale_processing_jobs() -> int:
    """Re-queue jobs stuck in processing longer than the configured stale window."""
    cutoff = timezone.now() - timedelta(seconds=_stale_processing_seconds())
    stale = list(
        DistressPdfExportJob.objects.filter(
            status=DistressPdfExportJob.STATUS_PROCESSING,
            started_at__lt=cutoff,
        ).order_by('started_at')[:50]
    )
    if not stale:
        return 0
    now = timezone.now()
    for job in stale:
        job.status = DistressPdfExportJob.STATUS_QUEUED
        job.started_at = None
        job.completed_reports = 0
        job.failed_reports = 0
        job.failure_details = []
        job.error_message = 'Requeued after stale processing timeout.'
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
        logger.warning('Requeued stale export job %s (started before %s)', job.id, cutoff)
    return len(stale)


def claim_next_job() -> DistressPdfExportJob | None:
    """Atomically claim one queued job for processing (worker process concurrency=1)."""
    from django.db import connection

    requeue_stale_processing_jobs()

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


class _ProgressCoordinator:
    """Single-threaded progress owner — workers never write the job row."""

    def __init__(self, job: DistressPdfExportJob, save_every: int):
        self.job = job
        self.save_every = max(1, save_every)
        self.completed = 0
        self.failed = 0
        self.failures: list[dict[str, Any]] = []
        self._since_save = 0

    def mark_success(self) -> None:
        self.completed += 1
        self._maybe_persist(force=False)

    def mark_failure(self, report_id: Any) -> None:
        self.failed += 1
        self.failures.append({
            'report_id': int(report_id) if str(report_id).isdigit() else report_id,
            'reason': 'Report could not be prepared for PDF export.',
        })
        self._maybe_persist(force=False)

    def _maybe_persist(self, *, force: bool) -> None:
        self._since_save += 1
        if not force and self._since_save < self.save_every:
            return
        self.persist()

    def persist(self) -> None:
        self.job.completed_reports = self.completed
        self.job.failed_reports = self.failed
        self.job.failure_details = list(self.failures)
        self.job.save(
            update_fields=[
                'completed_reports',
                'failed_reports',
                'failure_details',
            ]
        )
        self._since_save = 0


def process_export_job(job: DistressPdfExportJob) -> DistressPdfExportJob:
    """Prepare images with bounded concurrency, build one PDF, upload to S3."""
    work_dir = _job_temp_dir(job.id)
    image_cache: dict[str, str | None] = {}
    coordinator = _ProgressCoordinator(job, _progress_save_every())
    concurrency = _image_prep_concurrency()

    try:
        report_ids = list(job.report_ids or [])
        by_id = {
            r.id: r
            for r in DistressReport.objects.filter(
                username=job.username,
                pk__in=report_ids,
            )
        }

        # Preserve request order: classify missing reports first, then prepare images.
        ordered_reports: list[DistressReport] = []
        for report_id in report_ids:
            report = by_id.get(int(report_id)) if str(report_id).isdigit() or isinstance(report_id, int) else None
            if report is None:
                try:
                    report = by_id.get(int(report_id))
                except (TypeError, ValueError):
                    report = None
            if report is None:
                coordinator.mark_failure(report_id)
                continue
            ordered_reports.append(report)

        http = ImageHttpClient(
            timeout_seconds=IMAGE_TIMEOUT_SECONDS,
            max_bytes=IMAGE_MAX_BYTES,
            maxsize=concurrency,
        )
        cache_lock = threading.Lock()
        inflight: dict[str, threading.Event] = {}
        inflight_lock = threading.Lock()

        def _prepare_url(url: str) -> str | None:
            """Dedup concurrent downloads of the same URL; disk-backed result only."""
            with cache_lock:
                if url in image_cache:
                    return image_cache[url]

            with inflight_lock:
                event = inflight.get(url)
                is_leader = event is None
                if is_leader:
                    event = threading.Event()
                    inflight[url] = event

            if not is_leader:
                event.wait(timeout=IMAGE_TIMEOUT_SECONDS + 60)
                with cache_lock:
                    return image_cache.get(url)

            path = None
            try:
                path = download_image(url, work_dir, optimize=True, http_client=http)
                with cache_lock:
                    image_cache[url] = path
                return path
            finally:
                with inflight_lock:
                    inflight.pop(url, None)
                event.set()

        def _prepare_report(report: DistressReport) -> tuple[int, bool]:
            # Image download failure → placeholder later; report still succeeds.
            try:
                url = (report.Image_URL or '').strip()
                if url:
                    _prepare_url(url)
                return int(report.id), True
            except Exception as exc:
                logger.warning('Export job %s report %s prepare error: %s', job.id, report.id, exc)
                return int(report.id), False

        # Bounded in-flight futures (at most `concurrency` active downloads).
        pending = list(ordered_reports)
        in_flight: dict[Any, DistressReport] = {}
        ok_ids: set[int] = set()

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            while pending or in_flight:
                while pending and len(in_flight) < concurrency:
                    report = pending.pop(0)
                    fut = pool.submit(_prepare_report, report)
                    in_flight[fut] = report
                if not in_flight:
                    break
                done, _ = wait(list(in_flight.keys()), return_when=FIRST_COMPLETED)
                for fut in done:
                    report = in_flight.pop(fut)
                    try:
                        report_id, ok = fut.result()
                    except Exception:
                        report_id, ok = report.id, False
                    if ok:
                        ok_ids.add(int(report_id))
                        coordinator.mark_success()
                    else:
                        coordinator.mark_failure(report_id)

        # Keep PDF report order identical to requested IDs (successful only).
        successful = [r for r in ordered_reports if r.id in ok_ids]

        coordinator.persist()  # always flush progress before PDF / terminal states

        if not successful:
            job.status = DistressPdfExportJob.STATUS_FAILED
            job.error_message = 'No reports could be prepared for PDF export.'
            job.completed_at = timezone.now()
            job.expires_at = job.completed_at + JOB_EXPIRES_AFTER
            job.completed_reports = coordinator.completed
            job.failed_reports = coordinator.failed
            job.failure_details = list(coordinator.failures)
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
        job.completed_reports = coordinator.completed
        job.failed_reports = coordinator.failed
        job.failure_details = list(coordinator.failures)
        job.status = (
            DistressPdfExportJob.STATUS_COMPLETED_WITH_ERRORS
            if coordinator.failures
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
    except Exception:
        logger.exception('Export job %s failed', job.id)
        job.status = DistressPdfExportJob.STATUS_FAILED
        job.error_message = 'PDF export failed.'
        job.completed_at = timezone.now()
        job.expires_at = job.completed_at + JOB_EXPIRES_AFTER
        job.completed_reports = coordinator.completed
        job.failed_reports = coordinator.failed
        job.failure_details = list(coordinator.failures)
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
    finally:
        _cleanup_dir(work_dir)
