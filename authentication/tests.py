"""Tests for authentication JWT utilities."""

from django.test import TestCase, override_settings

from authentication.jwt_utils import create_access_token, decode_access_token
from authentication.user import JWTUser


@override_settings(JWT_SECRET_KEY='test-jwt-secret')
class JWTUtilsTests(TestCase):
    def test_create_and_decode_token(self):
        token = create_access_token('ram.patil')
        payload = decode_access_token(token)
        self.assertEqual(payload['username'], 'ram.patil')
        self.assertIn('exp', payload)
        self.assertIn('iat', payload)

    def test_jwt_user_equality(self):
        a = JWTUser('ram.patil')
        b = JWTUser('ram.patil')
        c = JWTUser('other.user')
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertTrue(a.is_authenticated)
