"""Stage 23.5 — Doctype-scoping tests.

Part A: Two trigger rows, different doctypes, same-named field + same condition.
Part B: trigger_doctype_select on all action types + Condition node skip.
Part C: Save-time validation for unscoped nodes in multi-doctype automations.
"""

import json
import uuid
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from automation_builder.dispatcher import (
    _evaluate_trigger_conditions,
    execute_automation,
    on_doc_event,
)


class TestSameFieldNameTwoTriggers(IntegrationTestCase):
    """Part A: Two trigger rows with different doctypes but same-named field.

    Both Lead and ToDo have a 'status' Select field. This test creates an
    automation with both trigger rows, each with a condition on 'status',
    and verifies they save, load, and evaluate independently.
    """

    def setUp(self):
        self.auto_name = "TEST-SameField-TwoTriggers"

        # Clean up any prior test data
        if frappe.db.exists("Automation", self.auto_name):
            frappe.delete_doc("Automation", self.auto_name, force=True)

        # Ensure ToDo status options include "New" and "Open"
        # (Lead already has these by default)
        frappe.db.sql("DELETE FROM `tabAutomation Run` WHERE automation = %s", self.auto_name)

    def tearDown(self):
        if frappe.db.exists("Automation", self.auto_name):
            frappe.delete_doc("Automation", self.auto_name, force=True)

    def test_save_load_two_triggers_same_field(self):
        """Two trigger rows (Lead + ToDo), each with condition on 'status = New'.

        After save+load, both trigger rows and their conditions must be
        preserved independently — editing one must not affect the other.
        """
        # 1. Build the automation via API
        from automation_builder.api import save_automation

        graph_def = json.dumps({
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "position": {"x": 0, "y": 0},
                 "data": {"trigger_doctype": "Lead", "trigger_event": "On Update"}},
                {"id": "trigger-2", "type": "trigger", "position": {"x": 300, "y": 0},
                 "data": {"trigger_doctype": "ToDo", "trigger_event": "On Update"}},
                {"id": "cond-1", "type": "condition", "position": {"x": 0, "y": 150},
                 "data": {
                     "condition_field": "status",
                     "condition_operator": "=",
                     "condition_value": "New",
                     "trigger_doctype_select": "Lead",
                 }},
                {"id": "action-1", "type": "action", "position": {"x": 0, "y": 300},
                 "data": {"action_type": "send_email",
                          "recipient": "test@example.com",
                          "subject": "Lead fired",
                          "body": "test",
                          "trigger_doctype_select": "Lead"}},
            ],
            "edges": [
                {"source": "trigger-1", "target": "cond-1"},
                {"source": "cond-1", "target": "action-1"},
            ],
        })

        triggers = [
            {
                "trigger_doctype": "Lead",
                "trigger_event": "On Update",
                "condition_logic": "All must match",
                "conditions": [
                    {"condition_field": "status", "condition_operator": "=", "condition_value": "New"},
                ],
            },
            {
                "trigger_doctype": "ToDo",
                "trigger_event": "On Update",
                "condition_logic": "All must match",
                "conditions": [
                    {"condition_field": "status", "condition_operator": "=", "condition_value": "New"},
                ],
            },
        ]

        result = save_automation(
            automation_name=self.auto_name,
            status="Draft",
            graph_definition=graph_def,
            triggers=triggers,
        )
        auto_name = result["name"]
        self.assertTrue(auto_name)

        # 2. Load back and verify both trigger rows + conditions
        from automation_builder.api import get_automation

        loaded = get_automation(auto_name)
        self.assertEqual(len(loaded["triggers"]), 2)

        # Lead trigger conditions
        lead_trigger = [t for t in loaded["triggers"] if t["trigger_doctype"] == "Lead"][0]
        self.assertEqual(len(lead_trigger["conditions"]), 1)
        self.assertEqual(lead_trigger["conditions"][0]["condition_field"], "status")
        self.assertEqual(lead_trigger["conditions"][0]["condition_operator"], "=")
        self.assertEqual(lead_trigger["conditions"][0]["condition_value"], "New")

        # ToDo trigger conditions
        todo_trigger = [t for t in loaded["triggers"] if t["trigger_doctype"] == "ToDo"][0]
        self.assertEqual(len(todo_trigger["conditions"]), 1)
        self.assertEqual(todo_trigger["conditions"][0]["condition_field"], "status")
        self.assertEqual(todo_trigger["conditions"][0]["condition_operator"], "=")
        self.assertEqual(todo_trigger["conditions"][0]["condition_value"], "New")

    def test_conditions_independent_across_triggers(self):
        """Editing one trigger's conditions must not affect the other.

        Save with different condition values on each trigger row, reload,
        verify each row kept its own value.
        """
        from automation_builder.api import save_automation, get_automation

        triggers = [
            {
                "trigger_doctype": "Lead",
                "trigger_event": "On Update",
                "condition_logic": "All must match",
                "conditions": [
                    {"condition_field": "status", "condition_operator": "=", "condition_value": "Qualified"},
                ],
            },
            {
                "trigger_doctype": "ToDo",
                "trigger_event": "On Update",
                "condition_logic": "All must match",
                "conditions": [
                    {"condition_field": "status", "condition_operator": "=", "condition_value": "Open"},
                ],
            },
        ]

        result = save_automation(
            automation_name=self.auto_name,
            status="Draft",
            triggers=triggers,
        )
        auto_name = result["name"]

        loaded = get_automation(auto_name)
        lead_trigger = [t for t in loaded["triggers"] if t["trigger_doctype"] == "Lead"][0]
        todo_trigger = [t for t in loaded["triggers"] if t["trigger_doctype"] == "ToDo"][0]

        # Lead has "Qualified", ToDo has "Open" — they must not cross-pollinate
        self.assertEqual(lead_trigger["conditions"][0]["condition_value"], "Qualified")
        self.assertEqual(todo_trigger["conditions"][0]["condition_value"], "Open")

    def test_trigger_conditions_evaluate_independently(self):
        """Each trigger row's conditions evaluate against the triggering doc.

        Lead with status=Open matches Lead trigger (status=Open) but NOT ToDo trigger.
        ToDo with status=Open matches ToDo trigger (status=Open) but NOT Lead trigger
        (because Lead trigger checks status=Open which exists on Lead, while ToDo trigger
        also checks status=Open which exists on ToDo).
        """
        # Create the automation with trigger-level conditions
        auto = frappe.get_doc({
            "doctype": "Automation",
            "automation_name": self.auto_name,
            "status": "Published",
            "enabled": 1,
        })
        auto.append("triggers", {
            "trigger_doctype": "Lead",
            "trigger_event": "On Update",
            "condition_logic": "All must match",
        })
        auto.append("triggers", {
            "trigger_doctype": "ToDo",
            "trigger_event": "On Update",
            "condition_logic": "All must match",
        })
        auto.insert(ignore_permissions=True)
        frappe.db.commit()

        # Add conditions to each trigger row — use different values to verify independence
        trigger_names = [t.name for t in auto.triggers]
        for tn, val in zip(trigger_names, ["Open", "Closed"]):
            frappe.get_doc({
                "doctype": "Automation Trigger Condition",
                "parent": tn,
                "parenttype": "Automation Trigger",
                "parentfield": "conditions",
                "condition_field": "status",
                "condition_operator": "=",
                "condition_value": val,
            }).insert(ignore_permissions=True)
        frappe.db.commit()

        # Create a Lead with status=Open.
        # Patch enqueue so on_doc_event (fired by insert) cannot dispatch this
        # doc to leftover published automations — that would create an
        # Automation Run whose dynamic link blocks deletion in cleanup.
        uid = uuid.uuid4().hex[:8]
        with patch("automation_builder.dispatcher.frappe.enqueue"):
            lead = frappe.get_doc({
                "doctype": "Lead",
                "lead_name": f"SameField Test Lead {uid}",
                "status": "Open",
                "email_id": f"samefield-{uid}@test.com",
            })
            lead.insert(ignore_permissions=True)
            frappe.db.commit()

        # Evaluate trigger conditions against the Lead
        result = _evaluate_trigger_conditions(self.auto_name, lead)
        # Lead trigger has status=Open → matches Lead doc (status=Open). TRUE.
        # ToDo trigger has status=Closed → doesn't match Lead (status=Open). FALSE.
        # OR semantics: ANY row matching is sufficient → TRUE.
        self.assertTrue(result)

        # Create a ToDo with status=Open (enqueue patched for same reason)
        with patch("automation_builder.dispatcher.frappe.enqueue"):
            todo = frappe.get_doc({
                "doctype": "ToDo",
                "description": "SameField Test ToDo",
                "status": "Open",
            })
            todo.insert(ignore_permissions=True)
            frappe.db.commit()

        # Evaluate trigger conditions against the ToDo
        result_todo = _evaluate_trigger_conditions(self.auto_name, todo)
        # ToDo trigger has status=Closed → doesn't match ToDo (status=Open). FALSE.
        # Lead trigger has status=Open → doesn't match ToDo fields (status exists but value differs). FALSE.
        # OR semantics: neither matches → FALSE.
        self.assertFalse(result_todo)

        # Cleanup: remove any Automation Run dynamic links first, then the docs
        for dt, dn in (("Lead", lead.name), ("ToDo", todo.name)):
            frappe.db.sql(
                "DELETE FROM `tabAutomation Run` WHERE reference_doctype = %s AND reference_name = %s",
                (dt, dn),
            )
            try:
                frappe.delete_doc(dt, dn, force=True, ignore_permissions=True)
            except Exception:
                frappe.db.rollback()
        frappe.db.commit()


class TestConditionNodeDoctypeScoping(IntegrationTestCase):
    """Part B: Condition node with trigger_doctype_select for skip-on-mismatch.

    When a Condition node is scoped to a specific doctype and the run was
    triggered by a different doctype, the condition should evaluate as
    'not applicable' and the graph walk should SKIP past it.
    """

    def setUp(self):
        self.auto_name = "TEST-ConditionNodeScoping"
        if frappe.db.exists("Automation", self.auto_name):
            frappe.delete_doc("Automation", self.auto_name, force=True)

    def tearDown(self):
        if frappe.db.exists("Automation", self.auto_name):
            frappe.delete_doc("Automation", self.auto_name, force=True)

    def test_condition_node_scoped_skips_on_wrong_trigger(self):
        """Condition node scoped to Lead skips when triggered by ToDo.

        Uses execute_automation directly to avoid picking up other automations
        that match the same doctype+event.
        """
        from automation_builder.api import save_automation

        # Build graph: trigger -> condition (scoped to Lead, field=status=Open) -> action
        graph_def = json.dumps({
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "position": {"x": 0, "y": 0},
                 "data": {"trigger_doctype": "Lead", "trigger_event": "On Update"}},
                {"id": "trigger-2", "type": "trigger", "position": {"x": 300, "y": 0},
                 "data": {"trigger_doctype": "ToDo", "trigger_event": "On Update"}},
                {"id": "cond-1", "type": "condition", "position": {"x": 0, "y": 150},
                 "data": {
                     "condition_field": "status",
                     "condition_operator": "=",
                     "condition_value": "Open",
                     "trigger_doctype_select": "Lead",
                 }},
                {"id": "action-1", "type": "action", "position": {"x": 0, "y": 300},
                 "data": {
                     "action_type": "send_email",
                     "recipient": "test@example.com",
                     "subject": "Condition matched",
                     "body": "test",
                     "trigger_doctype_select": "any",
                 }},
            ],
            "edges": [
                {"source": "trigger-1", "target": "cond-1"},
                {"source": "trigger-2", "target": "cond-1"},
                {"source": "cond-1", "target": "action-1"},
            ],
        })

        triggers = [
            {"trigger_doctype": "Lead", "trigger_event": "On Update", "condition_logic": "All must match", "conditions": []},
            {"trigger_doctype": "ToDo", "trigger_event": "On Update", "condition_logic": "All must match", "conditions": []},
        ]

        result = save_automation(
            automation_name=self.auto_name,
            status="Published",
            graph_definition=graph_def,
            triggers=triggers,
        )
        auto_name = result["name"]

        # Create a ToDo document
        todo = frappe.get_doc({"doctype": "ToDo", "description": "Test ToDo", "status": "Open"})
        todo.insert(ignore_permissions=True)
        frappe.db.commit()

        # Execute directly — the condition is scoped to Lead, run is ToDo → should skip
        execute_automation(auto_name, "ToDo", todo.name)

        runs = frappe.get_all(
            "Automation Run",
            filters={"automation": auto_name, "reference_name": todo.name},
            fields=["name", "status", "log"],
            order_by="creation desc",
            limit_page_length=1,
        )
        self.assertEqual(len(runs), 1, "Run should be created")
        log = json.loads(runs[0].log or "[]")
        # Find the condition step — should be skipped
        cond_steps = [s for s in log if s.get("step_type") == "condition"]
        self.assertTrue(len(cond_steps) > 0, "Condition step should exist in log")
        self.assertEqual(cond_steps[0]["branch_taken"], "skipped")
        self.assertIn("scoped to Lead", cond_steps[0]["output"])

        try:
            todo.delete(ignore_permissions=True)
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()


class TestMultiDoctypeSaveValidation(IntegrationTestCase):
    """Part C: Save-time validation rejects unscoped nodes in multi-doctype automations."""

    def setUp(self):
        self.auto_name = "TEST-SaveValidation"
        if frappe.db.exists("Automation", self.auto_name):
            frappe.delete_doc("Automation", self.auto_name, force=True)

    def tearDown(self):
        if frappe.db.exists("Automation", self.auto_name):
            frappe.delete_doc("Automation", self.auto_name, force=True)

    def test_unscoped_action_rejected_with_multi_doctype_triggers(self):
        """Save is rejected when automation has multiple trigger doctypes
        but an action node reads trigger fields without trigger_doctype_select.
        """
        from automation_builder.api import save_automation

        graph_def = json.dumps({
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "position": {"x": 0, "y": 0},
                 "data": {"trigger_doctype": "Lead", "trigger_event": "On Update"}},
                {"id": "trigger-2", "type": "trigger", "position": {"x": 300, "y": 0},
                 "data": {"trigger_doctype": "ToDo", "trigger_event": "On Update"}},
                {"id": "action-1", "type": "action", "position": {"x": 0, "y": 150},
                 "data": {
                     "action_type": "send_email",
                     "recipient": "test@example.com",
                     "subject": "{{trigger.lead_name}}",
                     # NO trigger_doctype_select — should be rejected
                 }},
            ],
            "edges": [
                {"source": "trigger-1", "target": "action-1"},
                {"source": "trigger-2", "target": "action-1"},
            ],
        })

        triggers = [
            {"trigger_doctype": "Lead", "trigger_event": "On Update", "condition_logic": "All must match", "conditions": []},
            {"trigger_doctype": "ToDo", "trigger_event": "On Update", "condition_logic": "All must match", "conditions": []},
        ]

        # This should raise a validation error
        with self.assertRaises(frappe.exceptions.ValidationError):
            save_automation(
                automation_name=self.auto_name,
                status="Draft",
                graph_definition=graph_def,
                triggers=triggers,
            )

    def test_scoped_action_accepted_with_multi_doctype_triggers(self):
        """Save succeeds when all field-reading nodes are explicitly scoped."""
        from automation_builder.api import save_automation

        graph_def = json.dumps({
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "position": {"x": 0, "y": 0},
                 "data": {"trigger_doctype": "Lead", "trigger_event": "On Update"}},
                {"id": "trigger-2", "type": "trigger", "position": {"x": 300, "y": 0},
                 "data": {"trigger_doctype": "ToDo", "trigger_event": "On Update"}},
                {"id": "action-1", "type": "action", "position": {"x": 0, "y": 150},
                 "data": {
                     "action_type": "send_email",
                     "recipient": "test@example.com",
                     "subject": "Test",
                     "trigger_doctype_select": "Lead",
                 }},
            ],
            "edges": [
                {"source": "trigger-1", "target": "action-1"},
                {"source": "trigger-2", "target": "action-1"},
            ],
        })

        triggers = [
            {"trigger_doctype": "Lead", "trigger_event": "On Update", "condition_logic": "All must match", "conditions": []},
            {"trigger_doctype": "ToDo", "trigger_event": "On Update", "condition_logic": "All must match", "conditions": []},
        ]

        # Should succeed
        result = save_automation(
            automation_name=self.auto_name,
            status="Draft",
            graph_definition=graph_def,
            triggers=triggers,
        )
        self.assertTrue(result["name"])

    def test_single_doctype_no_restriction(self):
        """No validation error when automation has only one trigger doctype."""
        from automation_builder.api import save_automation

        graph_def = json.dumps({
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "position": {"x": 0, "y": 0},
                 "data": {"trigger_doctype": "Lead", "trigger_event": "On Update"}},
                {"id": "trigger-2", "type": "trigger", "position": {"x": 300, "y": 0},
                 "data": {"trigger_doctype": "Lead", "trigger_event": "After Insert"}},
                {"id": "action-1", "type": "action", "position": {"x": 0, "y": 150},
                 "data": {
                     "action_type": "send_email",
                     "recipient": "test@example.com",
                     "subject": "{{trigger.lead_name}}",
                     # No trigger_doctype_select — OK because only one doctype
                 }},
            ],
            "edges": [
                {"source": "trigger-1", "target": "action-1"},
                {"source": "trigger-2", "target": "action-1"},
            ],
        })

        triggers = [
            {"trigger_doctype": "Lead", "trigger_event": "On Update", "condition_logic": "All must match", "conditions": []},
            {"trigger_doctype": "Lead", "trigger_event": "After Insert", "condition_logic": "All must match", "conditions": []},
        ]

        # Should succeed — single doctype, no ambiguity
        result = save_automation(
            automation_name=self.auto_name,
            status="Draft",
            graph_definition=graph_def,
            triggers=triggers,
        )
        self.assertTrue(result["name"])

    def test_any_mode_accepted_with_multi_doctype(self):
        """Save succeeds when node uses trigger_doctype_select='any'."""
        from automation_builder.api import save_automation

        graph_def = json.dumps({
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "position": {"x": 0, "y": 0},
                 "data": {"trigger_doctype": "Lead", "trigger_event": "On Update"}},
                {"id": "trigger-2", "type": "trigger", "position": {"x": 300, "y": 0},
                 "data": {"trigger_doctype": "ToDo", "trigger_event": "On Update"}},
                {"id": "action-1", "type": "action", "position": {"x": 0, "y": 150},
                 "data": {
                     "action_type": "telegram",
                     "chat_id": "123",
                     "message": "Generic message",
                     "trigger_doctype_select": "any",
                 }},
            ],
            "edges": [
                {"source": "trigger-1", "target": "action-1"},
                {"source": "trigger-2", "target": "action-1"},
            ],
        })

        triggers = [
            {"trigger_doctype": "Lead", "trigger_event": "On Update", "condition_logic": "All must match", "conditions": []},
            {"trigger_doctype": "ToDo", "trigger_event": "On Update", "condition_logic": "All must match", "conditions": []},
        ]

        # Should succeed — "any" is explicit scoping
        result = save_automation(
            automation_name=self.auto_name,
            status="Draft",
            graph_definition=graph_def,
            triggers=triggers,
        )
        self.assertTrue(result["name"])
