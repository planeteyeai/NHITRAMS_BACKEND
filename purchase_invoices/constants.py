"""Purchase Invoice Management constants."""

INVOICE_NUMBER_MAX_LENGTH = 100
BATCH_NUMBER_MAX_LENGTH = 100

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

SORTABLE_FIELDS = frozenset(
    {
        'id',
        'invoice_date',
        'invoice_number',
        'created_at',
        'updated_at',
    }
)

QUANTITY_MAX_DIGITS = 18
QUANTITY_DECIMAL_PLACES = 4
RATE_MAX_DIGITS = 18
RATE_DECIMAL_PLACES = 2
