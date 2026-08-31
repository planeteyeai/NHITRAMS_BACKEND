"""Contract tests for POST /ais_filter/."""

from __future__ import annotations

from datetime import date

from django.db import connection
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from ais.models import Accident
from inventory.models import Project, RoadSegment

ENDPOINT = '/ais_filter/'

PAYLOAD = {
    'chainage_start': 0,
    'chainage_end': 1381,
    'date': '2025-06-20',
    'direction': ['All'],
    'project_name': ['Abu Road to Swaroopganj'],
}

NHIT_FIELDS = {
    'date',
    'project_name',
    'direction',
    'chainage_start',
    'chainage_end',
    'non-injured_accident',
    'minor_accident',
    'major_accident',
    'fatal_accident',
    'total_accident',
    'fatalities',
    'minor_injury',
    'major_injury',
    'fatal_injury',
    'total_injury',
    'head-tail',
    'head-on',
    'overturning',
    'skidding',
    'sideswipe',
    'pedestrian/hit_&_run',
    'animals_on_road',
    'mechanical_fault',
    'vehicle_lost_control',
    'overspeed',
    'drunk_&_drive',
    'fault_of_driver',
    'pedestrian_related',
    'longitude',
    'latitude',
    'grievous_accident',
    'grievous_injury',
    'nature_of_accident',
    'cause_of_accident',
}


def _create_unmanaged_tables():
    with connection.schema_editor() as schema_editor:
        for model in (Project, RoadSegment, Accident):
            try:
                schema_editor.create_model(model)
            except Exception:
                pass


class AisFilterContractTests(TestCase):
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
        self.segment = RoadSegment.objects.create(
            project=self.project,
            chainage_start=647.5499877929688,
            chainage_end=647.6500244140625,
            direction='Decreasing',
            lane='L1',
            carriage_type='Flexible',
        )
        Accident.objects.create(
            project=self.project,
            segment=self.segment,
            accident_date=date(2025, 6, 20),
            latitude=24.475704193115234,
            longitude=72.79501342773438,
            non_injured_accident=1.0,
            minor_accident=0.0,
            major_accident=0.0,
            fatal_accident=0.0,
            total_accident=0.0,
            fatalities=0.0,
            minor_injury=1.0,
            major_injury=0.0,
            fatal_injury=0.0,
            total_injury=1.0,
            head_tail=0.0,
            head_on=0.0,
            overturning=1.0,
            skidding=0.0,
            sideswipe=0.0,
            pedestrian_hit_run=0.0,
            animals_on_road=0.0,
            mechanical_fault=0.0,
            vehicle_lost_control=0.0,
            overspeed=0.0,
            drunk_drive=0.0,
            fault_of_driver=0.0,
            pedestrian_related=0.0,
            grievous_accident=0.0,
            grievous_injury=0.0,
            nature_of_accident='Overturning ',
            cause_of_accident='Vehicle-Related Defect or Failure',
            source_file='Abu Road to Swaroopganj.xlsx',
        )

    def test_direction_all_case_insensitive(self):
        response = self.client.post(ENDPOINT, PAYLOAD, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(len(response.data[0]), 1)

    def test_nested_structure_and_fields(self):
        response = self.client.post(ENDPOINT, PAYLOAD, format='json')
        record = response.data[0][0]
        self.assertTrue(NHIT_FIELDS.issubset(record.keys()))
        self.assertEqual(record['direction'], 'Decreasing')
        self.assertEqual(record['non-injured_accident'], 1.0)
        self.assertEqual(record['overturning'], 1.0)
        self.assertEqual(record['nature_of_accident'], 'Overturning ')

    def test_direction_filter(self):
        response = self.client.post(
            ENDPOINT,
            {**PAYLOAD, 'direction': ['Increasing']},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 0)

    def test_invalid_chainage(self):
        response = self.client.post(
            ENDPOINT,
            {**PAYLOAD, 'chainage_start': 10, 'chainage_end': 1},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
