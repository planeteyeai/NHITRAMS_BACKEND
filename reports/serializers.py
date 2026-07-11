from rest_framework import serializers

from .models import AIReport


class AIReportCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating reports.

    Username is intentionally excluded — it is set from the JWT in the view.
    """

    class Meta:
        model = AIReport
        fields = ['prediction', 'confidence', 'image_path', 'report_json']
        extra_kwargs = {
            'image_path': {'required': False, 'allow_blank': True},
            'report_json': {'required': False},
        }


class AIReportSerializer(serializers.ModelSerializer):
    """Full report representation for read responses."""

    class Meta:
        model = AIReport
        fields = [
            'id',
            'username',
            'prediction',
            'confidence',
            'image_path',
            'report_json',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields
