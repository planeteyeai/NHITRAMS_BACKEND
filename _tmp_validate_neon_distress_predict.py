"""Read-only Neon validation for POST /distress_predict_filter/.

Loads credentials from .env. Does not seed or mutate distress rows.
"""
from __future__ import annotations

import gzip
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / '.env')

# Prefer Neon DB_* path (production naming).
os.environ.setdefault('DJANGO_ENV', 'production')
os.environ.setdefault('DISTRESS_PREDICT_FILTER_PERF_DEBUG', 'true')

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')
django.setup()

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import Client, override_settings

from authentication.jwt_utils import create_access_token
from distress.models import Distress, Project
from nhit.caching.invalidation import invalidate_distress_predict_filter_cache

ENDPOINT = '/distress_predict_filter/'
ABU_PAYLOAD = {
    'project_name': ['Abu Road to Swaroopganj'],
    'direction': ['All'],
    'distress_type': ['All'],
    'page': 1,
    'page_size': 200000,
}


def _headers(resp) -> dict:
    return {
        k: resp.get(k)
        for k in (
            'X-Distress-Paginated',
            'X-Distress-Page',
            'X-Distress-Page-Size',
            'X-Distress-Row-Count',
            'X-Distress-Has-More',
            'Content-Encoding',
            'Content-Length',
        )
    }


def main() -> int:
    host = settings.DATABASES['default'].get('HOST') or ''
    name = settings.DATABASES['default'].get('NAME') or ''
    print(f'db_name={name}')
    print(f'db_host_class={"neon" if "neon.tech" in host else "other"}')
    print(f'cache_enabled={getattr(settings, "NHIT_CACHE_ENABLED", False)}')
    print(f'cache_backend={(settings.CACHES.get("default") or {}).get("BACKEND")}')

    with connection.cursor() as c:
        c.execute('SELECT COUNT(*) FROM distress')
        print(f'distress_total={c.fetchone()[0]}')

    project = Project.objects.filter(project_name__iexact='Abu Road to Swaroopganj').first()
    if not project:
        # case variants
        project = Project.objects.filter(project_name__icontains='Abu Road').first()
    print(f'abu_project={getattr(project, "project_name", None)}')

    abu_count = 0
    if project:
        abu_count = Distress.objects.filter(
            project=project,
            latitude__isnull=False,
            longitude__isnull=False,
        ).count()
    print(f'abu_matching_rows={abu_count}')

    payload = dict(ABU_PAYLOAD)
    if project:
        payload['project_name'] = [project.project_name]

    with override_settings(
        ALLOWED_HOSTS=['*', 'testserver', 'localhost', '127.0.0.1'],
        DISTRESS_PREDICT_FILTER_PERF_DEBUG=True,
    ):
        # Fresh cache namespace so first call is a true MISS.
        invalidate_distress_predict_filter_cache()

        token = create_access_token('neon.validator')
        client = Client()
        auth = {
            'HTTP_AUTHORIZATION': f'Bearer {token}',
            'HTTP_HOST': 'localhost',
        }

        t0 = time.perf_counter()
        miss = client.post(
            ENDPOINT,
            data=json.dumps(payload),
            content_type='application/json',
            **auth,
        )
        miss_ms = round((time.perf_counter() - t0) * 1000, 2)

        t1 = time.perf_counter()
        hit = client.post(
            ENDPOINT,
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_ACCEPT_ENCODING='gzip',
            **auth,
        )
        hit_ms = round((time.perf_counter() - t1) * 1000, 2)

        print(f'payload_project={payload["project_name"]}')
        print(f'miss_status={miss.status_code} miss_ms={miss_ms}')
        print(f'hit_status={hit.status_code} hit_ms={hit_ms}')
        print(f'miss_headers={_headers(miss)}')
        print(f'hit_headers={_headers(hit)}')

        if miss.status_code != 200 or hit.status_code != 200:
            print('FAIL: non-200', miss.content[:300], hit.content[:200])
            return 1

        miss_json = json.loads(miss.content)
        hit_raw = hit.content
        if hit.get('Content-Encoding') == 'gzip':
            hit_json = json.loads(gzip.decompress(hit_raw))
        else:
            hit_json = json.loads(hit_raw)

        if miss_json != hit_json:
            print('FAIL: miss/hit JSON mismatch')
            return 1

        if isinstance(miss_json, dict) and miss_json.get('message') == 'No match':
            print('RESULT=no_match')
            print(f'PERF miss_ms={miss_ms} hit_ms={hit_ms}')
            return 0

        if not (
            isinstance(miss_json, list)
            and len(miss_json) == 1
            and isinstance(miss_json[0], list)
        ):
            print('FAIL: nested contract broken')
            return 1

        rows = miss_json[0]
        row_count = int(miss['X-Distress-Row-Count'])
        print(f'page_rows={len(rows)} header_row_count={row_count}')

        if abu_count and row_count != abu_count and miss.get('X-Distress-Has-More') == 'false':
            print(f'WARN: header_row_count={row_count} != abu_matching_rows={abu_count}')

        if rows:
            sample = rows[0]
            required = {
                'distress_id',
                'predicted_quantity',
                'resolved_quantity',
                'remaining_quantity',
                'direction',
                'project_name',
            }
            missing = required - set(sample)
            if missing:
                print(f'FAIL: missing fields {missing}')
                return 1
            if sample['direction'] != sample['direction'].lower():
                print('FAIL: direction not lowercased')
                return 1
            print(
                f'sample_id={sample["distress_id"]} '
                f'direction={sample["direction"]} '
                f'remaining={sample["remaining_quantity"]}'
            )

        # Page completeness across pages when needed
        collected = {r['distress_id'] for r in rows}
        page = 1
        has_more = miss.get('X-Distress-Has-More') == 'true'
        while has_more:
            page += 1
            page_payload = {**payload, 'page': page}
            resp = client.post(
                ENDPOINT,
                data=json.dumps(page_payload),
                content_type='application/json',
                **auth,
            )
            if resp.status_code != 200:
                print(f'FAIL: page {page} status {resp.status_code}')
                return 1
            body = json.loads(resp.content)
            if isinstance(body, dict) and body.get('message') == 'No match':
                print(f'FAIL: unexpected No match on page {page}')
                return 1
            page_rows = body[0]
            ids = {r['distress_id'] for r in page_rows}
            overlap = collected & ids
            print(f'page={page} rows={len(page_rows)} overlap={len(overlap)}')
            if overlap:
                print('FAIL: page overlap')
                return 1
            collected |= ids
            has_more = resp.get('X-Distress-Has-More') == 'true'
            if page > 20:
                print('FAIL: too many pages')
                return 1

        print(f'total_unique_ids={len(collected)}')
        if row_count and len(collected) != row_count and miss.get('X-Distress-Has-More') == 'false' and page == 1:
            # single page should equal header when has_more false
            if len(collected) != row_count:
                print(f'WARN: collected={len(collected)} row_count={row_count}')

        if abu_count and len(collected) != abu_count:
            print(f'WARN: collected={len(collected)} abu_matching_rows={abu_count}')

        if hit.get('Content-Encoding') != 'gzip':
            print('FAIL: HIT missing Content-Encoding=gzip')
            return 1

        print(
            f'PERF miss_ms={miss_ms} hit_ms={hit_ms} '
            f'speedup_x={round(miss_ms / hit_ms, 2) if hit_ms else "inf"}'
        )
        print('RESULT=ok')
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
