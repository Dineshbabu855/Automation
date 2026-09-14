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
    For Webhook triggers: only webhook_token required (auto-generated on save).
    """
    if not triggers:
        frappe.throw(
            _("Cannot publish: at least one trigger is required.")
        )
    for i, trigger in enumerate(triggers):
        doctype = trigger.get("trigger_doctype") if isinstance(trigger, dict) else getattr(trigger, "trigger_doctype", None)
        event = trigger.get("trigger_event") if isinstance(trigger, dict) else getattr(trigger, "trigger_event", None)
        trigger_type = trigger.get("trigger_type", "DocType Event") if isinstance(trigger, dict) else getattr(trigger, "trigger_type", "DocType Event")
        if trigger_type == "Webhook":
            token = trigger.get("webhook_token") if isinstance(trigger, dict) else getattr(trigger, "webhook_token", None)
            if not token:
                frappe.throw(
                    _("Cannot publish: Webhook trigger row {0} is missing webhook_token.").format(i + 1)
                )
        elif not doctype:
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

    Uses reachability analysis: for each field-reading node, compute which
    trigger rows can actually reach it through the graph. If all reachable
    trigger rows share the SAME doctype (or only one trigger row reaches it),
    no explicit scoping is required — the doctype can be inferred automatically.

    Only rejects when a node is reachable from trigger rows spanning MORE than
    one distinct doctype (a genuine convergence point) AND lacks explicit
    trigger_doctype_select.

    Called from save_automation BEFORE persisting. Raises frappe.ValidationError
    if any unscoped node is genuinely ambiguous.
    """
    if not graph_json or not triggers:
        return

    # Build trigger row map: row_index -> doctype
    # Trigger rows don't carry an explicit "name" key in the dicts passed from
    # save_automation. We key by their index in the triggers list and match them
    # to Trigger nodes in the graph by doctype.
    _WEBHOOK_DOCTYPE = "__webhook__"
    trigger_rows = {}  # row_index -> doctype
    for idx, t in enumerate(triggers):
        tt = t.get("trigger_type", "DocType Event") if isinstance(t, dict) else getattr(t, "trigger_type", "DocType Event")
        if tt == "Schedule":
            continue
        if tt == "Webhook":
            trigger_rows[idx] = _WEBHOOK_DOCTYPE
            continue
        dt = t.get("trigger_doctype") if isinstance(t, dict) else getattr(t, "trigger_doctype", None)
        if dt:
            trigger_rows[idx] = dt

    if len(trigger_rows) <= 1:
        return  # Single trigger row — no ambiguity possible

    try:
        graph = json.loads(graph_json) if isinstance(graph_json, str) else graph_json
    except (json.JSONDecodeError, TypeError):
        return  # Can't parse — skip validation (will fail at execution)

    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    # Build adjacency list (forward)
    forward = {}  # node_id -> [target_id, ...]
    for e in edges:
        src = e.get("source")
        tgt = e.get("target")
        if src and tgt:
            forward.setdefault(src, []).append(tgt)

    # Build edge data map for trigger-row filtering
    # source_id -> {target_id: edge_data}
    edge_data_map = {}
    for e in edges:
        src = e.get("source")
        tgt = e.get("target")
        if src and tgt:
            edge_data_map.setdefault(src, {})[tgt] = e

    # For each trigger row index, compute the set of nodes reachable via forward walk.
    # We match trigger row indices to Trigger nodes in the graph by doctype.
    # Multiple trigger rows with the same doctype share the same Trigger node in the graph.
    trigger_nodes = [n for n in nodes if n.get("type") == "trigger"]

    # Build a mapping from trigger_node_id -> list of trigger_row_indices
    node_to_trigger_indices = {}
    for tn in trigger_nodes:
        tn_data = tn.get("data", {})
        tn_doctype = tn_data.get("trigger_doctype", "")
        for idx, dt in trigger_rows.items():
            if not tn_doctype:
                # Empty doctype ("Any") means this trigger node fires for ALL rows
                node_to_trigger_indices.setdefault(tn["id"], []).append(idx)
            elif dt == tn_doctype or (not tn_doctype and dt == _WEBHOOK_DOCTYPE):
                node_to_trigger_indices.setdefault(tn["id"], []).append(idx)

    trigger_reachability = {}  # trigger_row_index -> set of reachable node_ids

    for idx in trigger_rows:
        reachable = set()
        # Find which trigger node(s) in the graph this row maps to
        queue = []
        for tn in trigger_nodes:
            if idx in node_to_trigger_indices.get(tn["id"], []):
                queue.append(tn["id"])

        while queue:
            nid = queue.pop(0)
            if nid in reachable:
                continue
            reachable.add(nid)
            targets = forward.get(nid, [])
            for tgt in targets:
                edge = edge_data_map.get(nid, {}).get(tgt, {})
                applies = edge.get("applies_to_triggers") or []
                # null/empty means "All" — always included
                if not applies or idx in applies:
                    queue.append(tgt)

        trigger_reachability[idx] = reachable

    # For each field-reading node, compute which trigger rows can reach it
    unscoped = []
    nodes_map = {n["id"]: n for n in nodes}

    for node in nodes:
        node_type = node.get("type")
        node_data = node.get("data", {})
        node_id = node.get("id", "unknown")

        # Determine if this is a field-reading node that could need scoping
        needs_scoping_check = False
        field_checked = ""
        if node_type == "action":
            action_type = node_data.get("action_type", "")
            if action_type in _SCOPABLE_NODE_TYPES:
                needs_scoping_check = True
                field_checked = action_type
        elif node_type == "condition":
            field = node_data.get("condition_field", "")
            if field and field != _PSEUDO_FIELD:
                needs_scoping_check = True
                field_checked = field
        elif node_type in ("if", "switch"):
            field = node_data.get("field_to_check", "")
            if field and field != _PSEUDO_FIELD:
                needs_scoping_check = True
                field_checked = field

        if not needs_scoping_check:
            continue

        # Already has explicit scoping — skip
        scoped = node_data.get("trigger_doctype_select", "")
        if scoped:
            continue

        # Find which trigger row indices can reach this node
        reachable_doctypes = set()
        for idx, reachable_nodes in trigger_reachability.items():
            if node_id in reachable_nodes:
                dt = trigger_rows.get(idx, "")
                if dt:
                    reachable_doctypes.add(dt)

        # If 0 or 1 distinct doctypes can reach this node — no ambiguity
        if len(reachable_doctypes) <= 1:
            continue

        # Genuine convergence: multiple doctypes can reach this unscoped node
        if node_type == "action":
            unscoped.append(f"Action node '{node_data.get('action_type', '')}' ({node_id})")
        elif node_type == "condition":
            unscoped.append(f"Condition node ({node_id}) checking '{field_checked}'")
        elif node_type in ("if", "switch"):
            label = "IF" if node_type == "if" else "Switch"
            unscoped.append(f"{label} node ({node_id}) checking '{field_checked}'")

    if unscoped:
        names = "; ".join(unscoped)
        all_doctypes = sorted(set(trigger_rows.values()))
        frappe.throw(
            _("This automation has multiple trigger DocTypes ({0}). "
              "The following nodes are reachable from more than one DocType "
              "but have no explicit DocType scope: {1}. Please set "
              "'Trigger DocType' on each node to a specific DocType, or "
              "select 'Any'.")
            .format(", ".join(all_doctypes), names)
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
            "webhook_token": trigger.webhook_token,
            "webhook_url_display": trigger.webhook_url_display,
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
                # Auto-generate webhook_token for Webhook triggers that don't have one
                if trigger_data.get("trigger_type") == "Webhook" and not trigger_data.get("webhook_token"):
                    trigger_data["webhook_token"] = frappe.generate_hash(length=40)
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
                # Auto-generate webhook_token for Webhook triggers that don't have one
                if trigger_data.get("trigger_type") == "Webhook" and not trigger_data.get("webhook_token"):
                    trigger_data["webhook_token"] = frappe.generate_hash(length=40)
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
def regenerate_webhook_token(automation_name, trigger_index):
    """Regenerate the webhook token for a specific Webhook trigger row.

    The old token is immediately invalidated — no grace period.
    Returns the new token so the frontend can update the URL display.
    """
    if not frappe.session.user or frappe.session.user == "Guest":
        frappe.throw(_("Login required"), frappe.DoesNotExistError)
    if not frappe.has_permission("Automation", "write"):
        frappe.throw(_("You need write access to regenerate webhook tokens"))

    if not frappe.db.exists("Automation", automation_name):
        frappe.throw(_("Automation {0} does not exist").format(automation_name))

    doc = frappe.get_doc("Automation", automation_name)
    idx = int(trigger_index)

    if idx < 0 or idx >= len(doc.triggers):
        frappe.throw(_("Invalid trigger index"))

    trigger = doc.triggers[idx]
    if trigger.trigger_type != "Webhook":
        frappe.throw(_("Trigger at index {0} is not a Webhook trigger").format(idx))

    new_token = frappe.generate_hash(length=40)
    trigger.webhook_token = new_token
    doc.save(ignore_permissions=True)

    return {"token": new_token, "trigger_index": idx}


# ---------------------------------------------------------------------------
# Webhook Trigger — public endpoint (guest-whitelisted)
# ---------------------------------------------------------------------------

# URL design: Frappe routes whitelisted methods via /api/method/{dotted.path}.
# Tokens are passed as a query parameter (?token=xxx) rather than a path
# segment because Frappe's routing doesn't support path-based parameter
# extraction for whitelisted methods without custom hooks. Query params are
# simpler, debuggable, and work with Frappe's built-in rate limiting.

_WEBHOOK_MAX_BODY_BYTES = 1024 * 100  # 100 KB — reject oversized payloads before buffering


@frappe.whitelist(allow_guest=True)
def webhook_trigger():
    """Public endpoint for Webhook-type triggers.

    Accepts POST with ?token=<hex> query parameter. Validates the token
    using constant-time comparison (hmac.compare_digest), enqueues the
    automation execution, and returns a fast 200 acknowledgment.

    Rate-limited to 30 requests per minute per IP via Frappe's built-in
    rate_limit decorator. Body size capped at 100 KB.
    """
    import hmac as _hmac

    # --- Body size check (before full parsing) ---
    content_length = frappe.request.content_length
    if content_length and content_length > _WEBHOOK_MAX_BODY_BYTES:
        frappe.throw(
            _("Payload too large (max {0} bytes)").format(_WEBHOOK_MAX_BODY_BYTES),
            frappe.ValidationError,
        )

    # --- Rate limit: 30 requests per minute per IP ---
    ip = frappe.local.request_ip or "unknown"
    rl_key = frappe.cache.make_key(f"rl:webhook_trigger:{ip}")
    count = frappe.cache.incrby(rl_key, 1)
    if count == 1:
        frappe.cache.expire(rl_key, 60)
    if count > 30:
        frappe.local.response["http_status_code"] = 429
        return {"status": "rate_limited"}

    # --- Token extraction ---
    token = frappe.form_dict.get("token", "")
    if not token or not isinstance(token, str):
        # Same generic response for missing/empty token as for invalid
        return _webhook_reject()

    # --- Token lookup (constant-time comparison) ---
    # Find the Automation Trigger row with this token
    trigger_row = frappe.db.sql(
        """SELECT at.name, at.parent AS automation_name
           FROM `tabAutomation Trigger` at
           INNER JOIN `tabAutomation` a ON a.name = at.parent
           WHERE at.trigger_type = 'Webhook'
             AND at.webhook_token IS NOT NULL
             AND at.webhook_token != ''
           LIMIT 500""",
        as_dict=True,
    )

    matched_automation = None
    for row in trigger_row:
        stored = row.get("webhook_token", "")
        if not stored:
            continue
        # Constant-time comparison — prevents timing-attack token discovery
        if _hmac.compare_digest(stored, token):
            matched_automation = row.automation_name
            break

    if not matched_automation:
        return _webhook_reject()

    # --- Validate automation is Published and enabled ---
    auto = frappe.db.get_value(
        "Automation",
        matched_automation,
        ["name", "status", "enabled"],
        as_dict=True,
    )
    if not auto or auto.status != "Published" or not auto.enabled:
        return _webhook_reject()

    # --- Parse payload ---
    payload = {}
    if frappe.request.data:
        try:
            import json as _json
            payload = _json.loads(frappe.request.data)
        except (ValueError, TypeError):
            return _webhook_reject()

    # --- Enqueue execution (do NOT run synchronously) ---
    from automation_builder.dispatcher import execute_webhook_trigger

    frappe.enqueue(
        execute_webhook_trigger,
        automation_name=matched_automation,
        payload=payload,
        queue="default",
        timeout=300,
    )

    return {"status": "queued"}


def _webhook_reject():
    """Return a generic rejection response — no distinguishing error detail.

    404 with a generic body regardless of whether the token was missing,
    invalid, or belonged to a Draft automation. Prevents token enumeration.
    """
    frappe.local.response["http_status_code"] = 404
    return {"status": "not_found"}


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
