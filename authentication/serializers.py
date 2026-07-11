from rest_framework import serializers


class LoginSerializer(serializers.Serializer):
    """Accepts only a username. No password validation — PHP already authenticated the user."""

    username = serializers.CharField(
        max_length=150,
        trim_whitespace=True,
        help_text='NHIT RAMS username (already authenticated by PHP).',
    )

    def validate_username(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError('Username is required.')
        return value.strip()
