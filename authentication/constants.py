"""Authentication and user management constants."""

# Frontend-owned role values (no separate role master table).
USER_ROLE_ADMIN = 'Admin'
USER_ROLE_SUPER_ADMIN = 'SuperAdmin'
USER_ROLE_SENIOR_MANAGEMENT = 'Senior Management'
USER_ROLE_BRIDGE_SITE_ENGINEER = 'Bridge Site Engineer'
USER_ROLE_ROAD_SITE_ENGINEER = 'Road Site Engineer'
USER_ROLE_SITE_ENGINEER_INVENTORY = 'Site Engineer Inventory'
USER_ROLE_SITE_ENGINEER_DISTRESS = 'Site Engineer Distress'
USER_ROLE_SITE_ENGINEER_ROUTINE_MAINTENANCE = 'Site Engineer Routine Maintenance'
USER_ROLE_SITE_STOREKEEPER = 'Site Storekeeper'

USER_ROLES = (
    USER_ROLE_ADMIN,
    USER_ROLE_SUPER_ADMIN,
    USER_ROLE_SENIOR_MANAGEMENT,
    USER_ROLE_BRIDGE_SITE_ENGINEER,
    USER_ROLE_ROAD_SITE_ENGINEER,
    USER_ROLE_SITE_ENGINEER_INVENTORY,
    USER_ROLE_SITE_ENGINEER_DISTRESS,
    USER_ROLE_SITE_ENGINEER_ROUTINE_MAINTENANCE,
    USER_ROLE_SITE_STOREKEEPER,
)

USER_MANAGER_ROLES = frozenset({USER_ROLE_ADMIN, USER_ROLE_SUPER_ADMIN})

ACCESS_TYPE_ALL = 'ALL'
ACCESS_TYPE_RIS = 'RIS'
ACCESS_TYPE_BIS = 'BIS'

ACCESS_TYPES = (
    ACCESS_TYPE_ALL,
    ACCESS_TYPE_RIS,
    ACCESS_TYPE_BIS,
)

# Role → allowed access types (frontend contract).
ROLE_ACCESS_MAP: dict[str, frozenset[str]] = {
    USER_ROLE_ADMIN: frozenset({ACCESS_TYPE_ALL}),
    USER_ROLE_SUPER_ADMIN: frozenset({ACCESS_TYPE_ALL}),
    USER_ROLE_SENIOR_MANAGEMENT: frozenset({ACCESS_TYPE_ALL}),
    USER_ROLE_BRIDGE_SITE_ENGINEER: frozenset({ACCESS_TYPE_BIS}),
    USER_ROLE_ROAD_SITE_ENGINEER: frozenset({ACCESS_TYPE_RIS}),
    USER_ROLE_SITE_ENGINEER_INVENTORY: frozenset({ACCESS_TYPE_ALL}),
    USER_ROLE_SITE_ENGINEER_DISTRESS: frozenset({ACCESS_TYPE_ALL}),
    USER_ROLE_SITE_ENGINEER_ROUTINE_MAINTENANCE: frozenset({ACCESS_TYPE_RIS}),
    USER_ROLE_SITE_STOREKEEPER: frozenset({ACCESS_TYPE_ALL}),
}

USERNAME_MAX_LENGTH = 150
FIRST_NAME_MAX_LENGTH = 150
LAST_NAME_MAX_LENGTH = 150
EMAIL_MAX_LENGTH = 254
PHONE_NUMBER_MAX_LENGTH = 15

# Indian mobile: 10 digits starting with 6–9 (same as Labour/Supplier).
PHONE_NUMBER_PATTERN = r'^[6-9]\d{9}$'

ROLE_MAX_LENGTH = 100
ACCESS_TYPE_MAX_LENGTH = 10

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

SORTABLE_FIELDS = frozenset(
    {
        'id',
        'username',
        'first_name',
        'last_name',
        'email',
        'role',
        'access_type',
        'created_at',
        'updated_at',
    }
)


def allowed_access_types_for_role(role: str) -> frozenset[str]:
    return ROLE_ACCESS_MAP.get(str(role or '').strip(), frozenset())
