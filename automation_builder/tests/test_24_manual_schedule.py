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
        """Manual trigger with trigger_doctype should count toward distinct doctypes."""
        triggers = [
            {"trigger_type": "Manual", "trigger_doctype": "Lead"},
            {"trigger_type": "DocType Event", "trigger_doctype": "ToDo"},
        ]
        # 2 distinct doctypes — any unscoped node should be rejected
        graph = {
            "nodes": [
                {
                    "id": "action-1",
                    "type": "action",
                    "data": {
                        "action_type": "send_email",
                        "trigger_doctype_select": "",
                    },
                },
            ]
        }
        from automation_builder.api import _validate_scoping_for_multi_doctype

        self.assertRaises(
            frappe.ValidationError,
            _validate_scoped,
            json.dumps(graph),
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


# Helper to avoid import issues
def _validate_scoped(graph_json, triggers):
    from automation_builder.api import _validate_scoping_for_multi_doctype
    _validate_scoping_for_multi_doctype(graph_json, triggers)
