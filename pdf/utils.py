"""PDF helpers: image download, temp cleanup, query parsing, streaming responses."""

from __future__ import annotations

import logging
import os
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from django.http import FileResponse
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from . import styles as S

logger = logging.getLogger(__name__)

# Keep image waits short so batch PDF export (e.g. 32 reports) cannot hang for minutes.
IMAGE_TIMEOUT_SECONDS = 8
IMAGE_MAX_BYTES = 15 * 1024 * 1024
# Default prefetch concurrency for small sync exports. Async worker uses 1.
IMAGE_PREFETCH_WORKERS = 8
# Soft product limit — large enough for a full-day inspection, avoids runaway exports.
MAX_PDF_EXPORT_REPORTS = 100
ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}

# Match DistressReportBuilder / InventoryReportBuilder detail image box.
# content_width * 0.52 by 75mm — target ~175 DPI of that on-page box (~2× 72dpi).
PDF_IMAGE_DISPLAY_WIDTH_PT = (A4[0] - 2 * S.MARGIN) * 0.52
PDF_IMAGE_DISPLAY_HEIGHT_PT = 75 * mm
PDF_IMAGE_TARGET_DPI = 175
PDF_IMAGE_MAX_WIDTH_PX = max(1, int(PDF_IMAGE_DISPLAY_WIDTH_PT / 72 * PDF_IMAGE_TARGET_DPI))
PDF_IMAGE_MAX_HEIGHT_PX = max(1, int(PDF_IMAGE_DISPLAY_HEIGHT_PT / 72 * PDF_IMAGE_TARGET_DPI))
PDF_IMAGE_JPEG_QUALITY = 80


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
    - More than MAX_PDF_EXPORT_REPORTS → 400
    """
    base = model.objects.filter(username=username).order_by('-created_at', '-id')

    if report_ids is None:
        if not base.exists():
            raise NotFound('No matching reports.')
        count = base.count()
        if count > MAX_PDF_EXPORT_REPORTS:
            raise ValidationError({
                'detail': (
                    f'Too many reports to export at once ({count}). '
                    f'Select at most {MAX_PDF_EXPORT_REPORTS} reports, '
                    f'or filter by date/project before downloading.'
                ),
            })
        return base

    unique_ids = list(dict.fromkeys(report_ids))  # preserve order, dedupe
    if len(unique_ids) > MAX_PDF_EXPORT_REPORTS:
        raise ValidationError({
            'detail': (
                f'Too many reports to export at once ({len(unique_ids)}). '
                f'Select at most {MAX_PDF_EXPORT_REPORTS} reports.'
            ),
        })

    # Batched ownership: one query instead of 2×N EXISTS round-trips.
    found_by_id = {
        obj.pk: obj
        for obj in model.objects.filter(pk__in=unique_ids)
    }
    for report_id in unique_ids:
        obj = found_by_id.get(report_id)
        if obj is None:
            raise NotFound(f'Report {report_id} not found.')
        if getattr(obj, 'username', None) != username:
            raise PermissionDenied('You do not have permission to export this report.')

    ordered = [found_by_id[i] for i in unique_ids]
    if not ordered:
        raise NotFound('No matching reports.')
    return ordered


def optimize_image_for_pdf(
    source_path: str,
    temp_dir: str,
    *,
    max_width_px: int = PDF_IMAGE_MAX_WIDTH_PX,
    max_height_px: int = PDF_IMAGE_MAX_HEIGHT_PX,
    jpeg_quality: int = PDF_IMAGE_JPEG_QUALITY,
) -> str | None:
    """Resize/compress an image for ReportLab embed. Returns optimized path or None.

    Lifecycle: open → resize → encode JPEG → close Pillow objects.
    On failure returns None so callers can fall back to placeholder behavior.
    """
    if not source_path or not os.path.isfile(source_path):
        return None

    try:
        from PIL import Image as PILImage
        from PIL import ImageOps
    except ImportError:
        logger.warning('Pillow unavailable; using original image %s', source_path)
        return source_path

    try:
        with PILImage.open(source_path) as img:
            img = ImageOps.exif_transpose(img)
            # Flatten alpha onto white for JPEG.
            if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
                rgba = img.convert('RGBA')
                background = PILImage.new('RGB', rgba.size, (255, 255, 255))
                background.paste(rgba, mask=rgba.split()[-1])
                img = background
            elif img.mode != 'RGB':
                img = img.convert('RGB')

            img.thumbnail((max_width_px, max_height_px), PILImage.Resampling.LANCZOS)

            fd, out_path = tempfile.mkstemp(suffix='.jpg', prefix='nhit_pdfimg_', dir=temp_dir)
            os.close(fd)
            img.save(
                out_path,
                format='JPEG',
                quality=jpeg_quality,
                optimize=True,
                progressive=True,
            )
            # Drop large in-memory buffers immediately.
            img.close()
        return out_path
    except Exception as exc:
        logger.warning('Image optimize failed for %s: %s', source_path, exc)
        return None


def download_image(
    url: str,
    temp_dir: str,
    *,
    optimize: bool = True,
    http_client: Any | None = None,
) -> str | None:
    """Download an image URL into temp_dir. Returns local path or None.

    When optimize=True (default), the raw download is resized/compressed for PDF
    display dimensions and the raw file is removed.
    """
    if not url or not str(url).strip():
        return None

    url = str(url).strip()
    raw_path: str | None = None

    if not url.lower().startswith(('http://', 'https://')):
        # Local path fallback
        if os.path.isfile(url):
            raw_path = url
        else:
            return None
    else:
        try:
            if http_client is not None:
                data, content_type = http_client.get_bytes(url)
            else:
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

            fd, raw_path = tempfile.mkstemp(suffix=ext, prefix='nhit_img_', dir=temp_dir)
            with os.fdopen(fd, 'wb') as fh:
                fh.write(data)
            # Release download buffer.
            del data
        except Exception as exc:
            # Broad catch: S3/network hangs must never abort the whole PDF export.
            logger.warning('Failed to download image %s: %s', url, exc)
            return None

    if not optimize:
        return raw_path

    optimized = optimize_image_for_pdf(raw_path, temp_dir)
    # Remove raw download when we created it (not a caller-supplied local path).
    if raw_path and raw_path != url and os.path.isfile(raw_path):
        try:
            if optimized and optimized != raw_path:
                os.remove(raw_path)
        except OSError:
            pass
    return optimized or raw_path


def prefetch_images(
    urls: Sequence[str],
    temp_dir: str,
    *,
    max_workers: int = IMAGE_PREFETCH_WORKERS,
    optimize: bool = True,
    on_each: Callable[[str, str | None], None] | None = None,
    http_client: Any | None = None,
) -> dict[str, str | None]:
    """Download unique image URLs with bounded workers. Missing/failed URLs map to None."""
    unique: list[str] = []
    seen: set[str] = set()
    for raw in urls:
        key = (raw or '').strip()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(key)

    results: dict[str, str | None] = {key: None for key in unique}
    if not unique:
        return results

    workers = max(1, min(max_workers, len(unique)))
    client = http_client
    if client is None and workers > 1:
        try:
            from .http_client import ImageHttpClient
            client = ImageHttpClient(
                timeout_seconds=IMAGE_TIMEOUT_SECONDS,
                max_bytes=IMAGE_MAX_BYTES,
                maxsize=workers,
            )
        except Exception:
            client = None

    def _one(url: str) -> tuple[str, str | None]:
        path = download_image(url, temp_dir, optimize=optimize, http_client=client)
        return url, path

    # Bounded submission: keep at most `workers` futures in flight.
    from concurrent.futures import wait, FIRST_COMPLETED

    pending_urls = list(unique)
    in_flight: dict[Any, str] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while pending_urls or in_flight:
            while pending_urls and len(in_flight) < workers:
                next_url = pending_urls.pop(0)
                fut = pool.submit(_one, next_url)
                in_flight[fut] = next_url
            done, _ = wait(in_flight.keys(), return_when=FIRST_COMPLETED)
            for future in done:
                url = in_flight.pop(future)
                try:
                    _, path = future.result()
                    results[url] = path
                except Exception as exc:
                    logger.warning('Image prefetch failed for %s: %s', url, exc)
                    results[url] = None
                if on_each:
                    on_each(url, results[url])
    return results


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
