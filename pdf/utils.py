"""PDF helpers: image download, temp cleanup, query parsing, streaming responses."""

from __future__ import annotations

import logging
import os
import tempfile
import urllib.error
import urllib.request
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from django.http import FileResponse, HttpResponse
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

logger = logging.getLogger(__name__)

IMAGE_TIMEOUT_SECONDS = 15
IMAGE_MAX_BYTES = 15 * 1024 * 1024
ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}


def parse_export_ids(query_params) -> list[int] | None:
    """Parse ?id=15 or ?ids=5,7,10 into a list of ints, or None for export-all."""
    id_param = query_params.get('id')
    ids_param = query_params.get('ids')

    if id_param is not None and ids_param is not None:
        raise ValidationError({'detail': 'Provide either id or ids, not both.'})

    if id_param is not None:
        try:
            return [int(str(id_param).strip())]
        except (TypeError, ValueError) as exc:
            raise ValidationError({'id': 'Must be an integer.'}) from exc

    if ids_param is not None:
        parts = [p.strip() for p in str(ids_param).split(',') if p.strip()]
        if not parts:
            raise ValidationError({'ids': 'Must contain at least one integer id.'})
        try:
            return [int(p) for p in parts]
        except ValueError as exc:
            raise ValidationError({'ids': 'All ids must be integers.'}) from exc

    return None


def get_owned_reports(model, username: str, report_ids: list[int] | None):
    """Return queryset of reports owned by username.

    - No ids → all owned reports
    - With ids → only those owned reports
    - Existing report owned by someone else → 403
    - Missing report id → 404
    - Empty result set → 404
    """
    base = model.objects.filter(username=username).order_by('-created_at', '-id')

    if report_ids is None:
        if not base.exists():
            raise NotFound('No matching reports.')
        return base

    unique_ids = list(dict.fromkeys(report_ids))  # preserve order, dedupe
    for report_id in unique_ids:
        exists = model.objects.filter(pk=report_id).exists()
        if not exists:
            raise NotFound(f'Report {report_id} not found.')
        owned = model.objects.filter(pk=report_id, username=username).exists()
        if not owned:
            raise PermissionDenied('You do not have permission to export this report.')

    qs = base.filter(pk__in=unique_ids)
    # Preserve requested order
    by_id = {obj.pk: obj for obj in qs}
    ordered = [by_id[i] for i in unique_ids if i in by_id]
    if not ordered:
        raise NotFound('No matching reports.')
    return ordered


def download_image(url: str, temp_dir: str) -> str | None:
    """Download an image URL into temp_dir. Returns local path or None."""
    if not url or not str(url).strip():
        return None

    url = str(url).strip()
    if not url.lower().startswith(('http://', 'https://')):
        # Local path fallback
        if os.path.isfile(url):
            return url
        return None

    try:
        request = urllib.request.Request(
            url,
            headers={'User-Agent': 'NHIT-AI-Identifier-PDF/1.0'},
        )
        with urllib.request.urlopen(request, timeout=IMAGE_TIMEOUT_SECONDS) as response:
            content_type = (response.headers.get('Content-Type') or '').lower()
            data = response.read(IMAGE_MAX_BYTES + 1)
            if len(data) > IMAGE_MAX_BYTES:
                logger.warning('Image too large, skipping: %s', url)
                return None

        ext = '.jpg'
        if 'png' in content_type:
            ext = '.png'
        elif 'gif' in content_type:
            ext = '.gif'
        elif 'webp' in content_type:
            ext = '.webp'
        else:
            path_ext = Path(url.split('?', 1)[0]).suffix.lower()
            if path_ext in ALLOWED_IMAGE_EXTENSIONS:
                ext = path_ext

        fd, path = tempfile.mkstemp(suffix=ext, prefix='nhit_img_', dir=temp_dir)
        with os.fdopen(fd, 'wb') as fh:
            fh.write(data)
        return path
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as exc:
        logger.warning('Failed to download image %s: %s', url, exc)
        return None


class TempWorkspace:
    """Context manager for temp image/PDF files with guaranteed cleanup."""

    def __init__(self):
        self.dir: str | None = None
        self.pdf_path: str | None = None

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix='nhit_pdf_')
        return self

    def __exit__(self, exc_type, exc, tb):
        self.cleanup()
        return False

    def make_pdf_path(self, prefix: str) -> str:
        assert self.dir is not None
        fd, path = tempfile.mkstemp(suffix='.pdf', prefix=prefix, dir=self.dir)
        os.close(fd)
        self.pdf_path = path
        return path

    def cleanup(self):
        if not self.dir:
            return
        try:
            for name in os.listdir(self.dir):
                try:
                    os.remove(os.path.join(self.dir, name))
                except OSError:
                    pass
            os.rmdir(self.dir)
        except OSError:
            logger.warning('Failed to fully clean temp dir %s', self.dir)
        finally:
            self.dir = None


def format_generated_date(dt: datetime | None = None) -> str:
    dt = dt or datetime.now()
    return dt.strftime('%Y-%m-%d')


def format_timestamp(dt: datetime | None = None) -> str:
    dt = dt or datetime.now()
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def filename_for(report_kind: str, generated_date: str | None = None) -> str:
    date_str = generated_date or format_generated_date()
    if report_kind == 'distress':
        return f'road_distress_report_{date_str}.pdf'
    return f'road_inventory_report_{date_str}.pdf'


def pdf_file_response(pdf_path: str, download_name: str, cleanup=None) -> FileResponse:
    """Stream a PDF file as an attachment; optional cleanup runs when the response closes."""

    class _CleanupFileResponse(FileResponse):
        def close(self):
            super().close()
            if cleanup:
                try:
                    cleanup()
                except Exception:
                    logger.warning('PDF temp cleanup failed', exc_info=True)

    fh = open(pdf_path, 'rb')
    response = _CleanupFileResponse(
        fh,
        content_type='application/pdf',
        as_attachment=True,
        filename=download_name,
    )
    response['Content-Disposition'] = f'attachment; filename="{download_name}"'
    return response


def count_by_field(records: Iterable[Any], field_name: str) -> list[tuple[str, int]]:
    """Return sorted (value, count) pairs for a breakdown table."""
    counts: dict[str, int] = {}
    for record in records:
        key = str(getattr(record, field_name, '') or 'Unknown').strip() or 'Unknown'
        counts[key] = counts.get(key, 0) + 1
    return sorted(counts.items(), key=lambda x: (-x[1], x[0]))
