"""Shared denylist of sensitive DocTypes that automation actions must NEVER target.

Automations run with ignore_permissions=True as a deliberate tradeoff (the
automation engine acts on behalf of the configuring user, not the running
user). However, certain core DocTypes must never be mutated by automation
actions — targeting them would allow privilege escalation or governance
bypass.

This denylist is checked by create_document and update_field before
executing any mutation. The check is mandatory and cannot be overridden.
"""

# Core Frappe doctypes that control permissions, users, and system config
FRAPPE_CORE_DENYLIST = frozenset({
    "User",
    "Role",
    "DocPerm",
    "DocType",
    "System Settings",
    "Permission Manager",
    "UserRole",
})

# Doctypes that grant code-execution or permission-altering capability
CODE_EXECUTION_DENYLIST = frozenset({
    "Server Script",       # Executes Python on the server
    "Client Script",       # Injects JS into desk pages (XSS-like surface)
    "Custom Field",        # Modifies any DocType's schema at runtime
    "Custom Report",       # Can execute arbitrary report scripts
})

# Governance and workflow doctypes that control business logic
GOVERNANCE_DENYLIST = frozenset({
    "Workflow",
    "Workflow State",
    "Workflow Action Master",
})

# This app's own governance doctypes — automations must never modify themselves
APP_GOVERNANCE_DENYLIST = frozenset({
    "Automation",
    "Automation Trigger",
    "Automation Run",
    "Automation Run Step",
    "Automation Builder Settings",
})

DENYLIST = (
    FRAPPE_CORE_DENYLIST
    | CODE_EXECUTION_DENYLIST
    | GOVERNANCE_DENYLIST
    | APP_GOVERNANCE_DENYLIST
)


def check_denylist(target_doctype):
    """Raise ValueError if target_doctype is in the denylist.

    Call this from any action type that mutates documents with
    ignore_permissions=True. Raises immediately with a clear error
    message — no partial execution.

    Returns None on success (doctype is allowed).
    """
    if target_doctype in DENYLIST:
        raise ValueError(
            f"Automation actions cannot target the '{target_doctype}' DocType. "
            f"This is a security restriction — the following DocTypes are "
            f"blocked: {', '.join(sorted(DENYLIST))}"
        )
    return None
