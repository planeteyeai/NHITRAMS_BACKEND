"""Contract tests for POST /traffic_filter/."""

from __future__ import annotations

from datetime import date

from django.db import connection
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from inventory.models import Project, RoadSegment
from traffic.models import Traffic

ENDPOINT = '/traffic_filter/'

PAYLOAD = {
    'chainage_start': 0,
    'chainage_end': 1381,
    'date': '2026-05-20',
    'direction': ['All'],
    'project_name': ['Abu Road to Swaroopganj'],
}

NHIT_FIELDS = {
    'date',
    'road_code',
    'project_name',
    'block',
    'district',
    'year',
    'direction',
    'length',
    'chainage_start',
    'chainage_end',
    '3-wheeler/_auto',
    'car/_jeep/_van/_taxi',
    'standard_bus',
    'lcv',
    '2-axle_trucks',
    '3-axle_trucks',
    'mav',
    'osv',
    'tractor',
    'others',
    'aadt_in_vehicles',
    'aadt_in_pcu',
    'cvd_in_vehicles',
    'longitude',
    'latitude',
    'toll_plaza',
}


def _create_unmanaged_tables():
    with connection.schema_editor() as schema_editor:
        for model in (Project, RoadSegment, Traffic):
            try:
                schema_editor.create_model(model)
            except Exception:
                pass


class TrafficFilterContractTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _create_unmanaged_tables()

    def setUp(self):
        self.client = APIClient()
        self.project = Project.objects.create(
            project_name='Abu Road to Swaroopganj',
            state='Rajasthan',
            district='Swaroopganj',
            block_taluka='Swaroopganj',
            road_code='47',
            originating='Abu Road',
            terminating='Swaroopganj',
            length_km=100.0,
        )
        self.increasing_segment = RoadSegment.objects.create(
            project=self.project,
            chainage_start=646.0,
            chainage_end=646.1,
            direction='Increasing',
            lane='L1',
            carriage_type='Flexible',
        )
        self.decreasing_segment = RoadSegment.objects.create(
            project=self.project,
            chainage_start=646.0,
            chainage_end=646.1,
            direction='Decreasing',
            lane='L1',
            carriage_type='Flexible',
        )
        common = {
            'project': self.project,
            'survey_date': date(2026, 5, 20),
            'year_val': 2026.0,
            'length_val': 100.0,
            'three_wheeler_auto': 1,
            'car_jeep_van_taxi': 2,
            'standard_bus': 3,
            'lcv': 4,
            'two_axle_trucks': 5,
            'three_axle_trucks': 6,
            'mav': 7,
            'osv': 8,
            'tractor': 9,
            'others': 10,
            'aadt_vehicles': 100,
            'aadt_pcu': 38151.69921875,
            'cvd_vehicles': 27378.43359375,
            'latitude': 24.466760635375977,
            'longitude': 72.78508758544922,
            'toll_plaza': 'Undavariya',
            'source_file': 'Abu Road to Swaroopganj.xlsx',
        }
        Traffic.objects.create(segment=self.increasing_segment, **common)
        Traffic.objects.create(segment=self.decreasing_segment, **common)

    def test_direction_all_is_case_insensitive(self):
        response = self.client.post(ENDPOINT, PAYLOAD, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(len(response.data[0]), 2)

    def test_nested_group_ordering(self):
        response = self.client.post(ENDPOINT, PAYLOAD, format='json')
        group = response.data[0]
        self.assertEqual(group[0]['direction'], 'Increasing')
        self.assertEqual(group[1]['direction'], 'Decreasing')

    def test_nhit_field_names_and_metadata(self):
        response = self.client.post(ENDPOINT, PAYLOAD, format='json')
        record = response.data[0][0]
        self.assertTrue(NHIT_FIELDS.issubset(record.keys()))
        self.assertEqual(record['road_code'], '47')
        self.assertEqual(record['district'], 'Swaroopganj')
        self.assertEqual(record['block'], 'Swaroopganj')
        self.assertEqual(record['year'], 2026.0)
        self.assertEqual(record['length'], 100.0)
        self.assertEqual(record['toll_plaza'], 'Undavariya')
        self.assertEqual(record['aadt_in_pcu'], 38151.69921875)
