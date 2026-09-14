"""Whitelisted API endpoints for the Automation Builder frontend."""

import json

import frappe
from frappe import _


def _enforce_publish_permission(status):
    """Enforce that only System Manager can set status to Published.

    Called from BOTH create and update paths of save_automation to ensure
    no code path can set status="Published" without this check.
    """
    if status == "Published" and "System Manager" not in frappe.get_roles():
        frappe.throw(_("Only System Manager can publish automations"))


def _validate_triggers_for_publish(triggers):
    """Require at least one valid trigger row when publishing.

    Called when status is being set to Published. An automation with no
    trigger data would be a silent no-op — Published but never fires.

    For DocType Event triggers: both trigger_doctype and trigger_event required.
    For Manual/Schedule triggers: only trigger_doctype required.
    """
    if not triggers:
        frappe.throw(
            _("Cannot publish: at least one trigger with trigger_doctype is required.")
        )
    for i, trigger in enumerate(triggers):
        doctype = trigger.get("trigger_doctype") if isinstance(trigger, dict) else getattr(trigger, "trigger_doctype", None)
        event = trigger.get("trigger_event") if isinstance(trigger, dict) else getattr(trigger, "trigger_event", None)
        trigger_type = trigger.get("trigger_type", "DocType Event") if isinstance(trigger, dict) else getattr(trigger, "trigger_type", "DocType Event")
        if not doctype:
            frappe.throw(
                _("Cannot publish: trigger row {0} is missing trigger_doctype.").format(i + 1)
            )
        if trigger_type == "DocType Event" and not event:
            frappe.throw(
                _("Cannot publish: DocType Event trigger row {0} is missing trigger_event.").format(i + 1)
            )


# Nodes whose config schema contains trigger_doctype_select.
# These are the node types that read trigger-document fields.
_SCOPABLE_NODE_TYPES = {"send_email", "create_document", "update_field", "http_request", "telegram"}

# Pseudo-field that resolves from context, not the document — never needs scoping.
_PSEUDO_FIELD = "__trigger_doctype__"


def _validate_scoping_for_multi_doctype(graph_json, triggers):
    """Reject automations where field-reading nodes lack explicit scoping.

    When an automation has 2+ distinct trigger doctypes, every node that
    reads trigger-document fields (actions with trigger_doctype_select in
    their config, condition/IF/Switch nodes checking a real field) MUST
    have explicit scoping set. Otherwise the node silently evaluates
    against the wrong document at runtime.

    Called from save_automation BEFORE persisting. Raises frappe.ValidationError
    if any node is unscoped in a multi-doctype context.
    """
    if not graph_json or not triggers:
        return

    # Determine distinct trigger doctypes
    doctypes = set()
    for t in triggers:
        dt = t.get("trigger_doctype") if isinstance(t, dict) else getattr(t, "trigger_doctype", None)
        if dt:
            doctypes.add(dt)

    if len(doctypes) <= 1:
        return  # No ambiguity — scoping is optional

    try:
        graph = json.loads(graph_json) if isinstance(graph_json, str) else graph_json
    except (json.JSONDecodeError, TypeError):
        return  # Can't parse — skip validation (will fail at execution)

    nodes = graph.get("nodes", [])

    unscoped = []
    for node in nodes:
        node_type = node.get("type")
        node_data = node.get("data", {})
        node_id = node.get("id", "unknown")

        if node_type == "action":
            action_type = node_data.get("action_type", "")
            if action_type not in _SCOPABLE_NODE_TYPES:
                continue
            scoped = node_data.get("trigger_doctype_select", "")
            if not scoped:
                unscoped.append(f"Action node '{action_type}' ({node_id})")

        elif node_type == "condition":
            field = node_data.get("condition_field", "")
            if field and field != _PSEUDO_FIELD:
                scoped = node_data.get("trigger_doctype_select", "")
                if not scoped:
                    unscoped.append(f"Condition node ({node_id}) checking '{field}'")

        elif node_type in ("if", "switch"):
            field = node_data.get("field_to_check", "")
            if field and field != _PSEUDO_FIELD:
                # IF/Switch reference a real field — needs scoping
                # (the __trigger_doctype__ pseudo-field is always safe)
                scoped = node_data.get("trigger_doctype_select", "")
                if not scoped:
                    label = "IF" if node_type == "if" else "Switch"
                    unscoped.append(f"{label} node ({node_id}) checking '{field}'")

    if unscoped:
        names = "; ".join(unscoped)
        frappe.throw(
            _("This automation has multiple trigger DocTypes ({0}). "
              "The following nodes read trigger fields but have no explicit "
              "DocType scope: {1}. Please set 'Trigger DocType' on each "
              "node to a specific DocType, or select 'Any'.")
            .format(", ".join(sorted(doctypes)), names)
        )


@frappe.whitelist()
def get_doctype_fields(doctype):
    """Return field list for a given DocType."""
    if not frappe.session.user or frappe.session.user == "Guest":
        frappe.throw(_("Login required"), frappe.DoesNotExistError)
    if not frappe.db.exists("DocType", doctype):
        frappe.throw(_("DocType {0} does not exist").format(doctype))
    if not frappe.has_permission(doctype, "read"):
        frappe.throw(_("Insufficient permissions to read {0} fields").format(doctype))

    meta = frappe.get_meta(doctype)
    fields = []
    for f in meta.fields:
        if f.fieldtype in ("Section Break", "Column Break", "Tab Break", "Button"):
            continue
        field_data = {
            "fieldname": f.fieldname,
            "label": f.label or f.fieldname,
            "fieldtype": f.fieldtype,
        }
        if f.fieldtype == "Link" and f.options:
            field_data["options"] = f.options
        fields.append(field_data)
    return fields


@frappe.whitelist()
def get_automation(name):
    """Return full Automation doc including graph_definition and triggers."""
    if not frappe.session.user or frappe.session.user == "Guest":
        frappe.throw(_("Login required"), frappe.DoesNotExistError)
    if not frappe.has_permission("Automation", "read"):
        frappe.throw(_("Insufficient permissions"))
    doc = frappe.get_doc("Automation", name)

    # Get triggers from child table
    # NOTE: When AutomationTrigger is loaded as a child of Automation,
    # Frappe does NOT populate grandchild table attributes (conditions).
    # We must query them separately via SQL.
    trigger_names = [t.name for t in doc.triggers]
    conditions_map = {}
    if trigger_names:
        conditions_rows = frappe.db.sql(
            """SELECT parent, condition_field, condition_operator, condition_value
               FROM `tabAutomation Trigger Condition`
               WHERE parent IN %s
               ORDER BY parent, idx""",
            (tuple(trigger_names),),
            as_dict=True,
        )
        for row in conditions_rows:
            conditions_map.setdefault(row.parent, []).append({
                "condition_field": row.condition_field,
                "condition_operator": row.condition_operator,
                "condition_value": row.condition_value,
            })

    triggers = []
    for trigger in doc.triggers:
        triggers.append({
            "trigger_type": trigger.trigger_type or "DocType Event",
            "trigger_doctype": trigger.trigger_doctype,
            "trigger_event": trigger.trigger_event,
            "schedule_frequency": trigger.schedule_frequency,
            "next_run": str(trigger.next_run) if trigger.next_run else None,
            "last_run": str(trigger.last_run) if trigger.last_run else None,
            "condition_logic": trigger.condition_logic or "All must match",
            "conditions": conditions_map.get(trigger.name, []),
            # Legacy flat fields for backward compat display
            "condition_field": trigger.condition_field,
            "condition_operator": trigger.condition_operator,
            "condition_value": trigger.condition_value,
        })

    return {
        "name": doc.name,
        "automation_name": doc.automation_name,
        "status": doc.status,
        "enabled": doc.enabled,
        "graph_definition": doc.graph_definition,
        "triggers": triggers,
        "description": doc.description,
        # Legacy fields for backward compatibility
        "trigger_doctype": doc.trigger_doctype or (triggers[0]["trigger_doctype"] if triggers else None),
        "trigger_event": doc.trigger_event or (triggers[0]["trigger_event"] if triggers else None),
        "condition_field": doc.condition_field or (triggers[0]["condition_field"] if triggers else None),
        "condition_operator": doc.condition_operator or (triggers[0]["condition_operator"] if triggers else None),
        "condition_value": doc.condition_value or (triggers[0]["condition_value"] if triggers else None),
        "workflow_json": doc.workflow_json or doc.graph_definition,
    }


def _insert_grandchild_conditions(doc, conditions_per_trigger):
    """Insert grandchild condition rows after parent save.

    Args:
        doc: Saved Automation doc (with triggers already persisted)
        conditions_per_trigger: List of lists; conditions_per_trigger[i] is the
            list of condition dicts for doc.triggers[i]
    """
    for idx, conditions in enumerate(conditions_per_trigger):
        if not conditions:
            continue
        trigger_name = doc.triggers[idx].name
        for cond_data in conditions:
            frappe.get_doc({
                "doctype": "Automation Trigger Condition",
                "parent": trigger_name,
                "parenttype": "Automation Trigger",
                "parentfield": "conditions",
                "condition_field": cond_data["condition_field"],
                "condition_operator": cond_data["condition_operator"],
                "condition_value": cond_data.get("condition_value", ""),
            }).insert(ignore_permissions=True)


@frappe.whitelist()
def save_automation(
    name=None,
    graph_definition=None,
    automation_name=None,
    status=None,
    enabled=1,
    triggers=None,
    description=None,
    # Legacy fields for backward compatibility
    workflow_json=None,
    trigger_doctype=None,
    trigger_event=None,
    condition_field=None,
    condition_operator=None,
    condition_value=None,
):
    """Create or update an Automation record."""
    if not frappe.has_permission("Automation", "write"):
        frappe.throw(_("Insufficient permissions"))

    if name and frappe.db.exists("Automation", name):
        doc = frappe.get_doc("Automation", name)
        doc.automation_name = automation_name or doc.automation_name

        # Parse triggers from JSON string if needed (frappe.call sends lists as strings)
        if isinstance(triggers, str):
            triggers = json.loads(triggers)

        # Handle status with permission check
        if status is not None:
            _enforce_publish_permission(status)
            if status == "Published":
                # Validate triggers exist before publishing
                effective_triggers = triggers if triggers is not None else [
                    {"trigger_doctype": t.trigger_doctype, "trigger_event": t.trigger_event,
                     "condition_field": t.condition_field, "condition_operator": t.condition_operator,
                     "condition_value": t.condition_value}
                    for t in doc.triggers
                ]
                _validate_triggers_for_publish(effective_triggers)
            doc.status = status

        doc.enabled = int(enabled)
        doc.description = description if description is not None else doc.description

        # Handle graph_definition (new format)
        if graph_definition is not None:
            doc.graph_definition = graph_definition

        # Handle triggers table (new format)
        saved_conditions = []
        if triggers is not None:
            # Delete existing grandchild conditions before replacing triggers
            old_trigger_names = [t.name for t in doc.triggers]
            if old_trigger_names:
                frappe.db.sql(
                    "DELETE FROM `tabAutomation Trigger Condition` WHERE parent IN %s",
                    (old_trigger_names,),
                )
            doc.triggers = []
            for trigger_data in triggers:
                conditions = trigger_data.pop("conditions", [])
                saved_conditions.append(conditions)
                cond_logic = trigger_data.pop("condition_logic", "All must match")
                trigger_data["condition_logic"] = cond_logic
                doc.append("triggers", trigger_data)

        # Handle legacy fields for backward compatibility
        if workflow_json is not None:
            doc.workflow_json = workflow_json
        if trigger_doctype is not None:
            doc.trigger_doctype = trigger_doctype
        if trigger_event is not None:
            doc.trigger_event = trigger_event
        if condition_field is not None:
            doc.condition_field = condition_field
        if condition_operator is not None:
            doc.condition_operator = condition_operator
        if condition_value is not None:
            doc.condition_value = condition_value

        # Validate scoping in multi-doctype automations (before save)
        effective_graph = graph_definition if graph_definition is not None else doc.graph_definition
        effective_triggers_for_validation = triggers if triggers is not None else [
            {"trigger_doctype": t.trigger_doctype, "trigger_event": t.trigger_event}
            for t in doc.triggers
        ]
        _validate_scoping_for_multi_doctype(effective_graph, effective_triggers_for_validation)

        doc.save(ignore_permissions=True)
        if saved_conditions:
            _insert_grandchild_conditions(doc, saved_conditions)
    else:
        # Parse triggers from JSON string if needed (frappe.call sends lists as strings)
        if isinstance(triggers, str):
            triggers = json.loads(triggers)

        doc = frappe.new_doc("Automation")
        doc.automation_name = automation_name
        # Enforce publish permission BEFORE setting status (create path)
        effective_status = status or "Draft"
        _enforce_publish_permission(effective_status)
        if effective_status == "Published":
            _validate_triggers_for_publish(triggers or [])
        doc.status = effective_status
        doc.enabled = int(enabled)
        doc.description = description

        # Handle graph_definition (new format)
        if graph_definition is not None:
            doc.graph_definition = graph_definition

        # Handle triggers table (new format)
        saved_conditions = []
        if triggers is not None:
            for trigger_data in triggers:
                conditions = trigger_data.pop("conditions", [])
                saved_conditions.append(conditions)
                cond_logic = trigger_data.pop("condition_logic", "All must match")
                trigger_data["condition_logic"] = cond_logic
                doc.append("triggers", trigger_data)

        # Handle legacy fields for backward compatibility
        if workflow_json is not None:
            doc.workflow_json = workflow_json
        if trigger_doctype is not None:
            doc.trigger_doctype = trigger_doctype
        if trigger_event is not None:
            doc.trigger_event = trigger_event
        if condition_field is not None:
            doc.condition_field = condition_field
        if condition_operator is not None:
            doc.condition_operator = condition_operator
        if condition_value is not None:
            doc.condition_value = condition_value

        # Validate scoping in multi-doctype automations (before insert)
        _validate_scoping_for_multi_doctype(graph_definition, triggers or [])

        doc.insert(ignore_permissions=True)
        _insert_grandchild_conditions(doc, saved_conditions)

    return {"name": doc.name, "automation_name": doc.automation_name}


@frappe.whitelist()
def list_automations():
    """Return list of automations for the list view."""
    if not frappe.session.user or frappe.session.user == "Guest":
        frappe.throw(_("Login required"), frappe.DoesNotExistError)
    if not frappe.has_permission("Automation", "read"):
        frappe.throw(_("Insufficient permissions"))
    return frappe.get_all(
        "Automation",
        fields=[
            "name",
            "automation_name",
            "status",
            "enabled",
            "modified",
        ],
        order_by="modified desc",
    )


@frappe.whitelist()
def list_runs(automation=None, limit_page_length=50):
    """Return Automation Run records, optionally filtered by automation."""
    if not frappe.session.user or frappe.session.user == "Guest":
        frappe.throw(_("Login required"), frappe.DoesNotExistError)
    if not frappe.has_permission("Automation Run", "read"):
        frappe.throw(_("Insufficient permissions"))
    filters = {}
    if automation:
        filters["automation"] = automation

    return frappe.get_all(
        "Automation Run",
        fields=[
            "name",
            "automation",
            "reference_doctype",
            "reference_name",
            "status",
            "started_at",
            "ended_at",
            "log",
            "error",
        ],
        filters=filters,
        order_by="started_at desc",
        limit_page_length=int(limit_page_length),
    )


@frappe.whitelist()
def get_doctype_list():
    """Return list of DocTypes for the trigger picker."""
    if not frappe.session.user or frappe.session.user == "Guest":
        frappe.throw(_("Login required"), frappe.DoesNotExistError)
    return frappe.get_all(
        "DocType",
        fields=["name"],
        filters={"istable": 0, "issingle": 0},
        order_by="name asc",
    )


@frappe.whitelist()
def get_action_types():
    """Return metadata for every registered action type.

    Returns label + config_schema for each type (not the execute functions).
    The frontend will use this to render config panels generically.
    """
    if not frappe.session.user or frappe.session.user == "Guest":
        frappe.throw(_("Login required"), frappe.DoesNotExistError)
    from automation_builder.action_types import get_all_action_types

    return get_all_action_types()


@frappe.whitelist()
def can_publish():
    """Check if current user can publish automations (System Manager only)."""
    return "System Manager" in frappe.get_roles()


@frappe.whitelist()
def search_documents(doctype, query=""):
    """Search for documents of a given DocType for the Manual Trigger picker.

    Returns up to 20 matching documents with name and a display label.
    """
    if not frappe.session.user or frappe.session.user == "Guest":
        frappe.throw(_("Login required"), frappe.DoesNotExistError)
    if not frappe.db.exists("DocType", doctype):
        frappe.throw(_("DocType {0} does not exist").format(doctype))
    if not frappe.has_permission(doctype, "read"):
        frappe.throw(_("Insufficient permissions to read {0}").format(doctype))

    meta = frappe.get_meta(doctype)
    title_field = meta.title_field or "name"

    filters = {}
    if query:
        filters[title_field] = ["like", f"%{query}%"]

    docs = frappe.get_all(
        doctype,
        fields=["name", title_field],
        filters=filters,
        limit_page_length=20,
        order_by="modified desc",
    )

    return [
        {"name": d.name, "label": d.get(title_field) or d.name}
        for d in docs
    ]


# ---------------------------------------------------------------------------
# Manual Trigger endpoint
# ---------------------------------------------------------------------------

@frappe.whitelist()
def run_automation_manually(automation_name, reference_doctype, reference_name):
    """Execute an automation as a manual test run against a real document.

    Permission: user must have write access to the Automation doc.
    Runs synchronously (not via frappe.enqueue) since the user is waiting.
    The resulting Automation Run record is marked with trigger_source='Manual'.
    """
    if not frappe.session.user or frappe.session.user == "Guest":
        frappe.throw(_("Login required"), frappe.DoesNotExistError)

    if not frappe.has_permission("Automation", "write"):
        frappe.throw(_("You need write access to the Automation to test-run it"))

    if not frappe.db.exists("Automation", automation_name):
        frappe.throw(_("Automation {0} does not exist").format(automation_name))

    if not frappe.db.exists(reference_doctype, reference_name):
        frappe.throw(_("{0} {1} does not exist").format(reference_doctype, reference_name))

    from automation_builder.dispatcher import execute_automation

    # Execute synchronously — reuse the exact same execute_automation path
    execute_automation(automation_name, reference_doctype, reference_name)

    # Mark the most recent run for this automation+document as Manual
    frappe.db.sql(
        "UPDATE `tabAutomation Run` SET trigger_source = 'Manual' "
        "WHERE automation = %s AND reference_doctype = %s AND reference_name = %s "
        "ORDER BY creation DESC LIMIT 1",
        (automation_name, reference_doctype, reference_name),
    )

    # Return the run record
    run = frappe.get_all(
        "Automation Run",
        filters={
            "automation": automation_name,
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
        },
        fields=["name", "status", "trigger_source", "started_at", "ended_at", "log"],
        order_by="creation desc",
        limit_page_length=1,
    )
    return run[0] if run else {"status": "unknown"}


# ---------------------------------------------------------------------------
# Email Template endpoints
# ---------------------------------------------------------------------------


@frappe.whitelist()
def list_email_templates():
    """Return list of Automation Email Templates."""
    if not frappe.session.user or frappe.session.user == "Guest":
        frappe.throw(_("Login required"), frappe.DoesNotExistError)
    if not frappe.has_permission("Automation Email Template", "read"):
        frappe.throw(_("Insufficient permissions"))
    return frappe.get_all(
        "Automation Email Template",
        fields=["name", "template_name", "subject", "modified"],
        order_by="modified desc",
    )


@frappe.whitelist()
def get_email_template(name):
    """Return full Email Template doc."""
    if not frappe.session.user or frappe.session.user == "Guest":
        frappe.throw(_("Login required"), frappe.DoesNotExistError)
    if not frappe.has_permission("Automation Email Template", "read"):
        frappe.throw(_("Insufficient permissions"))
    doc = frappe.get_doc("Automation Email Template", name)
    return {
        "name": doc.name,
        "template_name": doc.template_name,
        "subject": doc.subject,
        "body": doc.body,
    }


@frappe.whitelist()
def save_email_template(name=None, template_name=None, subject=None, body=None):
    """Create or update an Automation Email Template."""
    if not frappe.has_permission("Automation Email Template", "write"):
        frappe.throw(_("Insufficient permissions"))

    if name and frappe.db.exists("Automation Email Template", name):
        doc = frappe.get_doc("Automation Email Template", name)
        doc.template_name = template_name or doc.template_name
        doc.subject = subject if subject is not None else doc.subject
        doc.body = body if body is not None else doc.body
        doc.save(ignore_permissions=True)
    else:
        doc = frappe.new_doc("Automation Email Template")
        doc.template_name = template_name
        doc.subject = subject
        doc.body = body
        doc.insert(ignore_permissions=True)

    return {"name": doc.name, "template_name": doc.template_name}
