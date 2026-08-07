from rest_framework import serializers


def _is_numeric_id(value: str) -> bool:
    """True when value is only digits (PHP user id mistakenly sent as username)."""
    text = str(value or '').strip()
    return bool(text) and text.isdigit()


class LoginSerializer(serializers.Serializer):
    """Accepts the NHIT RAMS login username (already authenticated by PHP).

    PHP must send the real login username (e.g. ``ram.patil``), not the
    numeric user id. Optional aliases are accepted for compatibility:

    - username (preferred)
    - user_name / login_username / name (if username is missing or numeric)
    - user_id (ignored for JWT — never stored as username)
    """

    username = serializers.CharField(
        max_length=150,
        trim_whitespace=True,
        required=False,
        allow_blank=True,
        help_text='NHIT RAMS username (already authenticated by PHP). Not the numeric user id.',
    )
    user_name = serializers.CharField(
        max_length=150,
        trim_whitespace=True,
        required=False,
        allow_blank=True,
    )
    login_username = serializers.CharField(
        max_length=150,
        trim_whitespace=True,
        required=False,
        allow_blank=True,
    )
    name = serializers.CharField(
        max_length=150,
        trim_whitespace=True,
        required=False,
        allow_blank=True,
    )
    user_id = serializers.CharField(
        max_length=64,
        trim_whitespace=True,
        required=False,
        allow_blank=True,
        help_text='Optional PHP user id — never written into JWT username.',
    )

    def validate(self, attrs):
        candidates = [
            attrs.get('username'),
            attrs.get('user_name'),
            attrs.get('login_username'),
            attrs.get('name'),
        ]
        resolved = None
        for raw in candidates:
            text = ' '.join(str(raw or '').split())
            if not text:
                continue
            if _is_numeric_id(text):
                continue
            resolved = text
            break

        if not resolved:
            raise serializers.ValidationError(
                {
                    'username': [
                        'Provide the NHIT RAMS username (e.g. ram.patil), '
                        'not the numeric user id.'
                    ]
                }
            )

        attrs['username'] = resolved
        return attrs
