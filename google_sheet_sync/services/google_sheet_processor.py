"""Promote ``google_sheet_distress`` staging rows into production ``distress``."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any, Optional

from django.db import transaction
from django.db.models.functions import Lower
from django.utils import timezone

from distress.models import Distress, DistressType, Project, RoadSegment
from google_sheet_sync.models import GoogleSheetDistress

logger = logging.getLogger(__name__)

DISTRESS_SOURCE = 'Google Sheet'
SOURCE_SHEET = 'Google Sheet Sync'


class GoogleSheetProcessingError(Exception):
    """Base error while promoting a staging row to production distress."""

    def __init__(self, message: str, sync_id: str | None = None) -> None:
        super().__init__(message)
        self.sync_id = sync_id


class StagingValidationError(GoogleSheetProcessingError):
    """Staging record is missing mandatory fields."""


class DistressTypeNotFoundError(GoogleSheetProcessingError):
    """No matching row in ``distress_types``."""


class RoadSegmentNotFoundError(GoogleSheetProcessingError):
    """No matching row in ``road_segments``."""


class GoogleSheetProcessor:
    """Transform a staging ``GoogleSheetDistress`` row into production ``distress``."""

    @staticmethod
    def process(sync_record: GoogleSheetDistress) -> Distress:
        """Validate, resolve FKs, map fields, and upsert production distress.

        The staging row is never deleted or rolled back by this method.
        """
        logger.info(
            'GoogleSheetProcessor: start sync_id=%s project=%r type=%r',
            sync_record.sync_id,
            sync_record.project_name_raw,
            sync_record.distress_type,
        )
        try:
            GoogleSheetProcessor._validate_staging(sync_record)
            project = GoogleSheetProcessor._resolve_project(sync_record)
            distress_type = GoogleSheetProcessor._resolve_distress_type(
                sync_record.distress_type
            )
            segment = GoogleSheetProcessor._resolve_road_segment(
                project=project,
                sync_record=sync_record,
            )
            defaults = GoogleSheetProcessor._build_distress_defaults(
                sync_record=sync_record,
                project=project,
                segment=segment,
                distress_type=distress_type,
            )

            with transaction.atomic():
                logger.info(
                    'GoogleSheetProcessor: update_or_create lookup sync_id=%s '
                    'project_id=%s segment_id=%s distress_type_id=%s',
                    sync_record.sync_id,
                    project.project_id,
                    segment.segment_id,
                    distress_type.distress_type_id,
                )
                distress, created = Distress.objects.update_or_create(
                    sync_id=sync_record.sync_id,
                    defaults=defaults,
                )

            logger.info(
                'GoogleSheetProcessor: %s distress_id=%s sync_id=%s created=%s',
                'created' if created else 'updated',
                distress.distress_id,
                distress.sync_id,
                created,
            )
            return distress

        except GoogleSheetProcessingError:
            logger.exception(
                'GoogleSheetProcessor: processing failed for sync_id=%s '
                '(staging row retained)',
                sync_record.sync_id,
            )
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                'GoogleSheetProcessor: unexpected error for sync_id=%s '
                '(staging row retained): %s',
                sync_record.sync_id,
                exc,
            )
            raise GoogleSheetProcessingError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Step 1 — validate staging
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_staging(sync_record: GoogleSheetDistress) -> None:
        missing: list[str] = []

        project_name = (
            sync_record.project_name_raw
            or sync_record.project_name_normalized
            or ''
        ).strip()
        if not project_name:
            missing.append('project_name')

        if not (sync_record.distress_type or '').strip():
            missing.append('distress_type')

        if sync_record.latitude is None:
            missing.append('latitude')
        if sync_record.longitude is None:
            missing.append('longitude')

        if missing:
            raise StagingValidationError(
                'Missing required staging field(s): ' + ', '.join(missing)
            )

    # ------------------------------------------------------------------
    # Step 2 — project
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_project(sync_record: GoogleSheetDistress) -> Project:
        """Prefer the FK already set on staging; fall back to case-insensitive name."""
        if sync_record.project_id:
            project = Project.objects.filter(
                project_id=sync_record.project_id
            ).first()
            if project is not None:
                return project

        name = (sync_record.project_name_raw or '').strip()
        project = (
            Project.objects.annotate(name_lower=Lower('project_name'))
            .filter(name_lower=name.lower())
            .order_by('project_id')
            .first()
        )
        if project is None:
            # Spacing-tolerant match
            from .sync import ProjectNotFoundError, resolve_project

            try:
                project, _ = resolve_project(name)
            except ProjectNotFoundError as exc:
                raise GoogleSheetProcessingError('Project not found') from exc
        return project

    # ------------------------------------------------------------------
    # Step 3 — distress type
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_type_key(value: str) -> str:
        """Normalize a distress type label for comparison.

        ``Alligator Crack`` / ``alligator_crack`` / ``ALLIGATOR-CRACK`` →
        ``alligator_crack``.
        """
        text = (value or '').strip().lower()
        text = text.replace('-', '_').replace('/', '_')
        text = re.sub(r'\s+', '_', text)
        text = re.sub(r'[^a-z0-9_]+', '', text)
        text = re.sub(r'_+', '_', text).strip('_')
        return text

    @staticmethod
    def _resolve_distress_type(raw_name: str) -> DistressType:
        raw = (raw_name or '').strip()
        if not raw:
            raise DistressTypeNotFoundError('Distress type not found')

        # Exact case-insensitive match on stored name.
        match = (
            DistressType.objects.annotate(name_lower=Lower('distress_type_name'))
            .filter(name_lower=raw.lower())
            .order_by('distress_type_id')
            .first()
        )
        if match is not None:
            return match

        # Normalized key match (spaces / punctuation → underscores).
        wanted = GoogleSheetProcessor._normalize_type_key(raw)
        for dtype in DistressType.objects.all().order_by('distress_type_id'):
            if GoogleSheetProcessor._normalize_type_key(dtype.distress_type_name) == wanted:
                return dtype

        logger.warning('GoogleSheetProcessor: distress type not found for %r', raw)
        raise DistressTypeNotFoundError(
            f'Distress type not found: {raw!r}'
        )

    # ------------------------------------------------------------------
    # Step 4 — road segment
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_road_segment(
        *,
        project: Project,
        sync_record: GoogleSheetDistress,
    ) -> RoadSegment:
        qs = RoadSegment.objects.filter(project_id=project.project_id)

        direction = (sync_record.direction or '').strip()
        if direction:
            qs = qs.filter(direction__iexact=direction)

        lane = (sync_record.lane or '').strip()
        if lane:
            qs = qs.filter(lane__iexact=lane)

        carriage = (sync_record.carriage_type or '').strip()
        if carriage:
            qs = qs.filter(carriage_type__iexact=carriage)

        start = sync_record.chainage_start
        end = sync_record.chainage_end

        segment: Optional[RoadSegment] = None

        if start is not None and end is not None:
            # Prefer exact start/end match.
            segment = (
                qs.filter(chainage_start=start, chainage_end=end)
                .order_by('segment_id')
                .first()
            )
            if segment is None:
                # Segment that contains the midpoint of the reported range.
                mid = (float(start) + float(end)) / 2.0
                segment = (
                    qs.filter(chainage_start__lte=mid, chainage_end__gte=mid)
                    .order_by('chainage_start', 'segment_id')
                    .first()
                )
        elif start is not None:
            segment = (
                qs.filter(chainage_start__lte=start, chainage_end__gte=start)
                .order_by('chainage_start', 'segment_id')
                .first()
            )
        elif end is not None:
            segment = (
                qs.filter(chainage_start__lte=end, chainage_end__gte=end)
                .order_by('chainage_start', 'segment_id')
                .first()
            )

        if segment is None:
            raise RoadSegmentNotFoundError(
                'Road segment not found for project_id=%s chainage_start=%s '
                'chainage_end=%s direction=%r'
                % (
                    project.project_id,
                    start,
                    end,
                    direction or None,
                )
            )

        logger.info(
            'GoogleSheetProcessor: resolved segment_id=%s for project_id=%s',
            segment.segment_id,
            project.project_id,
        )
        return segment

    # ------------------------------------------------------------------
    # Step 5 — field mapping
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_date(raw: Any) -> date:
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return timezone.localdate()

        if isinstance(raw, datetime):
            return raw.date()
        if isinstance(raw, date):
            return raw

        text = str(raw).strip()
        for fmt in (
            '%Y-%m-%d',
            '%d-%m-%Y',
            '%d/%m/%Y',
            '%Y/%m/%d',
            '%d-%m-%y',
            '%m/%d/%Y',
        ):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue

        # ISO datetime prefix
        try:
            return datetime.fromisoformat(text.replace('Z', '+00:00')).date()
        except ValueError as exc:
            raise StagingValidationError(
                f'Invalid date value: {raw!r}'
            ) from exc

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        if value is None or value == '':
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_count(value: Any) -> float:
        """Map total_distress → count_val (DB column is real)."""
        if value is None or value == '':
            return 1.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 1.0

    @staticmethod
    def _build_distress_defaults(
        *,
        sync_record: GoogleSheetDistress,
        project: Project,
        segment: RoadSegment,
        distress_type: DistressType,
    ) -> dict[str, Any]:
        return {
            'project': project,
            'segment': segment,
            'distress_type': distress_type,
            'distress_date': GoogleSheetProcessor._parse_date(sync_record.date),
            'latitude': float(sync_record.latitude),
            'longitude': float(sync_record.longitude),
            'count_val': GoogleSheetProcessor._to_count(sync_record.total_distress),
            'length_val': GoogleSheetProcessor._to_float(sync_record.length),
            'area_val': GoogleSheetProcessor._to_float(sync_record.area),
            'width_val': GoogleSheetProcessor._to_float(sync_record.width),
            'depth_val': GoogleSheetProcessor._to_float(sync_record.depth),
            'distress_source': DISTRESS_SOURCE,
            'source_sheet': SOURCE_SHEET,
            'source_file': (
                f'google_sheet_distress:{sync_record.sync_id}'
            ),
        }
