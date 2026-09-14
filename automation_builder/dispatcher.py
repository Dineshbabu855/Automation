"""Hook dispatcher — routes document events to matching automations.

Uses a wildcard ``*`` doc_events hook so a single handler fires for every
DocType.  The handler is intentionally cheap: it does an indexed query for
enabled + published automations matching the doctype+event, and returns
immediately if there are none.
"""

import json
import operator as op

import frappe

from automation_builder.action_types import get_action_type
from automation_builder.action_types._helpers import TRIGGER_DOCTYPE_FIELD

# ---------------------------------------------------------------------------
# Event mapping: Frappe hook method names → automation trigger_event strings
# ---------------------------------------------------------------------------
EVENT_MAP = {
    "after_insert": "After Insert",
    "on_update": "On Update",
    "on_submit": "On Submit",
    "on_cancel": "On Cancel",
}

OPERATORS = {
    "=": op.eq,
    "!=": op.ne,
    ">": op.gt,
    "<": op.lt,
    ">=": op.ge,
    "<=": op.le,
}

# Operators that need special handling (not simple comparison)
_SPECIAL_OPERATORS = {"like", "not like", "in", "not in", "is set", "is not set"}


def _evaluate_single_condition(doc, cond, context=None):
    """Evaluate a single condition dict against a document.

    Works for both trigger-level conditions (from Automation Trigger Condition
    child table) and graph-walk condition nodes (from the graph canvas).

    Args:
        doc: The trigger document (has .get() method)
        cond: dict with keys: condition_field, condition_operator, condition_value
              (or field_to_check/operator/value for graph-walk nodes)
        context: optional execution context dict (needed for ``__trigger_doctype__``)

    Returns:
        bool: True if the condition matches.
    """
    field = cond.get("condition_field") or cond.get("field_to_check", "")
    operator_str = cond.get("condition_operator") or cond.get("operator", "")
    expected = cond.get("condition_value") or cond.get("value", "")

    if not field or not operator_str:
        return True

    if field == TRIGGER_DOCTYPE_FIELD and context is not None:
        actual = context.get("trigger_doctype", "")
    else:
        actual = doc.get(field) if doc else None

    # Handle special operators
    if operator_str in _SPECIAL_OPERATORS:
        return _evaluate_special_operator(actual, operator_str, expected)

    # Standard comparison operators
    comparator = OPERATORS.get(operator_str)
    if comparator is None:
        return False

    if operator_str in (">", "<", ">=", "<="):
        try:
            actual = float(actual)
            expected = float(expected)
        except (TypeError, ValueError):
            pass

    return comparator(actual, expected)


def _evaluate_special_operator(actual, operator_str, expected):
    """Evaluate special operators: like, not like, in, not in, is set, is not set."""
    actual_str = str(actual) if actual is not None else ""
    expected_str = str(expected) if expected is not None else ""

    if operator_str == "is set":
        return actual is not None and actual != ""
    elif operator_str == "is not set":
        return actual is None or actual == ""
    elif operator_str == "like":
        # Case-insensitive substring match. If expected contains %, treat as
        # simple wildcard (% matches any substring).
        if "%" in expected_str:
            import re
            # Escape everything except %, then replace % with .*
            pattern = re.escape(expected_str).replace("%", ".*")
            return bool(re.search(pattern, actual_str, re.IGNORECASE))
        return expected_str.lower() in actual_str.lower()
    elif operator_str == "not like":
        if "%" in expected_str:
            import re
            pattern = re.escape(expected_str).replace("%", ".*")
            return not bool(re.search(pattern, actual_str, re.IGNORECASE))
        return expected_str.lower() not in actual_str.lower()
    elif operator_str == "in":
        values = [v.strip() for v in expected_str.split(",")]
        return actual_str in values
    elif operator_str == "not in":
        values = [v.strip() for v in expected_str.split(",")]
        return actual_str not in values

    return False


# ---------------------------------------------------------------------------
# Hook entry point — called for every document save/submit/cancel site-wide
# ---------------------------------------------------------------------------
def on_doc_event(doc, method):
    """Fired on every document event. Finds matching automations and enqueues."""
    trigger_event = EVENT_MAP.get(method)
    if not trigger_event:
        return

    # Skip during migration to prevent recursion
    if frappe.flags.get("in_migrate"):
        return

    # Re-entry guard: skip if this doc is being saved by an automation action
    guard_key = f"_automation_running_{doc.doctype}_{doc.name}"
    if frappe.flags.get(guard_key):
        return

    try:
        # Query automations: must be enabled AND published
        # Join with Automation Trigger child table to match trigger_doctype and trigger_event
        # Only match DocType Event triggers — Manual and Schedule are dispatched separately
        automations = frappe.db.sql("""
            SELECT DISTINCT a.name
            FROM `tabAutomation` a
            INNER JOIN `tabAutomation Trigger` at
                ON at.parent = a.name
            WHERE a.enabled = 1
                AND a.status = 'Published'
                AND at.trigger_type = 'DocType Event'
                AND at.trigger_doctype = %s
                AND at.trigger_event = %s
        """, (doc.doctype, trigger_event), as_dict=True)

        if not automations:
            return

        # For each matching automation, check conditions from triggers table
        for auto in automations:
            if _evaluate_trigger_conditions(auto.name, doc):
                frappe.enqueue(
                    "automation_builder.dispatcher.execute_automation",
                    queue="short",
                    automation_name=auto.name,
                    ref_doctype=doc.doctype,
                    ref_name=doc.name,
                )
    except Exception:
        frappe.log_error(title="Automation Builder dispatch error")


def _evaluate_trigger_conditions(automation_name, doc):
    """Evaluate all trigger conditions for an automation against the document.

    An automation can have multiple triggers (OR across rows — ANY matching
    trigger row is sufficient). Each trigger row can have multiple conditions
    combined with AND/OR (governed by condition_logic on the row).

    Returns True if ANY trigger row's conditions match.
    """
    triggers = frappe.get_all(
        "Automation Trigger",
        filters={"parent": automation_name},
        fields=["name", "trigger_doctype", "condition_field", "condition_operator",
                "condition_value", "condition_logic"],
    )

    if not triggers:
        return True

    for trigger in triggers:
        # Only evaluate trigger rows that match the document's doctype
        if trigger.trigger_doctype != doc.doctype:
            continue
        # Check if this trigger row has conditions in the new child table
        conditions = frappe.get_all(
            "Automation Trigger Condition",
            filters={"parent": trigger.name},
            fields=["condition_field", "condition_operator", "condition_value"],
            order_by="idx asc",
        )

        if conditions:
            # New path: evaluate condition group with AND/OR logic
            logic = trigger.condition_logic or "All must match"
            if _evaluate_condition_group(doc, conditions, logic):
                return True
        else:
            # Legacy path: single flat condition fields on the trigger row
            if _evaluate_single_condition(doc, trigger):
                return True

    return False


def _evaluate_condition_group(doc, conditions, logic="All must match"):
    """Evaluate a group of conditions with AND/OR logic.

    Args:
        doc: The trigger document
        conditions: list of condition dicts (condition_field, condition_operator, condition_value)
        logic: "All must match" (AND) or "Any must match" (OR)

    Returns:
        bool: True if the group matches according to the logic.
    """
    if not conditions:
        return True

    results = [_evaluate_single_condition(doc, c) for c in conditions]

    if logic == "Any must match":
        return any(results)
    else:
        # Default: "All must match"
        return all(results)


# ---------------------------------------------------------------------------
# Background job — create Automation Run and execute actions via registry
# ---------------------------------------------------------------------------
def execute_automation(automation_name, ref_doctype, ref_name):
    """Background job: create Automation Run record and execute actions."""
    guard_key = f"_automation_running_{ref_doctype}_{ref_name}"
    frappe.flags[guard_key] = True
    try:
        automation = frappe.get_doc("Automation", automation_name)
        doc = frappe.get_doc(ref_doctype, ref_name)

        run = frappe.get_doc(
            {
                "doctype": "Automation Run",
                "automation": automation_name,
                "reference_doctype": ref_doctype,
                "reference_name": ref_name,
                "started_at": frappe.utils.now_datetime(),
            }
        )

        # Use graph_definition (new format), fall back to workflow_json (legacy)
        graph_json = automation.graph_definition or automation.workflow_json
        if not graph_json:
            run.status = "Failed"
            run.error = "No graph_definition found on automation"
            run.ended_at = frappe.utils.now_datetime()
            run.insert(ignore_permissions=True)
            return

        try:
            graph = json.loads(graph_json)
        except (json.JSONDecodeError, TypeError):
            run.status = "Failed"
            run.error = "Invalid graph_definition JSON"
            run.ended_at = frappe.utils.now_datetime()
            run.insert(ignore_permissions=True)
            return

        # Build node map for lookup during execution
        node_map = {n["id"]: n for n in graph.get("nodes", [])}

        context = {"doc": doc, "ref_doctype": ref_doctype, "ref_name": ref_name}

        # Also expose trigger_doctype in context for token resolution
        # In cross-doctype automations, this tells actions which doctype triggered this run
        context["trigger_doctype"] = ref_doctype

        # Find the firing trigger row for tagged-edge routing
        # Match ref_doctype against the automation's trigger rows.
        # Use the loop INDEX (as string) to match against applies_to_triggers
        # on edges, which stores index strings like ["0"], ["1"], etc.
        firing_indices = []
        for idx, t in enumerate(automation.triggers):
            if t.trigger_doctype == ref_doctype:
                firing_indices.append(str(idx))
        if firing_indices:
            context["firing_trigger_name"] = firing_indices[0]

        # Find the first trigger node dynamically (supports multiple triggers)
        trigger_nodes = [n for n in graph.get("nodes", []) if n.get("type") == "trigger"]
        start_id = trigger_nodes[0]["id"] if trigger_nodes else "trigger"
        step_trace = _walk_graph(graph, start_id, context)

        any_failed = False
        step_results = []

        for entry in step_trace:
            entry_type = entry.get("type")
            entry_node_id = entry.get("node_id")

            if entry_type == "branch":
                # Branching decision — log which branch was taken
                step_result = {
                    "step_type": entry.get("node_type", "unknown"),
                    "status": "Success",
                    "branch_taken": entry.get("branch_taken", ""),
                    "output": entry.get("output", ""),
                }
                step_results.append(step_result)

                # Also create Automation Run Step record
                _create_run_step(run, entry, node_map)

            elif entry_type == "action":
                node = node_map.get(entry_node_id, {})
                data = node.get("data", {})
                action_type = data.get("action_type")
                if not action_type:
                    continue
                config = {k: v for k, v in data.items() if k != "action_type"}
                step_result = _execute_action(action_type, config, context)
                step_results.append(step_result)

                if step_result.get("status") == "Failed":
                    any_failed = True

                # Create Automation Run Step record
                _create_run_step(run, {
                    "type": "action",
                    "node_id": entry_node_id,
                    "step_type": action_type,
                    "status": step_result.get("status", "Failed"),
                    "output": step_result.get("output", step_result.get("error", "")),
                }, node_map)

        run.status = "Failed" if any_failed else "Success"
        run.log = json.dumps(step_results, indent=2)
        run.ended_at = frappe.utils.now_datetime()
        run.insert(ignore_permissions=True)

    except Exception as e:
        frappe.log_error(title="Automation Builder execution error")
        try:
            frappe.get_doc(
                {
                    "doctype": "Automation Run",
                    "automation": automation_name,
                    "reference_doctype": ref_doctype,
                    "reference_name": ref_name,
                    "status": "Failed",
                    "error": frappe.get_traceback(),
                    "started_at": frappe.utils.now_datetime(),
                    "ended_at": frappe.utils.now_datetime(),
                }
            ).insert(ignore_permissions=True)
        except Exception:
            frappe.log_error(title="Automation Builder: failed to create error Run record")
    finally:
        frappe.flags[guard_key] = False


def _create_run_step(run, entry, node_map):
    """Create an Automation Run Step child record for a trace entry."""
    node_id = entry.get("node_id", "")
    node = node_map.get(node_id, {})
    node_type = node.get("type", entry.get("type", ""))

    step_type = entry.get("step_type", "")
    if not step_type and node_type == "action":
        step_type = node.get("data", {}).get("action_type", "action")

    step = frappe.get_doc({
        "doctype": "Automation Run Step",
        "node_id": node_id,
        "node_type": node_type,
        "step_type": step_type or node_type,
        "status": entry.get("status", "Success"),
        "branch_taken": entry.get("branch_taken", ""),
        "output": entry.get("output", ""),
        "error": entry.get("error", ""),
    })
    run.append("steps", step)


def _execute_action(action_type, config, context):
    """Look up *action_type* in the registry and call its execute().

    Before executing, checks ``trigger_doctype_select`` in the action config.
    If set to a specific doctype (not "any") and the current run's
    ``ref_doctype`` doesn't match, the action is skipped with a clear message.
    This prevents actions scoped to one trigger doctype from executing when
    a different doctype triggered the automation.
    """
    handler = get_action_type(action_type)
    if handler is None:
        return {
            "step_type": action_type,
            "status": "Failed",
            "error": f"Unknown action type: {action_type}",
        }

    # --- Doctype scoping check ---
    scoped_doctype = config.get("trigger_doctype_select")
    run_doctype = context.get("ref_doctype", "")
    if scoped_doctype and scoped_doctype != "any" and scoped_doctype != run_doctype:
        return {
            "step_type": action_type,
            "status": "Skipped",
            "output": (
                f"Action scoped to {scoped_doctype}, "
                f"this run was triggered by {run_doctype}"
            ),
        }

    try:
        return handler["execute"](context, config)
    except Exception as e:
        return {
            "step_type": action_type,
            "status": "Failed",
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Branching-aware graph walker
# ---------------------------------------------------------------------------
def _evaluate_branching_node(node, context):
    """Evaluate a branching node (IF/Switch) and determine which handle to follow.

    Returns (source_handle, log_message) tuple.
    """
    node_type = node.get("type")
    node_data = node.get("data", {})
    handler = get_action_type(
        "if_condition" if node_type == "if" else "switch_case"
    )
    if handler and "evaluate_branch" in handler:
        return handler["evaluate_branch"](node_data, context)
    return None, f"Unknown branching type: {node_type}"


def _walk_graph(graph, start_id, context=None):
    """Walk graph from start_id, evaluating branching nodes if context is provided.

    Returns a list of trace entries: [{"type": "branch"|"action"|"node", ...}]
    preserving execution order.

    When context is None (structural walk), follows the first outgoing edge of
    every node — used for UI layout or when branching evaluation is not needed.
    When context is provided (execution walk), branching nodes are evaluated and
    only the matching branch is followed.

    When the Trigger node has multiple outgoing edges, only follow edges whose
    ``applies_to_triggers`` list includes the firing trigger row's identifier,
    or edges with ``applies_to_triggers`` = null/empty (meaning "All").
    The firing trigger row identifier is looked up from context["firing_trigger_name"].
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    nodes_map = {n["id"]: n for n in nodes}

    # Build edge map: source_id -> [(sourceHandle, target_id, edge_data), ...]
    edge_map = {}
    for e in edges:
        src = e.get("source")
        tgt = e.get("target")
        sh = e.get("sourceHandle", "")
        if src and tgt:
            edge_map.setdefault(src, []).append((sh, tgt, e))

    trace = []
    seen = set()
    current_id = start_id

    while current_id and current_id not in seen:
        seen.add(current_id)
        node = nodes_map.get(current_id, {})
        node_type = node.get("type")
        outgoing = edge_map.get(current_id, [])

        if not outgoing:
            # Leaf node — end of this branch
            if node_type == "action" and node.get("data", {}).get("action_type"):
                trace.append({"type": "action", "node_id": current_id})
            break

        # Trigger node routing: if outgoing edges have applies_to_triggers, filter
        if node_type == "trigger" and context and outgoing:
            firing_trigger = context.get("firing_trigger_name", "")
            has_any_tags = any(
                (e_data.get("applies_to_triggers") or [])
                for _sh, _tgt, e_data in outgoing
            )
            if has_any_tags:
                filtered = []
                for sh, tgt, e_data in outgoing:
                    applies = e_data.get("applies_to_triggers") or []
                    # null/empty means "All" — always included
                    if not applies or (firing_trigger and firing_trigger in applies):
                        filtered.append((sh, tgt, e_data))
                if not filtered:
                    # No matching edge — dead end for this trigger row
                    trace.append({
                        "type": "branch",
                        "node_id": current_id,
                        "node_type": "trigger",
                        "branch_taken": "skipped",
                        "output": f"No downstream path applies to trigger row '{firing_trigger}'",
                    })
                    break
                outgoing = filtered

        if context and node_type in ("if", "switch"):
            # Doctype scoping: if the branching node is scoped to a specific
            # doctype and this run was triggered by a different doctype,
            # skip the evaluation and follow the first outgoing edge.
            node_data_for_scope = node.get("data", {})
            scoped_dt = node_data_for_scope.get("trigger_doctype_select")
            run_dt = context.get("ref_doctype", "")
            if scoped_dt and scoped_dt != "any" and scoped_dt != run_dt:
                trace.append({
                    "type": "branch",
                    "node_id": current_id,
                    "node_type": node_type,
                    "branch_taken": "skipped",
                    "output": f"{node_type.upper()} scoped to {scoped_dt}, this run was triggered by {run_dt} (skipped)",
                })
                current_id = outgoing[0][1] if outgoing else None
                continue

            # Evaluate branching node
            source_handle, log_msg = _evaluate_branching_node(node, context)
            trace.append({
                "type": "branch",
                "node_id": current_id,
                "node_type": node_type,
                "branch_taken": source_handle,
                "output": log_msg,
            })
            # Follow only the matching edge.
            # source_handle from evaluate_branch is a bare suffix like
            # "if-true", "if-false", "case-0", "default".
            # Edge sourceHandles are prefixed with the node ID, e.g.
            # "if-1-true", "sw-1-case-0".  Match by checking whether the
            # edge handle ends with the expected suffix.
            next_id = None
            for sh, tgt, _e_data in outgoing:
                if sh == source_handle or sh.endswith("-" + source_handle):
                    next_id = tgt
                    break
            if next_id is None and outgoing:
                # Fallback: follow first edge
                next_id = outgoing[0][1]
            current_id = next_id
        elif context and node_type == "condition":
            # Condition nodes evaluate like IF but have a single output.
            # If the condition matches, execution continues downstream.
            # If it doesn't match, execution STOPS (downstream actions are skipped).
            node_data = node.get("data", {})

            # Doctype scoping: if condition is scoped to a specific doctype
            # and this run was triggered by a different doctype, skip the
            # condition (treat as "not applicable") and continue downstream.
            scoped_dt = node_data.get("trigger_doctype_select")
            run_dt = context.get("ref_doctype", "")
            if scoped_dt and scoped_dt != "any" and scoped_dt != run_dt:
                trace.append({
                    "type": "branch",
                    "node_id": current_id,
                    "node_type": "condition",
                    "branch_taken": "skipped",
                    "output": f"Condition scoped to {scoped_dt}, this run was triggered by {run_dt} (skipped)",
                })
                # Follow the single outgoing edge — condition is not applicable
                current_id = outgoing[0][1] if outgoing else None
                continue

            matched = _evaluate_single_condition(context.get("doc"), node_data, context=context)

            trace.append({
                "type": "branch",
                "node_id": current_id,
                "node_type": "condition",
                "branch_taken": "condition-out" if matched else "skipped",
                "output": f"Condition {node_data.get('condition_field', '')} {node_data.get('condition_operator', '')} '{node_data.get('condition_value', '')}' -> {'TRUE' if matched else 'FALSE'} (action skipped)",
            })

            if not matched:
                # Condition failed — stop execution, downstream actions skipped
                break

            # Follow the single outgoing edge (condition matched or no condition set)
            current_id = outgoing[0][1] if outgoing else None
        else:
            # Non-branching node: action, trigger, condition
            if node_type == "action" and node.get("data", {}).get("action_type"):
                trace.append({"type": "action", "node_id": current_id})
            # Follow the single outgoing edge
            current_id = outgoing[0][1] if len(outgoing) == 1 else (outgoing[0][1] if outgoing else None)

    return trace


# ---------------------------------------------------------------------------
# Webhook Trigger — executed via frappe.enqueue from webhook_trigger endpoint
# ---------------------------------------------------------------------------

_WEBHOOK_DOCTYPE = "__webhook__"


def execute_webhook_trigger(automation_name, payload):
    """Execute an automation triggered by an external webhook POST.

    Called via frappe.enqueue — runs in a background worker. The incoming
    JSON payload is wrapped as a frappe._dict so {{trigger.fieldname}} tokens
    resolve via the same resolve_value path used by every other action type.

    context["doc"] = frappe._dict(payload)
    context["trigger_doctype"] = "__webhook__"
    """
    automation = frappe.get_doc("Automation", automation_name)

    graph_json = automation.graph_definition or automation.workflow_json
    if not graph_json:
        return

    run = frappe.get_doc({
        "doctype": "Automation Run",
        "automation": automation_name,
        "reference_doctype": "",
        "reference_name": "",
        "trigger_source": "Webhook",
        "started_at": frappe.utils.now_datetime(),
    })

    try:
        graph = json.loads(graph_json)
    except (json.JSONDecodeError, TypeError):
        run.status = "Failed"
        run.error = "Invalid graph_definition JSON"
        run.ended_at = frappe.utils.now_datetime()
        run.insert(ignore_permissions=True)
        return

    node_map = {n["id"]: n for n in graph.get("nodes", [])}

    # Wrap payload as frappe._dict for .get() and attribute access
    doc = frappe._dict(payload) if isinstance(payload, dict) else frappe._dict()

    context = {
        "doc": doc,
        "ref_doctype": "",
        "ref_name": "",
        "trigger_doctype": _WEBHOOK_DOCTYPE,
    }

    # Set firing_trigger_name to the index of this Webhook trigger row
    # so tagged-edge routing works for Webhook triggers too.
    for t_idx, t_row in enumerate(automation.triggers):
        if t_row.trigger_type == "Webhook":
            context["firing_trigger_name"] = str(t_idx)
            break

    trigger_nodes = [n for n in graph.get("nodes", []) if n.get("type") == "trigger"]
    start_id = trigger_nodes[0]["id"] if trigger_nodes else "trigger"
    step_trace = _walk_graph(graph, start_id, context)

    any_failed = False
    step_results = []

    for entry in step_trace:
        entry_type = entry.get("type")
        entry_node_id = entry.get("node_id")

        if entry_type == "branch":
            step_result = {
                "step_type": entry.get("node_type", "unknown"),
                "status": "Success",
                "branch_taken": entry.get("branch_taken", ""),
                "output": entry.get("output", ""),
            }
            step_results.append(step_result)
            _create_run_step(run, entry, node_map)

        elif entry_type == "action":
            node = node_map.get(entry_node_id, {})
            data = node.get("data", {})
            action_type = data.get("action_type")
            if not action_type:
                continue
            config = {k: v for k, v in data.items() if k != "action_type"}
            step_result = _execute_action(action_type, config, context)
            step_results.append(step_result)

            if step_result.get("status") == "Failed":
                any_failed = True

            _create_run_step(run, {
                "type": "action",
                "node_id": entry_node_id,
                "step_type": action_type,
                "status": step_result.get("status", "Failed"),
                "output": step_result.get("output", step_result.get("error", "")),
            }, node_map)

    run.status = "Failed" if any_failed else "Success"
    run.log = json.dumps(step_results, indent=2)
    run.ended_at = frappe.utils.now_datetime()
    run.insert(ignore_permissions=True)


# ---------------------------------------------------------------------------
# Schedule Trigger — tick function called by scheduler_events
# ---------------------------------------------------------------------------

def check_scheduled_automations():
    """Poll for due Schedule-type automations and execute them.

    Registered in hooks.py as a scheduler_events hook (every 15 minutes).
    Finds Published, enabled automations with a Schedule-type trigger row
    where next_run is null or in the past, executes each through the standard
    execute_automation path, then computes and stores the new next_run.

    Schedule triggers have no triggering document — execute_automation is
    called with an empty-string ref_name and doc=None context. Token
    resolution for {{trigger.*}} degrades to empty strings (no crash).
    """
    if frappe.flags.get("in_migrate"):
        return

    now = frappe.utils.now_datetime()

    # Find due schedule triggers
    due_triggers = frappe.db.sql("""
        SELECT at.name AS trigger_name, at.parent AS automation_name,
               at.schedule_frequency, at.next_run
        FROM `tabAutomation Trigger` at
        INNER JOIN `tabAutomation` a ON a.name = at.parent
        WHERE at.trigger_type = 'Schedule'
            AND a.enabled = 1
            AND a.status = 'Published'
            AND (at.next_run IS NULL OR at.next_run <= %s)
    """, (now,), as_dict=True)

    for trigger in due_triggers:
        try:
            _execute_schedule_trigger(trigger, now)
        except Exception:
            frappe.log_error(
                title=f"Schedule trigger error: {trigger.automation_name}"
            )


def _execute_schedule_trigger(trigger, now):
    """Execute a single schedule trigger and compute next_run."""
    automation_name = trigger.automation_name
    frequency = trigger.schedule_frequency or "Hourly"

    # Execute through the standard path with empty ref_doctype/ref_name
    # (no triggering document for Schedule triggers)
    from automation_builder.action_types._helpers import TRIGGER_DOCTYPE_FIELD

    automation = frappe.get_doc("Automation", automation_name)
    graph_json = automation.graph_definition or automation.workflow_json
    if not graph_json:
        return

    # Create Automation Run with trigger_source='Schedule'
    run = frappe.get_doc({
        "doctype": "Automation Run",
        "automation": automation_name,
        "reference_doctype": "",
        "reference_name": "",
        "trigger_source": "Schedule",
        "started_at": now,
    })

    try:
        graph = json.loads(graph_json)
    except (json.JSONDecodeError, TypeError):
        run.status = "Failed"
        run.error = "Invalid graph_definition JSON"
        run.ended_at = now
        run.insert(ignore_permissions=True)
        return

    node_map = {n["id"]: n for n in graph.get("nodes", [])}

    # Build context with doc=None — {{trigger.*}} resolves to empty
    context = {"doc": None, "ref_doctype": "", "ref_name": "", "trigger_doctype": ""}

    # Set firing_trigger_name to the index of this Schedule trigger row
    # so tagged-edge routing works for Schedule triggers too.
    for t_idx, t_row in enumerate(automation.triggers):
        if t_row.name == trigger.trigger_name:
            context["firing_trigger_name"] = str(t_idx)
            break

    trigger_nodes = [n for n in graph.get("nodes", []) if n.get("type") == "trigger"]
    start_id = trigger_nodes[0]["id"] if trigger_nodes else "trigger"
    step_trace = _walk_graph(graph, start_id, context)

    any_failed = False
    step_results = []

    for entry in step_trace:
        entry_type = entry.get("type")
        entry_node_id = entry.get("node_id")

        if entry_type == "branch":
            step_result = {
                "step_type": entry.get("node_type", "unknown"),
                "status": "Success",
                "branch_taken": entry.get("branch_taken", ""),
                "output": entry.get("output", ""),
            }
            step_results.append(step_result)
            _create_run_step(run, entry, node_map)

        elif entry_type == "action":
            node = node_map.get(entry_node_id, {})
            data = node.get("data", {})
            action_type = data.get("action_type")
            if not action_type:
                continue
            config = {k: v for k, v in data.items() if k != "action_type"}
            step_result = _execute_action(action_type, config, context)
            step_results.append(step_result)

            if step_result.get("status") == "Failed":
                any_failed = True

            _create_run_step(run, {
                "type": "action",
                "node_id": entry_node_id,
                "step_type": action_type,
                "status": step_result.get("status", "Failed"),
                "output": step_result.get("output", step_result.get("error", "")),
            }, node_map)

    run.status = "Failed" if any_failed else "Success"
    run.log = json.dumps(step_results, indent=2)
    run.ended_at = frappe.utils.now_datetime()
    run.insert(ignore_permissions=True)

    # Compute next_run based on frequency
    next_run = _compute_next_run(now, frequency)
    frappe.db.sql(
        "UPDATE `tabAutomation Trigger` SET last_run = %s, next_run = %s WHERE name = %s",
        (now, next_run, trigger.trigger_name),
    )
    frappe.db.commit()


def _compute_next_run(current_time, frequency):
    """Compute the next_run datetime based on frequency."""
    from datetime import timedelta

    if frequency == "Hourly":
        return current_time + timedelta(hours=1)
    elif frequency == "Daily":
        return current_time + timedelta(days=1)
    elif frequency == "Weekly":
        return current_time + timedelta(weeks=1)
    else:
        return current_time + timedelta(hours=1)


# ---------------------------------------------------------------------------
# Legacy helpers — kept for backward compatibility / structural graph walks
# ---------------------------------------------------------------------------
def _extract_actions_from_graph(graph):
    """Extract action configurations from graph, in structural traversal order.

    Follows the first outgoing edge at each node (no branching evaluation).
    Used for graph save/load validation, not execution.
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    if not nodes:
        return []

    node_map = {n["id"]: n for n in nodes}
    graph_adj = _build_edge_graph(edges)
    # Find first trigger node dynamically
    trigger_nodes = [n for n in nodes if n.get("type") == "trigger"]
    start_id = trigger_nodes[0]["id"] if trigger_nodes else "trigger"
    ordered_ids = _walk_graph_bfs(graph_adj, start_id)

    actions = []
    for nid in ordered_ids:
        node = node_map.get(nid)
        if node and node.get("type") == "action" and node.get("data", {}).get("action_type"):
            actions.append({
                "type": node["data"]["action_type"],
                "config": {k: v for k, v in node["data"].items() if k != "action_type"},
            })

    return actions


def _build_edge_graph(edges):
    """Build adjacency list from edges array: {source_id: [target_id, ...]}."""
    graph = {}
    for e in edges:
        src = e.get("source")
        tgt = e.get("target")
        if src and tgt:
            graph.setdefault(src, []).append(tgt)
    return graph


def _walk_graph_bfs(graph, start_id):
    """BFS walk from start_id following ALL outgoing edges.

    Returns ordered list of node IDs in execution order. Handles branching
    (multiple outgoing edges from a single node) by visiting all targets.
    """
    from collections import deque

    visited = []
    seen = set()
    queue = deque([start_id])

    while queue:
        current = queue.popleft()
        if current in seen:
            continue
        seen.add(current)
        visited.append(current)
        targets = graph.get(current, [])
        for t in targets:
            if t not in seen:
                queue.append(t)

    return visited
