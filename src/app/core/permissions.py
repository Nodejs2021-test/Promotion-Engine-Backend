"""Permission catalogue enforced by the API and the roles created in MongoDB at start-up.

Roles and their permissions are stored in the MongoDB ``roles`` collection and are editable by
administrators; the code only defines the permissions it enforces."""

# Every permission the API enforces: value -> (label, description)
PERMISSIONS: dict[str, tuple[str, str]] = {
    "view": ("View", "View campaigns, sales orders, the NetSuite field mapping and audit history"),
    "campaign:write": ("Manage campaigns", "Create and edit campaigns and their rules; submit them for approval"),
    "campaign:approve": (
        "Approve campaigns",
        "Approve or reject submitted campaigns, enable / disable them and set the campaign priority order",
    ),
    "salesorder:write": ("Record sales orders", "Paste, map, price and save NetSuite sales orders"),
    "users:manage": ("Administer", "Manage users, roles, application settings and the NetSuite field mapping"),
    "apikeys:manage": ("Manage API keys", "Create and revoke integration API keys"),
}

ADMIN_ROLE = "ADMIN"

# Permissions the ADMIN role can never lose (so the application always stays administrable).
ADMIN_REQUIRED = {"view", "users:manage", "apikeys:manage"}

# Roles written to MongoDB at start-up when missing; afterwards they are managed in the database.
INITIAL_ROLES: list[dict] = [
    {"role": ADMIN_ROLE, "label": "Administrator", "description": "Full access", "permissions": sorted(PERMISSIONS)},
    {
        "role": "CAMPAIGN_MANAGER",
        "label": "Campaign Manager",
        "description": "Creates campaigns and rules and submits them for approval",
        "permissions": ["campaign:write", "salesorder:write", "view"],
    },
    {
        "role": "CAMPAIGN_APPROVER",
        "label": "Campaign Approver",
        "description": "Approves or rejects campaigns and sets their priority",
        "permissions": ["campaign:approve", "view"],
    },
    {
        "role": "SALES_ORDER_USER",
        "label": "Sales Order User",
        "description": "Records and views sales orders",
        "permissions": ["salesorder:write", "view"],
    },
    {"role": "READ_ONLY", "label": "Read-only User", "description": "View only", "permissions": ["view"]},
]


def has_permission(user: dict, permission: str) -> bool:
    return permission in user.get("permissions", [])
