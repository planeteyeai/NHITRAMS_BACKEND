"""Transfer Stock Management constants."""

BATCH_NUMBER_MAX_LENGTH = 100
LOCATION_MAX_LENGTH = 255
REMARK_MAX_LENGTH = 2000

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

SORTABLE_FIELDS = frozenset(
    {
        'id',
        'date',
        'location',
        'supplier_id',
        'created_at',
        'updated_at',
    }
)

QUANTITY_MAX_DIGITS = 18
QUANTITY_DECIMAL_PLACES = 4
RATE_MAX_DIGITS = 18
RATE_DECIMAL_PLACES = 4
