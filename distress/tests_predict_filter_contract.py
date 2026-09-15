"""Contract + pagination tests for POST /distress_predict_filter/."""

from __future__ import annotations

import gzip
import json
from datetime import date
from unittest.mock import patch

from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from authentication.jwt_utils import create_access_token
from distress.models import Distress, DistressRepairLog, DistressType, Project, RoadSegment
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
}


def _create_unmanaged_tables():
    """Create unmanaged prediction tables for test DB if needed."""
    for model in (Project, DistressType, RoadSegment, Distress):
        try:
            with connection.schema_editor() as schema_editor:
                schema_editor.create_model(model)
        except Exception:
            # Table may already exist (keepdb). Ignore; do not rollback inside TestCase atomic.
            pass


@override_settings(JWT_SECRET_KEY='test-jwt-secret', NHIT_CACHE_ENABLED=False)
class DistressPredictFilterContractTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _create_unmanaged_tables()

    def setUp(self):
        self.client = APIClient()
        self.token = create_access_token('predict.tester')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}

        self.project = Project.objects.create(project_name='Abu Road to Swaroopganj')
        self.dtype = DistressType.objects.create(distress_type_name='Pothole')
        self.segment = RoadSegment.objects.create(
            project=self.project,
            chainage_start=650.0,
            chainage_end=650.5,
            direction='LHS',
            carriage_type='Flexible',
            lane='L1',
        )
        self.row = Distress.objects.create(
            segment=self.segment,
            project=self.project,
            distress_type=self.dtype,
            distress_date=date(2026, 8, 1),
            latitude=24.50012,
            longitude=72.80125,
            count_val=3,
            length_val=0,
            area_val=0,
            width_val=0,
            depth_val=0,
        )
        self.payload = {
            'project_name': ['Abu Road to Swaroopganj'],
            'direction': ['All'],
            'distress_type': ['All'],
        }

    def test_requires_jwt(self):
        response = self.client.post(ENDPOINT, self.payload, format='json')
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_nested_list_shape_and_fields(self):
        response = self.client.post(ENDPOINT, self.payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        body = response.json()
        self.assertIsInstance(body, list)
        self.assertEqual(len(body), 1)
        self.assertIsInstance(body[0], list)
        self.assertEqual(len(body[0]), 1)
        row = body[0][0]
        self.assertTrue(REQUIRED_ROW_KEYS.issubset(row.keys()))
        self.assertEqual(row['distress_id'], self.row.distress_id)
        self.assertEqual(row['direction'], 'lhs')
        self.assertEqual(row['distress_type'], 'Pothole')
        self.assertEqual(row['predicted_quantity'], 3.0)
        self.assertEqual(row['resolved_quantity'], 0.0)
        self.assertEqual(row['remaining_quantity'], 3.0)
        self.assertEqual(row['total_distress'], 3.0)
        self.assertEqual(row['pothole'], 3.0)
        self.assertEqual(row['alligator_crack'], 0)
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
            **self.auth,
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
            **self.auth,
        )
        self.assertEqual(ok.status_code, status.HTTP_200_OK)
        self.assertEqual(len(ok.json()[0]), 1)

        miss = self.client.post(
            ENDPOINT,
            {**self.payload, 'carriage_type': ['Rigid']},
            format='json',
            **self.auth,
        )
        self.assertEqual(miss.json(), {'message': 'No match'})

    def test_repair_fields_reflect_resolved_quantity(self):
        DistressRepairLog.objects.create(
            distress_id=self.row.distress_id,
            status=DistressRepairLog.STATUS_REPAIRED,
            quantity_resolved=1.0,
            updated_by='tester',
        )
        response = self.client.post(ENDPOINT, self.payload, format='json', **self.auth)
        row = response.json()[0][0]
        self.assertEqual(row['predicted_quantity'], 3.0)
        self.assertEqual(row['resolved_quantity'], 1.0)
        self.assertEqual(row['remaining_quantity'], 2.0)
        self.assertEqual(row['total_distress'], 2.0)
        self.assertEqual(row['pothole'], 2.0)

    def test_default_page_size_and_reject_over_max(self):
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
        other = Distress.objects.create(
            segment=self.segment,
            project=self.project,
            distress_type=self.dtype,
            distress_date=date(2026, 8, 2),
            latitude=24.5,
            longitude=72.8,
            count_val=1,
            length_val=0,
            area_val=0,
            width_val=0,
            depth_val=0,
        )
        page1 = self.client.post(
            ENDPOINT,
            {**self.payload, 'page': 1, 'page_size': 1},
            format='json',
            **self.auth,
        )
        page2 = self.client.post(
            ENDPOINT,
            {**self.payload, 'page': 2, 'page_size': 1},
            format='json',
            **self.auth,
        )
        beyond = self.client.post(
            ENDPOINT,
            {**self.payload, 'page': 3, 'page_size': 1},
            format='json',
            **self.auth,
        )
        self.assertEqual(page1['X-Distress-Has-More'], 'true')
        self.assertEqual(page1['X-Distress-Row-Count'], '2')
        self.assertEqual(len(page1.json()[0]), 1)
        self.assertEqual(page2['X-Distress-Has-More'], 'false')
        self.assertEqual(len(page2.json()[0]), 1)
        ids = {page1.json()[0][0]['distress_id'], page2.json()[0][0]['distress_id']}
        self.assertEqual(ids, {self.row.distress_id, other.distress_id})
        self.assertEqual(beyond.json(), {'message': 'No match'})

    def test_deterministic_order_by_chainage_then_id(self):
        seg_early = RoadSegment.objects.create(
            project=self.project,
            chainage_start=100.0,
            chainage_end=101.0,
            direction='LHS',
            carriage_type='Flexible',
            lane='L1',
        )
        early = Distress.objects.create(
            segment=seg_early,
            project=self.project,
            distress_type=self.dtype,
            distress_date=date(2026, 8, 1),
            latitude=24.1,
            longitude=72.1,
            count_val=1,
            length_val=0,
            area_val=0,
            width_val=0,
            depth_val=0,
        )
        response = self.client.post(ENDPOINT, self.payload, format='json', **self.auth)
        ids = [r['distress_id'] for r in response.json()[0]]
        self.assertEqual(ids[0], early.distress_id)
        self.assertIn(self.row.distress_id, ids)
