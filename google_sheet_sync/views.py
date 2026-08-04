"""API views for Google Sheets → PostgreSQL distress sync."""

from __future__ import annotations

import logging

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import GoogleSheetSyncSerializer
from .services import (
    GoogleSheetProcessingError,
    ProjectNotFoundError,
    sync_sheet_row,
)

logger = logging.getLogger(__name__)


class GoogleSheetSyncView(APIView):
    """POST /api/google-sheet-sync/

    Accepts one edited / added Google Sheets row as JSON, validates it,
    resolves the project by normalized name, then creates or updates the
    corresponding ``GoogleSheetDistress`` row.
    """

    # Apps Script uses a shared secret header when GOOGLE_SHEET_SYNC_API_KEY is set.
    authentication_classes = []
    permission_classes = []

    @swagger_auto_schema(
        operation_description=(
            'Upsert a single Google Sheets distress row. '
            'Empty sync_id creates a new row and returns a UUID; '
            'existing sync_id updates that row.'
        ),
        request_body=GoogleSheetSyncSerializer,
        responses={
            200: openapi.Response(
                description='Created or updated',
                examples={
                    'application/json': {
                        'success': True,
                        'sync_id': '550e8400-e29b-41d4-a716-446655440000',
                        'action': 'created',
                    }
                },
            ),
            400: openapi.Response(
                description='Validation error',
                examples={
                    'application/json': {
                        'success': False,
                        'message': 'Validation failed',
                        'errors': {},
                    }
                },
            ),
            404: openapi.Response(
                description='Project not found',
                examples={
                    'application/json': {
                        'success': False,
                        'message': 'Project not found',
                    }
                },
            ),
            500: openapi.Response(
                description='Unexpected error',
                examples={
                    'application/json': {
                        'success': False,
                        'message': 'Internal server error',
                    }
                },
            ),
        },
        tags=['Google Sheet Sync'],
    )
    def post(self, request):
        try:
            logger.info('REQUEST DATA: %s', request.data)
            serializer = GoogleSheetSyncSerializer(data=request.data)
            logger.info('SERIALIZER INITIAL DATA: %s', serializer.initial_data)

            if not serializer.is_valid():
                logger.warning(
                    'Google Sheet sync validation failed: %s',
                    serializer.errors,
                )
                return Response(
                    {
                        'success': False,
                        'message': 'Validation failed',
                        'errors': serializer.errors,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            logger.info('VALIDATED DATA: %s', serializer.validated_data)
            logger.info(
                'sync_id compare: request=%r validated=%r',
                request.data.get('sync_id'),
                serializer.validated_data.get('sync_id'),
            )

            try:
                instance, action = sync_sheet_row(serializer.validated_data)
            except ProjectNotFoundError:
                return Response(
                    {
                        'success': False,
                        'message': 'Project not found',
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )
            except GoogleSheetProcessingError as exc:
                # Staging row is already committed and retained.
                # Always return sync_id so Apps Script can write it back even
                # when production promote fails (avoids duplicate staging rows).
                logger.error('Google Sheet production promote failed: %s', exc)
                body = {
                    'success': False,
                    'message': str(exc),
                }
                if getattr(exc, 'sync_id', None):
                    body['sync_id'] = exc.sync_id
                logger.info('PROMOTE ERROR RESPONSE: %s', body)
                return Response(body, status=status.HTTP_400_BAD_REQUEST)

            response_body = {
                'success': True,
                'sync_id': str(instance.sync_id),
                'action': action,
            }
            logger.info('SUCCESS RESPONSE: %s', response_body)
            return Response(response_body, status=status.HTTP_200_OK)

        except Exception as exc:  # noqa: BLE001 — return stable JSON for Apps Script
            logger.exception('Google Sheet sync failed: %s', exc)
            return Response(
                {
                    'success': False,
                    'message': str(exc) or 'Internal server error',
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
