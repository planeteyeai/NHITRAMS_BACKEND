"""Investigation-only: stage-level timing of distress PDF image preparation.

DOES NOT modify production code. Mirrors pdf.utils.download_image +
optimize_image_for_pdf with fine-grained timers.

Usage:
    set PYTHONUNBUFFERED=1
    python -u tools/investigate_distress_pdf_image_prep.py --limit 32 --project-contains Agra --date 04-09-2026
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import tempfile
import time
import traceback
import urllib.request
from pathlib import Path


def _log(msg: str) -> None:
    print(msg, flush=True)


def _ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000.0


def profile_one_image(url: str, temp_dir: str, *, timeout: int, max_bytes: int,
                      max_w: int, max_h: int, jpeg_quality: int) -> dict:
    """Mirror production download+optimize with per-stage timing."""
    from PIL import Image as PILImage
    from PIL import ImageOps

    row: dict = {
        'image_url': url,
        'ok': False,
        'error': '',
        'source_content_type': '',
        'source_size_bytes': 0,
        'source_width': None,
        'source_height': None,
        'source_format': '',
        'opt_size_bytes': 0,
        'opt_width': None,
        'opt_height': None,
        'dns_connect_ttfb_ms': 0.0,  # open() until headers available (approx)
        'download_ms': 0.0,         # urlopen + read (network+read)
        'response_read_ms': 0.0,    # response.read only
        'file_write_ms': 0.0,
        'decode_ms': 0.0,           # PIL open + load
        'exif_ms': 0.0,
        'convert_ms': 0.0,
        'resize_ms': 0.0,
        'jpeg_encode_write_ms': 0.0,
        'cleanup_ms': 0.0,
        'total_image_ms': 0.0,
        'bytes_per_sec': 0.0,
    }
    t_all = time.perf_counter()
    raw_path = None
    opt_path = None
    t_dl = None

    try:
        request = urllib.request.Request(
            url,
            headers={'User-Agent': 'NHIT-AI-Identifier-PDF/1.0'},
        )
        t_dl = time.perf_counter()
        with urllib.request.urlopen(request, timeout=timeout) as response:
            row['dns_connect_ttfb_ms'] = _ms(t_dl)
            row['source_content_type'] = (response.headers.get('Content-Type') or '').lower()
            # Chunked read to observe throughput and avoid one giant stall attribution.
            t_read = time.perf_counter()
            chunks: list[bytes] = []
            total = 0
            while total <= max_bytes:
                piece = response.read(256 * 1024)
                if not piece:
                    break
                chunks.append(piece)
                total += len(piece)
            data = b''.join(chunks)
            row['response_read_ms'] = _ms(t_read)
        row['download_ms'] = _ms(t_dl)
        if row['response_read_ms'] > 0 and len(data) > 0:
            row['bytes_per_sec'] = len(data) / (row['response_read_ms'] / 1000.0)

        if len(data) > max_bytes:
            row['error'] = 'too_large'
            row['total_image_ms'] = _ms(t_all)
            return row

        row['source_size_bytes'] = len(data)
        content_type = row['source_content_type']
        ext = '.jpg'
        if 'png' in content_type:
            ext = '.png'
        elif 'gif' in content_type:
            ext = '.gif'
        elif 'webp' in content_type:
            ext = '.webp'
        else:
            path_ext = Path(url.split('?', 1)[0]).suffix.lower()
            if path_ext in {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}:
                ext = path_ext

        t_write = time.perf_counter()
        fd, raw_path = tempfile.mkstemp(suffix=ext, prefix='inv_raw_', dir=temp_dir)
        with os.fdopen(fd, 'wb') as fh:
            fh.write(data)
        del data
        row['file_write_ms'] = _ms(t_write)

        # --- Pillow pipeline (mirrors optimize_image_for_pdf) ---
        t_decode = time.perf_counter()
        with PILImage.open(raw_path) as img:
            img.load()  # force decode now so timing is explicit
            row['decode_ms'] = _ms(t_decode)
            row['source_width'], row['source_height'] = img.size
            row['source_format'] = img.format or ''

            t_exif = time.perf_counter()
            img = ImageOps.exif_transpose(img)
            row['exif_ms'] = _ms(t_exif)

            t_conv = time.perf_counter()
            if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
                rgba = img.convert('RGBA')
                background = PILImage.new('RGB', rgba.size, (255, 255, 255))
                background.paste(rgba, mask=rgba.split()[-1])
                img = background
            elif img.mode != 'RGB':
                img = img.convert('RGB')
            row['convert_ms'] = _ms(t_conv)

            t_resize = time.perf_counter()
            img.thumbnail((max_w, max_h), PILImage.Resampling.LANCZOS)
            row['resize_ms'] = _ms(t_resize)
            row['opt_width'], row['opt_height'] = img.size

            t_enc = time.perf_counter()
            fd2, opt_path = tempfile.mkstemp(suffix='.jpg', prefix='inv_opt_', dir=temp_dir)
            os.close(fd2)
            img.save(
                opt_path,
                format='JPEG',
                quality=jpeg_quality,
                optimize=True,
                progressive=True,
            )
            row['jpeg_encode_write_ms'] = _ms(t_enc)
            row['opt_size_bytes'] = os.path.getsize(opt_path)
            img.close()

        t_clean = time.perf_counter()
        if raw_path and os.path.isfile(raw_path):
            try:
                os.remove(raw_path)
            except OSError:
                pass
        row['cleanup_ms'] = _ms(t_clean)
        row['ok'] = True
    except Exception as exc:
        row['error'] = f'{type(exc).__name__}: {exc}'
        if t_dl is not None and not row['download_ms']:
            row['download_ms'] = _ms(t_dl)
    finally:
        row['total_image_ms'] = _ms(t_all)
        # leave opt_path for size inspection; cleaned with temp dir
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=32)
    parser.add_argument('--project-contains', default='')
    parser.add_argument('--date', default='')
    parser.add_argument('--username', default='')
    parser.add_argument(
        '--timeout',
        type=float,
        default=60.0,
        help='HTTP timeout seconds for investigation (production default is 8).',
    )
    parser.add_argument('--out-csv', default='tools/investigate_image_prep_results.csv')
    parser.add_argument('--out-json', default='tools/investigate_image_prep_summary.json')
    args = parser.parse_args()

    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')
    os.environ.setdefault('PYTHONUNBUFFERED', '1')
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

    import django
    django.setup()

    from distress.models import DistressReport
    from pdf.utils import (
        IMAGE_MAX_BYTES,
        IMAGE_TIMEOUT_SECONDS,
        PDF_IMAGE_JPEG_QUALITY,
        PDF_IMAGE_MAX_HEIGHT_PX,
        PDF_IMAGE_MAX_WIDTH_PX,
        TempWorkspace,
    )

    qs = DistressReport.objects.all().order_by('id')
    if args.username:
        qs = qs.filter(username=args.username)
    if args.project_contains:
        qs = qs.filter(Project_Name__icontains=args.project_contains)
    if args.date:
        qs = qs.filter(Date=args.date)
    reports = list(qs[: args.limit])
    if not reports:
        _log('ERROR: no reports matched')
        return 2

    _log('=== Image prep pipeline investigation (read-only; no prod code changes) ===')
    _log(f'reports={len(reports)} inv_timeout={args.timeout}s '
         f'(prod IMAGE_TIMEOUT_SECONDS={IMAGE_TIMEOUT_SECONDS}) '
         f'target_px={PDF_IMAGE_MAX_WIDTH_PX}x{PDF_IMAGE_MAX_HEIGHT_PX} q={PDF_IMAGE_JPEG_QUALITY}')
    _log('HTTP client: urllib.request.urlopen (no session reuse / no connection pool)')
    _log('Worker path: sequential download_image(..., optimize=True) concurrency=1')
    _log('')

    rows = []
    with TempWorkspace() as ws:
        for i, report in enumerate(reports, 1):
            url = (report.Image_URL or '').strip()
            _log(f'[{i:02d}/{len(reports)}] report={report.id} starting')
            if not url:
                row = {
                    'report_id': report.id,
                    'image_url': '',
                    'ok': False,
                    'error': 'empty_url',
                    'total_image_ms': 0.0,
                }
                rows.append(row)
                _log(f'[{i:02d}/{len(reports)}] report={report.id} EMPTY URL')
                continue

            row = profile_one_image(
                url,
                ws.dir,
                timeout=args.timeout,
                max_bytes=IMAGE_MAX_BYTES,
                max_w=PDF_IMAGE_MAX_WIDTH_PX,
                max_h=PDF_IMAGE_MAX_HEIGHT_PX,
                jpeg_quality=PDF_IMAGE_JPEG_QUALITY,
            )
            row['report_id'] = report.id
            rows.append(row)
            _log(
                f'[{i:02d}/{len(reports)}] report={report.id} '
                f'ok={row["ok"]} total={row["total_image_ms"]:.0f}ms '
                f'dl={row.get("download_ms", 0):.0f} '
                f'(ttfb={row.get("dns_connect_ttfb_ms", 0):.0f} read={row.get("response_read_ms", 0):.0f}) '
                f'write={row.get("file_write_ms", 0):.0f} '
                f'decode={row.get("decode_ms", 0):.0f} exif={row.get("exif_ms", 0):.0f} '
                f'conv={row.get("convert_ms", 0):.0f} resize={row.get("resize_ms", 0):.0f} '
                f'jpeg={row.get("jpeg_encode_write_ms", 0):.0f} '
                f'src={row.get("source_size_bytes", 0)/1024:.0f}KB '
                f'{row.get("source_width")}x{row.get("source_height")} '
                f'-> {row.get("opt_size_bytes", 0)/1024:.0f}KB '
                f'{row.get("opt_width")}x{row.get("opt_height")} '
                f'rate={row.get("bytes_per_sec", 0)/1024:.0f}KB/s '
                f'err={row.get("error") or "-"}'
            )

    ok_rows = [r for r in rows if r.get('ok')]
    stages = [
        'download_ms', 'dns_connect_ttfb_ms', 'response_read_ms', 'file_write_ms',
        'decode_ms', 'exif_ms', 'convert_ms', 'resize_ms', 'jpeg_encode_write_ms',
        'cleanup_ms', 'total_image_ms',
    ]

    def _sum(key):
        return sum(float(r.get(key) or 0) for r in ok_rows)

    def _mean(key):
        vals = [float(r.get(key) or 0) for r in ok_rows]
        return statistics.mean(vals) if vals else 0.0

    def _p95(key):
        vals = sorted(float(r.get(key) or 0) for r in ok_rows)
        if not vals:
            return 0.0
        idx = min(len(vals) - 1, int(round(0.95 * (len(vals) - 1))))
        return vals[idx]

    total_wall = _sum('total_image_ms')
    summary = {
        'n_reports': len(rows),
        'n_ok': len(ok_rows),
        'n_fail': len(rows) - len(ok_rows),
        'sum_ms': {k: round(_sum(k), 1) for k in stages},
        'mean_ms': {k: round(_mean(k), 1) for k in stages},
        'p95_ms': {k: round(_p95(k), 1) for k in stages},
        'share_of_total_pct': {
            k: round(100.0 * _sum(k) / total_wall, 1) if total_wall else 0.0
            for k in stages if k != 'total_image_ms'
        },
        'source_size_kb': {
            'mean': round(statistics.mean([r['source_size_bytes'] for r in ok_rows]) / 1024, 1) if ok_rows else 0,
            'max': round(max([r['source_size_bytes'] for r in ok_rows]) / 1024, 1) if ok_rows else 0,
        },
        'opt_size_kb': {
            'mean': round(statistics.mean([r['opt_size_bytes'] for r in ok_rows]) / 1024, 1) if ok_rows else 0,
            'max': round(max([r['opt_size_bytes'] for r in ok_rows]) / 1024, 1) if ok_rows else 0,
        },
        'notes': [
            'Benchmark "Image Prep" in process_export_job == sequential download_image(optimize=True) per unique URL.',
            'No HTTP session reuse; each image opens a new urllib connection.',
            'DB work for image prep is one queryset before the loop (not in image timer).',
            'Per-report job.save() progress updates are OUTSIDE download_image but INSIDE the export loop.',
        ],
    }

    # Also estimate DB save overhead separately (quick microbench on last job pattern)
    from distress.models import DistressPdfExportJob
    t0 = time.perf_counter()
    for i in range(min(5, len(reports))):
        # lightweight exists check pattern used by get_owned_reports at create time
        DistressReport.objects.filter(pk=reports[i].id).exists()
    summary['db_exists_x5_ms'] = round(_ms(t0), 1)

    _log('')
    _log('=== STAGE TOTALS (ok images only) ===')
    wall_s = summary['sum_ms']['total_image_ms'] / 1000.0
    _log('ok=%s fail=%s wall_sum_total=%.1fs' % (summary['n_ok'], summary['n_fail'], wall_s))
    for k in stages:
        if k == 'total_image_ms':
            continue
        sum_v = summary['sum_ms'][k]
        mean_v = summary['mean_ms'][k]
        p95_v = summary['p95_ms'][k]
        share_v = summary['share_of_total_pct'].get(k, 0)
        _log('%s sum=%8.0fms  mean=%7.0fms  p95=%7.0fms  share=%5.1f%%' % (
            k.ljust(24), sum_v, mean_v, p95_v, share_v))
    src = summary['source_size_kb']
    opt = summary['opt_size_kb']
    _log('source_kb mean/max=%s/%s' % (src['mean'], src['max']))
    _log('opt_kb    mean/max=%s/%s' % (opt['mean'], opt['max']))

    # Dominant stage
    shares = summary['share_of_total_pct']
    dominant = max(shares, key=shares.get) if shares else 'n/a'
    _log('')
    _log(f'DOMINANT STAGE: {dominant} ({shares.get(dominant, 0)}% of summed image time)')
    if shares.get('download_ms', 0) >= 50:
        _log('CONCLUSION: Image prep wall time is dominated by NETWORK download (urllib, sequential, no pooling).')
    elif shares.get('resize_ms', 0) + shares.get('decode_ms', 0) + shares.get('jpeg_encode_write_ms', 0) >= 50:
        _log('CONCLUSION: Image prep wall time is dominated by Pillow CPU (decode/resize/encode).')
    else:
        _log('CONCLUSION: Mixed bottleneck — see stage shares above.')

    out_csv = args.out_csv
    os.makedirs(os.path.dirname(out_csv) or '.', exist_ok=True)
    fieldnames = sorted({k for r in rows for k in r.keys()})
    with open(out_csv, 'w', newline='', encoding='utf-8') as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    with open(args.out_json, 'w', encoding='utf-8') as fh:
        json.dump(summary, fh, indent=2)
    _log(f'\nWrote {out_csv}')
    _log(f'Wrote {args.out_json}')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
