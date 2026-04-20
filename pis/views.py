from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from .models import ProjectInfo
from .serializers import PisReportRequestSerializer, PisReportResponseSerializer
from inventory.models import RoadSegment, Project
from django.db import connection
from datetime import datetime

class PisReportView(APIView):
    @swagger_auto_schema(
        operation_description="Fetch PIS report data filtered by criteria",
        request_body=PisReportRequestSerializer,
        responses={200: PisReportResponseSerializer(many=True), 400: 'Bad Request', 500: 'Internal Server Error'},
        tags=["PIS"]
    )
    def post(self, request):
        serializer = PisReportRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        print(f"Request data: {data}")

        page = data.get('page', 1)
        page_size = data.get('page_size', 100)

        queryset = RoadSegment.objects.select_related('project').filter(
            chainage_start__gte=data['chainage_start'],
            chainage_end__lte=data['chainage_end'],
            project__project_name__in=data['project_name']
        )

        if 'all' not in data['direction']:
            queryset = queryset.filter(direction__in=data['direction'])

        print(f"Generated SQL: {queryset.query}")

        # Fetch segments
        segments = list(queryset.order_by('chainage_start', 'chainage_end'))

        # Apply pagination
        start = (page - 1) * page_size
        end = start + page_size
        segments = segments[start:end]

        print(f"Number of segments after pagination: {len(segments)}")

        records = []
        for segment in segments:
            # Attach project_info loosely by project_id
            project_info = ProjectInfo.objects.filter(project_id=segment.project.project_id).first()

            record = {
                'project_name': segment.project.project_name,
                'state': segment.project.state,
                'district': segment.project.district,
                'block_taluka': segment.project.block_taluka,
                'road_code': segment.project.road_code,
                'originating': segment.project.originating,
                'terminating': segment.project.terminating,
                'chainage_start': segment.chainage_start,
                'chainage_end': segment.chainage_end,
                'direction': segment.direction,
                'lane': segment.lane,
                'carriage_type': segment.carriage_type,
                'latitude': project_info.latitude if project_info else None,
                'longitude': project_info.longitude if project_info else None,
                'kilometer': project_info.kilometer if project_info else None,
                'overall_progress': project_info.overall_progress if project_info else None,
                'toll_plaza_location': project_info.toll_plaza_location if project_info else None,
                'previous_date': None,
                'next_date': None,
                'mjb': project_info.mjb if project_info else 0,
                'mnb': project_info.mnb if project_info else 0,
                'flyover': project_info.flyover if project_info else 0,
                'aadt': project_info.aadt if project_info else 0,
                'pup': project_info.pup if project_info else 0,
                'vup': project_info.vup if project_info else 0,
                'rob': project_info.rob if project_info else 0,
                'structures': 0,
                'date': project_info.month_val if project_info else None,
                'length': project_info.length if project_info else None,
                'carriage_width': project_info.carriage_width if project_info else None,
            }

            # Fix date format
            from datetime import datetime, date

            def format_date(value):
                if not value:
                    return None

                # If already a date object
                if isinstance(value, date):
                    return value.strftime("%Y-%m-%d")

                # If string
                if isinstance(value, str):
                    try:
                        return datetime.strptime(value, "%d-%m-%Y").strftime("%Y-%m-%d")
                    except:
                        return value

                return None

            if project_info:
                record['previous_date'] = format_date(project_info.previous_date)
                record['next_date'] = format_date(project_info.next_date)

            # Derive structures as sum of structure counts
            record['structures'] = (
                (record.get('mjb') or 0) +
                (record.get('mnb') or 0) +
                (record.get('flyover') or 0) +
                (record.get('rob') or 0) +
                (record.get('pup') or 0) +
                (record.get('vup') or 0)
            )

            records.append(record)

        # Group by (chainage_start, chainage_end)
        from itertools import groupby
        from operator import itemgetter

        grouped = {}
        for key, group in groupby(records, key=itemgetter('chainage_start', 'chainage_end')):
            grouped[key] = list(group)

        # Convert to list of lists
        response_data = [grouped[key] for key in sorted(grouped.keys())]

        print(f"Response groups: {len(response_data)}")

        return Response({
            "page": page,
            "page_size": page_size,
            "data": response_data
        }, status=status.HTTP_200_OK)
