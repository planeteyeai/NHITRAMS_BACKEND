"""Serializers for distress repair status updates."""

from rest_framework import serializers

from distress.models import DistressRepairLog


class DistressRepairRequestSerializer(serializers.Serializer):
    distress_id = serializers.IntegerField(min_value=1)
    status = serializers.ChoiceField(
        choices=[
            DistressRepairLog.STATUS_REPAIRED,
            DistressRepairLog.STATUS_NOT_PRESENT,
        ],
    )
    quantity_resolved = serializers.FloatField(
        required=False,
        default=1.0,
        min_value=0.0,
    )
    remarks = serializers.CharField(
        required=False,
        allow_blank=True,
        default='',
        trim_whitespace=True,
    )

    def validate_status(self, value):
        return str(value).strip().upper()

    def validate_quantity_resolved(self, value):
        if value is None:
            return 1.0
        if value < 0:
            raise serializers.ValidationError('Must be >= 0.')
        return float(value)
