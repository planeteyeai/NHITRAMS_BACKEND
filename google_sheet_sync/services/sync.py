"""Upsert Google Sheet rows into the ``google_sheet_distress`` staging table."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from django.db import transaction
from django.db.models import CharField, Func, Value
from django.db.models.functions import Lower, Trim

from distress.models import Project

from google_sheet_sync.models import GoogleSheetDistress

logger = logging.getLogger(__name__)

# Fields written onto GoogleSheetDistress from validated serializer data
# (excludes sync_id / project which are handled separately).
UPSERT_FIELDS = (
    'status',
    'latitude',
    'longitude',
    'chainage_start',
    'chainage_end',
    'distress_type',
    'direction',
    'total_distress',
    'oblique_crack',
    'pothole',
    'edge_break',
    'patching',
    'bleeding',
    'hotspots',
    'alligator_crack',
    'roughness',
    'repair',
    'block_crack',
    'rutting',
    'longitudinal_crack',
    'raveling',
    'date',
    'length',
    'area',
    'carriage_type',
    'single_discrete_crack',
    'multiple_cracks',
    'joint_crack',
    'joint_seal_defects',
    'punchout',
    'slippage',
    'heaves',
    'simple_crack',
    'transverse_crack',
    'width',
    'depth',
    'lane',
    'hairline_crack',
    'hungry_surface',
    'settlement',
    'shoving',
    'stripping',
    'faulting_stepping_in_cracks',
    'popout_small_hole',
    'single_longitudinal_crack',
    'blowup_or_bulking',
    'polished_surface_glazing',
    'depression',
    'image_url',
)


class RegexpReplace(Func):
    """PostgreSQL REGEXP_REPLACE(expression, pattern, replacement, flags)."""

    function = 'REGEXP_REPLACE'
    output_field = CharField()
    arity = 4


class ProjectNotFoundError(Exception):
    """Raised when no Project matches the normalized sheet project name."""


def normalize_project_name(name: str) -> str:
    """Trim, collapse whitespace, and lowercase a project / site name.

    Example::
        \" Agra   Bypass Road \" → \"agra bypass road\"
    """
    if name is None:
        return ''
    collapsed = re.sub(r'\s+', ' ', str(name).strip())
    return collapsed.lower()


def resolve_project(project_name: str) -> tuple[Project, str]:
    """Resolve ``distress.Project`` by normalized name (not exact project_name).

    Matching is done in PostgreSQL so spacing / case differences still hit
    the correct row::

        LOWER(REGEXP_REPLACE(TRIM(project_name), '\\s+', ' ', 'g'))

    Returns:
        (project, normalized_name)

    Raises:
        ProjectNotFoundError: when no project matches.
    """
    normalized = normalize_project_name(project_name)
    if not normalized:
        raise ProjectNotFoundError('Project not found')

    project = (
        Project.objects.annotate(
            _normalized=Lower(
                RegexpReplace(
                    Trim('project_name'),
                    Value(r'\s+'),
                    Value(' '),
                    Value('g'),
                )
            )
        )
        .filter(_normalized=normalized)
        .order_by('project_id')
        .first()
    )

    if project is None:
        logger.warning(
            'Google Sheet sync: project not found for normalized name %r '
            '(raw=%r)',
            normalized,
            project_name,
        )
        raise ProjectNotFoundError('Project not found')

    logger.info(
        'Google Sheet sync: resolved project_id=%s name=%r from normalized=%r',
        project.project_id,
        project.project_name,
        normalized,
    )
    return project, normalized


def _defaults_from_validated(
    validated: dict[str, Any],
    *,
    project: Project,
    project_name_raw: str,
    project_name_normalized: str,
) -> dict[str, Any]:
    defaults = {field: validated.get(field) for field in UPSERT_FIELDS}
    defaults['project'] = project
    defaults['project_name_raw'] = project_name_raw
    defaults['project_name_normalized'] = project_name_normalized
    return defaults


def _save_staging_row(
    validated: dict[str, Any],
) -> tuple[GoogleSheetDistress, str]:
    """Persist the staging row and commit (separate from production upsert)."""
    project_name_raw = validated['project_name']
    project, normalized = resolve_project(project_name_raw)
    defaults = _defaults_from_validated(
        validated,
        project=project,
        project_name_raw=project_name_raw,
        project_name_normalized=normalized,
    )
    logger.info(
        'Staging defaults keys=%s project_id=%s',
        sorted(defaults.keys()),
        project.project_id,
    )

    with transaction.atomic():
        raw_sync_id = validated.get('sync_id')
        logger.info('Incoming sync_id = %r', raw_sync_id)

        if raw_sync_id in (None, ''):
            sync_id = uuid.uuid4()
            logger.info('Creating NEW staging row with generated sync_id=%s', sync_id)
            instance = GoogleSheetDistress.objects.create(
                sync_id=sync_id, **defaults
            )
            created = True
            action = 'created'
        else:
            sync_id = uuid.UUID(str(raw_sync_id))
            logger.info('Updating existing staging row with sync_id=%s', sync_id)
            instance, created = GoogleSheetDistress.objects.update_or_create(
                sync_id=sync_id,
                defaults=defaults,
            )
            action = 'created' if created else 'updated'
            logger.info(
                'update_or_create created_flag=%s action=%s',
                created,
                action,
            )

        row_count = GoogleSheetDistress.objects.filter(sync_id=sync_id).count()
        logger.info(
            'Staging persist done: action=%s created=%s pk/sync_id=%s '
            'row_count_for_sync_id=%s (must be 1)',
            action,
            created,
            instance.sync_id,
            row_count,
        )
        if row_count != 1:
            logger.error(
                'UNEXPECTED: google_sheet_distress has %s rows for sync_id=%s',
                row_count,
                sync_id,
            )

    logger.info(
        'Google Sheet sync: staging %s sync_id=%s project_id=%s',
        action,
        instance.sync_id,
        project.project_id,
    )
    return instance, action


def sync_sheet_row(validated: dict[str, Any]) -> tuple[GoogleSheetDistress, str]:
    """Save staging row, then promote it into production ``distress``.

    Staging is committed first so it remains the source of truth even if
    production processing fails.

    Returns:
        (instance, action) where action is ``\"created\"`` or ``\"updated\"``.

    Raises:
        GoogleSheetProcessingError: production promote failed (staging retained).
        ProjectNotFoundError: project could not be resolved for staging.
    """
    from .google_sheet_processor import GoogleSheetProcessingError, GoogleSheetProcessor

    instance, action = _save_staging_row(validated)
    try:
        GoogleSheetProcessor.process(instance)
    except GoogleSheetProcessingError as exc:
        # Attach sync_id so the API can return it without rolling back staging.
        if not getattr(exc, 'sync_id', None):
            exc.sync_id = str(instance.sync_id)
        raise
    return instance, action
