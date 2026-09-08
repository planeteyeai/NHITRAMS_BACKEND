"""Call inventory_filter responses and sum service_road from returned JSON."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')
os.environ.setdefault('PYTHONUNBUFFERED', '1')

import django

django.setup()

from django.test import Client, override_settings


def _sum_service_road(payload) -> tuple[float, int, list]:
    """Walk nested inventory_filter groups; sum service_road keys."""
    total = 0.0
    n = 0
    samples = []
    if isinstance(payload, dict) and payload.get('message') == 'No match':
        return 0.0, 0, []
    if not isinstance(payload, list):
        return 0.0, 0, []
    for group in payload:
        if not isinstance(group, list):
            continue
        for row in group:
            if isinstance(row, dict) and 'service_road' in row and row['service_road'] is not None:
                val = float(row['service_road'])
                total += val
                n += 1
                if len(samples) < 20:
                    samples.append(
                        {
                            'asset_type': row.get('asset_type'),
                            'service_road': row.get('service_road'),
                            'direction': row.get('direction'),
                            'chainage_start': row.get('chainage_start'),
                            'chainage_end': row.get('chainage_end'),
                        }
                    )
    return total, n, samples


def _post(client: Client, body: dict):
    resp = client.post(
        '/inventory_filter/',
        data=body,
        content_type='application/json',
    )
    try:
        data = resp.json()
    except Exception:
        data = None
    return resp.status_code, dict(resp.headers), data


def main() -> int:
    base = {
        'chainage_start': 0,
        'chainage_end': 99999,
        'date': '2026-07-28',
        'direction': ['All'],
        'project_name': ['Kothakota to Kurnool'],
        'asset_type': ['All'],
        'page': 1,
        'page_size': 15000,
    }

    results = {}

    with override_settings(NHIT_CACHE_ENABLED=False):
        client = Client()

        # 1) Exact body from investigation (page 1 only)
        status, headers, data = _post(client, base)
        s, n, samples = _sum_service_road(data)
        results['all_assets_page1'] = {
            'status': status,
            'row_count_header': headers.get('X-Inventory-Row-Count'),
            'page': headers.get('X-Inventory-Page'),
            'page_size': headers.get('X-Inventory-Page-Size'),
            'has_more': headers.get('X-Inventory-Has-More'),
            'service_road_sum': s,
            'service_road_emissions': n,
            'samples': samples,
            'top_level_type': type(data).__name__,
            'groups_in_body': len(data) if isinstance(data, list) else None,
        }
        print('=== asset_type All, page 1 ===')
        print(json.dumps(results['all_assets_page1'], indent=2, default=str))

        # 2) Same filters but asset_type = Service Road only (should fit in one page)
        body_sr = {**base, 'asset_type': ['Service Road'], 'page': 1, 'page_size': 15000}
        status, headers, data = _post(client, body_sr)
        s, n, samples = _sum_service_road(data)
        # Also count all rows returned (with or without quantity)
        row_n = 0
        zero_omitted = 0
        if isinstance(data, list):
            for g in data:
                for row in g:
                    row_n += 1
                    if 'service_road' not in row:
                        zero_omitted += 1
        results['service_road_asset_only'] = {
            'status': status,
            'row_count_header': headers.get('X-Inventory-Row-Count'),
            'has_more': headers.get('X-Inventory-Has-More'),
            'rows_in_response': row_n,
            'rows_without_service_road_key': zero_omitted,
            'service_road_sum': s,
            'service_road_emissions': n,
            'samples': samples,
        }
        print('=== asset_type Service Road only ===')
        print(json.dumps(results['service_road_asset_only'], indent=2, default=str))

        # 3) Page through asset_type=Service Road with small pages to mimic multi-page clients
        total_sum = 0.0
        total_n = 0
        page = 1
        pages_hit = 0
        while True:
            body = {**body_sr, 'page': page, 'page_size': 10}
            status, headers, data = _post(client, body)
            if status != 200:
                break
            if isinstance(data, dict) and data.get('message') == 'No match':
                break
            s, n, _ = _sum_service_road(data)
            total_sum += s
            total_n += n
            pages_hit += 1
            has_more = (headers.get('X-Inventory-Has-More') or '').lower() == 'true'
            print(f'SR-only page={page} emissions={n} page_sum={s} running_sum={total_sum} has_more={has_more}')
            if not has_more:
                break
            page += 1
            if page > 20:
                break

        results['service_road_asset_all_pages_size_10'] = {
            'pages': pages_hit,
            'service_road_sum': total_sum,
            'service_road_emissions': total_n,
        }
        print('=== Service Road asset, paged page_size=10 ===')
        print(json.dumps(results['service_road_asset_all_pages_size_10'], indent=2))

        # 4) Summary API for comparison
        resp = client.post('/inventory_summary/', data=base, content_type='application/json')
        summary = resp.json()
        results['inventory_summary'] = {
            'status': resp.status_code,
            'service_road': summary.get('service_road'),
        }
        print('=== inventory_summary ===')
        print(json.dumps(results['inventory_summary'], indent=2))

        results['verdict'] = {
            'filter_all_page1_sum': results['all_assets_page1']['service_road_sum'],
            'filter_service_road_only_sum': results['service_road_asset_only']['service_road_sum'],
            'filter_service_road_paged_sum': results['service_road_asset_all_pages_size_10']['service_road_sum'],
            'summary_service_road': summary.get('service_road'),
            'match_summary_vs_filter_sr_only': (
                abs(float(summary.get('service_road') or 0) - float(results['service_road_asset_only']['service_road_sum']))
                < 1e-6
            ),
            'match_summary_vs_filter_sr_paged': (
                abs(float(summary.get('service_road') or 0) - float(results['service_road_asset_all_pages_size_10']['service_road_sum']))
                < 1e-6
            ),
        }
        print('=== VERDICT ===')
        print(json.dumps(results['verdict'], indent=2))

    out = ROOT / 'tools' / 'count_service_road_from_inventory_filter.json'
    out.write_text(json.dumps(results, indent=2, default=str), encoding='utf-8')
    print('wrote', out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
