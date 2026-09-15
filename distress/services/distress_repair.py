"""Distress repair log services (resolve AI predictions without mutating them)."""

from __future__ import annotations

import math

from django.db import transaction
from django.db.models import FloatField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, ValidationError

from distress.models import DistressRepairLog, PredictedDistress
from nhit.caching.invalidation import invalidate_distress_predict_filter_cache

RESOLVED_STATUSES = (
    DistressRepairLog.STATUS_REPAIRED,
    DistressRepairLog.STATUS_NOT_PRESENT,
)


class ConflictError(APIException):
    status_code = 409
    default_detail = 'Conflict.'
    default_code = 'conflict'


def _finite_predicted_quantity(count_val) -> float:
    """Return finite predicted quantity or raise 400 for NaN / non-finite values."""
    try:
        value = float(count_val)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            {'distress_id': ['Predicted quantity (count_val) is invalid.']}
        ) from exc
    if not math.isfinite(value):
        raise ValidationError(
            {
                'distress_id': [
                    'Predicted quantity (count_val) is not a finite number; '
                    'repair is not allowed until the prediction data is fixed.'
                ]
            }
        )
    if value < 0:
        raise ValidationError(
            {'distress_id': ['Predicted quantity (count_val) cannot be negative.']}
        )
    return value


def _resolved_quantity(distress_id: int) -> float:
    total = DistressRepairLog.objects.filter(
        distress_id=distress_id,
        status__in=RESOLVED_STATUSES,
    ).aggregate(total=Sum('quantity_resolved'))['total']
    try:
        value = float(total or 0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(value):
        return 0.0
    return value


def resolved_quantities_by_distress_ids(distress_ids) -> dict[int, float]:
    """Batch Sum(quantity_resolved) for many prediction rows."""
    ids = [int(i) for i in distress_ids if i is not None]
    if not ids:
        return {}
    rows = (
        DistressRepairLog.objects.filter(
            distress_id__in=ids,
            status__in=RESOLVED_STATUSES,
        )
        .values('distress_id')
        .annotate(total=Sum('quantity_resolved'))
    )
    result: dict[int, float] = {}
    for row in rows:
        try:
            value = float(row['total'] or 0)
        except (TypeError, ValueError):
            value = 0.0
        if not math.isfinite(value):
            value = 0.0
        result[int(row['distress_id'])] = value
    return result


def annotate_resolved_quantity(queryset):
    """Annotate each distress row with Sum(quantity_resolved) from repair logs."""
    repair_total_subquery = (
        DistressRepairLog.objects.filter(
            distress_id=OuterRef('distress_id'),
            status__in=RESOLVED_STATUSES,
        )
        .values('distress_id')
        .annotate(total=Sum('quantity_resolved'))
        .values('total')[:1]
    )
    return queryset.annotate(
        resolved_quantity_db=Coalesce(
            Subquery(repair_total_subquery, output_field=FloatField()),
            Value(0.0),
            output_field=FloatField(),
        )
    )


def quantity_breakdown(count_val, resolved: float = 0.0) -> dict[str, float]:
    """predicted / resolved / remaining for display (never negative remaining)."""
    try:
        predicted = float(count_val)
    except (TypeError, ValueError):
        predicted = 0.0
    if not math.isfinite(predicted) or predicted < 0:
        predicted = 0.0
    try:
        resolved_qty = float(resolved or 0)
    except (TypeError, ValueError):
        resolved_qty = 0.0
    if not math.isfinite(resolved_qty) or resolved_qty < 0:
        resolved_qty = 0.0
    remaining = max(predicted - resolved_qty, 0.0)
    return {
        'predicted_quantity': predicted,
        'resolved_quantity': resolved_qty,
        'remaining_quantity': remaining,
    }


@transaction.atomic
def record_distress_repair(
    *,
    distress_id: int,
    status: str,
    updated_by: str,
    quantity_resolved: float = 1.0,
    remarks: str = '',
) -> dict:
    """Append a repair log for a prediction row and return quantity totals.

    Strategy A (default): clients typically resolve the full remaining quantity
    in one action (or omit quantity_resolved when predicted is 1 → default 1).
    Schema allows multiple append-only logs for future partial repairs.
    """
    status_value = str(status or '').strip().upper()
    allowed = {DistressRepairLog.STATUS_REPAIRED, DistressRepairLog.STATUS_NOT_PRESENT}
    if status_value not in allowed:
        raise ValidationError(
            {'status': [f'Must be one of: {", ".join(sorted(allowed))}.']}
        )

    try:
        quantity = float(quantity_resolved)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            {'quantity_resolved': ['Must be a number >= 0.']}
        ) from exc
    if not math.isfinite(quantity) or quantity < 0:
        raise ValidationError({'quantity_resolved': ['Must be a number >= 0.']})

    try:
        prediction = PredictedDistress.objects.get(predicted_distress_id=distress_id)
    except PredictedDistress.DoesNotExist as exc:
        raise NotFound('No predicted distress found for this distress_id.') from exc

    predicted_quantity = _finite_predicted_quantity(prediction.total_distress)
    resolved_so_far = _resolved_quantity(prediction.predicted_distress_id)
    remaining_before = max(predicted_quantity - resolved_so_far, 0.0)

    if remaining_before <= 0:
        raise ConflictError(
            'All predicted distresses for this location have already been resolved.'
        )

    if quantity > remaining_before:
        raise ValidationError(
            {
                'quantity_resolved': [
                    f'Cannot resolve {quantity}; only {remaining_before} remaining '
                    f'for distress_id={prediction.predicted_distress_id}.'
                ]
            }
        )

    DistressRepairLog.objects.create(
        distress_id=prediction.predicted_distress_id,
        status=status_value,
        quantity_resolved=quantity,
        updated_by=updated_by,
        updated_at=timezone.now(),
        remarks=str(remarks or '').strip(),
    )

    invalidate_distress_predict_filter_cache()

    resolved_after = resolved_so_far + quantity
    remaining_after = max(predicted_quantity - resolved_after, 0.0)

    return {
        'distress_id': prediction.predicted_distress_id,
        'status': status_value,
        'predicted_quantity': predicted_quantity,
        'resolved_quantity': resolved_after,
        'remaining_quantity': remaining_after,
        'updated_by': updated_by,
        'updated_at': timezone.now().isoformat(),
    }
