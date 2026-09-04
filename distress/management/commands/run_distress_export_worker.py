"""Poll and process queued DistressPdfExportJob rows (concurrency=1).

Railway worker start command:

    python manage.py run_distress_export_worker

Redis is NOT required — jobs are claimed from PostgreSQL.
"""

from __future__ import annotations

import logging
import signal
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from distress.export_jobs import claim_next_job, process_export_job, _image_prep_concurrency

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Process queued AIS distress PDF export jobs (one at a time).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--once',
            action='store_true',
            help='Process at most one job then exit.',
        )
        parser.add_argument(
            '--poll-interval',
            type=float,
            default=2.0,
            help='Seconds to sleep when the queue is empty (default: 2).',
        )
        parser.add_argument(
            '--max-jobs',
            type=int,
            default=0,
            help='Stop after N jobs (0 = unlimited).',
        )

    def handle(self, *args, **options):
        stop = {'flag': False}

        def _request_stop(signum, _frame):
            logger.info('Received signal %s — stopping after current job', signum)
            stop['flag'] = True

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _request_stop)
            except Exception:
                pass

        poll_interval = max(0.5, float(options['poll_interval']))
        once = bool(options['once'])
        max_jobs = int(options['max_jobs'] or 0)
        processed = 0

        self.stdout.write(self.style.SUCCESS(
            f'Distress PDF export worker started '
            f'(job concurrency=1, image_prep_concurrency={_image_prep_concurrency()})'
        ))

        while not stop['flag']:
            close_old_connections()
            job = claim_next_job()
            if job is None:
                if once:
                    self.stdout.write('No queued jobs.')
                    return
                time.sleep(poll_interval)
                continue

            self.stdout.write(f'Processing job {job.id} ({job.total_reports} reports)...')
            process_export_job(job)
            job.refresh_from_db()
            self.stdout.write(self.style.SUCCESS(
                f'Job {job.id} → {job.status} '
                f'({job.completed_reports}/{job.total_reports}, '
                f'failed={job.failed_reports}, progress={job.progress}%)'
            ))
            processed += 1
            if once or (max_jobs and processed >= max_jobs):
                return
