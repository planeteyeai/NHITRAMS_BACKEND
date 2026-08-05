"""Views for Asset Change Detection and Monitoring DPR generation (read-only)."""

import logging

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from inventory.authentication import JWTAuthentication
from inventory.permissions import IsJWTAuthenticated
from inventory.serializers_dpr_generate import AssetChangeDPRGenerateRequestSerializer
from inventory.services.dpr_generate import DPRGenerationService, generate_asset_change_dpr
from inventory.services.dpr_generate_pdf import (
    build_dpr_pdf_filename,
    render_asset_change_dpr_pdf,
)

logger = logging.getLogger(__name__)


class AssetChangeDPRGenerateView(APIView):
    """POST /api/inventory/dpr/generate — build DPR JSON from existing reports."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]

    @swagger_auto_schema(
        operation_description=(
            'Generate Asset Change Detection and Monitoring DPR JSON for a project. '
            'Read-only: aggregates Inventory Reports and Routine Maintenance reports. '
            'Does not create or modify records.'
        ),
        request_body=AssetChangeDPRGenerateRequestSerializer,
        responses={
            200: openapi.Response('DPR generated'),
            400: 'Validation error',
            401: 'Unauthorized',
        },
        security=[{'Bearer': []}],
        tags=['DPR Generate'],
    )
    def post(self, request):
        serializer = AssetChangeDPRGenerateRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        try:
            payload = generate_asset_change_dpr(
                project_name=data['project_name'],
                reporting_engineer_name=data['reporting_engineer_name'],
                assessment_date=data['assessment_date'],
                from_date=data['from_date'],
                to_date=data['to_date'],
                username=request.user.username,
            )
        except ValidationError as exc:
            return Response(
                {
                    'success': False,
                    'errors': exc.detail if hasattr(exc, 'detail') else {'detail': str(exc)},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                'success': True,
                'message': 'DPR generated successfully.',
                'data': payload,
            },
            status=status.HTTP_200_OK,
        )


class AssetChangeDPRGeneratePDFView(APIView):
    """POST /api/inventory/dpr/generate-pdf/ — PDF from DPRGenerationService JSON."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]

    @swagger_auto_schema(
        operation_description=(
            'Generate Asset Change Detection and Monitoring DPR as a downloadable PDF. '
            'Reuses DPRGenerationService JSON (same request body as /api/inventory/dpr/generate/). '
            'Does not create or modify records.'
        ),
        request_body=AssetChangeDPRGenerateRequestSerializer,
        responses={
            200: openapi.Response('PDF file', schema=openapi.Schema(type=openapi.TYPE_FILE)),
            400: 'Validation error',
            401: 'Unauthorized',
            500: 'PDF generation failure',
        },
        security=[{'Bearer': []}],
        tags=['DPR Generate'],
    )
    def post(self, request):
        from pdf.utils import TempWorkspace, pdf_file_response

        serializer = AssetChangeDPRGenerateRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        try:
            payload = DPRGenerationService.generate(
                project_name=data['project_name'],
                reporting_engineer_name=data['reporting_engineer_name'],
                assessment_date=data['assessment_date'],
                from_date=data['from_date'],
                to_date=data['to_date'],
                username=request.user.username,
            )
        except ValidationError as exc:
            return Response(
                {
                    'success': False,
                    'errors': exc.detail if hasattr(exc, 'detail') else {'detail': str(exc)},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        details = payload.get('project_details') or {}
        download_name = build_dpr_pdf_filename(
            details.get('project_name') or data['project_name'],
            details.get('from_date') or data['from_date'],
            details.get('to_date') or data['to_date'],
        )

        workspace = TempWorkspace()
        workspace.__enter__()
        try:
            pdf_path = workspace.make_pdf_path('dpr_asset_change_')
            render_asset_change_dpr_pdf(payload, pdf_path, temp_dir=workspace.dir)
            return pdf_file_response(pdf_path, download_name, cleanup=workspace.cleanup)
        except Exception as exc:
            workspace.cleanup()
            logger.exception('Asset Change DPR PDF generation failed')
            return Response(
                {
                    'success': False,
                    'message': 'PDF generation failure.',
                    'error': str(exc),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
