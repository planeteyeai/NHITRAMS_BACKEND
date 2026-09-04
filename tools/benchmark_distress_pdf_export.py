"""Unbuffered benchmark for async distress PDF export (background path).

Usage (from repo root):

    set PYTHONUNBUFFERED=1
    python -u tools/benchmark_distress_pdf_export.py --counts 1,5,10,32

Does NOT hold the HTTP GET export open. Creates ExportJob rows and runs
process_export_job directly (same code path as the Railway worker).

S3 upload is mocked by default unless --real-s3 is passed.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from unittest.mock import patch


def _log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--counts', default='1,5,10,32')
    parser.add_argument('--project-contains', default='',
                        help='Optional Project_Name icontains filter (empty = no filter)')
    parser.add_argument('--date', default='')
    parser.add_argument('--username', default='')
    parser.add_argument('--real-s3', action='store_true')
    parser.add_argument('--compare-images', action='store_true',
                        help='Compare raw vs optimized image sizes for first N URLs')
    args = parser.parse_args()

    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')
    os.environ.setdefault('PYTHONUNBUFFERED', '1')

    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

    import django
    django.setup()

    from distress.export_jobs import create_export_job, process_export_job
    from distress.models import DistressReport
    from pdf.utils import TempWorkspace, download_image, optimize_image_for_pdf

    counts = [int(x.strip()) for x in args.counts.split(',') if x.strip()]
    qs = DistressReport.objects.all().order_by('id')
    if args.username:
        qs = qs.filter(username=args.username)
    if args.project_contains:
        qs = qs.filter(Project_Name__icontains=args.project_contains)
    if args.date:
        qs = qs.filter(Date=args.date)

    reports = list(qs[: max(counts)])
    if not reports:
        _log('ERROR: no reports matched filters')
        return 2

    username = reports[0].username
    _log(f'Using username={username} matched={len(reports)}')

    if args.compare_images:
        urls = [(r.Image_URL or '').strip() for r in reports[:5] if (r.Image_URL or '').strip()]
        _log(f'--- Image optimization sample ({len(urls)} urls) ---')
        with TempWorkspace() as ws:
            for i, url in enumerate(urls, 1):
                t0 = time.perf_counter()
                raw = download_image(url, ws.dir, optimize=False)
                dl = time.perf_counter() - t0
                if not raw:
                    _log(f'[{i}] download failed')
                    continue
                raw_size = os.path.getsize(raw)
                t1 = time.perf_counter()
                opt = optimize_image_for_pdf(raw, ws.dir)
                rz = time.perf_counter() - t1
                opt_size = os.path.getsize(opt) if opt else 0
                _log(
                    f'[{i}] raw_kb={raw_size/1024:.1f} opt_kb={opt_size/1024:.1f} '
                    f'download_s={dl:.2f} resize_s={rz:.2f}'
                )

    rows = []
    for n in counts:
        subset = reports[:n]
        if len(subset) < n:
            _log(f'SKIP count={n}: only {len(subset)} reports available')
            continue

        ids = [r.id for r in subset]
        _log(f'\n=== Benchmark {n} reports ===')
        t_create0 = time.perf_counter()
        job = create_export_job(username=username, ids=ids)
        create_s = time.perf_counter() - t_create0
        _log(f'job_create_s={create_s:.3f} job_id={job.id}')

        # Mark processing like the worker claim step.
        from django.utils import timezone
        from distress.models import DistressPdfExportJob
        job.status = DistressPdfExportJob.STATUS_PROCESSING
        job.started_at = timezone.now()
        job.save(update_fields=['status', 'started_at'])

        def _run():
            t0 = time.perf_counter()
            # Progress printer while processing via monkeypatch of save counts is heavy;
            # instead wrap download_image for per-report timing.
            from distress import export_jobs as ej
            orig = ej.download_image
            times = {'download': 0.0, 'n': 0}

            def timed_download(url, temp_dir, optimize=True):
                idx = times['n'] + 1
                times['n'] = idx
                _log(f'[{idx:02d}/{n}] image prepare started')
                t = time.perf_counter()
                path = orig(url, temp_dir, optimize=optimize)
                elapsed = time.perf_counter() - t
                times['download'] += elapsed
                _log(f'[{idx:02d}/{n}] image prepare completed {elapsed:.2f}s')
                return path

            ej.download_image = timed_download
            try:
                process_export_job(job)
            finally:
                ej.download_image = orig
            total = time.perf_counter() - t0
            job.refresh_from_db()
            return total, times['download']

        try:
            if args.real_s3:
                total_s, s3_dl_s = _run()
            else:
                with patch(
                    'distress.export_jobs.upload_local_file_to_s3',
                    return_value='Distress PDF Exports/benchmark.pdf',
                ):
                    total_s, s3_dl_s = _run()
            job.refresh_from_db()
            _log(
                f'RESULT n={n} status={job.status} progress={job.progress} '
                f'completed={job.completed_reports} failed={job.failed_reports} '
                f'total_s={total_s:.1f} image_prep_s={s3_dl_s:.1f} '
                f's3_key={job.output_s3_key or "-"}'
            )
            rows.append((n, create_s, s3_dl_s, total_s, job.status))
        except Exception:
            _log(f'FAILED n={n}')
            traceback.print_exc()
            rows.append((n, create_s, None, None, 'exception'))

    _log('\n| Reports | Job Create | Image Prep | Total | Result |')
    _log('|---:|---:|---:|---:|---|')
    for n, create_s, img_s, total_s, result in rows:
        _log(
            f'| {n} | {create_s:.3f}s | '
            f'{("-" if img_s is None else f"{img_s:.1f}s")} | '
            f'{("-" if total_s is None else f"{total_s:.1f}s")} | {result} |'
        )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
