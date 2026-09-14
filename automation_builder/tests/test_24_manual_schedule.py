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
        """All existing Automation Trigger rows should have trigger_type='DocType Event' after migration."""
        triggers = frappe.get_all(
            "Automation Trigger",
            fields=["name", "trigger_type", "trigger_event"],
        )
        for t in triggers:
            self.assertEqual(
                t.trigger_type,
                "DocType Event",
                f"Trigger {t.name} should have trigger_type='DocType Event', got '{t.trigger_type}'",
            )
            # Existing triggers should still have their original event
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
    """Verify reachability-based scoping replaces blanket doctype-count."""

    def test_separate_chains_no_ambiguity(self):
        """Two separate chains from Trigger node with tagged edges:
        Lead chain and ToDo chain are independent — no scoping needed."""
        from automation_builder.api import _validate_scoping_for_multi_doctype

        graph = {
            "nodes": [
                {"id": "trigger", "type": "trigger", "position": {"x": 250, "y": 50},
                 "data": {"trigger_doctype": "", "trigger_event": "On Update"}},
                {"id": "action-lead", "type": "action", "position": {"x": 100, "y": 200},
                 "data": {"action_type": "send_email", "trigger_doctype_select": ""}},
                {"id": "action-todo", "type": "action", "position": {"x": 400, "y": 200},
                 "data": {"action_type": "send_email", "trigger_doctype_select": ""}},
            ],
            "edges": [
                {"id": "e-1", "source": "trigger", "target": "action-lead",
                 "sourceHandle": "trigger-out", "type": "smoothstep",
                 "applies_to_triggers": ["0"]},
                {"id": "e-2", "source": "trigger", "target": "action-todo",
                 "sourceHandle": "trigger-out", "type": "smoothstep",
                 "applies_to_triggers": ["1"]},
            ],
        }
        triggers = [
            {"trigger_type": "DocType Event", "trigger_doctype": "Lead", "trigger_event": "On Update"},
            {"trigger_type": "DocType Event", "trigger_doctype": "ToDo", "trigger_event": "On Update"},
        ]
        # Should NOT raise — tagged edges route to separate chains
        _validate_scoping_for_multi_doctype(json.dumps(graph), triggers)

    def test_tagged_edges_narrow_reachability(self):
        """Edges with applies_to_triggers restrict which trigger rows reach downstream."""
        from automation_builder.api import _validate_scoping_for_multi_doctype

        graph = {
            "nodes": [
                {"id": "trigger", "type": "trigger", "position": {"x": 250, "y": 50},
                 "data": {"trigger_doctype": "", "trigger_event": "On Update"}},
                {"id": "action-1", "type": "action", "position": {"x": 250, "y": 200},
                 "data": {"action_type": "send_email", "trigger_doctype_select": ""}},
                {"id": "action-2", "type": "action", "position": {"x": 250, "y": 350},
                 "data": {"action_type": "create_document", "trigger_doctype_select": ""}},
            ],
            "edges": [
                {"id": "e-1", "source": "trigger", "target": "action-1",
                 "type": "smoothstep", "applies_to_triggers": ["0"]},
                {"id": "e-2", "source": "trigger", "target": "action-2",
                 "type": "smoothstep", "applies_to_triggers": ["1"]},
            ],
        }
        triggers = [
            {"trigger_type": "DocType Event", "trigger_doctype": "Lead", "trigger_event": "On Update"},
            {"trigger_type": "DocType Event", "trigger_doctype": "ToDo", "trigger_event": "On Update"},
        ]
        # Each action is reachable from only ONE trigger row — no ambiguity
        _validate_scoping_for_multi_doctype(json.dumps(graph), triggers)

    def test_shared_chain_needs_scoping(self):
        """When both trigger rows reach the same unscoped action, rejection is correct."""
        from automation_builder.api import _validate_scoping_for_multi_doctype

        graph = {
            "nodes": [
                {"id": "trigger", "type": "trigger", "position": {"x": 250, "y": 50},
                 "data": {"trigger_doctype": "", "trigger_event": "On Update"}},
                {"id": "action-1", "type": "action", "position": {"x": 250, "y": 200},
                 "data": {"action_type": "send_email", "trigger_doctype_select": ""}},
            ],
            "edges": [
                {"id": "e-1", "source": "trigger", "target": "action-1",
                 "type": "smoothstep"},
            ],
        }
        triggers = [
            {"trigger_type": "DocType Event", "trigger_doctype": "Lead", "trigger_event": "On Update"},
            {"trigger_type": "DocType Event", "trigger_doctype": "ToDo", "trigger_event": "On Update"},
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
                {"id": "trigger", "type": "trigger", "position": {"x": 250, "y": 50},
                 "data": {"trigger_doctype": "", "trigger_event": "On Update"}},
                {"id": "action-orphan", "type": "action", "position": {"x": 500, "y": 200},
                 "data": {"action_type": "send_email", "trigger_doctype_select": ""}},
            ],
            "edges": [],
        }
        triggers = [
            {"trigger_type": "DocType Event", "trigger_doctype": "Lead", "trigger_event": "On Update"},
            {"trigger_type": "DocType Event", "trigger_doctype": "ToDo", "trigger_event": "On Update"},
        ]
        _validate_scoping_for_multi_doctype(json.dumps(graph), triggers)

    def test_convergence_two_edges_different_rows_rejected(self):
        """Regression: applies_to_triggers values are strings but idx is int.

        Two edges from trigger to the same action, each tagged to a different
        trigger row (e.g. ["0"] and ["1"]). Both rows can reach the action,
        so it should be flagged as unscoped convergence. Previously failed
        because ``0 in ["0"]`` is False in Python.
        """
        from automation_builder.api import _validate_scoping_for_multi_doctype

        graph = {
            "nodes": [
                {"id": "trigger", "type": "trigger", "position": {"x": 250, "y": 50},
                 "data": {"trigger_doctype": "", "trigger_event": "On Update"}},
                {"id": "act-shared", "type": "action", "position": {"x": 0, "y": 0},
                 "data": {"action_type": "create_document", "trigger_doctype_select": "",
                          "target_doctype": "Note", "field_mapping": []}},
            ],
            "edges": [
                {"id": "e-0", "source": "trigger", "target": "act-shared",
                 "sourceHandle": "trigger-out", "targetHandle": "act-shared-in-0",
                 "type": "smoothstep", "applies_to_triggers": ["0"]},
                {"id": "e-1", "source": "trigger", "target": "act-shared",
                 "sourceHandle": "trigger-out", "targetHandle": "act-shared-in-1",
                 "type": "smoothstep", "applies_to_triggers": ["1"]},
            ],
        }
        triggers = [
            {"trigger_type": "DocType Event", "trigger_doctype": "Lead", "trigger_event": "On Update"},
            {"trigger_type": "DocType Event", "trigger_doctype": "ToDo", "trigger_event": "On Update"},
        ]
        with self.assertRaises(frappe.ValidationError):
            _validate_scoping_for_multi_doctype(json.dumps(graph), triggers)


class TestStage26WalkerTaggedEdgeRouting(unittest.TestCase):
    """Verify _walk_graph routes based on applies_to_triggers."""

    def test_walker_follows_tagged_edge_for_matching_trigger(self):
        """Walker follows the edge whose applies_to_triggers includes the firing trigger row."""
        from automation_builder.dispatcher import _walk_graph

        graph = {
            "nodes": [
                {"id": "trigger", "type": "trigger", "position": {"x": 0, "y": 0},
                 "data": {"trigger_doctype": ""}},
                {"id": "action-lead", "type": "action", "position": {"x": 0, "y": 100},
                 "data": {"action_type": "send_email"}},
                {"id": "action-todo", "type": "action", "position": {"x": 200, "y": 100},
                 "data": {"action_type": "create_document"}},
            ],
            "edges": [
                {"id": "e-1", "source": "trigger", "target": "action-lead",
                 "sourceHandle": "trigger-out", "type": "smoothstep",
                 "applies_to_triggers": ["0"]},
                {"id": "e-2", "source": "trigger", "target": "action-todo",
                 "sourceHandle": "trigger-out", "type": "smoothstep",
                 "applies_to_triggers": ["1"]},
            ],
        }
        context = {
            "doc": {"name": "LEAD-001"},
            "ref_doctype": "Lead",
            "ref_name": "LEAD-001",
            "firing_trigger_name": "0",
        }
        trace = _walk_graph(graph, "trigger", context)
        # Should follow only the Lead edge
        node_ids = [e.get("node_id") for e in trace if e.get("node_id")]
        self.assertIn("action-lead", node_ids)
        self.assertNotIn("action-todo", node_ids)

    def test_walker_skips_all_edges_when_no_match(self):
        """Walker skips when no edge matches the firing trigger row."""
        from automation_builder.dispatcher import _walk_graph

        graph = {
            "nodes": [
                {"id": "trigger", "type": "trigger", "position": {"x": 0, "y": 0},
                 "data": {"trigger_doctype": ""}},
                {"id": "action-1", "type": "action", "position": {"x": 0, "y": 100},
                 "data": {"action_type": "send_email"}},
            ],
            "edges": [
                {"id": "e-1", "source": "trigger", "target": "action-1",
                 "sourceHandle": "trigger-out", "type": "smoothstep",
                 "applies_to_triggers": ["0"]},
            ],
        }
        context = {
            "doc": {"name": "TODO-001"},
            "ref_doctype": "ToDo",
            "ref_name": "TODO-001",
            "firing_trigger_name": "1",  # Does not match edge
        }
        trace = _walk_graph(graph, "trigger", context)
        # Should hit the "skipped" branch
        branches = [e for e in trace if e.get("type") == "branch"]
        self.assertTrue(any(b.get("branch_taken") == "skipped" for b in branches))

    def test_walker_follows_null_applies_to_triggers(self):
        """Edges with applies_to_triggers=null (\"All\") are always followed."""
        from automation_builder.dispatcher import _walk_graph

        graph = {
            "nodes": [
                {"id": "trigger", "type": "trigger", "position": {"x": 0, "y": 0},
                 "data": {"trigger_doctype": ""}},
                {"id": "action-1", "type": "action", "position": {"x": 0, "y": 100},
                 "data": {"action_type": "send_email"}},
            ],
            "edges": [
                {"id": "e-1", "source": "trigger", "target": "action-1",
                 "sourceHandle": "trigger-out", "type": "smoothstep",
                 "applies_to_triggers": None},
            ],
        }
        context = {
            "doc": {"name": "LEAD-001"},
            "ref_doctype": "Lead",
            "ref_name": "LEAD-001",
            "firing_trigger_name": "0",
        }
        trace = _walk_graph(graph, "trigger", context)
        node_ids = [e.get("node_id") for e in trace if e.get("node_id")]
        self.assertIn("action-1", node_ids)
def _validate_scoped(graph_json, triggers):
    from automation_builder.api import _validate_scoping_for_multi_doctype
    _validate_scoping_for_multi_doctype(graph_json, triggers)
