"""S3 helpers for PDF export artifacts (upload + fresh presigned GET)."""

from __future__ import annotations

import logging
import mimetypes
import os
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_PDF_EXPORT_FOLDER = 'Distress PDF Exports'
DEFAULT_PRESIGN_EXPIRES_SECONDS = 3600


def _s3_client():
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError(
            'boto3 is required for S3 uploads. Install dependencies from requirements.txt.'
        ) from exc

    bucket = getattr(settings, 'AWS_STORAGE_BUCKET_NAME', '') or ''
    region = getattr(settings, 'AWS_S3_REGION_NAME', 'ap-south-1')
    access_key = getattr(settings, 'AWS_ACCESS_KEY_ID', '') or ''
    secret_key = getattr(settings, 'AWS_SECRET_ACCESS_KEY', '') or ''

    if not bucket:
        raise RuntimeError('AWS_STORAGE_BUCKET_NAME is not configured.')
    if not access_key or not secret_key:
        raise RuntimeError('AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY are not configured.')

    client = boto3.client(
        's3',
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )
    return client, bucket, region


def _build_object_key(filename: str, folder: str) -> str:
    safe_name = (filename or 'export.pdf').replace('\\', '/').split('/')[-1]
    safe_name = safe_name.replace(' ', '_')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    unique = uuid.uuid4().hex[:12]
    folder = (folder or '').strip('/')
    return f'{folder}/{stamp}_{unique}_{safe_name}'


def upload_local_file_to_s3(
    local_path: str,
    *,
    folder: str | None = None,
    filename: str | None = None,
    content_type: str = 'application/pdf',
) -> str:
    """Upload a local file and return the object key (not a URL)."""
    from botocore.exceptions import BotoCoreError, ClientError

    if not local_path or not os.path.isfile(local_path):
        raise RuntimeError('Local file for S3 upload is missing.')

    client, bucket, _region = _s3_client()
    folder = folder if folder is not None else getattr(
        settings,
        'AWS_S3_DISTRESS_PDF_FOLDER',
        DEFAULT_PDF_EXPORT_FOLDER,
    )
    key = _build_object_key(filename or os.path.basename(local_path), folder)
    guessed = mimetypes.guess_type(key)[0]
    extra_args = {'ContentType': content_type or guessed or 'application/octet-stream'}
    acl = getattr(settings, 'AWS_DEFAULT_ACL', None)
    if acl:
        extra_args['ACL'] = acl

    try:
        with open(local_path, 'rb') as fh:
            client.upload_fileobj(fh, bucket, key, ExtraArgs=extra_args)
    except (BotoCoreError, ClientError) as exc:
        logger.exception('S3 upload failed for key=%s', key)
        raise RuntimeError(f'S3 upload failed: {exc}') from exc

    return key


def generate_presigned_get_url(
    key: str,
    *,
    expires_in: int | None = None,
    download_filename: str | None = None,
) -> str:
    """Return a fresh presigned GET URL for an existing object key."""
    if not key or not str(key).strip():
        raise RuntimeError('S3 object key is required for presigning.')

    client, bucket, _region = _s3_client()
    expires = expires_in
    if expires is None:
        expires = int(
            getattr(
                settings,
                'AWS_S3_PDF_PRESIGN_EXPIRES_SECONDS',
                DEFAULT_PRESIGN_EXPIRES_SECONDS,
            )
        )
    expires = max(60, min(int(expires), 7 * 24 * 3600))

    params: dict = {'Bucket': bucket, 'Key': str(key).strip()}
    if download_filename:
        safe = quote(download_filename.replace('"', ''))
        params['ResponseContentDisposition'] = f'attachment; filename="{safe}"'
        params['ResponseContentType'] = 'application/pdf'

    return client.generate_presigned_url(
        'get_object',
        Params=params,
        ExpiresIn=expires,
    )
