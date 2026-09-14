"""Create and test Dual-Condition-Converge automation.

Two triggers (Lead + ToDo), each with a condition node, both wiring
to the same shared action — no IF node.

Usage:
    bench --site learning.localhost execute automation_builder.e2e_dual_condition.create_and_test
"""

import frappe
import json
from datetime import timedelta


def create_and_test():
    frappe.set_user("Administrator")

    name = "Dual-Condition-Converge"

    # --- Cleanup previous run ---
    if frappe.db.exists("Automation", name):
        frappe.delete_doc("Automation", name, force=True)
    _cleanup_notes("DUAL-COND-FIRED")

    # ===================================================================
    # Create the automation
    # ===================================================================
    graph = {
        "nodes": [
            {
                "id": "trigger",
                "type": "trigger",
                "position": {"x": 250, "y": 50},
                "data": {"trigger_doctype": "", "trigger_event": "On Update"},
            },
            {
                "id": "cond-lead",
                "type": "condition",
                "position": {"x": 100, "y": 200},
                "data": {
                    "condition_field": "status",
                    "condition_operator": "=",
                    "condition_value": "Lead",
                    "trigger_doctype_select": "",
                },
            },
            {
                "id": "cond-todo",
                "type": "condition",
                "position": {"x": 400, "y": 200},
                "data": {
                    "condition_field": "priority",
                    "condition_operator": "=",
                    "condition_value": "Low",
                    "trigger_doctype_select": "",
                },
            },
            {
                "id": "act-note",
                "type": "action",
                "position": {"x": 250, "y": 350},
                "data": {
                    "action_type": "create_document",
                    "trigger_doctype_select": "",
                    "target_doctype": "Note",
                    "field_mapping": [
                        {"target_field": "title", "source_value": "DUAL-COND-FIRED"}
                    ],
                },
            },
        ],
        "edges": [
            # trigger → cond-lead (tagged to Lead row 0)
            {
                "id": "e-trigger-cond-lead",
                "source": "trigger",
                "target": "cond-lead",
                "sourceHandle": "trigger-out",
                "targetHandle": "cond-lead-in",
                "type": "smoothstep",
                "applies_to_triggers": ["0"],
            },
            # trigger → cond-todo (tagged to ToDo row 1)
            {
                "id": "e-trigger-cond-todo",
                "source": "trigger",
                "target": "cond-todo",
                "sourceHandle": "trigger-out",
                "targetHandle": "cond-todo-in",
                "type": "smoothstep",
                "applies_to_triggers": ["1"],
            },
            # cond-lead → act-note (shared action)
            {
                "id": "e-cond-lead-act",
                "source": "cond-lead",
                "target": "act-note",
                "sourceHandle": "cond-lead-out",
                "targetHandle": "act-note-in",
                "type": "smoothstep",
            },
            # cond-todo → act-note (shared action)
            {
                "id": "e-cond-todo-act",
                "source": "cond-todo",
                "target": "act-note",
                "sourceHandle": "cond-todo-out",
                "targetHandle": "act-note-in",
                "type": "smoothstep",
            },
        ],
    }

    auto = frappe.new_doc("Automation")
    auto.automation_name = name
    auto.status = "Draft"
    auto.enabled = 1
    auto.graph_definition = json.dumps(graph)
    # Trigger 0: Lead On Update
    auto.append("triggers", {
        "trigger_type": "DocType Event",
        "trigger_doctype": "Lead",
        "trigger_event": "On Update",
    })
    # Trigger 1: ToDo On Update
    auto.append("triggers", {
        "trigger_type": "DocType Event",
        "trigger_doctype": "ToDo",
        "trigger_event": "On Update",
    })
    auto.insert(ignore_permissions=True)
    frappe.db.commit()
    print(f"[OK] Created {name}")

    # Publish
    frappe.db.set_value("Automation", name, "status", "Published")
    frappe.db.commit()
    print(f"[OK] Published {name}")

    # ===================================================================
    # Test 1: Lead with status=Lead → should fire
    # ===================================================================
    print("\n=== TEST 1: Lead status=Lead → should fire ===")
    lead1 = frappe.new_doc("Lead")
    lead1.lead_name = "E2E-DualCond-Lead-Fire"
    lead1.status = "Lead"
    lead1.insert(ignore_permissions=True)
    frappe.db.commit()

    _call_automation(name, lead1.doctype, lead1.name)
    frappe.db.commit()

    notes1 = frappe.db.get_value("Note", {"title": "DUAL-COND-FIRED"}, ["name"], order_by="creation desc")
    if notes1:
        print(f"  [PASS] Note created: {notes1}")
    else:
        print("  [FAIL] No note created")

    frappe.delete_doc("Lead", lead1.name, force=True)

    # ===================================================================
    # Test 2: Lead with status=Open → should NOT fire
    # ===================================================================
    print("\n=== TEST 2: Lead status=Open → should NOT fire ===")
    count_before = frappe.db.count("Note", {"title": "DUAL-COND-FIRED"})

    lead2 = frappe.new_doc("Lead")
    lead2.lead_name = "E2E-DualCond-Lead-NoFire"
    lead2.status = "Open"
    lead2.insert(ignore_permissions=True)
    frappe.db.commit()

    _call_automation(name, lead2.doctype, lead2.name)
    frappe.db.commit()

    count_after = frappe.db.count("Note", {"title": "DUAL-COND-FIRED"})
    if count_after == count_before:
        print("  [PASS] No note created (condition blocked)")
    else:
        print(f"  [FAIL] Note was created unexpectedly (count went from {count_before} to {count_after})")

    frappe.delete_doc("Lead", lead2.name, force=True)

    # ===================================================================
    # Test 3: ToDo priority=Low → should fire
    # ===================================================================
    print("\n=== TEST 3: ToDo priority=Low → should fire ===")
    todo3 = frappe.new_doc("ToDo")
    todo3.description = "E2E-DualCond-Todo-Fire"
    todo3.status = "Open"
    todo3.priority = "Low"
    todo3.insert(ignore_permissions=True)
    frappe.db.commit()

    _call_automation(name, todo3.doctype, todo3.name)
    frappe.db.commit()

    notes3 = frappe.db.get_value("Note", {"title": "DUAL-COND-FIRED"}, ["name"], order_by="creation desc")
    if notes3:
        print(f"  [PASS] Note created: {notes3}")
    else:
        print("  [FAIL] No note created")

    frappe.delete_doc("ToDo", todo3.name, force=True)

    # ===================================================================
    # Test 4: ToDo priority=Medium → should NOT fire
    # ===================================================================
    print("\n=== TEST 4: ToDo priority=Medium → should NOT fire ===")
    count_before = frappe.db.count("Note", {"title": "DUAL-COND-FIRED"})

    todo4 = frappe.new_doc("ToDo")
    todo4.description = "E2E-DualCond-Todo-NoFire"
    todo4.status = "Open"
    todo4.priority = "Medium"
    todo4.insert(ignore_permissions=True)
    frappe.db.commit()

    _call_automation(name, todo4.doctype, todo4.name)
    frappe.db.commit()

    count_after = frappe.db.count("Note", {"title": "DUAL-COND-FIRED"})
    if count_after == count_before:
        print("  [PASS] No note created (condition blocked)")
    else:
        print(f"  [FAIL] Note was created unexpectedly (count went from {count_before} to {count_after})")

    frappe.delete_doc("ToDo", todo4.name, force=True)

    # ===================================================================
    # Check Automation Run records
    # ===================================================================
    print("\n=== Automation Run Records ===")
    runs = frappe.get_all(
        "Automation Run",
        filters={"automation": name},
        fields=["name", "status", "reference_doctype", "reference_name", "trigger_source"],
        order_by="started_at desc",
        limit_page_length=10,
    )
    for r in runs:
        ref = r.reference_name or ""
        print(f"  {r.status:10s} | {r.trigger_source:12s} | {r.reference_doctype or '':10s} | {ref}")

    # ===================================================================
    # Cleanup
    # ===================================================================
    _cleanup_notes("DUAL-COND-FIRED")
    _cleanup_notes("E2E-DualCond")
    frappe.delete_doc("Automation", name, force=True)
    frappe.db.commit()
    print("\n[OK] Cleaned up all test data")


def _call_automation(automation_name, ref_doctype, ref_name):
    """Call execute_automation directly (no background worker)."""
    from automation_builder.dispatcher import execute_automation
    execute_automation(automation_name, ref_doctype, ref_name)


def _cleanup_notes(title_prefix):
    """Delete all Notes whose title starts with the given prefix."""
    notes = frappe.get_all("Note", filters={"title": ["like", f"{title_prefix}%"]}, fields=["name"])
    for n in notes:
        frappe.delete_doc("Note", n.name, force=True)
