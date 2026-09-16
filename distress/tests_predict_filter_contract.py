"""Contract + pagination tests for POST /distress_predict_filter/."""

from __future__ import annotations

from datetime import date

from django.db import connection
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from distress.models import DistressRepairLog, PredictedDistress
from distress.serializers import DistressFilterSerializer
from distress.services.distress_predict_filter_response import (
    DEFAULT_DISTRESS_PREDICT_FILTER_PAGE_SIZE,
    MAX_DISTRESS_PREDICT_FILTER_PAGE_SIZE,
)

ENDPOINT = '/distress_predict_filter/'

REQUIRED_ROW_KEYS = {
    'distress_id',
    'latitude',
    'longitude',
    'project_name',
    'chainage_start',
    'chainage_end',
    'total_distress',
    'predicted_quantity',
    'resolved_quantity',
    'remaining_quantity',
    'distress_type',
    'direction',
    'lane',
    'date',
    'carriage_type',
    'pothole',
    'alligator_crack',
    'block_crack/oblique_crack',
    'edge_break',
    'patchwork',
    'bleeding',
    'hotspots',
    'rutting',
    'raveling',
    'transverse_crack',
    'rough_spot',
    'hairline_crack',
    'longitudinal_crack',
}


def _create_unmanaged_tables():
    """Create unmanaged predicted_distress table for test DB if needed."""
    try:
        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(PredictedDistress)
    except Exception:
        pass


def _make_row(**overrides):
    defaults = {
        'latitude': 24.50012,
        'longitude': 72.80125,
        'project_name': 'Abu Road to Swaroopganj',
        'chainage_start': 650.0,
        'chainage_end': 650.5,
        'total_distress': 3,
        'distress_type': 'Pothole',
        'pothole': 3,
        'alligator_crack': 0,
        'block_crack_oblique_crack': 0,
        'edge_break': 0,
        'patchwork': 0,
        'bleeding': 0,
        'hotspots': 0,
        'rutting': 0,
        'raveling': 0,
        'transverse_crack': 0,
        'rough_spot': 0,
        'direction': 'LHS',
        'lane': 'L1',
        'distress_date': date(2026, 8, 1),
        'carriage_type': 'Flexible',
    }
    defaults.update(overrides)
    return PredictedDistress.objects.create(**defaults)


@override_settings(NHIT_CACHE_ENABLED=False)
class DistressPredictFilterContractTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _create_unmanaged_tables()

    def setUp(self):
        self.client = APIClient()
        self.row = _make_row()
        self.payload = {
            'project_name': ['Abu Road to Swaroopganj'],
            'direction': ['All'],
            'distress_type': ['All'],
        }

    def test_allows_unauthenticated(self):
        response = self.client.post(ENDPOINT, self.payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
    def test_nested_list_shape_and_fields(self):
        response = self.client.post(ENDPOINT, self.payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        body = response.json()
        self.assertIsInstance(body, list)
        self.assertEqual(len(body), 1)
        self.assertIsInstance(body[0], list)
        self.assertEqual(len(body[0]), 1)
        row = body[0][0]
        self.assertTrue(REQUIRED_ROW_KEYS.issubset(row.keys()))
        self.assertEqual(row['distress_id'], self.row.predicted_distress_id)
        self.assertEqual(row['direction'], 'Lhs')
        self.assertEqual(row['distress_type'], 'Pothole')
        self.assertEqual(row['predicted_quantity'], 3.0)
        self.assertEqual(row['resolved_quantity'], 0.0)
        self.assertEqual(row['remaining_quantity'], 3.0)
        self.assertEqual(row['total_distress'], 3.0)
        self.assertEqual(row['pothole'], 3.0)
        self.assertEqual(row['alligator_crack'], 0.0)
        self.assertEqual(row['hairline_crack'], 0.0)
        self.assertEqual(row['longitudinal_crack'], 0.0)
        self.assertEqual(response['X-Distress-Paginated'], 'true')
        self.assertEqual(response['X-Distress-Page'], '1')
        self.assertEqual(
            response['X-Distress-Page-Size'],
            str(DEFAULT_DISTRESS_PREDICT_FILTER_PAGE_SIZE),
        )
        self.assertEqual(response['X-Distress-Row-Count'], '1')
        self.assertEqual(response['X-Distress-Has-More'], 'false')

    def test_no_match(self):
        response = self.client.post(
            ENDPOINT,
            {**self.payload, 'project_name': ['Does Not Exist']},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), {'message': 'No match'})

    def test_all_semantics_and_carriage_type(self):
        ok = self.client.post(
            ENDPOINT,
            {
                **self.payload,
                'carriage_type': ['Flexible'],
                'direction': ['All'],
                'distress_type': ['all'],
            },
            format='json',
        )
        self.assertEqual(ok.status_code, status.HTTP_200_OK)
        self.assertEqual(len(ok.json()[0]), 1)

        miss = self.client.post(
            ENDPOINT,
            {**self.payload, 'carriage_type': ['Rigid']},
            format='json',
        )
        self.assertEqual(miss.json(), {'message': 'No match'})

    def test_repair_fields_reflect_resolved_quantity(self):
        DistressRepairLog.objects.create(
            distress_id=self.row.predicted_distress_id,
            status=DistressRepairLog.STATUS_REPAIRED,
            quantity_resolved=1.0,
            updated_by='tester',
        )
        response = self.client.post(ENDPOINT, self.payload, format='json')
        row = response.json()[0][0]
        self.assertEqual(row['predicted_quantity'], 3.0)
        self.assertEqual(row['resolved_quantity'], 1.0)
        self.assertEqual(row['remaining_quantity'], 2.0)
        self.assertEqual(row['total_distress'], 2.0)
        self.assertEqual(row['pothole'], 2.0)
        self.assertEqual(row['alligator_crack'], 0.0)
        self.assertEqual(row['hairline_crack'], 0.0)
        self.assertEqual(row['longitudinal_crack'], 0.0)

    def test_direction_and_distress_type_canonical_case(self):
        PredictedDistress.objects.filter(
            predicted_distress_id=self.row.predicted_distress_id,
        ).update(direction='increasing', distress_type='longitudinal crack', pothole=0)
        # Rebuild unmanaged row fields for serializer path via values().
        self.row.direction = 'increasing'
        self.row.distress_type = 'longitudinal crack'
        self.row.pothole = 0
        self.row.longitudinal_crack = 3
        self.row.save()

        response = self.client.post(ENDPOINT, self.payload, format='json')
        row = response.json()[0][0]
        self.assertEqual(row['direction'], 'Increasing')
        self.assertEqual(row['distress_type'], 'Longitudinal Crack')
        self.assertEqual(row['longitudinal_crack'], 3.0)
        self.assertEqual(row['pothole'], 0.0)
        self.assertEqual(row['hairline_crack'], 0.0)

    def test_not_applicable_direction_and_lane_canonical(self):
        self.row.direction = 'not applicable'
        self.row.lane = 'Not applicable'
        self.row.save()
        response = self.client.post(ENDPOINT, self.payload, format='json')
        row = response.json()[0][0]
        self.assertEqual(row['direction'], 'Not Applicable')
        self.assertEqual(row['lane'], 'Not Applicable')
        ser = DistressFilterSerializer(data={})
        self.assertTrue(ser.is_valid(), ser.errors)
        self.assertEqual(ser.validated_data['page'], 1)
        self.assertEqual(
            ser.validated_data['page_size'],
            DEFAULT_DISTRESS_PREDICT_FILTER_PAGE_SIZE,
        )

        bad = DistressFilterSerializer(
            data={'page_size': MAX_DISTRESS_PREDICT_FILTER_PAGE_SIZE + 1}
        )
        self.assertFalse(bad.is_valid())
        self.assertIn('page_size', bad.errors)

        zero = DistressFilterSerializer(data={'page_size': 0})
        self.assertFalse(zero.is_valid())
        neg = DistressFilterSerializer(data={'page': 0})
        self.assertFalse(neg.is_valid())

    def test_page_two_and_beyond_last_page(self):
        other = _make_row(distress_date=date(2026, 8, 2), total_distress=1, pothole=1)
        page1 = self.client.post(
            ENDPOINT,
            {**self.payload, 'page': 1, 'page_size': 1},
            format='json',
        )
        page2 = self.client.post(
            ENDPOINT,
            {**self.payload, 'page': 2, 'page_size': 1},
            format='json',
        )
        beyond = self.client.post(
            ENDPOINT,
            {**self.payload, 'page': 3, 'page_size': 1},
            format='json',
        )
        self.assertEqual(page1['X-Distress-Has-More'], 'true')
        self.assertEqual(page1['X-Distress-Row-Count'], '2')
        self.assertEqual(len(page1.json()[0]), 1)
        self.assertEqual(page2['X-Distress-Has-More'], 'false')
        self.assertEqual(len(page2.json()[0]), 1)
        ids = {page1.json()[0][0]['distress_id'], page2.json()[0][0]['distress_id']}
        self.assertEqual(ids, {self.row.predicted_distress_id, other.predicted_distress_id})
        self.assertEqual(beyond.json(), {'message': 'No match'})

    def test_deterministic_order_by_chainage_then_id(self):
        early = _make_row(chainage_start=100.0, chainage_end=101.0, total_distress=1, pothole=1)
        response = self.client.post(ENDPOINT, self.payload, format='json')
        ids = [r['distress_id'] for r in response.json()[0]]
        self.assertEqual(ids[0], early.predicted_distress_id)
        self.assertIn(self.row.predicted_distress_id, ids)
