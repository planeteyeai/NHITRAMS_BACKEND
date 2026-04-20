from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.db.models import Sum
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from .models import Inventory, Project, SubAssetType
from .serializers import InventoryFilterSerializer

class Inventory2FilterView(APIView):
    @swagger_auto_schema(
        operation_description="Filter inventory data and aggregate by asset types",
        request_body=InventoryFilterSerializer,
        responses={200: openapi.Response('Success'), 400: 'Bad Request'}
    )
    def post(self, request):
        serializer = InventoryFilterSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data

        # Convert project_name to project_ids
        project_ids = Project.objects.filter(
            project_name__in=data['project_name']
        ).values_list("project_id", flat=True) if 'project_name' in data and data['project_name'] else []

        if not project_ids:
            return Response([], status=status.HTTP_200_OK)

        # Convert asset_type to sub_asset_type_ids
        sub_asset_type_ids = SubAssetType.objects.filter(
            sub_asset_type_name__in=data['asset_type']
        ).values_list("sub_asset_type_id", flat=True) if data['asset_type'] else []

        # Build queryset
        queryset = Inventory.objects.all()

        # Apply filters
        queryset = queryset.filter(project_id__in=project_ids)

        if sub_asset_type_ids:
            queryset = queryset.filter(sub_asset_type_id__in=sub_asset_type_ids)

        if 'date' in data:
            queryset = queryset.filter(survey_date=data['date'])

        if 'chainage_start' in data:
            queryset = queryset.filter(segment__chainage_start__gte=data['chainage_start'])

        if 'chainage_end' in data:
            queryset = queryset.filter(segment__chainage_end__lte=data['chainage_end'])

        if 'direction' in data and data['direction']:
            queryset = queryset.filter(segment__direction__in=data['direction'])

        # Aggregation
        results = queryset.values(
            "project_id",
            "sub_asset_type_id"
        ).annotate(
            total_count=Sum("count_val")
        ).order_by('project_id', 'sub_asset_type_id')

        # Return as list of dicts
        return Response(list(results), status=status.HTTP_200_OK)
