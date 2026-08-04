"""Shared inventory utilities (S3 uploads, file validation)."""

from __future__ import annotations

import logging
import mimetypes
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

from django.conf import settings
from rest_framework.exceptions import ValidationError

logger = logging.getLogger(__name__)

ALLOWED_IMAGE_CONTENT_TYPES = frozenset({
    'image/jpeg',
    'image/jpg',
    'image/png',
    'image/webp',
    'image/gif',
    'image/heic',
    'image/heif',
})

ALLOWED_IMAGE_EXTENSIONS = frozenset({
    '.jpg',
    '.jpeg',
    '.png',
    '.webp',
    '.gif',
    '.heic',
    '.heif',
})


def _max_upload_bytes() -> int:
    return int(getattr(settings, 'AWS_S3_MAX_UPLOAD_BYTES', 10 * 1024 * 1024))


def validate_image_file(uploaded_file) -> None:
    """Validate an uploaded image before sending it to S3."""
    name = getattr(uploaded_file, 'name', '') or ''
    content_type = (getattr(uploaded_file, 'content_type', '') or '').lower()
    size = getattr(uploaded_file, 'size', None)

    ext = ''
    if '.' in name:
        ext = '.' + name.rsplit('.', 1)[-1].lower()

    if ext and ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValidationError(
            f'Unsupported file type "{ext}". Allowed: {", ".join(sorted(ALLOWED_IMAGE_EXTENSIONS))}.'
        )

    if content_type and content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
        # Some clients send application/octet-stream; fall back to extension.
        if not (ext and ext in ALLOWED_IMAGE_EXTENSIONS and content_type == 'application/octet-stream'):
            raise ValidationError(
                f'Unsupported content type "{content_type}". Only image files are allowed.'
            )

    if size is not None and size > _max_upload_bytes():
        max_mb = _max_upload_bytes() / (1024 * 1024)
        raise ValidationError(f'File "{name}" exceeds the maximum upload size of {max_mb:.0f} MB.')

    if size == 0:
        raise ValidationError(f'File "{name}" is empty.')


def _s3_public_url(bucket: str, region: str, key: str) -> str:
    """Build the public object URL matching the Damaged+Assets path style."""
    encoded_key = quote(key, safe='/')
    return f'https://{bucket}.s3.{region}.amazonaws.com/{encoded_key}'


def _build_object_key(filename: str, folder: str) -> str:
    safe_name = (filename or 'evidence.jpg').replace('\\', '/').split('/')[-1]
    safe_name = safe_name.replace(' ', '_')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    unique = uuid.uuid4().hex[:12]
    folder = (folder or '').strip('/')
    return f'{folder}/{stamp}_{unique}_{safe_name}'


def upload_file_to_s3(uploaded_file, *, folder: str | None = None) -> str:
    """Upload a single file to the configured S3 bucket and return its public URL."""
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError as exc:
        raise RuntimeError(
            'boto3 is required for S3 uploads. Install dependencies from requirements.txt.'
        ) from exc

    validate_image_file(uploaded_file)

    bucket = getattr(settings, 'AWS_STORAGE_BUCKET_NAME', '') or ''
    region = getattr(settings, 'AWS_S3_REGION_NAME', 'ap-south-1')
    access_key = getattr(settings, 'AWS_ACCESS_KEY_ID', '') or ''
    secret_key = getattr(settings, 'AWS_SECRET_ACCESS_KEY', '') or ''
    folder = folder if folder is not None else getattr(
        settings, 'AWS_S3_DPR_FOLDER', 'Damaged Assets'
    )

    if not bucket:
        raise RuntimeError('AWS_STORAGE_BUCKET_NAME is not configured.')
    if not access_key or not secret_key:
        raise RuntimeError('AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY are not configured.')

    key = _build_object_key(getattr(uploaded_file, 'name', 'evidence.jpg'), folder)
    content_type = (
        getattr(uploaded_file, 'content_type', None)
        or mimetypes.guess_type(key)[0]
        or 'application/octet-stream'
    )

    client = boto3.client(
        's3',
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )

    extra_args = {'ContentType': content_type}
    acl = getattr(settings, 'AWS_DEFAULT_ACL', None)
    if acl:
        extra_args['ACL'] = acl

    try:
        uploaded_file.seek(0)
        client.upload_fileobj(
            uploaded_file,
            bucket,
            key,
            ExtraArgs=extra_args,
        )
    except (BotoCoreError, ClientError) as exc:
        logger.exception('S3 upload failed for key=%s', key)
        raise RuntimeError(f'S3 upload failed: {exc}') from exc

    return _s3_public_url(bucket, region, key)


def upload_dpr_images(files) -> list[str]:
    """Validate and upload multiple DPR evidence images. Returns public S3 URLs."""
    urls: list[str] = []
    for uploaded_file in files:
        urls.append(upload_file_to_s3(uploaded_file))
    return urls
