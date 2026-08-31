"""Equipment Management constants."""

ITEM_NAME_MAX_LENGTH = 255
MAKE_AND_MODEL_MAX_LENGTH = 255
EQUIPMENT_CODE_MAX_LENGTH = 100
STATUS_MAX_LENGTH = 50
REMARK_MAX_LENGTH = 2000

MIN_YEAR_OF_MAKE = 1900

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

SORTABLE_FIELDS = frozenset(
    {
        'id',
        'item_name',
        'year_of_make',
        'make_and_model',
        'equipment_code',
        'estimate_cost',
        'status',
        'created_at',
        'updated_at',
    }
)

ESTIMATE_COST_MAX_DIGITS = 18
ESTIMATE_COST_DECIMAL_PLACES = 2
