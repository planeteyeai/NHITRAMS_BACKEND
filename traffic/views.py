from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from .models import Traffic
from .serializers import TrafficFilterRequestSerializer

class TrafficFilterView(APIView):
    @swagger_auto_schema(
        operation_description="Filter traffic data based on criteria",
        request_body=TrafficFilterRequestSerializer,
        responses={200: 'Success', 400: 'Bad Request', 500: 'Internal Server Error'},
        tags=["Traffic"]
    )
    def post(self, request):
        serializer = TrafficFilterRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data

        queryset = Traffic.objects.select_related("project", "segment")

        if data['chainage_start'] is not None and data['chainage_end'] is not None:
            queryset = queryset.filter(
                segment__chainage_start__gte=data['chainage_start'],
                segment__chainage_end__lte=data['chainage_end']
            )

        if data['date']:
            queryset = queryset.filter(survey_date=data['date'])

        if data['project_name']:
            queryset = queryset.filter(project__project_name__in=data['project_name'])

        if "all" not in data['direction']:
            queryset = queryset.filter(segment__direction__in=data['direction'])

        queryset = queryset[:500]

        records = list(queryset.values(
            "project__road_code",
            "project__project_name",
            "project__district",
            "project__block_taluka",
            "project__length_km",

            "segment__chainage_start",
            "segment__chainage_end",
            "segment__direction",

            "three_wheeler_auto",
            "car_jeep_van_taxi",
            "standard_bus",
            "lcv",
            "two_axle_trucks",
            "three_axle_trucks",
            "mav",
            "osv",
            "tractor",
            "others",

            "aadt_vehicles",
            "aadt_pcu",
            "cvd_vehicles",

            "latitude",
            "longitude",
            "toll_plaza",
            "survey_date"
        ))

        # Map to Excel-style keys
        response_data = []
        for r in records:
            response_data.append({
                "Date": r["survey_date"],

                "Road Code": r["project__road_code"],
                "Project Name": r["project__project_name"],
                "District": r["project__district"],
                "Block": r["project__block_taluka"],

                "Direction": r["segment__direction"],
                "Length": r["project__length_km"],

                "Chainage From": r["segment__chainage_start"],
                "Chainage To": r["segment__chainage_end"],

                "3-Wheeler/ Auto": r["three_wheeler_auto"],
                "Car/ Jeep/ Van/ Taxi": r["car_jeep_van_taxi"],
                "Standard Bus": r["standard_bus"],
                "LCV": r["lcv"],
                "2-Axle Trucks": r["two_axle_trucks"],
                "3-Axle Trucks": r["three_axle_trucks"],
                "MAV": r["mav"],
                "OSV": r["osv"],
                "Tractor": r["tractor"],
                "Others": r["others"],

                "AADT in Vehicles": r["aadt_vehicles"],
                "AADT in PCU": r["aadt_pcu"],
                "CVD in Vehicles": r["cvd_vehicles"],

                "Longitude": r["longitude"],
                "Latitude": r["latitude"],
                "Toll Plaza": r["toll_plaza"],
            })

        return Response(response_data, status=status.HTTP_200_OK)
