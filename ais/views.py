from django.db.models import Q

from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from nhit.caching import CACHE_NS_AIS_FILTER, cached_filter_response

from .models import Accident
from .serializers import AisFilterRequestSerializer
from .services.ais_filter_response import build_ais_filter_groups


class AisFilterView(APIView):
    @swagger_auto_schema(
        operation_description='Filter AIS accident data based on criteria',
        request_body=AisFilterRequestSerializer,
        responses={200: 'Success', 400: 'Bad Request', 500: 'Internal Server Error'},
        tags=['AIS'],
    )
    def post(self, request):
        serializer = AisFilterRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data

        def build_response():
            queryset = Accident.objects.select_related('project', 'segment')

            if data['chainage_start'] is not None and data['chainage_end'] is not None:
                queryset = queryset.filter(
                    segment__chainage_start__gte=data['chainage_start'],
                    segment__chainage_end__lte=data['chainage_end'],
                )

            if data['date']:
                queryset = queryset.filter(accident_date=data['date'])

            if data['project_name'] and not any(
                str(name).strip().casefold() == 'all' for name in data['project_name']
            ):
                q_objects = Q()
                for name in data['project_name']:
                    q_objects |= Q(project__project_name__iexact=name)
                queryset = queryset.filter(q_objects)

            if data['direction'] and not any(
                str(value).strip().casefold() == 'all' for value in data['direction']
            ):
                queryset = queryset.filter(segment__direction__in=data['direction'])

            return build_ais_filter_groups(queryset)

        return cached_filter_response(
            namespace=CACHE_NS_AIS_FILTER,
            payload=data,
            builder=build_response,
        )
