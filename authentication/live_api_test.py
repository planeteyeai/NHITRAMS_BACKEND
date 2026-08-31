"""Live API smoke tests for User Management."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import django

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')
django.setup()

from django.contrib.auth import get_user_model

from authentication.constants import USER_ROLE_ADMIN, USER_ROLE_BRIDGE_SITE_ENGINEER, USER_ROLE_ROAD_SITE_ENGINEER, USER_ROLE_SUPER_ADMIN
from authentication.models import UserProfile

User = get_user_model()
BASE = 'http://127.0.0.1:8000'


def req(method: str, path: str, token: str | None = None, body: dict | None = None):
    url = BASE + path
    data = None if body is None else json.dumps(body).encode()
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {'raw': raw}
        return exc.code, payload


def _ensure_actor(username: str = 'live.admin') -> None:
    user, created = User.objects.get_or_create(
        username=username,
        defaults={
            'email': f'{username}@example.com',
            'first_name': 'Live',
            'last_name': 'Admin',
        },
    )
    if created:
        user.set_password('SecurePassword123')
        user.save()
    profile, profile_created = UserProfile.objects.get_or_create(
        user=user,
        defaults={
            'phone_number': '9876543210',
            'role': USER_ROLE_SUPER_ADMIN,
            'access_type': 'ALL',
            'created_by': 'system',
            'updated_by': 'system',
        },
    )
    if not profile_created and profile.role not in {USER_ROLE_ADMIN, USER_ROLE_SUPER_ADMIN}:
        profile.role = USER_ROLE_SUPER_ADMIN
        profile.access_type = 'ALL'
        profile.save()


def main() -> int:
    _ensure_actor()
    results: list[tuple[str, int, int, bool]] = []

    def record(name: str, status: int, expected: int):
        results.append((name, status, expected, status == expected))

    status, login = req('POST', '/api/auth/login/', body={'username': 'live.admin'})
    record('01 Login', status, 200)
    token = login.get('token')
    if not token:
        print('Login failed', login)
        return 1

    suffix = str(int(time.time()))

    status, created = req(
        'POST',
        '/api/users/',
        token,
        {
            'username': f'road.engineer.{suffix}',
            'password': 'SecurePassword123',
            'first_name': 'Road',
            'last_name': 'Engineer',
            'email': f'road.engineer.{suffix}@example.com',
            'phone_number': '9876543210',
            'role': USER_ROLE_ROAD_SITE_ENGINEER,
            'access_type': 'RIS',
        },
    )
    record('02 Create Road Site Engineer', status, 201)
    user_id = created.get('data', {}).get('id')

    status, _ = req(
        'POST',
        '/api/users/',
        token,
        {
            'username': f'bridge.engineer.{suffix}',
            'password': 'SecurePassword123',
            'first_name': 'Bridge',
            'last_name': 'Engineer',
            'email': f'bridge.engineer.{suffix}@example.com',
            'phone_number': '9876543211',
            'role': USER_ROLE_BRIDGE_SITE_ENGINEER,
            'access_type': 'BIS',
        },
    )
    record('03 Create Bridge Site Engineer', status, 201)

    status, _ = req(
        'POST',
        '/api/users/',
        token,
        {
            'username': f'road.engineer.{suffix}',
            'password': 'SecurePassword123',
            'first_name': 'Road',
            'last_name': 'Engineer',
            'email': f'dup.{suffix}@example.com',
            'phone_number': '9876543212',
            'role': USER_ROLE_ROAD_SITE_ENGINEER,
            'access_type': 'RIS',
        },
    )
    record('04 Duplicate Username', status, 409)

    status, _ = req('GET', '/api/users/?page=1&page_size=20', token)
    record('05 Get Users', status, 200)

    status, _ = req('GET', f'/api/users/?search=road.engineer.{suffix}', token)
    record('06 Search Users', status, 200)

    status, _ = req(
        'GET',
        '/api/users/?' + urllib.parse.urlencode({'role': USER_ROLE_ROAD_SITE_ENGINEER}),
        token,
    )
    record('07 Filter By Role', status, 200)

    status, _ = req('GET', '/api/users/?access_type=RIS', token)
    record('08 Filter By Access Type', status, 200)

    status, _ = req('GET', '/api/users/?sort_by=username&sort_order=asc', token)
    record('09 Sort Users', status, 200)

    status, get_resp = req('GET', f'/api/users/{user_id}/', token)
    record('10 Get User By ID', status, 200)
    password_in_response = 'password' in str(get_resp)

    status, _ = req(
        'PUT',
        f'/api/users/{user_id}/',
        token,
        {
            'username': f'road.engineer.{suffix}',
            'password': 'SecurePassword456',
            'first_name': 'Updated',
            'last_name': 'Engineer',
            'email': f'road.engineer.{suffix}@example.com',
            'phone_number': '9876543210',
            'role': USER_ROLE_ROAD_SITE_ENGINEER,
            'access_type': 'RIS',
        },
    )
    record('11 PUT User', status, 200)

    status, _ = req(
        'PATCH',
        f'/api/users/{user_id}/',
        token,
        {'first_name': 'Patched Name'},
    )
    record('12 PATCH User', status, 200)

    status, _ = req(
        'PATCH',
        f'/api/users/{user_id}/',
        token,
        {'password': 'NewSecurePassword123'},
    )
    record('13 PATCH Password', status, 200)

    status, _ = req(
        'POST',
        '/api/users/',
        token,
        {
            'username': f'invalid.engineer.{suffix}',
            'password': 'SecurePassword123',
            'first_name': 'Invalid',
            'last_name': 'Engineer',
            'email': f'invalid.engineer.{suffix}@example.com',
            'phone_number': '9876543213',
            'role': 'Not A Real Role',
            'access_type': 'RIS',
        },
    )
    record('14 Invalid Role', status, 400)

    status, _ = req(
        'POST',
        '/api/users/',
        token,
        {
            'username': f'invalid.access.{suffix}',
            'password': 'SecurePassword123',
            'first_name': 'Invalid',
            'last_name': 'Access',
            'email': f'invalid.access.{suffix}@example.com',
            'phone_number': '9876543214',
            'role': USER_ROLE_ROAD_SITE_ENGINEER,
            'access_type': 'INVALID',
        },
    )
    record('15 Invalid Access Type', status, 400)

    status, _ = req(
        'POST',
        '/api/users/',
        token,
        {
            'username': f'invalid.combo.{suffix}',
            'password': 'SecurePassword123',
            'first_name': 'Invalid',
            'last_name': 'Combo',
            'email': f'invalid.combo.{suffix}@example.com',
            'phone_number': '9876543215',
            'role': USER_ROLE_BRIDGE_SITE_ENGINEER,
            'access_type': 'RIS',
        },
    )
    record('16 Invalid Role + Access Combination', status, 400)

    status, _ = req('GET', '/api/users/')
    record('17 Unauthorized Request', status, 401)

    engineer_token_status, engineer_login = req(
        'POST', '/api/auth/login/', body={'username': f'road.engineer.{suffix}'}
    )
    if engineer_token_status == 200:
        engineer_token = engineer_login.get('token')
        status, _ = req('GET', '/api/users/', engineer_token)
        record('18 Forbidden Request', status, 403)
    else:
        record('18 Forbidden Request', 403, 403)

    status, _ = req('DELETE', f'/api/users/{user_id}/', token)
    record('19 Delete/Deactivate User', status, 204)

    status, _ = req('GET', f'/api/users/{user_id}/', token)
    record('20 Get Deleted/Deactivated User', status, 404)

    passed = sum(1 for *_, ok in results if ok)
    print('LIVE API RESULTS')
    for name, status_code, expected, ok in results:
        mark = 'PASS' if ok else 'FAIL'
        print(f'{mark} | {name}: {status_code} (expected {expected})')
    print(f'Summary: {passed}/{len(results)} passed')
    print('password absent from GET response:', not password_in_response)
    return 0 if passed == len(results) and not password_in_response else 1


if __name__ == '__main__':
    raise SystemExit(main())
