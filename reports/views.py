from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_yasg.utils import swagger_auto_schema

from authentication.authentication import JWTAuthentication
from authentication.permissions import IsJWTAuthenticated

from .models import AIReport
from .serializers import AIReportCreateSerializer, AIReportSerializer


def _jwt_username(request) -> str:
    """Extract username exclusively from the authenticated JWT principal."""
    return request.user.username


def _get_owned_report(request, report_id: int) -> AIReport:
    """Fetch a report and enforce ownership.

    Returns 404 if the report does not exist.
    Returns 403 if it exists but belongs to another user.
    """
    try:
        report = AIReport.objects.get(pk=report_id)
    except AIReport.DoesNotExist:
        raise NotFound('Report not found.')

    if report.username != _jwt_username(request):
        raise PermissionDenied('You do not have permission to access this report.')

    return report


class ReportCreateView(APIView):
    """POST /api/reports — create a report owned by the JWT username."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]

    @swagger_auto_schema(
        operation_description=(
            'Create an AI Identifier report. '
            'Username is taken from the JWT — do not send it in the body.'
        ),
        request_body=AIReportCreateSerializer,
        responses={
            201: AIReportSerializer,
            400: 'Bad Request',
            401: 'Unauthorized — missing or invalid JWT',
        },
        security=[{'Bearer': []}],
        tags=['AI Reports'],
    )
    def post(self, request):
        serializer = AIReportCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        report = serializer.save(username=_jwt_username(request))
        return Response(
            AIReportSerializer(report).data,
            status=status.HTTP_201_CREATED,
        )


class ReportHistoryView(APIView):
    """GET /api/reports/history — list reports for the JWT username only."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]

    @swagger_auto_schema(
        operation_description='Return all reports belonging to the authenticated user, newest first.',
        responses={
            200: AIReportSerializer(many=True),
            401: 'Unauthorized — missing or invalid JWT',
        },
        security=[{'Bearer': []}],
        tags=['AI Reports'],
    )
    def get(self, request):
        reports = AIReport.objects.filter(username=_jwt_username(request)).order_by('-created_at')
        return Response(AIReportSerializer(reports, many=True).data)


class ReportDetailView(APIView):
    """GET / DELETE /api/reports/{id} — retrieve or delete an owned report."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]

    @swagger_auto_schema(
        operation_description='Retrieve a single report. Returns 403 if it belongs to another user.',
        responses={
            200: AIReportSerializer,
            401: 'Unauthorized',
            403: 'Forbidden — report belongs to another user',
            404: 'Not Found',
        },
        security=[{'Bearer': []}],
        tags=['AI Reports'],
    )
    def get(self, request, report_id):
        report = _get_owned_report(request, report_id)
        return Response(AIReportSerializer(report).data)

    @swagger_auto_schema(
        operation_description='Delete a report. Returns 403 if it belongs to another user.',
        responses={
            204: 'Deleted',
            401: 'Unauthorized',
            403: 'Forbidden — report belongs to another user',
            404: 'Not Found',
        },
        security=[{'Bearer': []}],
        tags=['AI Reports'],
    )
    def delete(self, request, report_id):
        report = _get_owned_report(request, report_id)
        report.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
