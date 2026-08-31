from django.db.models import Q

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from nhit.caching import CACHE_NS_PMS_FILTER, cached_filter_response

from .models import Pavement
from .serializers import PmsFilterRequestSerializer
from .services.pms_filter_response import build_pms_filter_groups


def _includes_all(values: list) -> bool:
    return any(str(value).strip().casefold() == 'all' for value in values)


class PavementDataView(APIView):
    @swagger_auto_schema(
        operation_description='Retrieve pavement data based on NHIT-compatible filters',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'chainage_start': openapi.Schema(type=openapi.TYPE_NUMBER),
                'chainage_end': openapi.Schema(type=openapi.TYPE_NUMBER),
                'date': openapi.Schema(type=openapi.TYPE_STRING, format='date'),
                'direction': openapi.Schema(
                    type=openapi.TYPE_ARRAY,
                    items=openapi.Items(type=openapi.TYPE_STRING),
                ),
                'project_name': openapi.Schema(
                    type=openapi.TYPE_ARRAY,
                    items=openapi.Items(type=openapi.TYPE_STRING),
                ),
            },
            required=['chainage_start', 'chainage_end', 'date', 'direction', 'project_name'],
        ),
        responses={200: 'Success', 400: 'Bad Request'},
        tags=['Pavement'],
    )
    def post(self, request):
        serializer = PmsFilterRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        direction_filter_all = _includes_all(data['direction'])

        def build_response():
            queryset = Pavement.objects.select_related('project', 'segment')

            if data['chainage_start'] is not None and data['chainage_end'] is not None:
                queryset = queryset.filter(
                    segment__chainage_start__gte=data['chainage_start'],
                    segment__chainage_end__lte=data['chainage_end'],
                )

            if data['date']:
                queryset = queryset.filter(date_val=data['date'])

            if data['project_name'] and not _includes_all(data['project_name']):
                q_objects = Q()
                for name in data['project_name']:
                    q_objects |= Q(project__project_name__iexact=name)
                queryset = queryset.filter(q_objects)

            if data['direction'] and not direction_filter_all:
                queryset = queryset.filter(segment__direction__in=data['direction'])

            return build_pms_filter_groups(queryset, direction_filter_all=direction_filter_all)

        return cached_filter_response(
            namespace=CACHE_NS_PMS_FILTER,
            payload={**data, '_direction_filter_all': direction_filter_all},
            builder=build_response,
        )
