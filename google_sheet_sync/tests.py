"""Tests for Google Sheets distress synchronization."""

from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import MagicMock, patch

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from distress.models import Distress, DistressType, Project, RoadSegment
from google_sheet_sync.models import GoogleSheetDistress
from google_sheet_sync.serializers import GoogleSheetSyncSerializer
from google_sheet_sync.services import (
    DistressTypeNotFoundError,
    ProjectNotFoundError,
    RoadSegmentNotFoundError,
    StagingValidationError,
    normalize_project_name,
    sync_sheet_row,
)
from google_sheet_sync.services.google_sheet_processor import GoogleSheetProcessor

ENDPOINT = '/api/google-sheet-sync/'

BASE_PAYLOAD = {
    'sync_id': '',
    'status': '',
    'latitude': 19.991,
    'longitude': 73.789,
    'chainage_start': 100,
    'chainage_end': '',
    'project_name': 'Agra Bypass Road',
    'distress_type': 'Crack',
    'direction': '',
    'total_distress': '',
    'oblique_crack': '',
    'pothole': '',
    'edge_break': '',
    'patching': '',
    'bleeding': '',
    'hotspots': '',
    'alligator_crack': '',
    'roughness': '',
    'repair': '',
    'block_crack': '',
    'rutting': '',
    'longitudinal_crack': '',
    'raveling': '',
    'date': '2026-07-17',
    'length': '',
    'area': '',
    'carriage_type': '',
    'single_discrete_crack': '',
    'multiple_cracks': '',
    'joint_crack': '',
    'joint_seal_defects': '',
    'punchout': '',
    'slippage': '',
    'heaves': '',
    'simple_crack': '',
    'transverse_crack': '',
    'width': '',
    'depth': '',
    'lane': '',
    'hairline_crack': '',
    'hungry_surface': '',
    'settlement': '',
    'shoving': '',
    'stripping': '',
    'faulting_stepping_in_cracks': '',
    'popout_small_hole': '',
    'single_longitudinal_crack': '',
    'blowup_or_bulking': '',
    'polished_surface_glazing': '',
    'depression': '',
    'image_url': 'https://example.com/img.jpg',
}


class NormalizeProjectNameTests(TestCase):
    def test_trims_and_collapses_spaces_and_lowercases(self):
        self.assertEqual(
            normalize_project_name(' Agra   Bypass Road '),
            'agra bypass road',
        )

    def test_empty_and_none(self):
        self.assertEqual(normalize_project_name(''), '')
        self.assertEqual(normalize_project_name('   '), '')
        self.assertEqual(normalize_project_name(None), '')


class GoogleSheetSyncSerializerTests(TestCase):
    def test_blank_strings_become_none_and_strings_are_trimmed(self):
        payload = {
            **BASE_PAYLOAD,
            'direction': '  LHS  ',
            'chainage_end': '',
            'status': '   ',
        }
        serializer = GoogleSheetSyncSerializer(data=payload)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['direction'], 'LHS')
        self.assertIsNone(serializer.validated_data['chainage_end'])
        self.assertIsNone(serializer.validated_data['status'])
        self.assertIsNone(serializer.validated_data['sync_id'])

    def test_project_name_and_distress_type_required(self):
        payload = {**BASE_PAYLOAD, 'project_name': '', 'distress_type': ''}
        serializer = GoogleSheetSyncSerializer(data=payload)
        self.assertFalse(serializer.is_valid())
        self.assertIn('project_name', serializer.errors)
        self.assertIn('distress_type', serializer.errors)

    def test_latitude_longitude_bounds(self):
        bad_lat = GoogleSheetSyncSerializer(data={**BASE_PAYLOAD, 'latitude': 91})
        self.assertFalse(bad_lat.is_valid())
        self.assertIn('latitude', bad_lat.errors)

        bad_lng = GoogleSheetSyncSerializer(data={**BASE_PAYLOAD, 'longitude': -181})
        self.assertFalse(bad_lng.is_valid())
        self.assertIn('longitude', bad_lng.errors)

    def test_invalid_sync_id_rejected(self):
        serializer = GoogleSheetSyncSerializer(
            data={**BASE_PAYLOAD, 'sync_id': 'not-a-uuid'}
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('sync_id', serializer.errors)


class GoogleSheetSyncServiceTests(TestCase):
    def setUp(self):
        self.project = Project(project_id=5, project_name='Agra Bypass Road')

    @patch('google_sheet_sync.services.google_sheet_processor.GoogleSheetProcessor.process')
    @patch('google_sheet_sync.services.sync.resolve_project')
    def test_create_when_sync_id_empty(self, mock_resolve, mock_process):
        mock_resolve.return_value = (self.project, 'agra bypass road')
        mock_process.return_value = MagicMock()
        serializer = GoogleSheetSyncSerializer(data=BASE_PAYLOAD)
        self.assertTrue(serializer.is_valid(), serializer.errors)

        instance, action = sync_sheet_row(serializer.validated_data)

        self.assertEqual(action, 'created')
        self.assertIsInstance(instance.sync_id, uuid.UUID)
        self.assertEqual(instance.distress_type, 'Crack')
        self.assertEqual(instance.project_id, 5)
        self.assertEqual(instance.project_name_normalized, 'agra bypass road')
        self.assertIsNone(instance.chainage_end)
        self.assertEqual(GoogleSheetDistress.objects.count(), 1)
        mock_process.assert_called_once()

    @patch('google_sheet_sync.services.google_sheet_processor.GoogleSheetProcessor.process')
    @patch('google_sheet_sync.services.sync.resolve_project')
    def test_update_when_sync_id_exists(self, mock_resolve, mock_process):
        mock_resolve.return_value = (self.project, 'agra bypass road')
        mock_process.return_value = MagicMock()
        serializer = GoogleSheetSyncSerializer(data=BASE_PAYLOAD)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        created, _ = sync_sheet_row(serializer.validated_data)

        update_payload = {
            **BASE_PAYLOAD,
            'sync_id': str(created.sync_id),
            'distress_type': 'Pothole',
            'latitude': 20.1,
        }
        update_serializer = GoogleSheetSyncSerializer(data=update_payload)
        self.assertTrue(update_serializer.is_valid(), update_serializer.errors)

        updated, action = sync_sheet_row(update_serializer.validated_data)

        self.assertEqual(action, 'updated')
        self.assertEqual(updated.sync_id, created.sync_id)
        self.assertEqual(updated.distress_type, 'Pothole')
        self.assertEqual(updated.latitude, 20.1)
        self.assertEqual(GoogleSheetDistress.objects.count(), 1)

    @patch('google_sheet_sync.services.sync.resolve_project')
    def test_project_not_found_raises(self, mock_resolve):
        mock_resolve.side_effect = ProjectNotFoundError('Project not found')
        serializer = GoogleSheetSyncSerializer(data=BASE_PAYLOAD)
        self.assertTrue(serializer.is_valid(), serializer.errors)

        with self.assertRaises(ProjectNotFoundError):
            sync_sheet_row(serializer.validated_data)


class GoogleSheetSyncApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.project = Project(project_id=5, project_name='Agra Bypass Road')

    @patch('google_sheet_sync.services.google_sheet_processor.GoogleSheetProcessor.process')
    @patch('google_sheet_sync.services.sync.resolve_project')
    def test_post_create_success(self, mock_resolve, mock_process):
        mock_resolve.return_value = (self.project, 'agra bypass road')
        mock_process.return_value = MagicMock()
        response = self.client.post(ENDPOINT, BASE_PAYLOAD, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertEqual(response.data['action'], 'created')
        self.assertTrue(response.data['sync_id'])
        uuid.UUID(response.data['sync_id'])  # valid UUID
        mock_process.assert_called_once()

    @patch('google_sheet_sync.services.google_sheet_processor.GoogleSheetProcessor.process')
    @patch('google_sheet_sync.services.sync.resolve_project')
    def test_post_update_success(self, mock_resolve, mock_process):
        mock_resolve.return_value = (self.project, 'agra bypass road')
        mock_process.return_value = MagicMock()
        create_resp = self.client.post(ENDPOINT, BASE_PAYLOAD, format='json')
        sync_id = create_resp.data['sync_id']

        update_payload = {**BASE_PAYLOAD, 'sync_id': sync_id, 'distress_type': 'Rutting'}
        response = self.client.post(ENDPOINT, update_payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['action'], 'updated')
        self.assertEqual(response.data['sync_id'], sync_id)

    @patch('google_sheet_sync.services.sync.resolve_project')
    def test_project_not_found_returns_404(self, mock_resolve):
        mock_resolve.side_effect = ProjectNotFoundError('Project not found')
        response = self.client.post(ENDPOINT, BASE_PAYLOAD, format='json')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            response.data,
            {'success': False, 'message': 'Project not found'},
        )

    def test_validation_error_returns_400(self):
        payload = {**BASE_PAYLOAD, 'project_name': '', 'latitude': 999}
        response = self.client.post(ENDPOINT, payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data['success'])
        self.assertEqual(response.data['message'], 'Validation failed')
        self.assertIn('errors', response.data)

    @patch(
        'google_sheet_sync.services.google_sheet_processor.GoogleSheetProcessor.process'
    )
    @patch('google_sheet_sync.services.sync.resolve_project')
    def test_promote_failure_still_returns_sync_id_and_next_edit_updates(
        self, mock_resolve, mock_process
    ):
        """Regression: processing errors must not leave the sheet without sync_id.

        Simulate Apps Script writing sync_id from the error response, then
        sending it on the next edit — staging count must stay at 1.
        """
        from google_sheet_sync.services.google_sheet_processor import (
            DistressTypeNotFoundError,
        )

        mock_resolve.return_value = (self.project, 'agra bypass road')
        mock_process.side_effect = DistressTypeNotFoundError(
            "Distress type not found: 'Crack'"
        )

        first = self.client.post(ENDPOINT, BASE_PAYLOAD, format='json')
        self.assertEqual(first.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(first.data['success'])
        self.assertIn('sync_id', first.data)
        sync_id = first.data['sync_id']
        self.assertEqual(GoogleSheetDistress.objects.count(), 1)

        # Promote succeeds on retry with same sync_id.
        mock_process.side_effect = None
        mock_process.return_value = MagicMock()
        second = self.client.post(
            ENDPOINT,
            {**BASE_PAYLOAD, 'sync_id': sync_id, 'distress_type': 'pothole'},
            format='json',
        )
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(second.data['action'], 'updated')
        self.assertEqual(second.data['sync_id'], sync_id)
        self.assertEqual(GoogleSheetDistress.objects.count(), 1)
        self.assertEqual(
            GoogleSheetDistress.objects.filter(sync_id=sync_id).count(), 1
        )


class GoogleSheetProcessorUnitTests(TestCase):
    def test_validate_staging_requires_core_fields(self):
        record = GoogleSheetDistress(
            sync_id=uuid.uuid4(),
            project_id=5,
            project_name_raw='',
            project_name_normalized='',
            distress_type='',
            latitude=None,
            longitude=None,
        )
        with self.assertRaises(StagingValidationError):
            GoogleSheetProcessor._validate_staging(record)

    def test_normalize_type_key(self):
        self.assertEqual(
            GoogleSheetProcessor._normalize_type_key('Alligator Crack'),
            'alligator_crack',
        )
        self.assertEqual(
            GoogleSheetProcessor._normalize_type_key('POTHOLE'),
            'pothole',
        )

    def test_parse_date_formats(self):
        self.assertEqual(
            GoogleSheetProcessor._parse_date('2026-07-17'),
            date(2026, 7, 17),
        )
        self.assertEqual(
            GoogleSheetProcessor._parse_date('17-07-2026'),
            date(2026, 7, 17),
        )

    @patch.object(GoogleSheetProcessor, '_resolve_road_segment')
    @patch.object(GoogleSheetProcessor, '_resolve_distress_type')
    @patch.object(GoogleSheetProcessor, '_resolve_project')
    @patch.object(Distress.objects, 'update_or_create')
    def test_process_upserts_by_sync_id(
        self,
        mock_upsert,
        mock_project,
        mock_dtype,
        mock_segment,
    ):
        sync_id = uuid.uuid4()
        project = Project(project_id=5, project_name='Agra Bypass Road')
        dtype = DistressType(distress_type_id=2, distress_type_name='pothole')
        segment = RoadSegment(
            segment_id=1,
            project_id=5,
            chainage_start=100.0,
            chainage_end=100.1,
            direction='Increasing',
        )
        mock_project.return_value = project
        mock_dtype.return_value = dtype
        mock_segment.return_value = segment

        distress = Distress(distress_id=99, sync_id=sync_id)
        mock_upsert.return_value = (distress, True)

        record = GoogleSheetDistress(
            sync_id=sync_id,
            project_id=5,
            project_name_raw='Agra Bypass Road',
            project_name_normalized='agra bypass road',
            distress_type='Pothole',
            latitude=19.9,
            longitude=73.7,
            chainage_start=100.0,
            date='2026-07-17',
            total_distress='3',
            length=1.5,
            area=2.0,
            width=0.4,
            depth=0.1,
        )

        result = GoogleSheetProcessor.process(record)

        self.assertEqual(result.distress_id, 99)
        mock_upsert.assert_called_once()
        call_kwargs = mock_upsert.call_args
        self.assertEqual(call_kwargs.kwargs.get('sync_id') or call_kwargs[1].get('sync_id'), sync_id)
        defaults = call_kwargs.kwargs.get('defaults') or call_kwargs[1]['defaults']
        self.assertEqual(defaults['distress_source'], 'Google Sheet')
        self.assertEqual(defaults['count_val'], 3.0)
        self.assertEqual(defaults['length_val'], 1.5)
        self.assertEqual(defaults['distress_date'], date(2026, 7, 17))

    @patch('google_sheet_sync.services.google_sheet_processor.DistressType.objects')
    def test_resolve_distress_type_not_found(self, mock_objects):
        mock_objects.annotate.return_value.filter.return_value.order_by.return_value.first.return_value = None
        mock_objects.all.return_value.order_by.return_value = []
        with self.assertRaises(DistressTypeNotFoundError):
            GoogleSheetProcessor._resolve_distress_type('Totally Unknown Type XYZ')

    @patch('google_sheet_sync.services.google_sheet_processor.RoadSegment.objects')
    def test_resolve_segment_not_found(self, mock_objects):
        project = Project(project_id=5, project_name='Agra Bypass Road')
        record = GoogleSheetDistress(
            sync_id=uuid.uuid4(),
            project_id=5,
            project_name_raw='Agra Bypass Road',
            project_name_normalized='agra bypass road',
            distress_type='pothole',
            latitude=19.9,
            longitude=73.7,
            chainage_start=999999.0,
            chainage_end=999999.1,
            direction='NoSuchDirection',
        )
        empty = MagicMock()
        empty.filter.return_value = empty
        empty.order_by.return_value = empty
        empty.first.return_value = None
        mock_objects.filter.return_value = empty

        with self.assertRaises(RoadSegmentNotFoundError):
            GoogleSheetProcessor._resolve_road_segment(
                project=project, sync_record=record
            )
