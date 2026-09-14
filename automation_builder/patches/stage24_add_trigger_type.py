"""Stage 24 migration: set trigger_type='DocType Event' on all existing rows.

All existing Automation Trigger rows were created under the DocType Event
model (the only trigger type that existed before Stage 24). This migration
explicitly sets trigger_type='DocType Event' on every row so that:
1. No row is left with a blank trigger_type (which would violate reqd=1).
2. Existing automation behavior is preserved exactly — no triggers change
   their event mapping, condition evaluation, or dispatch path.

This patch is a pre_model_sync patch, so the schema columns may not exist
yet when it first runs. We guard for that and rely on schema sync to
create the column with the correct default.
"""

import frappe


def execute():
    """Set trigger_type='DocType Event' on all existing Automation Trigger rows."""
    try:
        frappe.db.sql(
            "UPDATE `tabAutomation Trigger` SET trigger_type = 'DocType Event' "
            "WHERE trigger_type IS NULL OR trigger_type = ''"
        )
    except Exception:
        # Column doesn't exist yet — schema sync will create it with default 'DocType Event'
        pass
