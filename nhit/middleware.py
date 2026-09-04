"""GZip middleware that skips binary downloads (PDF, zip, media)."""

from __future__ import annotations

from django.middleware.gzip import GZipMiddleware


class SelectiveGZipMiddleware(GZipMiddleware):
    """Same as Django GZipMiddleware, but never compress PDF/binary attachments.

    Compressing ``application/pdf`` FileResponses breaks many browser download
    flows (fetch → blob → save) that persist the gzip payload as a ``.pdf``.
    """

    _SKIP_EXACT = frozenset({
        'application/pdf',
        'application/zip',
        'application/octet-stream',
        'application/gzip',
        'application/x-gzip',
    })
    _SKIP_PREFIXES = (
        'image/',
        'video/',
        'audio/',
    )

    def process_response(self, request, response):
        content_type = (response.get('Content-Type') or '').split(';', 1)[0].strip().lower()
        if content_type in self._SKIP_EXACT:
            return response
        if content_type.startswith(self._SKIP_PREFIXES):
            return response
        disposition = (response.get('Content-Disposition') or '').lower()
        if 'attachment' in disposition and content_type.startswith('application/'):
            return response
        return super().process_response(request, response)
