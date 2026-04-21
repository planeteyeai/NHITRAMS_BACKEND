from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .models import RoadsideFeatures, ProjectInfo
from .serializers import RWFISFilterSerializer
from drf_yasg.utils import swagger_auto_schema
from django.db.models import OuterRef, Subquery
import time


class RoadsideFeaturesView(APIView):
    @swagger_auto_schema(request_body=RWFISFilterSerializer)
    def post(self, request):
        serializer = RWFISFilterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        start_time = time.time()
        chainage_start = data.get('chainage_start')
        chainage_end = data.get('chainage_end')
        date = data.get('date')
        direction = data.get('direction', [])
        project_names = data.get('project_name', [])

        qs = RoadsideFeatures.objects.select_related(
            'project', 'segment'
        )

        if chainage_start is not None and chainage_end is not None:
            qs = qs.filter(segment__chainage_start__gte=chainage_start, segment__chainage_end__lte=chainage_end)

        if date:
            qs = qs.filter(survey_date=date)

        if direction and "all" not in direction:
            qs = qs.filter(segment__direction__in=direction)

        if project_names and "all" not in project_names:
            qs = qs.filter(project__project_name__in=project_names)

        count = qs.count()
        print(f"Query time for count: {time.time() - start_time}")
        # Debug date
        debug_data = list(qs.values('survey_date')[:10])
        print("Debug survey_date:", debug_data)

        # Pagination
        page = data.get("page", 1)
        page_size = data.get("page_size", 100)
        start = (page - 1) * page_size
        end = start + page_size

        if end > 10000:
            return Response({"error": "Too much data. Please refine filters."}, status=status.HTTP_400_BAD_REQUEST)

        # Select only required fields
        values_qs = qs.values(
            'feature_desc', 'material_type', 'feature_condition', 'safety_hazard', 'land_use', 'terrain',
            'latitude', 'longitude', 'altitude', 'remarks', 'length_val', 'cross_section_location', 'offset_from_center',
            'segment__chainage_start', 'segment__chainage_end', 'segment__direction', 'segment__carriage_type', 'segment__lane',
            'project__project_name', 'project__district', 'project__block_taluka', 'survey_date'
        )[start:end]

        results = list(values_qs)
        print(f"Query time for values: {time.time() - start_time}")

        # Rename fields and handle nulls
        for result in results:
            result['feature'] = result.pop('feature_desc')
            result['safety_hazard_(y/n)'] = result.pop('safety_hazard')
            result['length'] = result.pop('length_val')
            result['offset_from_center_line'] = result.pop('offset_from_center')
            result['chainage_start'] = result.pop('segment__chainage_start')
            result['chainage_end'] = result.pop('segment__chainage_end')
            result['direction'] = result.pop('segment__direction')
            result['carriage_type'] = result.pop('segment__carriage_type')
            result['lane'] = result.pop('segment__lane')
            result['project_name'] = result.pop('project__project_name')
            result['district'] = result.pop('project__district') or ""
            result['block/taluka'] = result.pop('project__block_taluka') or ""
            result['date'] = result.pop('survey_date')
            # Convert remarks to int
            result['remarks'] = int(float(result['remarks'])) if result['remarks'] else 0

        response_data = {
            'count': count,
            'page': page,
            'page_size': page_size,
            'results': results
        }
        print(f"Total time: {time.time() - start_time}")
        return Response(response_data, status=status.HTTP_200_OK)