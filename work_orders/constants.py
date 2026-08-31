"""Work Order Management constants."""

TYPE_OF_WORK_OTHER = 'Other'

WORK_ORDER_ID_MAX_LENGTH = 100
TYPE_OF_WORK_MAX_LENGTH = 100
OTHER_TYPE_MAX_LENGTH = 150

CHAINAGE_MAX_DIGITS = 12
CHAINAGE_DECIMAL_PLACES = 3

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

SORTABLE_FIELDS = frozenset(
    {
        'id',
        'work_order_id',
        'type_of_work',
        'chainage_km_from',
        'chainage_km_to',
        'created_at',
        'updated_at',
    }
)


def is_other_type_of_work(value: str | None) -> bool:
    """True when type_of_work represents the frontend 'Other' option."""
    return str(value or '').strip().casefold() == TYPE_OF_WORK_OTHER.casefold()
