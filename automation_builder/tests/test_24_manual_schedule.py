"""Stage 24 tests: Manual Trigger and Schedule Trigger.

Covers:
- Migration regression: existing triggers get trigger_type='DocType Event'
- Manual run endpoint: permissions, execution, trigger_source marking
- Schedule tick: frequency computation, next_run scheduling
- Trigger type validation: Manual/Schedule don't require trigger_event
- Scoping addendum: trigger_type in multi-doctype validation
- Token resolution: {{trigger.*}} with doc=None (Schedule scenario)
"""

import frappe
from frappe.tests import IntegrationTestCase
from unittest.mock import patch
import json
import unittest
from datetime import timedelta

from automation_builder.api import (
    save_automation,
    get_automation,
    run_automation_manually,
    _validate_triggers_for_publish,
)
from automation_builder.dispatcher import (
    check_scheduled_automations,
    _compute_next_run,
    _execute_schedule_trigger,
)


class TestStage24MigrationRegression(IntegrationTestCase):
    """Verify migration sets trigger_type='DocType Event' on existing rows."""

    def test_existing_trigger_rows_have_doc_type_event(self):
        """All existing Automation Trigger rows should have a valid trigger_type.

        Post-Stage 24/25 the site legitimately contains DocType Event, Webhook,
        and Schedule trigger rows. DocType Event rows must carry a doc-event
        value; Webhook/Schedule rows have no trigger_event.
        """
        triggers = frappe.get_all(
            "Automation Trigger",
            fields=["name", "trigger_type", "trigger_event"],
        )
        valid_types = ("DocType Event", "Webhook", "Schedule", "Manual")
        for t in triggers:
            self.assertIn(
                t.trigger_type,
                valid_types,
                f"Trigger {t.name} has invalid trigger_type '{t.trigger_type}'",
            )
            if t.trigger_type == "DocType Event":
                # DocType Event rows should still have their original event
                self.assertIn(
                    t.trigger_event,
                    ["After Insert", "On Update", "On Submit", "On Cancel"],
                )

    def test_trigger_type_field_exists(self):
        """Automation Trigger should have a trigger_type field."""
        meta = frappe.get_meta("Automation Trigger")
        fieldnames = [f.fieldname for f in meta.fields]
        self.assertIn("trigger_type", fieldnames)

    def test_trigger_source_field_exists(self):
        """Automation Run should have a trigger_source field."""
        meta = frappe.get_meta("Automation Run")
        fieldnames = [f.fieldname for f in meta.fields]
        self.assertIn("trigger_source", fieldnames)


class TestStage24PublishValidation(IntegrationTestCase):
    """Verify _validate_triggers_for_publish handles Manual and Schedule types."""

    def test_manual_trigger_accepted_without_event(self):
        """Manual trigger row should be accepted even without trigger_event."""
        triggers = [
            {"trigger_type": "Manual", "trigger_doctype": "ToDo"},
        ]
        # Should not raise
        _validate_triggers_for_publish(triggers)

    def test_schedule_trigger_accepted_without_event(self):
        """Schedule trigger row should be accepted even without trigger_event."""
        triggers = [
            {"trigger_type": "Schedule", "trigger_doctype": "ToDo"},
        ]
        # Should not raise
        _validate_triggers_for_publish(triggers)

    def test_doctype_event_still_requires_event(self):
        """DocType Event trigger should still require trigger_event."""
        triggers = [
            {"trigger_type": "DocType Event", "trigger_doctype": "ToDo"},
        ]
        self.assertRaises(
            frappe.ValidationError,
            _validate_triggers_for_publish,
            triggers,
        )

    def test_empty_triggers_rejected(self):
        """Empty triggers list should be rejected when publishing."""
        self.assertRaises(
            frappe.ValidationError,
            _validate_triggers_for_publish,
            [],
        )

    def test_missing_doctype_rejected(self):
        """Trigger row without trigger_doctype should be rejected."""
        triggers = [
            {"trigger_type": "DocType Event", "trigger_event": "On Update"},
        ]
        self.assertRaises(
            frappe.ValidationError,
            _validate_triggers_for_publish,
            triggers,
        )


class TestStage24ScheduleTriggerTick(IntegrationTestCase):
    """Verify the schedule trigger tick function."""

    def test_compute_next_run_hourly(self):
        """Hourly frequency should add 1 hour."""
        now = frappe.utils.now_datetime()
        result = _compute_next_run(now, "Hourly")
        expected = now + timedelta(hours=1)
        self.assertEqual(result, expected)

    def test_compute_next_run_daily(self):
        """Daily frequency should add 1 day."""
        now = frappe.utils.now_datetime()
        result = _compute_next_run(now, "Daily")
        expected = now + timedelta(days=1)
        self.assertEqual(result, expected)

    def test_compute_next_run_weekly(self):
        """Weekly frequency should add 1 week."""
        now = frappe.utils.now_datetime()
        result = _compute_next_run(now, "Weekly")
        expected = now + timedelta(weeks=1)
        self.assertEqual(result, expected)

    def test_scheduler_events_registered(self):
        """hooks.py should have scheduler_events with check_scheduled_automations."""
        from automation_builder import hooks

        self.assertIn("scheduler_events", dir(hooks))
        scheduler = getattr(hooks, "scheduler_events", {})
        self.assertIn("cron", scheduler)
        cron = scheduler["cron"]
        self.assertIn("*/15 * * * *", cron)
        hooks_list = cron["*/15 * * * *"]
        self.assertIn(
            "automation_builder.dispatcher.check_scheduled_automations",
            hooks_list,
        )

    def test_tick_no_due_triggers(self):
        """Tick function should complete without error when no triggers are due."""
        # Should not raise
        check_scheduled_automations()


class TestStage24ScopingAddendum(IntegrationTestCase):
    """Verify trigger_type filtering in multi-doctype validation."""

    def test_schedule_trigger_not_in_doctype_set(self):
        """Schedule triggers (no trigger_doctype) should not add to the doctype set."""
        triggers = [
            {"trigger_type": "Schedule", "trigger_doctype": ""},
            {"trigger_type": "DocType Event", "trigger_doctype": "Lead"},
        ]
        # Only 1 distinct doctype (Lead) — scoping validation should be skipped
        from automation_builder.api import _validate_scoping_for_multi_doctype

        # No graph — should not raise
        _validate_scoping_for_multi_doctype(None, triggers)

    def test_manual_trigger_counts_as_distinct_doctype(self):
        """Manual trigger with trigger_doctype counts toward distinct doctypes
        when action is reachable from multiple trigger rows.
        """
        triggers = [
            {"trigger_type": "Manual", "trigger_doctype": "Lead"},
            {"trigger_type": "DocType Event", "trigger_doctype": "ToDo"},
        ]
        from automation_builder.api import _validate_scoping_for_multi_doctype

        # Action unreachable (no trigger node in graph) → no error
        graph_no_trigger = {
            "nodes": [
                {"id": "action-1", "type": "action", "data": {"action_type": "send_email", "trigger_doctype_select": ""}},
            ]
        }
        _validate_scoping_for_multi_doctype(json.dumps(graph_no_trigger), triggers)

        # Action reachable from both triggers → error
        graph_with_trigger = {
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "position": {"x": 0, "y": 0}, "data": {"trigger_doctype": "Lead"}},
                {"id": "trigger-2", "type": "trigger", "position": {"x": 300, "y": 0}, "data": {"trigger_doctype": "ToDo"}},
                {"id": "action-1", "type": "action", "position": {"x": 0, "y": 150}, "data": {"action_type": "send_email", "trigger_doctype_select": ""}},
            ],
            "edges": [
                {"source": "trigger-1", "target": "action-1"},
                {"source": "trigger-2", "target": "action-1"},
            ],
        }
        self.assertRaises(
            frappe.ValidationError,
            _validate_scoped,
            json.dumps(graph_with_trigger),
            triggers,
        )


class TestStage24TokenResolution(IntegrationTestCase):
    """Verify {{trigger.*}} tokens degrade gracefully when doc=None."""

    def test_trigger_token_resolves_empty_when_no_doc(self):
        """{{trigger.field}} should resolve to empty string when doc is None."""
        from automation_builder.action_types._helpers import resolve_value

        context = {"doc": None, "ref_doctype": "ToDo", "ref_name": "", "trigger_doctype": ""}
        result = resolve_value("{{trigger.name}}", context)
        self.assertEqual(result, "")


class TestStage26ReachabilityScoping(IntegrationTestCase):
    """Verify forward-walk scoping with the canonical multi-Trigger-node shape.

    Canonical design (Stage 29): one Trigger node per trigger row, each with
    its own outgoing edge. graph_node_id links rows to nodes.
    """

    def test_separate_chains_no_ambiguity(self):
        """Two Trigger nodes, each with its own chain:
        Lead chain and ToDo chain are independent — no scoping needed."""
        from automation_builder.api import _validate_scoping_for_multi_doctype

        graph = {
            "nodes": [
                {"id": "trigger-lead", "type": "trigger", "position": {"x": 100, "y": 50},
                 "data": {"trigger_doctype": "Lead", "trigger_event": "On Update"}},
                {"id": "trigger-todo", "type": "trigger", "position": {"x": 400, "y": 50},
                 "data": {"trigger_doctype": "ToDo", "trigger_event": "On Update"}},
                {"id": "action-lead", "type": "action", "position": {"x": 100, "y": 200},
                 "data": {"action_type": "send_email", "trigger_doctype_select": ""}},
                {"id": "action-todo", "type": "action", "position": {"x": 400, "y": 200},
                 "data": {"action_type": "send_email", "trigger_doctype_select": ""}},
            ],
            "edges": [
                {"id": "e-1", "source": "trigger-lead", "target": "action-lead",
                 "sourceHandle": "trigger-lead-out", "targetHandle": "action-lead-in",
                 "type": "smoothstep"},
                {"id": "e-2", "source": "trigger-todo", "target": "action-todo",
                 "sourceHandle": "trigger-todo-out", "targetHandle": "action-todo-in",
                 "type": "smoothstep"},
            ],
        }
        triggers = [
            {"trigger_type": "DocType Event", "trigger_doctype": "Lead", "trigger_event": "On Update",
             "graph_node_id": "trigger-lead"},
            {"trigger_type": "DocType Event", "trigger_doctype": "ToDo", "trigger_event": "On Update",
             "graph_node_id": "trigger-todo"},
        ]
        # Should NOT raise — each action is reachable from only one doctype
        _validate_scoping_for_multi_doctype(json.dumps(graph), triggers)

    def test_separate_chains_narrow_reachability(self):
        """Reachability is per-chain: an unscoped action on ONE trigger's chain
        is fine even when the other chain also has actions."""
        from automation_builder.api import _validate_scoping_for_multi_doctype

        graph = {
            "nodes": [
                {"id": "trigger-lead", "type": "trigger", "position": {"x": 100, "y": 50},
                 "data": {"trigger_doctype": "Lead", "trigger_event": "On Update"}},
                {"id": "trigger-todo", "type": "trigger", "position": {"x": 400, "y": 50},
                 "data": {"trigger_doctype": "ToDo", "trigger_event": "On Update"}},
                {"id": "action-1", "type": "action", "position": {"x": 100, "y": 200},
                 "data": {"action_type": "send_email", "trigger_doctype_select": ""}},
                {"id": "action-2", "type": "action", "position": {"x": 400, "y": 200},
                 "data": {"action_type": "create_document", "trigger_doctype_select": ""}},
            ],
            "edges": [
                {"id": "e-1", "source": "trigger-lead", "target": "action-1",
                 "sourceHandle": "trigger-lead-out", "targetHandle": "action-1-in",
                 "type": "smoothstep"},
                {"id": "e-2", "source": "trigger-todo", "target": "action-2",
                 "sourceHandle": "trigger-todo-out", "targetHandle": "action-2-in",
                 "type": "smoothstep"},
            ],
        }
        triggers = [
            {"trigger_type": "DocType Event", "trigger_doctype": "Lead", "trigger_event": "On Update",
             "graph_node_id": "trigger-lead"},
            {"trigger_type": "DocType Event", "trigger_doctype": "ToDo", "trigger_event": "On Update",
             "graph_node_id": "trigger-todo"},
        ]
        # Each action is reachable from only ONE trigger row — no ambiguity
        _validate_scoping_for_multi_doctype(json.dumps(graph), triggers)

    def test_shared_chain_needs_scoping(self):
        """When both Trigger nodes reach the same unscoped action, rejection is correct."""
        from automation_builder.api import _validate_scoping_for_multi_doctype

        graph = {
            "nodes": [
                {"id": "trigger-lead", "type": "trigger", "position": {"x": 100, "y": 50},
                 "data": {"trigger_doctype": "Lead", "trigger_event": "On Update"}},
                {"id": "trigger-todo", "type": "trigger", "position": {"x": 400, "y": 50},
                 "data": {"trigger_doctype": "ToDo", "trigger_event": "On Update"}},
                {"id": "action-1", "type": "action", "position": {"x": 250, "y": 200},
                 "data": {"action_type": "send_email", "trigger_doctype_select": ""}},
            ],
            "edges": [
                {"id": "e-1", "source": "trigger-lead", "target": "action-1",
                 "sourceHandle": "trigger-lead-out", "targetHandle": "action-1-in",
                 "type": "smoothstep"},
                {"id": "e-2", "source": "trigger-todo", "target": "action-1",
                 "sourceHandle": "trigger-todo-out", "targetHandle": "action-1-in-left",
                 "type": "smoothstep"},
            ],
        }
        triggers = [
            {"trigger_type": "DocType Event", "trigger_doctype": "Lead", "trigger_event": "On Update",
             "graph_node_id": "trigger-lead"},
            {"trigger_type": "DocType Event", "trigger_doctype": "ToDo", "trigger_event": "On Update",
             "graph_node_id": "trigger-todo"},
        ]
        self.assertRaises(
            frappe.ValidationError,
            _validate_scoped,
            json.dumps(graph),
            triggers,
        )

    def test_unreachable_node_not_flagged(self):
        """An action node with no incoming edges is unreachable — no scoping needed."""
        from automation_builder.api import _validate_scoping_for_multi_doctype

        graph = {
            "nodes": [
                {"id": "trigger-lead", "type": "trigger", "position": {"x": 100, "y": 50},
                 "data": {"trigger_doctype": "Lead", "trigger_event": "On Update"}},
                {"id": "trigger-todo", "type": "trigger", "position": {"x": 400, "y": 50},
                 "data": {"trigger_doctype": "ToDo", "trigger_event": "On Update"}},
                {"id": "action-orphan", "type": "action", "position": {"x": 500, "y": 200},
                 "data": {"action_type": "send_email", "trigger_doctype_select": ""}},
            ],
            "edges": [],
        }
        triggers = [
            {"trigger_type": "DocType Event", "trigger_doctype": "Lead", "trigger_event": "On Update",
             "graph_node_id": "trigger-lead"},
            {"trigger_type": "DocType Event", "trigger_doctype": "ToDo", "trigger_event": "On Update",
             "graph_node_id": "trigger-todo"},
        ]
        _validate_scoping_for_multi_doctype(json.dumps(graph), triggers)

    def test_convergence_two_edges_different_rows_rejected(self):
        """Two Trigger nodes converge on the same unscoped action via separate
        edges. Both rows can reach the action, so it is flagged as unscoped
        convergence (the canonical equivalent of the old tagged-edge case)."""
        from automation_builder.api import _validate_scoping_for_multi_doctype

        graph = {
            "nodes": [
                {"id": "trigger-lead", "type": "trigger", "position": {"x": 100, "y": 50},
                 "data": {"trigger_doctype": "Lead", "trigger_event": "On Update"}},
                {"id": "trigger-todo", "type": "trigger", "position": {"x": 400, "y": 50},
                 "data": {"trigger_doctype": "ToDo", "trigger_event": "On Update"}},
                {"id": "act-shared", "type": "action", "position": {"x": 250, "y": 200},
                 "data": {"action_type": "create_document", "trigger_doctype_select": "",
                          "target_doctype": "Note", "field_mapping": []}},
            ],
            "edges": [
                {"id": "e-0", "source": "trigger-lead", "target": "act-shared",
                 "sourceHandle": "trigger-lead-out", "targetHandle": "act-shared-in",
                 "type": "smoothstep"},
                {"id": "e-1", "source": "trigger-todo", "target": "act-shared",
                 "sourceHandle": "trigger-todo-out", "targetHandle": "act-shared-in-left",
                 "type": "smoothstep"},
            ],
        }
        triggers = [
            {"trigger_type": "DocType Event", "trigger_doctype": "Lead", "trigger_event": "On Update",
             "graph_node_id": "trigger-lead"},
            {"trigger_type": "DocType Event", "trigger_doctype": "ToDo", "trigger_event": "On Update",
             "graph_node_id": "trigger-todo"},
        ]
        with self.assertRaises(frappe.ValidationError):
            _validate_scoping_for_multi_doctype(json.dumps(graph), triggers)


def _validate_scoped(graph_json, triggers):
    from automation_builder.api import _validate_scoping_for_multi_doctype
    _validate_scoping_for_multi_doctype(graph_json, triggers)
