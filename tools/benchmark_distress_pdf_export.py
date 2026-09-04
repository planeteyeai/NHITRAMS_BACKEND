"""Unbuffered benchmark for async distress PDF export (background path).

Usage:

    set PYTHONUNBUFFERED=1
    python -u tools/benchmark_distress_pdf_export.py --counts 1,5,10,32 --concurrency 1,2,4

S3 upload is mocked by default. Source image downloads are real.
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


def _rss_mb() -> float | None:
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except Exception:
        try:
            import resource
            # ru_maxrss is KB on Linux, bytes on macOS — report best-effort.
            val = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            if sys.platform == 'darwin':
                return val / (1024 * 1024)
            return val / 1024.0
        except Exception:
            return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--counts', default='1,5,10,32')
    parser.add_argument('--concurrency', default='4',
                        help='Comma-separated DISTRESS_PDF_IMAGE_PREP_CONCURRENCY values')
    parser.add_argument('--project-contains', default='')
    parser.add_argument('--date', default='')
    parser.add_argument('--username', default='')
    parser.add_argument('--real-s3', action='store_true')
    args = parser.parse_args()

    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nhit.settings')
    os.environ.setdefault('PYTHONUNBUFFERED', '1')
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

    import django
    django.setup()

    from django.conf import settings
    from django.test.utils import override_settings
    from django.utils import timezone

    from distress.export_jobs import create_export_job, process_export_job
    from distress.models import DistressPdfExportJob, DistressReport
    from pdf.utils import get_owned_reports

    counts = [int(x.strip()) for x in args.counts.split(',') if x.strip()]
    conc_list = [int(x.strip()) for x in args.concurrency.split(',') if x.strip()]

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
    table_rows = []

    for conc in conc_list:
        _log(f'\n######## concurrency={conc} ########')
        for n in counts:
            subset = reports[:n]
            if len(subset) < n:
                _log(f'SKIP count={n}: only {len(subset)} reports available')
                continue
            ids = [r.id for r in subset]
            _log(f'\n=== n={n} concurrency={conc} ===')
            rss0 = _rss_mb()

            t_own0 = time.perf_counter()
            get_owned_reports(DistressReport, username, ids)
            ownership_ms = (time.perf_counter() - t_own0) * 1000

            t_create0 = time.perf_counter()
            with override_settings(DISTRESS_PDF_IMAGE_PREP_CONCURRENCY=conc):
                job = create_export_job(username=username, ids=ids)
            create_ms = (time.perf_counter() - t_create0) * 1000
            _log(f'job_create_ms={create_ms:.0f} ownership_ms={ownership_ms:.0f} job_id={job.id}')

            job.status = DistressPdfExportJob.STATUS_PROCESSING
            job.started_at = timezone.now()
            job.save(update_fields=['status', 'started_at'])

            # Instrument download_image wall time via monkeypatch.
            from distress import export_jobs as ej
            orig = ej.download_image
            dl_ms = {'sum': 0.0, 'n': 0}

            def timed_download(url, temp_dir, optimize=True, http_client=None):
                t0 = time.perf_counter()
                path = orig(url, temp_dir, optimize=optimize, http_client=http_client)
                elapsed = (time.perf_counter() - t0) * 1000
                dl_ms['sum'] += elapsed
                dl_ms['n'] += 1
                _log(f'  [{dl_ms["n"]:02d}] prepare {elapsed:.0f}ms')
                return path

            ej.download_image = timed_download
            t_job0 = time.perf_counter()
            try:
                with override_settings(DISTRESS_PDF_IMAGE_PREP_CONCURRENCY=conc):
                    # Re-read settings helper picks up override via getattr(settings,...)
                    if args.real_s3:
                        process_export_job(job)
                    else:
                        with patch(
                            'distress.export_jobs.upload_local_file_to_s3',
                            return_value='Distress PDF Exports/benchmark.pdf',
                        ):
                            process_export_job(job)
            except Exception:
                _log(f'FAILED n={n} concurrency={conc}')
                traceback.print_exc()
                table_rows.append((conc, n, create_ms, None, None, 'exception', rss0, None))
                continue
            finally:
                ej.download_image = orig

            total_ms = (time.perf_counter() - t_job0) * 1000
            job.refresh_from_db()
            rss1 = _rss_mb()
            prep_ms = dl_ms['sum']
            # PDF approx = total - download wall (overlaps under concurrency, so clamp)
            pdf_approx = max(0.0, total_ms - (prep_ms / max(conc, 1)))
            _log(
                f'RESULT n={n} conc={conc} status={job.status} '
                f'ok={job.completed_reports} fail={job.failed_reports} '
                f'progress={job.progress} total_ms={total_ms:.0f} '
                f'image_prepare_sum_ms={prep_ms:.0f} rss0={rss0} rss1={rss1}'
            )
            table_rows.append((
                conc, n, create_ms, prep_ms, total_ms, job.status, rss0, rss1,
                job.completed_reports, job.failed_reports,
            ))

    _log('\n| Conc | Reports | Job Create | Image Prep Sum | Total | Status | RSS start | RSS end |')
    _log('|---:|---:|---:|---:|---:|---|---:|---:|')
    for row in table_rows:
        conc, n, create_ms, prep_ms, total_ms, status, rss0, rss1 = row[:8]
        _log(
            f'| {conc} | {n} | '
            f'{create_ms:.0f}ms | '
            f'{("-" if prep_ms is None else f"{prep_ms:.0f}ms")} | '
            f'{("-" if total_ms is None else f"{total_ms:.0f}ms")} | '
            f'{status} | {rss0} | {rss1} |'
        )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
