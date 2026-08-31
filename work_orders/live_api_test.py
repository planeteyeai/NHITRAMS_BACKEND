"""Live API smoke tests for Work Order Management."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

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


def main() -> int:
    results: list[tuple[str, int, int, bool]] = []

    def record(name: str, status: int, expected: int):
        results.append((name, status, expected, status == expected))

    status, login = req('POST', '/api/auth/login/', body={'username': 'ram.patil'})
    record('01 Login', status, 200)
    token = login.get('token')
    if not token:
        print('Login failed:', login)
        return 1

    suffix = str(int(time.time()))
    wo_normal = {
        'work_order_id': f'WO-LIVE-{suffix}',
        'type_of_work': 'Routine Maintenance',
        'other_type': '',
        'chainage_km_from': '10.500',
        'chainage_km_to': '12.750',
    }
    wo_other = {
        'work_order_id': f'WO-OTHER-{suffix}',
        'type_of_work': 'Other',
        'other_type': 'Emergency Drainage Work',
        'chainage_km_from': '15.000',
        'chainage_km_to': '16.500',
    }

    status, created = req('POST', '/api/work-orders/', token, wo_normal)
    record('02 Create Work Order', status, 201)
    wo_id = created.get('data', {}).get('id')

    status, _ = req('POST', '/api/work-orders/', token, wo_other)
    record('03 Create Other Work Order', status, 201)

    status, _ = req('POST', '/api/work-orders/', token, wo_normal)
    record('04 Duplicate Work Order', status, 409)

    status, _ = req('GET', '/api/work-orders/?page=1&page_size=20', token)
    record('05 Get Work Order List', status, 200)

    status, _ = req('GET', f'/api/work-orders/?search={wo_normal["work_order_id"]}', token)
    record('06 Search', status, 200)

    status, _ = req(
        'GET',
        '/api/work-orders/?type_of_work=' + urllib.parse.quote('Routine Maintenance'),
        token,
    )
    record('07 Filter', status, 200)

    status, _ = req('GET', '/api/work-orders/?sort_by=created_at&sort_order=desc', token)
    record('08 Sort', status, 200)

    status, _ = req('GET', f'/api/work-orders/{wo_id}/', token)
    record('09 Get By ID', status, 200)

    status, _ = req(
        'PUT',
        f'/api/work-orders/{wo_id}/',
        token,
        {
            **wo_normal,
            'type_of_work': 'Special Repair',
            'chainage_km_from': '11.000',
            'chainage_km_to': '13.000',
        },
    )
    record('10 PUT', status, 200)

    status, _ = req('PATCH', f'/api/work-orders/{wo_id}/', token, {'chainage_km_from': '11.500'})
    record('11 PATCH', status, 200)

    status, patched = req(
        'PATCH',
        f'/api/work-orders/{wo_id}/',
        token,
        {'type_of_work': 'Other', 'other_type': 'Emergency Work'},
    )
    record('12 PATCH Type To Other', status, 200)

    status, patched = req(
        'PATCH',
        f'/api/work-orders/{wo_id}/',
        token,
        {'type_of_work': 'Routine Maintenance'},
    )
    record('13 PATCH Other To Normal', status, 200)
    other_cleared = patched.get('data', {}).get('other_type') in (None, '')

    status, _ = req(
        'POST',
        '/api/work-orders/',
        token,
        {
            'work_order_id': f'WO-BAD-{suffix}',
            'type_of_work': 'Routine Maintenance',
            'other_type': '',
            'chainage_km_from': '20',
            'chainage_km_to': '10',
        },
    )
    record('14 Invalid Chainage', status, 400)

    status, _ = req(
        'POST',
        '/api/work-orders/',
        token,
        {
            'work_order_id': f'WO-BAD2-{suffix}',
            'type_of_work': 'Other',
            'other_type': '',
            'chainage_km_from': '10',
            'chainage_km_to': '20',
        },
    )
    record('15 Invalid Other Type', status, 400)

    status, _ = req('DELETE', f'/api/work-orders/{wo_id}/', token)
    record('16 Delete', status, 204)

    status, _ = req('GET', f'/api/work-orders/{wo_id}/', token)
    record('17 Get Deleted', status, 404)

    status, _ = req('GET', '/api/work-orders/')
    record('18 Unauthorized', status, 401)

    passed = sum(1 for *_, ok in results if ok)
    print('LIVE API RESULTS')
    for name, status_code, expected, ok in results:
        mark = 'PASS' if ok else 'FAIL'
        print(f'{mark} | {name}: {status_code} (expected {expected})')
    print(f'Summary: {passed}/{len(results)} passed')
    print('other_type cleared on PATCH:', other_cleared)
    return 0 if passed == len(results) and other_cleared else 1


if __name__ == '__main__':
    raise SystemExit(main())
