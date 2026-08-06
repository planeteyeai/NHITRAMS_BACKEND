"""Verify Distress APIs reuse Inventory's shared JWTAuthentication flow."""

from __future__ import annotations

from django.db import connection
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.test import APIClient, APIRequestFactory

from authentication.authentication import JWTAuthentication
from authentication.jwt_utils import create_access_token
from authentication.permissions import IsJWTAuthenticated
from distress.authentication import JWTAuthentication as DistressJWTAuth
from distress.models import Distress, DistressType, Project, RoadSegment
from distress.permissions import IsJWTAuthenticated as DistressIsJWT
from distress.views import (
    ChainageSummaryView,
    DistressFullResponseView,
    DistressPredictFilterView,
    DistressReportFilterView,
    DistressReportSummaryView,
)
from distress.views_repair import DistressRepairView
from inventory.authentication import JWTAuthentication as InventoryJWTAuth
from inventory.permissions import IsJWTAuthenticated as InventoryIsJWT


AUTH_ENDPOINTS = [
    ('POST', '/distress_predict_filter/', {'project_name': ['Agra Bypass Road']}),
    ('POST', '/distress_report_filter/', {'project_name': ['Agra Bypass Road']}),
    ('POST', '/api/distress/report/filter/', {'project_name': ['Agra Bypass Road']}),
    ('POST', '/api/distress/report/summary/', {'project_name': ['Agra Bypass Road']}),
    ('POST', '/chainage-summary', {'dataset': 'distress_predicted'}),
    ('GET', '/projects-dates/distress_predicted/', None),
    ('PATCH', '/api/distress/repair', {'distress_id': 1, 'status': 'REPAIRED'}),
]


def _create_unmanaged_tables():
    with connection.schema_editor() as schema_editor:
        for model in (Project, DistressType, RoadSegment, Distress):
            try:
                schema_editor.create_model(model)
            except Exception:
                pass


@override_settings(JWT_SECRET_KEY='test-jwt-secret')
class DistressSharedAuthParityTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _create_unmanaged_tables()

    def setUp(self):
        self.client = APIClient()
        # Same token creation path Inventory uses.
        self.token = create_access_token('76')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}

    def test_distress_reexports_same_classes_as_inventory(self):
        self.assertIs(DistressJWTAuth, InventoryJWTAuth)
        self.assertIs(DistressJWTAuth, JWTAuthentication)
        self.assertIs(DistressIsJWT, InventoryIsJWT)
        self.assertIs(DistressIsJWT, IsJWTAuthenticated)

    def test_protected_views_use_shared_auth_and_permission(self):
        for view_cls in (
            DistressPredictFilterView,
            DistressFullResponseView,
            DistressReportFilterView,
            DistressReportSummaryView,
            ChainageSummaryView,
            DistressRepairView,
        ):
            self.assertEqual(view_cls.authentication_classes, [JWTAuthentication])
            self.assertEqual(view_cls.permission_classes, [IsJWTAuthenticated])

    def test_endpoints_require_authorization_header(self):
        for method, url, body in AUTH_ENDPOINTS:
            if method == 'GET':
                response = self.client.get(url)
            elif method == 'PATCH':
                response = self.client.patch(url, body, format='json')
            else:
                response = self.client.post(url, body, format='json')
            self.assertEqual(
                response.status_code,
                status.HTTP_401_UNAUTHORIZED,
                msg=f'{method} {url} expected 401 without auth, got {response.status_code}',
            )

    def test_same_jwt_authenticates_distress_endpoints(self):
        """Valid Inventory-style Bearer JWT must not be rejected as 401/403."""
        for method, url, body in AUTH_ENDPOINTS:
            if method == 'GET':
                response = self.client.get(url, **self.auth)
            elif method == 'PATCH':
                response = self.client.patch(url, body, format='json', **self.auth)
            else:
                response = self.client.post(url, body, format='json', **self.auth)
            self.assertNotEqual(
                response.status_code,
                status.HTTP_401_UNAUTHORIZED,
                msg=f'{method} {url} rejected valid JWT: {getattr(response, "data", None)}',
            )
            self.assertNotEqual(
                response.status_code,
                status.HTTP_403_FORBIDDEN,
                msg=f'{method} {url} permission denied with valid JWT: {getattr(response, "data", None)}',
            )

    def test_request_user_username_populated(self):
        factory = APIRequestFactory()
        django_request = factory.post(
            '/distress_predict_filter/',
            {'project_name': ['Agra Bypass Road']},
            format='json',
            HTTP_AUTHORIZATION=f'Bearer {self.token}',
        )
        drf_request = Request(django_request, parsers=[JSONParser()])
        authenticated = JWTAuthentication().authenticate(drf_request)
        self.assertIsNotNone(authenticated)
        user, _token = authenticated
        self.assertTrue(user.is_authenticated)
        self.assertEqual(user.username, '76')
