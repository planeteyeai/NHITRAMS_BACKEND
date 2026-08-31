"""Supplier Management constants."""

# Indian mobile: 10 digits starting with 6–9 (same convention as Labour).
MOBILE_NUMBER_PATTERN = r'^[6-9]\d{9}$'

COMPANY_NAME_MAX_LENGTH = 255
CONTACT_PERSON_MAX_LENGTH = 150
MOBILE_NUMBER_MAX_LENGTH = 15
EMAIL_MAX_LENGTH = 254
STATE_MAX_LENGTH = 100
CITY_MAX_LENGTH = 100
ADDRESS_MAX_LENGTH = 500

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

SORTABLE_FIELDS = frozenset(
    {
        'id',
        'company_name',
        'contact_person',
        'mobile_number',
        'email',
        'state',
        'city',
        'created_at',
        'updated_at',
    }
)
