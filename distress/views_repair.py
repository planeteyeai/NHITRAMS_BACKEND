"""Views for distress repair status (AI predicted distresses)."""

from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema

from .authentication import JWTAuthentication
from .permissions import IsJWTAuthenticated
from .serializers_repair import DistressRepairRequestSerializer
from .services.distress_repair import ConflictError, record_distress_repair


class DistressRepairView(APIView):
    """PATCH /api/distress/repair

    Field engineers mark a predicted distress as REPAIRED or NOT_PRESENT
    using distress_id. Appends an audit log only; never mutates predictions.
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsJWTAuthenticated]

    @swagger_auto_schema(
        operation_description=(
            'Update repair status for an AI-predicted distress by distress_id. '
            'Creates an append-only repair log with optional quantity_resolved '
            '(default 1). Does not modify prediction data. '
            'Username is taken from the JWT.'
        ),
        request_body=DistressRepairRequestSerializer,
        responses={
            200: openapi.Response(
                description='Distress updated successfully',
                examples={
                    'application/json': {
                        'success': True,
                        'message': 'Distress updated successfully.',
                        'data': {
                            'distress_id': 1846255,
                            'status': 'REPAIRED',
                            'predicted_quantity': 1.0,
                            'resolved_quantity': 1.0,
                            'remaining_quantity': 0.0,
                        },
                    }
                },
            ),
            400: 'Validation error',
            401: 'Unauthorized — missing or invalid JWT',
            404: 'No predicted distress found for this distress_id',
            409: 'Distress already fully resolved',
        },
        security=[{'Bearer': []}],
        tags=['Distress Repair'],
    )
    def patch(self, request):
        serializer = DistressRepairRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        try:
            result = record_distress_repair(
                distress_id=data['distress_id'],
                status=data['status'],
                quantity_resolved=data.get('quantity_resolved', 1.0),
                updated_by=request.user.username,
                remarks=data.get('remarks', ''),
            )
        except ValidationError as exc:
            return Response(
                exc.detail if hasattr(exc, 'detail') else {'detail': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except NotFound as exc:
            return Response(
                {'success': False, 'message': str(exc.detail)},
                status=status.HTTP_404_NOT_FOUND,
            )
        except ConflictError as exc:
            return Response(
                {
                    'success': False,
                    'message': str(exc.detail),
                },
                status=status.HTTP_409_CONFLICT,
            )

        return Response(
            {
                'success': True,
                'message': 'Distress updated successfully.',
                'data': {
                    'distress_id': result['distress_id'],
                    'status': result['status'],
                    'predicted_quantity': result['predicted_quantity'],
                    'resolved_quantity': result['resolved_quantity'],
                    'remaining_quantity': result['remaining_quantity'],
                },
            },
            status=status.HTTP_200_OK,
        )
