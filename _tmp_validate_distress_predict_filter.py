"""Read-only validation for POST /distress_predict_filter/ (Abu Road, page_size=200000).

Does not seed or mutate distress rows. Uses LocMem cache when Redis is unavailable
so MISS vs HIT path can still be exercised locally.
"""
from __future__ import annotations

import gzip
import json
import os
import time

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')
os.environ.setdefault('DISTRESS_PREDICT_FILTER_PERF_DEBUG', 'true')

import django

django.setup()

from django.conf import settings
from django.core.cache import cache
from django.test import Client, override_settings

from authentication.jwt_utils import create_access_token
from distress.models import Distress, Project
from nhit.caching.invalidation import invalidate_distress_predict_filter_cache

ENDPOINT = '/distress_predict_filter/'
PAYLOAD = {
    'project_name': ['Abu Road to Swaroopganj'],
    'direction': ['All'],
    'distress_type': ['All'],
    'page': 1,
    'page_size': 200000,
}

_LOCMEM = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'distress-predict-readonly-validate',
        'KEY_PREFIX': 'nhit-validate-dpgz1',
    }
}


def main() -> int:
    projects = list(Project.objects.values_list('project_name', flat=True)[:20])
    print(f'sample_projects={projects}')

    project = Project.objects.filter(project_name__iexact='Abu Road to Swaroopganj').first()
    total_db = 0
    if project:
        total_db = Distress.objects.filter(
            project=project,
            latitude__isnull=False,
            longitude__isnull=False,
        ).count()
        total_all = Distress.objects.filter(project=project).count()
        print(f'project_found=True distress_all={total_all} distress_with_latlng={total_db}')
    else:
        print('project_found=False')

    # Prefer any project that actually has lat/lng rows for completeness check.
    effective_payload = dict(PAYLOAD)
    if total_db == 0:
        with_data = (
            Distress.objects.filter(latitude__isnull=False, longitude__isnull=False, project__isnull=False)
            .values_list('project__project_name', flat=True)
            .distinct()[:5]
        )
        names = list(with_data)
        print(f'projects_with_latlng_rows={names}')
        if names:
            effective_payload['project_name'] = [names[0]]
            total_db = Distress.objects.filter(
                project__project_name=names[0],
                latitude__isnull=False,
                longitude__isnull=False,
            ).count()
            print(f'using_fallback_project={names[0]} rows={total_db}')

    with override_settings(
        ALLOWED_HOSTS=['*', 'testserver', 'localhost', '127.0.0.1'],
        NHIT_CACHE_ENABLED=True,
        CACHES=_LOCMEM,
        DISTRESS_PREDICT_FILTER_PERF_DEBUG=True,
        JWT_SECRET_KEY=getattr(settings, 'JWT_SECRET_KEY', None) or 'test-jwt-secret',
    ):
        cache.clear()
        invalidate_distress_predict_filter_cache()

        token = create_access_token('readonly.validator')
        client = Client()
        auth = {'HTTP_AUTHORIZATION': f'Bearer {token}', 'HTTP_HOST': 'localhost'}

        t0 = time.perf_counter()
        miss = client.post(
            ENDPOINT,
            data=json.dumps(effective_payload),
            content_type='application/json',
            **auth,
        )
        miss_ms = round((time.perf_counter() - t0) * 1000, 2)

        t1 = time.perf_counter()
        hit = client.post(
            ENDPOINT,
            data=json.dumps(effective_payload),
            content_type='application/json',
            HTTP_ACCEPT_ENCODING='gzip',
            **auth,
        )
        hit_ms = round((time.perf_counter() - t1) * 1000, 2)

        print(f'payload_project={effective_payload["project_name"]}')
        print(f'miss_status={miss.status_code} miss_ms={miss_ms}')
        print(f'hit_status={hit.status_code} hit_ms={hit_ms}')

        for label, resp in (('miss', miss), ('hit', hit)):
            headers = {
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
            print(f'{label}_headers={headers}')

        if miss.status_code != 200 or hit.status_code != 200:
            print('FAIL: non-200', miss.content[:500], hit.content[:200])
            return 1

        miss_body = miss.content
        hit_body = (
            gzip.decompress(hit.content)
            if hit.get('Content-Encoding') == 'gzip'
            else hit.content
        )
        miss_json = json.loads(miss_body)
        hit_json = json.loads(hit_body)

        if miss_json != hit_json:
            print('FAIL: miss/hit JSON mismatch')
            return 1

        if isinstance(miss_json, dict) and miss_json.get('message') == 'No match':
            print('RESULT=no_match')
            print(f'PERF miss_ms={miss_ms} hit_ms={hit_ms}')
            # Still require gzip HIT for empty? Empty returns early before cache.
            # For empty filter, both return No match without DPGZ1 path — acceptable.
            return 0

        if not (
            isinstance(miss_json, list)
            and len(miss_json) == 1
            and isinstance(miss_json[0], list)
        ):
            print(f'FAIL: nested contract broken')
            return 1

        rows = miss_json[0]
        row_count_header = int(miss['X-Distress-Row-Count'])
        print(f'page_rows={len(rows)} header_row_count={row_count_header}')

        if total_db and row_count_header != total_db and miss.get('X-Distress-Has-More') == 'false':
            # Full set should match when no more pages
            print(f'WARN: header_row_count={row_count_header} != db_matching_rows={total_db}')

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
                f'sample_distress_id={sample["distress_id"]} '
                f'direction={sample["direction"]} '
                f'remaining={sample["remaining_quantity"]}'
            )

        has_more = miss.get('X-Distress-Has-More') == 'true'
        if has_more:
            page2 = client.post(
                ENDPOINT,
                data=json.dumps({**effective_payload, 'page': 2}),
                content_type='application/json',
                **auth,
            )
            body2 = json.loads(page2.content)
            ids1 = {r['distress_id'] for r in rows}
            ids2 = {r['distress_id'] for r in body2[0]}
            overlap = ids1 & ids2
            print(f'page2_rows={len(body2[0])} page_overlap={len(overlap)}')
            if overlap:
                print('FAIL: page overlap')
                return 1

        if hit.get('Content-Encoding') != 'gzip':
            print('FAIL: HIT path did not return Content-Encoding=gzip')
            return 1

        print(
            f'PERF miss_ms={miss_ms} hit_ms={hit_ms} '
            f'speedup_x={round(miss_ms / hit_ms, 2) if hit_ms else "inf"}'
        )
        print('RESULT=ok')
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
