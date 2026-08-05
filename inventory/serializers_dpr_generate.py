"""Serializers for Asset Change Detection and Monitoring DPR generation."""

from rest_framework import serializers

MAX_REPORT_RANGE_DAYS = 31


class AssetChangeDPRGenerateRequestSerializer(serializers.Serializer):
    project_name = serializers.CharField(max_length=255)
    reporting_engineer_name = serializers.CharField(max_length=255)
    assessment_date = serializers.DateField(
        input_formats=['%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y']
    )
    from_date = serializers.DateField(
        input_formats=['%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y']
    )
    to_date = serializers.DateField(
        input_formats=['%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y']
    )

    def validate_project_name(self, value):
        text = ' '.join(str(value or '').split())
        if not text:
            raise serializers.ValidationError('This field is required.')
        return text

    def validate_reporting_engineer_name(self, value):
        text = ' '.join(str(value or '').split())
        if not text:
            raise serializers.ValidationError('This field is required.')
        return text

    def validate(self, attrs):
        from_date = attrs.get('from_date')
        to_date = attrs.get('to_date')
        if from_date is None or to_date is None:
            return attrs

        if from_date > to_date:
            raise serializers.ValidationError(
                {'to_date': ['to_date must be greater than or equal to from_date.']}
            )

        # Inclusive calendar-day span must be <= 31 days.
        span_days = (to_date - from_date).days + 1
        if span_days > MAX_REPORT_RANGE_DAYS:
            raise serializers.ValidationError(
                {
                    'to_date': [
                        f'Report period cannot exceed {MAX_REPORT_RANGE_DAYS} days.'
                    ]
                }
            )
        return attrs
