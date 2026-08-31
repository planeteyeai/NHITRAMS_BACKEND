"""Material Management constants."""

# Frontend-owned dropdown values are stored as-is on Material records.
# Only this sentinel is used for conditional other_type validation.
MATERIAL_TYPE_OTHER = 'Other'

MATERIAL_NAME_MAX_LENGTH = 255
MATERIAL_UNIT_MAX_LENGTH = 50
MATERIAL_TYPE_MAX_LENGTH = 100
OTHER_TYPE_MAX_LENGTH = 150
MATERIAL_DESCRIPTION_MAX_LENGTH = 1000

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

SORTABLE_FIELDS = frozenset(
    {
        'id',
        'material_name',
        'material_unit',
        'material_type',
        'other_type',
        'created_at',
        'updated_at',
    }
)


def is_other_material_type(value: str | None) -> bool:
    """True when material_type represents the frontend 'Other' option."""
    return str(value or '').strip().casefold() == MATERIAL_TYPE_OTHER.casefold()
