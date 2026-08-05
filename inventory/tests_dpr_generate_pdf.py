"""Tests for Asset Change Detection DPR PDF generation API."""

from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from authentication.jwt_utils import create_access_token
from inventory.services.dpr_generate_pdf import (
    build_dpr_pdf_filename,
    render_asset_change_dpr_pdf,
)
from pdf.utils import TempWorkspace

SAMPLE_PROJECT = {
    'id': 1,
    'project_name': 'Abu Road to Swaroopganj',
    'start_chainage': '646.000',
    'end_chainage': '680.000',
    'total_project_length': '34 km',
}

SAMPLE_PAYLOAD = {
    'project_details': {
        'project_name': 'Abu Road to Swaroopganj',
        'start_chainage': '646.000',
        'end_chainage': '680.000',
        'total_project_length': '34 km',
        'reporting_engineer_name': 'Ram Patil',
        'assessment_date': '2026-08-05',
        'assessment_date_display': '05/08/2026',
        'from_date': '2026-08-01',
        'to_date': '2026-08-05',
        'from_date_display': '01/08/2026',
        'to_date_display': '05/08/2026',
        'report_period_display': '01/08/2026 - 05/08/2026',
        'generation_date': '2026-08-05T10:00:00+00:00',
        'generation_date_display': '05/08/2026',
        'generation_datetime_display': '05/08/2026 15:30 IST',
        'generated_by': 'ram.patil',
    },
    'overall_summary': [
        {
            'asset_type': 'Informatory Signs',
            'total_quantity': 1000,
            'damaged_quantity': 5,
            'available_quantity': 995,
        }
    ],
    'removed_damaged_assets': [
        {
            'sr_no': 1,
            'asset_type': 'Informatory Signs',
            'chainage': '650.200',
            'latitude': 24.5,
            'longitude': 72.8,
            'side': 'LHS L1',
            'date_and_time': '05-08-2026 10:00 AM',
            'description': 'Damaged',
            'evidence_available': 'Refer Image No. 1',
            'nos': 1,
            'length': 0,
            'width': 0,
            'depth': 0,
            'total_quantity': 1,
            'unit': 'Nos',
            'severity': 'Low',
        }
    ],
    'removed_damaged_asset_photos': [
        {
            'image_number': 1,
            'image_label': 'Image 1',
            'asset_type': 'Informatory Signs',
            'chainage': '650.200',
            'photo_url': 'https://example.com/missing.jpg',
        }
    ],
    'routine_maintenance': [
        {
            'sr_no': 1,
            'maintenance_type': 'Trimming, Pruning of Median plants',
            'chainage': '651.000',
            'latitude': 24.51,
            'longitude': 72.81,
            'side': 'LHS L1',
            'date_and_time': '05-08-2026 11:00 AM',
            'description': 'Trimmed',
            'evidence_available': 'Refer Image No. 1',
            'nos': 0,
            'length': 10,
            'width': 0,
            'depth': 0,
            'total_quantity': 10,
            'unit': 'RMT',
            'severity': 'Low',
        }
    ],
    'routine_maintenance_photos': [
        {
            'image_number': 1,
            'image_label': 'Image 1',
            'maintenance_type': 'Trimming, Pruning of Median plants',
            'chainage': '651.000',
            'photo_url': 'https://example.com/rm-missing.jpg',
        }
    ],
}


def _body(**overrides):
    data = {
        'project_name': SAMPLE_PROJECT['project_name'],
        'reporting_engineer_name': 'Ram Patil',
        'assessment_date': '2026-08-05',
        'from_date': '2026-08-01',
        'to_date': '2026-08-05',
    }
    data.update(overrides)
    return data


@override_settings(JWT_SECRET_KEY='test-jwt-secret')
class AssetChangeDPRGeneratePDFTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.token = create_access_token('ram.patil')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}

    def test_requires_auth(self):
        response = self.client.post(
            '/api/inventory/dpr/generate-pdf/',
            _body(),
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_filename_helper(self):
        name = build_dpr_pdf_filename(
            'Abu Road to Swaroopganj', '2026-08-01', '2026-08-05'
        )
        self.assertEqual(name, 'DPR_Abu_Road_to_Swaroopganj_20260801_20260805.pdf')

    def test_pdf_builder_empty_sections(self):
        payload = {
            'project_details': SAMPLE_PAYLOAD['project_details'],
            'overall_summary': [],
            'removed_damaged_assets': [],
            'removed_damaged_asset_photos': [],
            'routine_maintenance': [],
            'routine_maintenance_photos': [],
        }
        with TempWorkspace() as workspace:
            pdf_path = workspace.make_pdf_path('empty_')
            render_asset_change_dpr_pdf(payload, pdf_path, temp_dir=workspace.dir)
            with open(pdf_path, 'rb') as fh:
                content = fh.read()
            self.assertTrue(content.startswith(b'%PDF'))
            self.assertGreater(len(content), 500)

    def test_pdf_builder_with_tables_and_missing_images(self):
        with TempWorkspace() as workspace:
            pdf_path = workspace.make_pdf_path('full_')
            with patch(
                'inventory.services.dpr_generate_pdf.download_image',
                return_value=None,
            ):
                render_asset_change_dpr_pdf(
                    SAMPLE_PAYLOAD, pdf_path, temp_dir=workspace.dir
                )
            with open(pdf_path, 'rb') as fh:
                content = fh.read()
            self.assertTrue(content.startswith(b'%PDF'))
            self.assertGreater(len(content), 1000)

    @patch(
        'inventory.views_dpr_generate.DPRGenerationService.generate',
        return_value=SAMPLE_PAYLOAD,
    )
    @patch(
        'inventory.services.dpr_generate_pdf.download_image',
        return_value=None,
    )
    def test_pdf_endpoint_returns_attachment(self, _mock_img, _mock_generate):
        response = self.client.post(
            '/api/inventory/dpr/generate-pdf/',
            _body(),
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn(
            'DPR_Abu_Road_to_Swaroopganj_20260801_20260805.pdf',
            response['Content-Disposition'],
        )
        chunks = b''.join(response.streaming_content)
        self.assertTrue(chunks.startswith(b'%PDF'))

    @patch(
        'inventory.views_dpr_generate.DPRGenerationService.generate',
        side_effect=ValidationError({'project_name': ['Invalid project selected.']}),
    )
    def test_invalid_project(self, _mock_generate):
        response = self.client.post(
            '/api/inventory/dpr/generate-pdf/',
            _body(project_name='Does Not Exist'),
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data['success'])

    def test_invalid_date_range_rejected(self):
        response = self.client.post(
            '/api/inventory/dpr/generate-pdf/',
            _body(from_date='2026-08-10', to_date='2026-08-01'),
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch(
        'inventory.views_dpr_generate.DPRGenerationService.generate',
        return_value={
            **SAMPLE_PAYLOAD,
            'overall_summary': [],
            'removed_damaged_assets': [],
            'removed_damaged_asset_photos': [],
            'routine_maintenance': [],
            'routine_maintenance_photos': [],
        },
    )
    def test_empty_project_still_returns_pdf(self, _mock_generate):
        response = self.client.post(
            '/api/inventory/dpr/generate-pdf/',
            _body(),
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        chunks = b''.join(response.streaming_content)
        self.assertTrue(chunks.startswith(b'%PDF'))
