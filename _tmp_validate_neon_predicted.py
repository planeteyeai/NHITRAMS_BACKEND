"""Read-only Neon validation against predicted_distress for distress_predict_filter."""
from __future__ import annotations

import gzip
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / '.env', override=True)
os.environ['DJANGO_ENV'] = 'production'
os.environ.setdefault('DISTRESS_PREDICT_FILTER_PERF_DEBUG', 'true')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')

import django

django.setup()

from django.db import connection
from django.test import Client, override_settings

from authentication.jwt_utils import create_access_token
from distress.models import PredictedDistress
from nhit.caching.invalidation import invalidate_distress_predict_filter_cache

ENDPOINT = '/distress_predict_filter/'
PAYLOAD = {
    'project_name': ['Abu Road to Swaroopganj'],
    'direction': ['All'],
    'distress_type': ['All'],
    'page': 1,
    'page_size': 200000,
}


def main() -> int:
    with connection.cursor() as c:
        c.execute('SELECT COUNT(*) FROM predicted_distress')
        total = c.fetchone()[0]
    print(f'table=predicted_distress total={total}')

    abu = PredictedDistress.objects.filter(
        project_name__iexact='Abu Road to Swaroopganj',
        latitude__isnull=False,
        longitude__isnull=False,
    ).count()
    print(f'abu_matching_rows={abu}')

    invalidate_distress_predict_filter_cache()
    token = create_access_token('neon.validator')
    client = Client()
    auth = {'HTTP_AUTHORIZATION': f'Bearer {token}', 'HTTP_HOST': 'localhost'}

    with override_settings(
        ALLOWED_HOSTS=['*'],
        DISTRESS_PREDICT_FILTER_PERF_DEBUG=True,
    ):
        t0 = time.perf_counter()
        miss = client.post(
            ENDPOINT, data=json.dumps(PAYLOAD), content_type='application/json', **auth
        )
        miss_ms = round((time.perf_counter() - t0) * 1000, 2)

        t1 = time.perf_counter()
        hit = client.post(
            ENDPOINT,
            data=json.dumps(PAYLOAD),
            content_type='application/json',
            HTTP_ACCEPT_ENCODING='gzip',
            **auth,
        )
        hit_ms = round((time.perf_counter() - t1) * 1000, 2)

    print(f'miss_status={miss.status_code} miss_ms={miss_ms}')
    print(f'hit_status={hit.status_code} hit_ms={hit_ms}')
    print(
        'miss_headers',
        {
            k: miss.get(k)
            for k in (
                'X-Distress-Row-Count',
                'X-Distress-Page-Size',
                'X-Distress-Has-More',
                'Content-Length',
            )
        },
    )
    print(
        'hit_headers',
        {
            k: hit.get(k)
            for k in ('Content-Encoding', 'Content-Length', 'X-Distress-Row-Count')
        },
    )

    if miss.status_code != 200 or hit.status_code != 200:
        print('FAIL', miss.content[:200], hit.content[:200])
        return 1

    body = json.loads(miss.content)
    if isinstance(body, dict) and body.get('message') == 'No match':
        print('RESULT=no_match')
        return 0

    rows = body[0]
    print(f'page_rows={len(rows)}')
    sample = rows[0]
    print(
        f'sample_id={sample["distress_id"]} type={sample["distress_type"]} '
        f'direction={sample["direction"]} remaining={sample["remaining_quantity"]}'
    )
    if hit.get('Content-Encoding') != 'gzip':
        print('FAIL: missing gzip')
        return 1
    if json.loads(gzip.decompress(hit.content)) != body:
        print('FAIL: miss/hit mismatch')
        return 1
    if abu and int(miss['X-Distress-Row-Count']) != abu:
        print(f'WARN header={miss["X-Distress-Row-Count"]} abu={abu}')
    print(f'PERF miss_ms={miss_ms} hit_ms={hit_ms}')
    print('RESULT=ok')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
