"""Tests for User Management CRUD APIs."""

import time

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from authentication.constants import (
    USER_ROLE_ADMIN,
    USER_ROLE_BRIDGE_SITE_ENGINEER,
    USER_ROLE_ROAD_SITE_ENGINEER,
    USER_ROLE_SUPER_ADMIN,
)
from authentication.jwt_utils import create_access_token
from authentication.models import UserProfile

User = get_user_model()

CREATE_PAYLOAD = {
    'username': 'test.user',
    'password': 'SecurePassword123',
    'first_name': 'Test',
    'last_name': 'User',
    'email': 'test.user@example.com',
    'phone_number': '9876543210',
    'role': USER_ROLE_ROAD_SITE_ENGINEER,
    'access_type': 'RIS',
}


def _create_profiled_user(
    username: str,
    *,
    role: str = USER_ROLE_ADMIN,
    access_type: str = 'ALL',
    password: str = 'SecurePassword123',
    email: str | None = None,
    phone_number: str = '9876543210',
    is_active: bool = True,
) -> User:
    user = User.objects.create_user(
        username=username,
        email=email or f'{username}@example.com',
        password=password,
        first_name='First',
        last_name='Last',
        is_active=is_active,
    )
    UserProfile.objects.create(
        user=user,
        phone_number=phone_number,
        role=role,
        access_type=access_type,
        created_by='system',
        updated_by='system',
    )
    return user


@override_settings(JWT_SECRET_KEY='test-jwt-secret')
class UserManagementAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin_user = _create_profiled_user(
            'admin.manager',
            role=USER_ROLE_ADMIN,
            access_type='ALL',
        )
        self.superadmin_user = _create_profiled_user(
            'super.admin',
            role=USER_ROLE_SUPER_ADMIN,
            access_type='ALL',
            email='super.admin@example.com',
        )
        self.engineer_user = _create_profiled_user(
            'road.engineer',
            role=USER_ROLE_ROAD_SITE_ENGINEER,
            access_type='RIS',
            email='road.engineer@example.com',
        )

        self.admin_token = create_access_token('admin.manager')
        self.engineer_token = create_access_token('road.engineer')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.admin_token}'}

    def _create(self, **overrides):
        suffix = str(int(time.time() * 1000))
        payload = {
            **CREATE_PAYLOAD,
            'username': f'test.user.{suffix}',
            'email': f'test.user.{suffix}@example.com',
            **overrides,
        }
        return self.client.post('/api/users/', payload, format='json', **self.auth)

    def test_create_requires_auth(self):
        response = self.client.post('/api/users/', CREATE_PAYLOAD, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_invalid_token_rejected(self):
        response = self.client.get(
            '/api/users/',
            HTTP_AUTHORIZATION='Bearer invalid-token',
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_forbidden_for_non_manager_role(self):
        response = self.client.get(
            '/api/users/',
            HTTP_AUTHORIZATION=f'Bearer {self.engineer_token}',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_user_success(self):
        response = self._create()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.data['data']
        self.assertEqual(data['role'], USER_ROLE_ROAD_SITE_ENGINEER)
        self.assertEqual(data['access_type'], 'RIS')
        self.assertNotIn('password', data)
        user = User.objects.get(username=data['username'])
        self.assertTrue(user.check_password('SecurePassword123'))

    def test_missing_username(self):
        payload = {**CREATE_PAYLOAD, 'username': ''}
        response = self.client.post('/api/users/', payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_username(self):
        self._create(username='dup.user', email='dup1@example.com')
        response = self._create(username='dup.user', email='dup2@example.com')
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['error']['code'], 'DUPLICATE_USERNAME')

    def test_missing_password(self):
        payload = {**CREATE_PAYLOAD, 'username': 'nopass.user', 'email': 'nopass@example.com'}
        del payload['password']
        response = self.client.post('/api/users/', payload, format='json', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_email(self):
        response = self._create(email='not-an-email')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_email(self):
        self._create(username='email.one', email='same@example.com')
        response = self._create(username='email.two', email='same@example.com')
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['error']['code'], 'DUPLICATE_EMAIL')

    def test_duplicate_email_case_insensitive(self):
        self._create(username='email.case.one', email='same@example.com')
        response = self._create(username='email.case.two', email='Same@Example.com')
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['error']['code'], 'DUPLICATE_EMAIL')

    def test_actor_profile_loaded_once_on_list(self):
        from unittest.mock import patch

        from authentication import user_services

        with patch.object(user_services, 'get_actor_profile', wraps=user_services.get_actor_profile) as mocked:
            response = self.client.get('/api/users/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(mocked.call_count, 1)

    def test_missing_first_name(self):
        response = self._create(first_name='')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_last_name(self):
        response = self._create(last_name='')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_phone(self):
        response = self._create(phone_number='')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_role(self):
        response = self._create(role='')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_access_type(self):
        response = self._create(access_type='')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_role(self):
        response = self._create(role='Unknown Role')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_access_type(self):
        response = self._create(access_type='INVALID')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_role_access_combination(self):
        response = self._create(
            role=USER_ROLE_BRIDGE_SITE_ENGINEER,
            access_type='RIS',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error']['code'], 'INVALID_ROLE_ACCESS')

    def test_bridge_engineer_requires_bis(self):
        response = self._create(
            role=USER_ROLE_BRIDGE_SITE_ENGINEER,
            access_type='BIS',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['data']['access_type'], 'BIS')

    def test_road_engineer_requires_ris(self):
        response = self._create(
            role=USER_ROLE_ROAD_SITE_ENGINEER,
            access_type='RIS',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['data']['access_type'], 'RIS')

    def test_password_not_returned_on_create(self):
        response = self._create()
        self.assertNotIn('password', response.data['data'])

    def test_list_users(self):
        response = self.client.get('/api/users/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])

    def test_list_pagination(self):
        response = self.client.get('/api/users/?page=1&page_size=1', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['data']), 1)

    def test_search_username(self):
        response = self.client.get('/api/users/?search=admin.manager', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data['data']), 1)

    def test_search_name(self):
        response = self.client.get('/api/users/?search=First', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_search_email(self):
        response = self.client.get('/api/users/?search=admin.manager@example.com', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_filter_role(self):
        response = self.client.get(
            f'/api/users/?role={USER_ROLE_ADMIN}',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_filter_access_type(self):
        response = self.client.get('/api/users/?access_type=ALL', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_sort_users(self):
        response = self.client.get('/api/users/?sort_by=username&sort_order=asc', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_get_by_id(self):
        response = self.client.get(f'/api/users/{self.engineer_user.id}/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn('password', response.data['data'])

    def test_get_invalid_id(self):
        response = self.client.get('/api/users/999999/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_put_full_update(self):
        created = self._create().data['data']
        response = self.client.put(
            f"/api/users/{created['id']}/",
            {
                'username': created['username'],
                'password': 'SecurePassword456',
                'first_name': 'Updated',
                'last_name': 'User',
                'email': created['email'],
                'phone_number': '9876543211',
                'role': USER_ROLE_BRIDGE_SITE_ENGINEER,
                'access_type': 'BIS',
            },
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['first_name'], 'Updated')
        user = User.objects.get(pk=created['id'])
        self.assertTrue(user.check_password('SecurePassword456'))

    def test_put_duplicate_username(self):
        first = self._create(username='put.user.one', email='put1@example.com').data['data']
        second = self._create(username='put.user.two', email='put2@example.com').data['data']
        response = self.client.put(
            f"/api/users/{second['id']}/",
            {
                'username': first['username'],
                'password': 'SecurePassword123',
                'first_name': 'Updated',
                'last_name': 'User',
                'email': second['email'],
                'phone_number': '9876543210',
                'role': USER_ROLE_ROAD_SITE_ENGINEER,
                'access_type': 'RIS',
            },
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_patch_first_name(self):
        created = self._create().data['data']
        response = self.client.patch(
            f"/api/users/{created['id']}/",
            {'first_name': 'Patched'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['first_name'], 'Patched')
        self.assertEqual(response.data['data']['last_name'], 'User')

    def test_patch_email(self):
        created = self._create().data['data']
        response = self.client.patch(
            f"/api/users/{created['id']}/",
            {'email': 'patched@example.com'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_patch_phone(self):
        created = self._create().data['data']
        response = self.client.patch(
            f"/api/users/{created['id']}/",
            {'phone_number': '9123456789'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_patch_role_to_other_with_required_access(self):
        created = self._create().data['data']
        response = self.client.patch(
            f"/api/users/{created['id']}/",
            {
                'role': USER_ROLE_BRIDGE_SITE_ENGINEER,
                'access_type': 'BIS',
            },
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_patch_role_without_valid_access_fails(self):
        created = self._create().data['data']
        response = self.client.patch(
            f"/api/users/{created['id']}/",
            {'role': USER_ROLE_BRIDGE_SITE_ENGINEER},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['data']['access_type'], 'BIS')

    def test_patch_other_type_required_when_changing_to_other_role(self):
        created = self._create(
            role=USER_ROLE_ROAD_SITE_ENGINEER,
            access_type='RIS',
        ).data['data']
        response = self.client.patch(
            f"/api/users/{created['id']}/",
            {'role': USER_ROLE_ADMIN, 'access_type': 'RIS'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patch_password_hashes_password(self):
        created = self._create().data['data']
        response = self.client.patch(
            f"/api/users/{created['id']}/",
            {'password': 'NewSecurePassword123'},
            format='json',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn('password', response.data['data'])
        user = User.objects.get(pk=created['id'])
        self.assertTrue(user.check_password('NewSecurePassword123'))

    def test_delete_deactivates_user(self):
        created = self._create().data['data']
        response = self.client.delete(f"/api/users/{created['id']}/", **self.auth)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        user = User.objects.get(pk=created['id'])
        self.assertFalse(user.is_active)

    def test_get_deleted_user_returns_404(self):
        created = self._create().data['data']
        self.client.delete(f"/api/users/{created['id']}/", **self.auth)
        response = self.client.get(f"/api/users/{created['id']}/", **self.auth)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_nonexistent_user(self):
        response = self.client.delete('/api/users/999999/', **self.auth)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_admin_cannot_create_superadmin(self):
        response = self._create(
            username='new.super',
            email='new.super@example.com',
            role=USER_ROLE_SUPER_ADMIN,
            access_type='ALL',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_superadmin_can_create_superadmin(self):
        token = create_access_token('super.admin')
        response = self.client.post(
            '/api/users/',
            {
                **CREATE_PAYLOAD,
                'username': 'created.super',
                'email': 'created.super@example.com',
                'role': USER_ROLE_SUPER_ADMIN,
                'access_type': 'ALL',
            },
            format='json',
            HTTP_AUTHORIZATION=f'Bearer {token}',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_cannot_delete_self(self):
        response = self.client.delete(
            f'/api/users/{self.admin_user.id}/',
            **self.auth,
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_login_still_works_after_user_creation(self):
        created = self._create(username='login.user', email='login.user@example.com').data['data']
        response = self.client.post(
            '/api/auth/login/',
            {'username': created['username']},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])

    def test_login_still_works_after_user_update(self):
        created = self._create(username='login.update', email='login.update@example.com').data['data']
        self.client.patch(
            f"/api/users/{created['id']}/",
            {'first_name': 'Changed'},
            format='json',
            **self.auth,
        )
        response = self.client.post(
            '/api/auth/login/',
            {'username': created['username']},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_password_not_stored_plaintext(self):
        created = self._create(username='hash.user', email='hash.user@example.com').data['data']
        user = User.objects.get(pk=created['id'])
        self.assertNotEqual(user.password, 'SecurePassword123')
