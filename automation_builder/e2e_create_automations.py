"""Create 5 end-to-end test automations.

Usage:
    bench --site learning.localhost execute automation_builder.e2e_create_automations.run
"""

import frappe
import json


def run():
    frappe.set_user("Administrator")
    created = []

    # ===================================================================
    # Automation 1: Multi-Trigger IF Router
    # ===================================================================
    name1 = "E2E-IF-Router"
    _cleanup(name1)

    graph1 = {
        "nodes": [
            {"id": "trigger", "type": "trigger", "position": {"x": 250, "y": 50}, "data": {"trigger_doctype": "", "trigger_event": "On Update"}},
            {"id": "if-1", "type": "if", "position": {"x": 250, "y": 200}, "data": {"field_to_check": "__trigger_doctype__", "operator": "=", "value": "Lead", "trigger_doctype_select": ""}},
            {"id": "act-lead", "type": "action", "position": {"x": 100, "y": 350}, "data": {"action_type": "create_document", "trigger_doctype_select": "", "target_doctype": "Note", "field_mapping": [{"target_field": "title", "source_value": "IF-LEAD-FIRED"}]}},
            {"id": "act-todo", "type": "action", "position": {"x": 400, "y": 350}, "data": {"action_type": "create_document", "trigger_doctype_select": "", "target_doctype": "Note", "field_mapping": [{"target_field": "title", "source_value": "IF-TODO-FIRED"}]}},
        ],
        "edges": [
            {"id": "e-trigger-if", "source": "trigger", "target": "if-1", "sourceHandle": "trigger-out", "targetHandle": "if-1-in", "type": "smoothstep", "applies_to_triggers": ["0", "1"]},
            {"id": "e-if-lead", "source": "if-1", "target": "act-lead", "sourceHandle": "if-1-true", "targetHandle": "act-lead-in", "type": "smoothstep"},
            {"id": "e-if-todo", "source": "if-1", "target": "act-todo", "sourceHandle": "if-1-false", "targetHandle": "act-todo-in", "type": "smoothstep"},
        ],
    }

    auto1 = frappe.new_doc("Automation")
    auto1.automation_name = name1
    auto1.status = "Draft"
    auto1.enabled = 1
    auto1.graph_definition = json.dumps(graph1)
    auto1.append("triggers", {"trigger_type": "DocType Event", "trigger_doctype": "Lead", "trigger_event": "On Update"})
    auto1.append("triggers", {"trigger_type": "DocType Event", "trigger_doctype": "ToDo", "trigger_event": "On Update"})
    auto1.insert(ignore_permissions=True)
    frappe.db.commit()
    created.append(name1)
    print(f"[OK] Created {name1}")

    # ===================================================================
    # Automation 2: Lead Status Switch
    # ===================================================================
    name2 = "E2E-Status-Switch"
    _cleanup(name2)

    graph2 = {
        "nodes": [
            {"id": "trigger", "type": "trigger", "position": {"x": 250, "y": 50}, "data": {"trigger_doctype": "", "trigger_event": "On Update"}},
            {"id": "sw-1", "type": "switch", "position": {"x": 250, "y": 200}, "data": {"field_to_check": "status", "cases": [{"case_value": "Open"}, {"case_value": "Contacted"}], "trigger_doctype_select": ""}},
            {"id": "act-open", "type": "action", "position": {"x": 50, "y": 350}, "data": {"action_type": "create_document", "trigger_doctype_select": "", "target_doctype": "Note", "field_mapping": [{"target_field": "title", "source_value": "STATUS-OPEN"}]}},
            {"id": "act-contacted", "type": "action", "position": {"x": 250, "y": 350}, "data": {"action_type": "create_document", "trigger_doctype_select": "", "target_doctype": "Note", "field_mapping": [{"target_field": "title", "source_value": "STATUS-CONTACTED"}]}},
            {"id": "act-other", "type": "action", "position": {"x": 450, "y": 350}, "data": {"action_type": "create_document", "trigger_doctype_select": "", "target_doctype": "Note", "field_mapping": [{"target_field": "title", "source_value": "STATUS-OTHER"}]}},
        ],
        "edges": [
            {"id": "e-trigger-sw", "source": "trigger", "target": "sw-1", "sourceHandle": "trigger-out", "targetHandle": "sw-1-in", "type": "smoothstep"},
            {"id": "e-sw-open", "source": "sw-1", "target": "act-open", "sourceHandle": "sw-1-case-0", "targetHandle": "act-open-in", "type": "smoothstep"},
            {"id": "e-sw-contacted", "source": "sw-1", "target": "act-contacted", "sourceHandle": "sw-1-case-1", "targetHandle": "act-contacted-in", "type": "smoothstep"},
            {"id": "e-sw-other", "source": "sw-1", "target": "act-other", "sourceHandle": "sw-1-default", "targetHandle": "act-other-in", "type": "smoothstep"},
        ],
    }

    auto2 = frappe.new_doc("Automation")
    auto2.automation_name = name2
    auto2.status = "Draft"
    auto2.enabled = 1
    auto2.graph_definition = json.dumps(graph2)
    auto2.append("triggers", {"trigger_type": "DocType Event", "trigger_doctype": "Lead", "trigger_event": "On Update"})
    auto2.insert(ignore_permissions=True)
    frappe.db.commit()
    created.append(name2)
    print(f"[OK] Created {name2}")

    # ===================================================================
    # Automation 3: Cross-Doctype Tagged Paths
    # ===================================================================
    name3 = "E2E-Tagged-Paths"
    _cleanup(name3)

    graph3 = {
        "nodes": [
            {"id": "trigger", "type": "trigger", "position": {"x": 250, "y": 50}, "data": {"trigger_doctype": "", "trigger_event": "On Update"}},
            {"id": "act-lead", "type": "action", "position": {"x": 100, "y": 200}, "data": {"action_type": "create_document", "trigger_doctype_select": "", "target_doctype": "Note", "field_mapping": [{"target_field": "title", "source_value": "LEAD-PATH"}]}},
            {"id": "act-todo", "type": "action", "position": {"x": 400, "y": 200}, "data": {"action_type": "create_document", "trigger_doctype_select": "", "target_doctype": "Note", "field_mapping": [{"target_field": "title", "source_value": "TODO-PATH"}]}},
        ],
        "edges": [
            {"id": "e-trigger-lead", "source": "trigger", "target": "act-lead", "sourceHandle": "trigger-out", "targetHandle": "act-lead-in", "type": "smoothstep", "applies_to_triggers": ["0"]},
            {"id": "e-trigger-todo", "source": "trigger", "target": "act-todo", "sourceHandle": "trigger-out", "targetHandle": "act-todo-in", "type": "smoothstep", "applies_to_triggers": ["1"]},
        ],
    }

    auto3 = frappe.new_doc("Automation")
    auto3.automation_name = name3
    auto3.status = "Draft"
    auto3.enabled = 1
    auto3.graph_definition = json.dumps(graph3)
    auto3.append("triggers", {"trigger_type": "DocType Event", "trigger_doctype": "Lead", "trigger_event": "On Update"})
    auto3.append("triggers", {"trigger_type": "DocType Event", "trigger_doctype": "ToDo", "trigger_event": "On Update"})
    auto3.insert(ignore_permissions=True)
    frappe.db.commit()
    created.append(name3)
    print(f"[OK] Created {name3}")

    # ===================================================================
    # Automation 4: Scheduled Note Creator
    # ===================================================================
    name4 = "E2E-Schedule-Tick"
    _cleanup(name4)

    graph4 = {
        "nodes": [
            {"id": "trigger", "type": "trigger", "position": {"x": 250, "y": 50}, "data": {"trigger_doctype": "", "trigger_event": "On Update"}},
            {"id": "act-note", "type": "action", "position": {"x": 250, "y": 200}, "data": {"action_type": "create_document", "trigger_doctype_select": "", "target_doctype": "Note", "field_mapping": [{"target_field": "title", "source_value": "SCHEDULE-TICK"}]}},
        ],
        "edges": [
            {"id": "e-trigger-note", "source": "trigger", "target": "act-note", "sourceHandle": "trigger-out", "targetHandle": "act-note-in", "type": "smoothstep"},
        ],
    }

    auto4 = frappe.new_doc("Automation")
    auto4.automation_name = name4
    auto4.status = "Draft"
    auto4.enabled = 1
    auto4.graph_definition = json.dumps(graph4)
    auto4.append("triggers", {"trigger_type": "Schedule", "trigger_doctype": "", "trigger_event": "", "schedule_frequency": "Hourly"})
    auto4.insert(ignore_permissions=True)
    frappe.db.commit()
    created.append(name4)
    print(f"[OK] Created {name4}")

    # ===================================================================
    # Automation 5: Webhook Payload Note
    # ===================================================================
    name5 = "E2E-Webhook-Note"
    _cleanup(name5)

    graph5 = {
        "nodes": [
            {"id": "trigger", "type": "trigger", "position": {"x": 250, "y": 50}, "data": {"trigger_doctype": "", "trigger_event": "On Update"}},
            {"id": "act-note", "type": "action", "position": {"x": 250, "y": 200}, "data": {"action_type": "create_document", "trigger_doctype_select": "", "target_doctype": "Note", "field_mapping": [{"target_field": "title", "source_value": "WEBHOOK-{{trigger.title}}"}]}},
        ],
        "edges": [
            {"id": "e-trigger-note", "source": "trigger", "target": "act-note", "sourceHandle": "trigger-out", "targetHandle": "act-note-in", "type": "smoothstep"},
        ],
    }

    auto5 = frappe.new_doc("Automation")
    auto5.automation_name = name5
    auto5.status = "Draft"
    auto5.enabled = 1
    auto5.graph_definition = json.dumps(graph5)
    auto5.append("triggers", {"trigger_type": "Webhook", "trigger_doctype": "", "trigger_event": ""})
    auto5.insert(ignore_permissions=True)
    frappe.db.commit()
    created.append(name5)
    print(f"[OK] Created {name5}")

    # ===================================================================
    # Publish all 5
    # ===================================================================
    for a_name in created:
        frappe.db.set_value("Automation", a_name, "status", "Published")
        frappe.db.set_value("Automation", a_name, "enabled", 1)
    frappe.db.commit()
    for a_name in created:
        print(f"[OK] Published {a_name}")

    # Print webhook token
    auto5_doc = frappe.get_doc("Automation", name5)
    print(f"\nWebhook token for {name5}: {auto5_doc.triggers[0].webhook_token}")
    print(f"All {len(created)} automations created and published successfully.")


def _cleanup(name):
    if frappe.db.exists("Automation", name):
        frappe.delete_doc("Automation", name, force=True)
