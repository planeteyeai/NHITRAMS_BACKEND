"""Allowed values for Labour Management dropdowns.

No labour-type / gender master tables exist in this project yet, so
allowed values live here and are exposed via master endpoints for the FE.
"""

LABOUR_TYPE_SKILLED = 'Skilled'
LABOUR_TYPE_SEMI_SKILLED = 'Semi-Skilled'
LABOUR_TYPE_UNSKILLED = 'Unskilled'

LABOUR_TYPES = (
    LABOUR_TYPE_SKILLED,
    LABOUR_TYPE_SEMI_SKILLED,
    LABOUR_TYPE_UNSKILLED,
)

GENDER_MALE = 'Male'
GENDER_FEMALE = 'Female'
GENDER_OTHER = 'Other'

GENDERS = (
    GENDER_MALE,
    GENDER_FEMALE,
    GENDER_OTHER,
)

# Indian mobile: 10 digits starting with 6–9.
CONTACT_NUMBER_PATTERN = r'^[6-9]\d{9}$'

FIRST_NAME_MAX_LENGTH = 100
LAST_NAME_MAX_LENGTH = 100
CREW_ID_MAX_LENGTH = 50
CONTACT_NUMBER_MAX_LENGTH = 15
ADDRESS_MAX_LENGTH = 500
LABOUR_TYPE_MAX_LENGTH = 50
GENDER_MAX_LENGTH = 20

# Soft business bounds for DOB (not future; not older than this many years).
MAX_AGE_YEARS = 100
MIN_AGE_YEARS = 14

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

SORTABLE_FIELDS = frozenset(
    {
        'id',
        'first_name',
        'last_name',
        'date_of_birth',
        'labour_type',
        'crew_id',
        'gender',
        'contact_number',
        'created_at',
        'updated_at',
    }
)
