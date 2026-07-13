"""Tests for distress and inventory PDF export APIs."""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from authentication.jwt_utils import create_access_token
from distress.models import DistressReport
from inventory.models import InventoryReport
from pdf.builders import DistressReportBuilder, InventoryReportBuilder
from pdf.utils import TempWorkspace, download_image, filename_for, parse_export_ids


DISTRESS_PAYLOAD = {
    'Latitude': 19.076,
    'Longitude': 72.877,
    'Chainage_Start': 10.0,
    'Chainage_End': 10.5,
    'Project_Name': 'NH-48',
    'Distress_Type': 'Pothole',
    'Direction': 'LHS',
    'Date': '2026-07-11',
    'Length': 1.2,
    'Carriage_Type': 'Flexible',
    'Width': 0.5,
    'Depth': 0.1,
    'Lane': 'L1',
    'No_of_Distress': 1,
    'Image_URL': '',
}

INVENTORY_PAYLOAD = {
    'Project_Name': 'NH-48',
    'Chainage_start': 10.0,
    'Chainage_end': 10.5,
    'Direction': 'LHS',
    'Asset_type': 'Sign Boards',
    'Latitude': 19.076,
    'Longitude': 72.877,
    'Date': '2026-07-11',
    'Sub_Asset_Type': 'Gantry',
    'Carriage_Type': 'Flexible',
    'Lane': 'L1',
    'No_of_inventories': 2,
    'Image_URL': '',
    'Video_URL': 'https://example.com/video.mp4',
}


def _pdf_bytes(response) -> bytes:
    """Read PDF bytes from a streaming or buffered DRF/Django response."""
    if hasattr(response, 'streaming_content'):
        return b''.join(response.streaming_content)
    return response.content


@override_settings(JWT_SECRET_KEY='test-jwt-secret')
class DistressPDFExportTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.token = create_access_token('ram.patil')
        self.other_token = create_access_token('other.user')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}
        self.other_auth = {'HTTP_AUTHORIZATION': f'Bearer {self.other_token}'}

    def _create(self, username='ram.patil', **overrides):
        return DistressReport.objects.create(username=username, **{**DISTRESS_PAYLOAD, **overrides})

    def test_export_requires_auth(self):
        response = self.client.get('/distress/export/pdf/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_export_all_owned_reports(self):
        self._create()
        self._create(Distress_Type='Rutting')
        self._create(username='other.user')

        response = self.client.get('/distress/export/pdf/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn('road_distress_report_', response['Content-Disposition'])
        self.assertTrue(_pdf_bytes(response).startswith(b'%PDF'))

    def test_export_single_report(self):
        report = self._create()
        response = self.client.get(f'/distress/export/pdf/?id={report.id}', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(_pdf_bytes(response).startswith(b'%PDF'))

    def test_export_multiple_reports(self):
        a = self._create()
        b = self._create(Distress_Type='Cracking')
        response = self.client.get(f'/distress/export/pdf/?ids={a.id},{b.id}', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(_pdf_bytes(response).startswith(b'%PDF'))

    def test_export_other_users_report_returns_403(self):
        report = self._create(username='other.user')
        response = self.client.get(f'/distress/export/pdf/?id={report.id}', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_export_missing_report_returns_404(self):
        response = self.client.get('/distress/export/pdf/?id=99999', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_export_empty_history_returns_404(self):
        response = self.client.get('/distress/export/pdf/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_builder_placeholder_when_no_image(self):
        report = self._create(Image_URL='')
        with TempWorkspace() as ws:
            path = ws.make_pdf_path('d_')
            DistressReportBuilder([report], temp_dir=ws.dir).build(path)
            with open(path, 'rb') as fh:
                self.assertTrue(fh.read(4) == b'%PDF')


@override_settings(JWT_SECRET_KEY='test-jwt-secret')
class InventoryPDFExportTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.token = create_access_token('ram.patil')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}

    def _create(self, username='ram.patil', **overrides):
        return InventoryReport.objects.create(username=username, **{**INVENTORY_PAYLOAD, **overrides})

    def test_export_requires_auth(self):
        response = self.client.get('/inventory/export/pdf/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_export_all_owned_reports(self):
        self._create()
        self._create(Asset_type='Culvert', Video_URL='')
        response = self.client.get('/inventory/export/pdf/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('road_inventory_report_', response['Content-Disposition'])
        self.assertTrue(_pdf_bytes(response).startswith(b'%PDF'))

    def test_export_single_report(self):
        report = self._create()
        response = self.client.get(f'/inventory/export/pdf/?id={report.id}', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_export_multiple_reports(self):
        a = self._create()
        b = self._create(Asset_type='Trees')
        response = self.client.get(f'/inventory/export/pdf/?ids={a.id},{b.id}', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_export_other_users_report_returns_403(self):
        report = self._create(username='other.user')
        response = self.client.get(f'/inventory/export/pdf/?id={report.id}', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_video_url_available_in_builder(self):
        with_video = self._create(Video_URL='https://example.com/v.mp4')
        without_video = self._create(Video_URL='')
        builder = InventoryReportBuilder([with_video, without_video], temp_dir='.')
        available = dict(builder._metric_pairs(with_video))
        missing = dict(builder._metric_pairs(without_video))
        self.assertEqual(available['Video URL'], 'Available')
        self.assertEqual(missing['Video URL'], 'Not Available')
        with TempWorkspace() as ws:
            path = ws.make_pdf_path('i_')
            InventoryReportBuilder([with_video, without_video], temp_dir=ws.dir).build(path)
            with open(path, 'rb') as fh:
                self.assertTrue(fh.read(4) == b'%PDF')


class PDFUtilsTests(TestCase):
    def test_parse_id_and_ids(self):
        self.assertEqual(parse_export_ids({'id': '15'}), [15])
        self.assertEqual(parse_export_ids({'ids': '5,7,10'}), [5, 7, 10])
        self.assertIsNone(parse_export_ids({}))

    def test_filename_helpers(self):
        self.assertEqual(filename_for('distress', '2026-07-13'), 'road_distress_report_2026-07-13.pdf')
        self.assertEqual(filename_for('inventory', '2026-07-13'), 'road_inventory_report_2026-07-13.pdf')

    def test_download_image_invalid_returns_none(self):
        with TempWorkspace() as ws:
            self.assertIsNone(download_image('', ws.dir))
            self.assertIsNone(download_image('not-a-url', ws.dir))

    @patch('pdf.utils.urllib.request.urlopen')
    def test_download_image_success(self, mock_urlopen):
        class FakeResp:
            headers = {'Content-Type': 'image/jpeg'}

            def read(self, n=-1):
                return b'\xff\xd8\xff' + b'0' * 100

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        mock_urlopen.return_value = FakeResp()
        with TempWorkspace() as ws:
            path = download_image('https://example.com/photo.jpg', ws.dir)
            self.assertIsNotNone(path)
            self.assertTrue(path.endswith('.jpg'))
