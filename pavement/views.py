from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from .models import Pavement
from .serializers import PavementSerializer
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi


class PavementDataView(APIView):
    @swagger_auto_schema(
        operation_description="Retrieve pavement data based on filters",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'chainage_start': openapi.Schema(type=openapi.TYPE_NUMBER, description='Start chainage value'),
                'chainage_end': openapi.Schema(type=openapi.TYPE_NUMBER, description='End chainage value'),
                'date': openapi.Schema(type=openapi.TYPE_STRING, format='date', description='Date of survey'),
                'direction': openapi.Schema(type=openapi.TYPE_ARRAY, items=openapi.Items(type=openapi.TYPE_STRING), description='List of road IDs (ignore if "all")'),
                'project_name': openapi.Schema(type=openapi.TYPE_ARRAY, items=openapi.Items(type=openapi.TYPE_STRING), description='List of project names (ignore if "all")'),
            },
            required=[],
        ),
        responses={
            200: PavementSerializer(many=True),
        }
    )
    def post(self, request):
        data = request.data
        chainage_start = data.get('chainage_start')
        chainage_end = data.get('chainage_end')
        date = data.get('date')
        direction = data.get('direction', [])
        project_names = data.get('project_name', [])

        qs = Pavement.objects.select_related('project__project_info', 'segment')

        if chainage_start is not None and chainage_end is not None:
            qs = qs.filter(sum_chainage__range=(chainage_start, chainage_end))

        if date:
            qs = qs.filter(date_of_survey=date)

        if direction and "all" not in direction:
            qs = qs.filter(road_id__in=direction)

        if project_names and "all" not in project_names:
            qs = qs.filter(project__project_name__in=project_names)

        # Pagination
        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(qs, request)
        if page is not None:
            serializer = PavementSerializer(page, many=True)
            response = paginator.get_paginated_response(serializer.data)
            return response

        serializer = PavementSerializer(qs, many=True)
        response_data = {
            'count': qs.count(),
            'results': serializer.data
        }
        return Response(response_data, status=status.HTTP_200_OK)