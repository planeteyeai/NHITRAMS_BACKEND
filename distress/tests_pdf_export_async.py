"""Tests for async distress PDF export jobs and image optimization."""

from __future__ import annotations

import os
from io import BytesIO
from unittest.mock import patch

from django.test import TestCase, override_settings
from PIL import Image as PILImage
from rest_framework import status
from rest_framework.test import APIClient

from authentication.jwt_utils import create_access_token
from distress.export_jobs import (
    ASYNC_PDF_FILENAME,
    claim_next_job,
    create_export_job,
    process_export_job,
    serialize_job,
)
from distress.models import DistressPdfExportJob, DistressReport
from pdf.utils import (
    PDF_IMAGE_MAX_HEIGHT_PX,
    PDF_IMAGE_MAX_WIDTH_PX,
    TempWorkspace,
    download_image,
    optimize_image_for_pdf,
)
from pdf.tests import DISTRESS_PAYLOAD


@override_settings(JWT_SECRET_KEY='test-jwt-secret')
class DistressPDFAsyncExportAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.token = create_access_token('ram.patil')
        self.other_token = create_access_token('other.user')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}
        self.other_auth = {'HTTP_AUTHORIZATION': f'Bearer {self.other_token}'}

    def _create(self, username='ram.patil', **overrides):
        return DistressReport.objects.create(username=username, **{**DISTRESS_PAYLOAD, **overrides})

    def test_async_requires_auth(self):
        response = self.client.post('/distress/export/pdf/async/', {'ids': [1]}, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_async_rejects_empty_ids(self):
        response = self.client.post(
            '/distress/export/pdf/async/',
            {'ids': []},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_async_rejects_invalid_ids(self):
        response = self.client.post(
            '/distress/export/pdf/async/',
            {'ids': ['abc']},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_async_rejects_unauthorized_ids(self):
        report = self._create(username='other.user')
        response = self.client.post(
            '/distress/export/pdf/async/',
            {'ids': [report.id]},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_async_creates_job_202_and_dedupes(self):
        a = self._create()
        b = self._create(Distress_Type='Cracking')
        response = self.client.post(
            '/distress/export/pdf/async/',
            {'ids': [a.id, b.id, a.id]},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        body = response.json()
        self.assertEqual(body['status'], 'queued')
        self.assertEqual(body['total_reports'], 2)
        self.assertEqual(body['completed_reports'], 0)
        self.assertEqual(body['failed_reports'], 0)
        self.assertEqual(body['progress'], 0)
        self.assertIsNone(body['download_url'])
        self.assertTrue(body['job_id'])

        job = DistressPdfExportJob.objects.get(pk=body['job_id'])
        self.assertEqual(job.report_ids, [a.id, b.id])
        self.assertEqual(job.username, 'ram.patil')

    def test_status_ownership_and_progress(self):
        a = self._create()
        create = self.client.post(
            '/distress/export/pdf/async/',
            {'ids': [a.id]},
            format='json',
            **self.auth,
        )
        job_id = create.json()['job_id']

        other = self.client.get(
            f'/distress/export/pdf/jobs/{job_id}/status/',
            **self.other_auth,
        )
        self.assertEqual(other.status_code, status.HTTP_403_FORBIDDEN)

        job = DistressPdfExportJob.objects.get(pk=job_id)
        job.status = DistressPdfExportJob.STATUS_PROCESSING
        job.completed_reports = 1
        job.total_reports = 2
        job.failed_reports = 0
        job.save()

        ok = self.client.get(
            f'/distress/export/pdf/jobs/{job_id}/status/',
            **self.auth,
        )
        self.assertEqual(ok.status_code, status.HTTP_200_OK)
        body = ok.json()
        self.assertEqual(body['status'], 'processing')
        self.assertEqual(body['progress'], 50)
        self.assertIsNone(body['download_url'])

    @patch('distress.export_jobs.generate_presigned_get_url')
    def test_status_completed_returns_fresh_presign(self, mock_presign):
        mock_presign.return_value = 'https://example.com/signed.pdf'
        a = self._create()
        create = self.client.post(
            '/distress/export/pdf/async/',
            {'ids': [a.id]},
            format='json',
            **self.auth,
        )
        job_id = create.json()['job_id']
        job = DistressPdfExportJob.objects.get(pk=job_id)
        job.status = DistressPdfExportJob.STATUS_COMPLETED
        job.completed_reports = 1
        job.total_reports = 1
        job.output_s3_key = 'Distress PDF Exports/test.pdf'
        job.save()

        response = self.client.get(
            f'/distress/export/pdf/jobs/{job_id}/status/',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body['download_url'], 'https://example.com/signed.pdf')
        self.assertEqual(body['progress'], 100)
        mock_presign.assert_called_once()
        self.assertEqual(mock_presign.call_args.args[0], 'Distress PDF Exports/test.pdf')


@override_settings(JWT_SECRET_KEY='test-jwt-secret')
class DistressPDFExportWorkerTests(TestCase):
    def _create(self, username='ram.patil', **overrides):
        return DistressReport.objects.create(username=username, **{**DISTRESS_PAYLOAD, **overrides})

    @patch('distress.export_jobs.upload_local_file_to_s3')
    def test_process_one_report_job(self, mock_upload):
        mock_upload.return_value = 'Distress PDF Exports/one.pdf'
        report = self._create(Image_URL='')
        job = create_export_job(username='ram.patil', ids=[report.id])
        claimed = claim_next_job()
        self.assertEqual(claimed.id, job.id)
        self.assertEqual(claimed.status, DistressPdfExportJob.STATUS_PROCESSING)

        process_export_job(claimed)
        claimed.refresh_from_db()
        self.assertEqual(claimed.status, DistressPdfExportJob.STATUS_COMPLETED)
        self.assertEqual(claimed.completed_reports, 1)
        self.assertEqual(claimed.failed_reports, 0)
        self.assertEqual(claimed.progress, 100)
        self.assertEqual(claimed.output_s3_key, 'Distress PDF Exports/one.pdf')
        mock_upload.assert_called_once()
        self.assertEqual(mock_upload.call_args.kwargs.get('filename'), ASYNC_PDF_FILENAME)

    @patch('distress.export_jobs.upload_local_file_to_s3')
    def test_process_multiple_and_partial_failure(self, mock_upload):
        mock_upload.return_value = 'Distress PDF Exports/multi.pdf'
        a = self._create()
        b = self._create(Distress_Type='Rutting')
        job = create_export_job(username='ram.patil', ids=[a.id, b.id])
        # Inject a missing id after creation to simulate mid-job disappearance.
        job.report_ids = [a.id, 999999, b.id]
        job.total_reports = 3
        job.save(update_fields=['report_ids', 'total_reports'])

        claimed = claim_next_job()
        process_export_job(claimed)
        claimed.refresh_from_db()
        self.assertEqual(claimed.status, DistressPdfExportJob.STATUS_COMPLETED_WITH_ERRORS)
        self.assertEqual(claimed.completed_reports, 2)
        self.assertEqual(claimed.failed_reports, 1)
        self.assertEqual(claimed.progress, 100)
        self.assertTrue(claimed.output_s3_key)
        self.assertEqual(len(claimed.failure_details), 1)
        self.assertEqual(claimed.failure_details[0]['report_id'], 999999)

    @patch('distress.export_jobs.upload_local_file_to_s3', side_effect=RuntimeError('S3 down'))
    def test_s3_failure_marks_failed(self, _mock_upload):
        report = self._create()
        job = create_export_job(username='ram.patil', ids=[report.id])
        claimed = claim_next_job()
        process_export_job(claimed)
        claimed.refresh_from_db()
        self.assertEqual(claimed.status, DistressPdfExportJob.STATUS_FAILED)
        self.assertEqual(claimed.error_message, 'PDF export failed.')
        self.assertFalse(claimed.output_s3_key)

    @patch('distress.export_jobs.upload_local_file_to_s3')
    def test_temp_dir_cleaned_after_success(self, mock_upload):
        mock_upload.return_value = 'Distress PDF Exports/clean.pdf'
        report = self._create()
        job = create_export_job(username='ram.patil', ids=[report.id])
        claimed = claim_next_job()
        from distress.export_jobs import _job_temp_dir

        expected_dir = _job_temp_dir(claimed.id)
        process_export_job(claimed)
        self.assertFalse(os.path.isdir(expected_dir))

    def test_serialize_progress_formula(self):
        job = DistressPdfExportJob(
            username='ram.patil',
            total_reports=32,
            completed_reports=18,
            failed_reports=1,
            status=DistressPdfExportJob.STATUS_PROCESSING,
        )
        self.assertEqual(job.progress, 59)
        payload = serialize_job(job, include_download_url=False)
        self.assertEqual(payload['progress'], 59)
        self.assertIsNone(payload['download_url'])

    def test_batched_ownership_uses_few_queries(self):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        from pdf.utils import get_owned_reports

        reports = [self._create(Distress_Type=f'T{i}') for i in range(8)]
        ids = [r.id for r in reports]
        with CaptureQueriesContext(connection) as ctx:
            ordered = get_owned_reports(DistressReport, 'ram.patil', ids)
        self.assertEqual([r.id for r in ordered], ids)
        # One batched SELECT — not 2×N EXISTS.
        self.assertLessEqual(len(ctx.captured_queries), 3)

    @override_settings(DISTRESS_PDF_IMAGE_PREP_CONCURRENCY=4, DISTRESS_PDF_PROGRESS_SAVE_EVERY=4)
    @patch('distress.export_jobs.upload_local_file_to_s3')
    def test_concurrent_image_prep_and_throttled_progress(self, mock_upload):
        mock_upload.return_value = 'Distress PDF Exports/conc.pdf'
        reports = [self._create(Image_URL='', Distress_Type=f'T{i}') for i in range(8)]
        job = create_export_job(username='ram.patil', ids=[r.id for r in reports])
        claimed = claim_next_job()

        save_calls = {'n': 0}
        real_save = DistressPdfExportJob.save

        def counting_save(self, *args, **kwargs):
            save_calls['n'] += 1
            return real_save(self, *args, **kwargs)

        with patch.object(DistressPdfExportJob, 'save', counting_save):
            process_export_job(claimed)
        claimed.refresh_from_db()
        self.assertEqual(claimed.status, DistressPdfExportJob.STATUS_COMPLETED)
        self.assertEqual(claimed.completed_reports, 8)
        # Throttled progress + final terminal save should be << 8 progress-only writes.
        self.assertLess(save_calls['n'], 20)

    def test_requeue_stale_processing_jobs(self):
        from datetime import timedelta
        from django.utils import timezone
        from distress.export_jobs import requeue_stale_processing_jobs

        report = self._create()
        job = create_export_job(username='ram.patil', ids=[report.id])
        job.status = DistressPdfExportJob.STATUS_PROCESSING
        job.started_at = timezone.now() - timedelta(hours=2)
        job.save(update_fields=['status', 'started_at'])

        with override_settings(DISTRESS_PDF_STALE_PROCESSING_SECONDS=60):
            n = requeue_stale_processing_jobs()
        self.assertGreaterEqual(n, 1)
        job.refresh_from_db()
        self.assertEqual(job.status, DistressPdfExportJob.STATUS_QUEUED)
        self.assertIsNone(job.started_at)


class PDFImageOptimizationTests(TestCase):
    def _make_large_png(self, path: str, size=(2000, 1500)):
        img = PILImage.new('RGB', size, color=(30, 120, 200))
        img.save(path, format='PNG')

    def test_optimize_resizes_and_strips_to_jpeg(self):
        with TempWorkspace() as ws:
            raw = os.path.join(ws.dir, 'big.png')
            self._make_large_png(raw)
            out = optimize_image_for_pdf(raw, ws.dir)
            self.assertIsNotNone(out)
            self.assertTrue(out.endswith('.jpg'))
            with PILImage.open(out) as img:
                self.assertEqual(img.format, 'JPEG')
                self.assertLessEqual(img.width, PDF_IMAGE_MAX_WIDTH_PX)
                self.assertLessEqual(img.height, PDF_IMAGE_MAX_HEIGHT_PX)
                self.assertIsNone(img.info.get('exif'))

    @patch('pdf.utils.urllib.request.urlopen')
    def test_download_image_optimizes(self, mock_urlopen):
        buf = BytesIO()
        PILImage.new('RGB', (1800, 1200), color=(10, 20, 30)).save(buf, format='JPEG')
        payload = buf.getvalue()

        class FakeResp:
            headers = {'Content-Type': 'image/jpeg'}

            def read(self, n=-1):
                return payload

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        mock_urlopen.return_value = FakeResp()
        with TempWorkspace() as ws:
            path = download_image('https://example.com/photo.jpg', ws.dir, optimize=True)
            self.assertIsNotNone(path)
            with PILImage.open(path) as img:
                self.assertLessEqual(img.width, PDF_IMAGE_MAX_WIDTH_PX)
                self.assertLessEqual(img.height, PDF_IMAGE_MAX_HEIGHT_PX)

    def test_display_targets_match_layout_box(self):
        # Sanity: targets are derived from ~2x@175dpi of the PDF image box.
        self.assertGreater(PDF_IMAGE_MAX_WIDTH_PX, 400)
        self.assertLess(PDF_IMAGE_MAX_WIDTH_PX, 1200)
        self.assertGreater(PDF_IMAGE_MAX_HEIGHT_PX, 300)
        self.assertLess(PDF_IMAGE_MAX_HEIGHT_PX, 1000)
