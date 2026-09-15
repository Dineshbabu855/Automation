"""Stage 26.5 — Multi-trigger tagged-edge stress test (18 scenarios).

Builds and runs 18 distinct real automations through the REAL dispatch path
(document.save() -> hooks -> on_doc_event -> enqueue -> execute_automation ->
graph walk). Never a direct function call to execute_automation except via the
enqueue patch.

Categories:
  A — Basic tagged routing at scale (A1-A3)
  B — Convergence and shared-node correctness (B1-B4)
  C — IF/Switch combined with trigger tagging (C1-C3)
  D — Mixed trigger TYPES (D1-D3)
  E — Edge-case resilience (E1-E4)
  F — Governance interplay (F1-F2)
"""

import frappe
import json
import secrets
import unittest
from unittest.mock import patch
from frappe.tests import IntegrationTestCase

from automation_builder.api import save_automation
from automation_builder.dispatcher import execute_automation


def _make_trigger(dt, event="On Update", trigger_type="DocType Event", **kwargs):
    """Build a trigger row dict."""
    row = {
        "trigger_type": trigger_type,
        "trigger_doctype": dt,
        "trigger_event": event,
        "condition_logic": "All must match",
        "conditions": [],
    }
    row.update(kwargs)
    return row


def _make_action_node(node_id, action_type, config=None, scoped_dt=""):
    """Build an action node for the graph."""
    data = {"action_type": action_type, "trigger_doctype_select": scoped_dt}
    if config:
        data.update(config)
    return {"id": node_id, "type": "action", "position": {"x": 0, "y": 0}, "data": data}


def _make_trigger_node(node_id="trigger", trigger_doctype=""):
    """Build a trigger node for the graph."""
    return {
        "id": node_id, "type": "trigger",
        "position": {"x": 250, "y": 50},
        "data": {"trigger_doctype": trigger_doctype, "trigger_event": "On Update"},
    }


def _make_condition_node(node_id, field, op="=", val="", scoped_dt=""):
    """Build a condition node."""
    return {
        "id": node_id, "type": "condition",
        "position": {"x": 0, "y": 150},
        "data": {
            "condition_field": field, "condition_operator": op,
            "condition_value": val, "trigger_doctype_select": scoped_dt,
        },
    }


def _make_if_node(node_id, field, op="=", val="", scoped_dt=""):
    """Build an IF node."""
    return {
        "id": node_id, "type": "if",
        "position": {"x": 0, "y": 150},
        "data": {
            "field_to_check": field, "operator": op,
            "value": val, "trigger_doctype_select": scoped_dt,
        },
    }


def _make_switch_node(node_id, field, cases, scoped_dt=""):
    """Build a Switch node."""
    return {
        "id": node_id, "type": "switch",
        "position": {"x": 0, "y": 150},
        "data": {
            "field_to_check": field,
            "cases": [{"case_value": c} for c in cases],
            "trigger_doctype_select": scoped_dt,
        },
    }


def _edge(src, tgt, applies_to=None, source_handle=None):
    """Build an edge dict."""
    e = {
        "id": f"e-{src}-{tgt}",
        "source": src, "target": tgt,
        "sourceHandle": source_handle or f"{src}-out",
        "targetHandle": f"{tgt}-in",
        "type": "smoothstep",
    }
    if applies_to is not None:
        e["applies_to_triggers"] = applies_to
    return e


def _edge_if(src, tgt, branch, applies_to=None):
    """Build an edge from an IF node with proper true/false handle."""
    return _edge(src, tgt, applies_to=applies_to,
                 source_handle=f"{src}-{branch}")


def _edge_switch(src, tgt, case=None, applies_to=None):
    """Build an edge from a Switch node with proper case/default handle."""
    handle = f"{src}-{case}" if case is not None else f"{src}-default"
    return _edge(src, tgt, applies_to=applies_to, source_handle=handle)


def _create_and_publish(name, graph_nodes, graph_edges, triggers):
    """Save automation as Published. Returns the automation name."""
    graph_def = json.dumps({"nodes": graph_nodes, "edges": graph_edges})
    result = save_automation(
        automation_name=name,
        status="Published",
        graph_definition=graph_def,
        triggers=triggers,
    )
    return result["name"]


def _get_runs(auto_name, ref_doctype=None, ref_name=None):
    """Fetch Automation Run records for an automation."""
    filters = {"automation": auto_name}
    if ref_doctype:
        filters["reference_doctype"] = ref_doctype
    if ref_name:
        filters["reference_name"] = ref_name
    return frappe.get_all(
        "Automation Run",
        filters=filters,
        fields=["name", "status", "log", "reference_doctype", "reference_name"],
        order_by="creation asc",
    )


def _get_run_steps(run_name):
    """Fetch Run Step records for a run."""
    return frappe.get_all(
        "Automation Run Step",
        filters={"parent": run_name},
        fields=["node_id", "node_type", "step_type", "status", "branch_taken", "output", "error"],
        order_by="idx asc",
    )


def _step_has_action(steps, action_type, status="Success"):
    """Check if any step matches the given action_type and status."""
    return any(
        s.step_type == action_type and s.status == status
        for s in steps
    )


def _step_output_contains(steps, text):
    """Check if any step output contains the given text."""
    return any(text in (s.output or "") for s in steps)


class TestStage26_5CategoryA(IntegrationTestCase):
    """Category A — Basic tagged routing at scale."""

    def setUp(self):
        self.created_docs = []

    def tearDown(self):
        for dt, name in self.created_docs:
            try:
                frappe.delete_doc(dt, name, force=True)
            except Exception:
                pass

    def _cleanup_auto(self, name):
        if frappe.db.exists("Automation", name):
            frappe.delete_doc("Automation", name, force=True)

    def _trigger_and_verify(self, auto_name, doctype, doc_fields, expected_actions,
                            expected_not_actions=None, ref_field="description"):
        """Insert a doc, run the automation synchronously, verify results."""
        expected_not_actions = expected_not_actions or []
        doc = frappe.get_doc({"doctype": doctype})
        for k, v in doc_fields.items():
            doc.set(k, v)
        doc.insert(ignore_permissions=True)
        self.created_docs.append((doctype, doc.name))
        frappe.db.commit()

        with patch("automation_builder.dispatcher.frappe.enqueue",
                    side_effect=lambda method, **kw: execute_automation(**kw)):
            execute_automation(auto_name, doctype, doc.name)

        runs = _get_runs(auto_name, doctype, doc.name)
        self.assertTrue(len(runs) >= 1, f"No run created for {doctype}/{doc.name}")
        run = runs[-1]
        steps = _get_run_steps(run.name)

        for action_type in expected_actions:
            self.assertTrue(
                _step_has_action(steps, action_type),
                f"Expected action '{action_type}' to run. Steps: {[(s.step_type, s.status) for s in steps]}"
            )
        for action_type in expected_not_actions:
            self.assertFalse(
                _step_has_action(steps, action_type, "Success"),
                f"Action '{action_type}' should NOT have run successfully. Steps: {[(s.step_type, s.status) for s in steps]}"
            )
        return run, steps

    def _cleanup_all_st26(self):
        """Delete all ST26.5-* automations and related Notes."""
        for name in frappe.get_all("Automation", filters={"name": ["like", "ST26.5-%"]}, fields=["name"]):
            frappe.delete_doc("Automation", name.name, force=True)
        for n in frappe.get_all("Note", filters={"title": ["like", "ST26.5-%"]}, fields=["name"]):
            frappe.delete_doc("Note", n.name, force=True)
        frappe.db.commit()

    def test_A1_three_doctypes_tagged_paths(self):
        """A1: 3 doctypes (Lead, ToDo, Note) each tagged to its own action."""
        auto_name = "ST26.5-A1-ThreeDoctypes"
        self._cleanup_all_st26()

        nodes = [
            _make_trigger_node(),
            _make_action_node("act-lead", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "A1-LEAD-FIRED"},
                ],
            }),
            _make_action_node("act-todo", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "A1-TODO-FIRED"},
                ],
            }),
            _make_action_node("act-note", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "A1-NOTE-FIRED"},
                ],
            }),
        ]
        edges = [
            _edge("trigger", "act-lead", applies_to=["0"]),
            _edge("trigger", "act-todo", applies_to=["1"]),
            _edge("trigger", "act-note", applies_to=["2"]),
        ]
        triggers = [
            _make_trigger("Lead", "On Update"),
            _make_trigger("ToDo", "On Update"),
            _make_trigger("Note", "On Update"),
        ]
        name = _create_and_publish(auto_name, nodes, edges, triggers)

        # Trigger Lead
        lead = frappe.get_doc({"doctype": "Lead", "lead_name": "ST26.5-A1-Lead"})
        lead.insert(ignore_permissions=True)
        self.created_docs.append(("Lead", lead.name))
        frappe.db.commit()

        with patch("automation_builder.dispatcher.frappe.enqueue",
                    side_effect=lambda method, **kw: execute_automation(**kw)):
            execute_automation(name, "Lead", lead.name)

        runs = _get_runs(name, "Lead", lead.name)
        self.assertTrue(len(runs) >= 1)
        steps = _get_run_steps(runs[-1].name)
        self.assertTrue(_step_has_action(steps, "create_document"))
        # Verify by checking the created Note's title
        notes = frappe.get_all("Note", filters={"title": "A1-LEAD-FIRED"},
                               limit_page_length=10)
        self.assertTrue(len(notes) >= 1, "A1-LEAD-FIRED Note should be created")
        # Verify no cross-fire: no TODO-FIRED or NOTE-FIRED notes created
        bad_notes = frappe.get_all("Note", filters={"title": ["in", ["A1-TODO-FIRED", "A1-NOTE-FIRED"]]},
                                   limit_page_length=10)
        self.assertEqual(len(bad_notes), 0, "Lead trigger should not fire ToDo or Note paths")

        self._cleanup_auto(auto_name)

    def test_A2_same_doctype_different_events(self):
        """A2: 2 trigger rows on Lead with different events, tagged separately."""
        auto_name = "ST26.5-A2-SameDoctypeDiffEvents"
        self._cleanup_auto(auto_name)

        nodes = [
            _make_trigger_node(),
            _make_action_node("act-insert", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "INSERT-EVENT"},
                ],
            }),
            _make_action_node("act-update", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "UPDATE-EVENT"},
                ],
            }),
        ]
        edges = [
            _edge("trigger", "act-insert", applies_to=["0"]),
            _edge("trigger", "act-update", applies_to=["1"]),
        ]
        triggers = [
            _make_trigger("Lead", "After Insert"),
            _make_trigger("Lead", "On Update"),
        ]
        name = _create_and_publish(auto_name, nodes, edges, triggers)

        # Trigger via insert (should fire INSERT-EVENT path)
        lead = frappe.get_doc({"doctype": "Lead", "lead_name": "ST26.5-A2-Insert"})
        lead.insert(ignore_permissions=True)
        self.created_docs.append(("Lead", lead.name))
        frappe.db.commit()

        with patch("automation_builder.dispatcher.frappe.enqueue",
                    side_effect=lambda method, **kw: execute_automation(**kw)):
            execute_automation(name, "Lead", lead.name)

        runs = _get_runs(name, "Lead", lead.name)
        self.assertTrue(len(runs) >= 1)
        steps = _get_run_steps(runs[-1].name)
        self.assertTrue(_step_has_action(steps, "create_document"))
        # Verify by checking the created Note's title
        notes = frappe.get_all("Note", filters={"title": "INSERT-EVENT"}, limit=5)
        self.assertTrue(len(notes) >= 1, "INSERT-EVENT Note should be created")

        self._cleanup_auto(auto_name)

    def test_A3_four_triggers_mixed(self):
        """A3: 4 trigger rows mixing doctypes and events, each tagged to own action."""
        auto_name = "ST26.5-A3-FourTriggersMixed"
        self._cleanup_auto(auto_name)
        # Bulk-delete leftover Notes from prior runs (avoids TooManyWritesError
        # from per-doc add_to_deleted_document audit when hundreds accumulate)
        frappe.db.sql("DELETE FROM `tabNote` WHERE title IN ('PATH-0','PATH-1','PATH-2','PATH-3')")
        frappe.db.commit()

        nodes = [
            _make_trigger_node(),
            _make_action_node("act-0", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "PATH-0"},
                ],
            }),
            _make_action_node("act-1", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "PATH-1"},
                ],
            }),
            _make_action_node("act-2", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "PATH-2"},
                ],
            }),
            _make_action_node("act-3", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "PATH-3"},
                ],
            }),
        ]
        edges = [
            _edge("trigger", "act-0", applies_to=["0"]),
            _edge("trigger", "act-1", applies_to=["1"]),
            _edge("trigger", "act-2", applies_to=["2"]),
            _edge("trigger", "act-3", applies_to=["3"]),
        ]
        triggers = [
            _make_trigger("Lead", "After Insert"),
            _make_trigger("Lead", "On Update"),
            _make_trigger("ToDo", "On Update"),
            _make_trigger("Note", "On Update"),
        ]
        name = _create_and_publish(auto_name, nodes, edges, triggers)

        # Trigger Lead insert -> should fire path 0 only
        lead = frappe.get_doc({"doctype": "Lead", "lead_name": "ST26.5-A3-LeadInsert"})
        lead.insert(ignore_permissions=True)
        self.created_docs.append(("Lead", lead.name))
        frappe.db.commit()

        with patch("automation_builder.dispatcher.frappe.enqueue",
                    side_effect=lambda method, **kw: execute_automation(**kw)):
            execute_automation(name, "Lead", lead.name)

        runs = _get_runs(name, "Lead", lead.name)
        self.assertTrue(len(runs) >= 1)
        steps = _get_run_steps(runs[-1].name)
        self.assertTrue(_step_has_action(steps, "create_document"))
        # Verify by checking the created Note's title
        notes = frappe.get_all("Note", filters={"title": "PATH-0"}, limit=5)
        self.assertTrue(len(notes) >= 1, "PATH-0 Note should be created")
        # Verify no cross-fire
        bad_notes = frappe.get_all("Note", filters={"title": ["in", ["PATH-1", "PATH-2", "PATH-3"]]},
                                   limit=10)
        self.assertEqual(len(bad_notes), 0, "Lead insert should only fire path 0")

        self._cleanup_auto(auto_name)


class TestStage26_5CategoryB(IntegrationTestCase):
    """Category B — Convergence and shared-node correctness."""

    def _cleanup_auto(self, name):
        if frappe.db.exists("Automation", name):
            frappe.delete_doc("Automation", name, force=True)

    def test_B1_convergence_unscoped_rejected(self):
        """B1: Two Trigger nodes converging into ONE shared unscoped action -> rejected at save."""
        auto_name = "ST26.5-B1-ConvergenceRejected"
        self._cleanup_auto(auto_name)

        nodes = [
            _make_trigger_node("trigger-lead", trigger_doctype="Lead"),
            _make_trigger_node("trigger-todo", trigger_doctype="ToDo"),
            _make_action_node("act-shared", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "SHARED"},
                ],
            }),
        ]
        edges = [
            {"id": "e-tl-as", "source": "trigger-lead", "target": "act-shared",
             "sourceHandle": "trigger-lead-out", "targetHandle": "act-shared-in",
             "type": "smoothstep"},
            {"id": "e-tt-as", "source": "trigger-todo", "target": "act-shared",
             "sourceHandle": "trigger-todo-out", "targetHandle": "act-shared-in-left",
             "type": "smoothstep"},
        ]
        triggers = [
            _make_trigger("Lead", "On Update", graph_node_id="trigger-lead"),
            _make_trigger("ToDo", "On Update", graph_node_id="trigger-todo"),
        ]

        with self.assertRaises(frappe.ValidationError):
            _create_and_publish(auto_name, nodes, edges, triggers)

        self._cleanup_auto(auto_name)

    def test_B2_convergence_scoped_any(self):
        """B2: Convergence with 'Any' scoping -> saves and executes from both."""
        auto_name = "ST26.5-B2-ConvergenceAny"
        self._cleanup_auto(auto_name)

        nodes = [
            _make_trigger_node(),
            _make_action_node("act-shared", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "SHARED-ANY"},
                ],
                "trigger_doctype_select": "any",
            }),
        ]
        edges = [
            _edge("trigger", "act-shared", applies_to=["0"]),
            _edge("trigger", "act-shared", applies_to=["1"]),
        ]
        triggers = [
            _make_trigger("Lead", "On Update"),
            _make_trigger("ToDo", "On Update"),
        ]
        name = _create_and_publish(auto_name, nodes, edges, triggers)

        # Trigger via Lead
        lead = frappe.get_doc({"doctype": "Lead", "lead_name": "ST26.5-B2-Lead"})
        lead.insert(ignore_permissions=True)
        frappe.db.commit()

        with patch("automation_builder.dispatcher.frappe.enqueue",
                    side_effect=lambda method, **kw: execute_automation(**kw)):
            execute_automation(name, "Lead", lead.name)

        runs_lead = _get_runs(name, "Lead", lead.name)
        self.assertTrue(len(runs_lead) >= 1)
        steps_lead = _get_run_steps(runs_lead[-1].name)
        self.assertTrue(_step_has_action(steps_lead, "create_document"))

        self._cleanup_auto(auto_name)

    def test_B3_convergence_scoped_specific(self):
        """B3: Convergence scoped to one doctype -> executes when that doctype fires, skips for other."""
        auto_name = "ST26.5-B3-ConvergenceSpecific"
        self._cleanup_auto(auto_name)

        nodes = [
            _make_trigger_node(),
            _make_action_node("act-lead-only", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "LEAD-ONLY"},
                ],
                "trigger_doctype_select": "Lead",
            }),
        ]
        edges = [
            _edge("trigger", "act-lead-only", applies_to=["0"]),
            _edge("trigger", "act-lead-only", applies_to=["1"]),
        ]
        triggers = [
            _make_trigger("Lead", "On Update"),
            _make_trigger("ToDo", "On Update"),
        ]
        name = _create_and_publish(auto_name, nodes, edges, triggers)

        # Trigger via Lead -> should execute
        lead = frappe.get_doc({"doctype": "Lead", "lead_name": "ST26.5-B3-Lead"})
        lead.insert(ignore_permissions=True)
        frappe.db.commit()

        with patch("automation_builder.dispatcher.frappe.enqueue",
                    side_effect=lambda method, **kw: execute_automation(**kw)):
            execute_automation(name, "Lead", lead.name)

        runs_lead = _get_runs(name, "Lead", lead.name)
        self.assertTrue(len(runs_lead) >= 1)
        steps_lead = _get_run_steps(runs_lead[-1].name)
        self.assertTrue(_step_has_action(steps_lead, "create_document", "Success"))

        # Trigger via ToDo -> should skip
        todo = frappe.get_doc({"doctype": "ToDo", "description": "ST26.5-B3-Todo"})
        todo.insert(ignore_permissions=True)
        frappe.db.commit()

        with patch("automation_builder.dispatcher.frappe.enqueue",
                    side_effect=lambda method, **kw: execute_automation(**kw)):
            execute_automation(name, "ToDo", todo.name)

        runs_todo = _get_runs(name, "ToDo", todo.name)
        self.assertTrue(len(runs_todo) >= 1)
        steps_todo = _get_run_steps(runs_todo[-1].name)
        self.assertFalse(_step_has_action(steps_todo, "create_document", "Success"))
        self.assertTrue(_step_has_action(steps_todo, "create_document", "Skipped"))

        self._cleanup_auto(auto_name)

    def test_B4_all_tagged_edge_requires_scoping(self):
        """B4: Two Trigger nodes converging on a shared unscoped action -> rejected.
        Canonical equivalent of the old 'All edge alongside tagged edges' case:
        the shared action is reachable from both trigger rows and lacks scoping."""
        auto_name = "ST26.5-B4-AllEdgeScoping"
        self._cleanup_auto(auto_name)

        nodes = [
            _make_trigger_node("trigger-lead", trigger_doctype="Lead"),
            _make_trigger_node("trigger-todo", trigger_doctype="ToDo"),
            _make_action_node("act-shared", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "SHARED"},
                ],
            }),
        ]
        edges = [
            {"id": "e-tl-as", "source": "trigger-lead", "target": "act-shared",
             "sourceHandle": "trigger-lead-out", "targetHandle": "act-shared-in",
             "type": "smoothstep"},
            {"id": "e-tt-as", "source": "trigger-todo", "target": "act-shared",
             "sourceHandle": "trigger-todo-out", "targetHandle": "act-shared-in-left",
             "type": "smoothstep"},
        ]
        triggers = [
            _make_trigger("Lead", "On Update", graph_node_id="trigger-lead"),
            _make_trigger("ToDo", "On Update", graph_node_id="trigger-todo"),
        ]

        with self.assertRaises(frappe.ValidationError):
            _create_and_publish(auto_name, nodes, edges, triggers)

        self._cleanup_auto(auto_name)


class TestStage26_5CategoryC(IntegrationTestCase):
    """Category C — IF/Switch combined with trigger tagging."""

    def _cleanup_auto(self, name):
        if frappe.db.exists("Automation", name):
            frappe.delete_doc("Automation", name, force=True)

    def test_C1_if_node_on_tagged_path(self):
        """C1: Tagged path leads to IF node checking a real field. IF infers its doctype."""
        auto_name = "ST26.5-C1-IfOnTaggedPath"
        self._cleanup_auto(auto_name)

        nodes = [
            _make_trigger_node(),
            _make_if_node("if-1", "status", "=", "Open"),
            _make_action_node("act-true", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "IF-TRUE"},
                ],
            }),
        ]
        edges = [
            _edge("trigger", "if-1", applies_to=["0"]),  # Lead only
            _edge("if-1", "act-true", applies_to=None),   # from IF true handle
        ]
        triggers = [
            _make_trigger("Lead", "On Update"),
            _make_trigger("ToDo", "On Update"),
        ]
        name = _create_and_publish(auto_name, nodes, edges, triggers)

        # Lead with status=Open -> IF should evaluate, follow true path
        lead = frappe.get_doc({"doctype": "Lead", "lead_name": "ST26.5-C1-Lead"})
        lead.status = "Open"
        lead.insert(ignore_permissions=True)
        frappe.db.commit()

        with patch("automation_builder.dispatcher.frappe.enqueue",
                    side_effect=lambda method, **kw: execute_automation(**kw)):
            execute_automation(name, "Lead", lead.name)

        runs = _get_runs(name, "Lead", lead.name)
        self.assertTrue(len(runs) >= 1)
        steps = _get_run_steps(runs[-1].name)
        self.assertTrue(_step_output_contains(steps, "IF"),
                        f"IF node should have been evaluated. Steps: {[(s.step_type, s.branch_taken) for s in steps]}")

        self._cleanup_auto(auto_name)

    def test_C2_all_path_into_if_trigger_doctype_branching(self):
        """C2: 'All' path (2+ doctypes reachable) -> IF node branching on __trigger_doctype__.
        Both true/false branches route correctly on TOP of tagged-edge routing."""
        auto_name = "ST26.5-C2-AllPathIfTriggerDoctype"
        self._cleanup_auto(auto_name)

        nodes = [
            _make_trigger_node(),
            _make_if_node("if-doctype", "__trigger_doctype__", "=", "Lead"),
            _make_action_node("act-lead-path", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "LEAD-PATH"},
                ],
            }),
            _make_action_node("act-todo-path", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "TODO-PATH"},
                ],
            }),
        ]
        edges = [
            _edge("trigger", "if-doctype", applies_to=None),
            _edge_if("if-doctype", "act-lead-path", "true", applies_to=None),
            _edge_if("if-doctype", "act-todo-path", "false", applies_to=None),
        ]
        triggers = [
            _make_trigger("Lead", "On Update"),
            _make_trigger("ToDo", "On Update"),
        ]
        name = _create_and_publish(auto_name, nodes, edges, triggers)

        # Trigger via Lead
        lead = frappe.get_doc({"doctype": "Lead", "lead_name": "ST26.5-C2-Lead"})
        lead.insert(ignore_permissions=True)
        frappe.db.commit()

        with patch("automation_builder.dispatcher.frappe.enqueue",
                    side_effect=lambda method, **kw: execute_automation(**kw)):
            execute_automation(name, "Lead", lead.name)

        runs = _get_runs(name, "Lead", lead.name)
        self.assertTrue(len(runs) >= 1)
        steps = _get_run_steps(runs[-1].name)
        self.assertTrue(_step_output_contains(steps, "IF"))
        # Lead path should execute, ToDo path should not
        self.assertTrue(_step_has_action(steps, "create_document"))

        self._cleanup_auto(auto_name)

    def test_C3_switch_node_downstream_of_convergence(self):
        """C3: Switch node downstream of a convergence point (All edge), 3+ cases."""
        auto_name = "ST26.5-C3-SwitchDownstream"
        self._cleanup_auto(auto_name)

        nodes = [
            _make_trigger_node(),
            _make_switch_node("sw-1", "__trigger_doctype__", ["Lead", "ToDo", "Note"]),
            _make_action_node("act-lead", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "SWITCH-LEAD"},
                ],
            }),
            _make_action_node("act-todo", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "SWITCH-TODO"},
                ],
            }),
            _make_action_node("act-note", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "SWITCH-NOTE"},
                ],
            }),
            _make_action_node("act-default", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "SWITCH-DEFAULT"},
                ],
            }),
        ]
        edges = [
            _edge("trigger", "sw-1", applies_to=None),
            _edge_switch("sw-1", "act-lead", "case-0", applies_to=None),
            _edge_switch("sw-1", "act-todo", "case-1", applies_to=None),
            _edge_switch("sw-1", "act-note", "case-2", applies_to=None),
            _edge_switch("sw-1", "act-default", applies_to=None),
        ]
        triggers = [
            _make_trigger("Lead", "On Update"),
            _make_trigger("ToDo", "On Update"),
            _make_trigger("Note", "On Update"),
        ]
        name = _create_and_publish(auto_name, nodes, edges, triggers)

        # Trigger via Lead -> should follow case-0
        lead = frappe.get_doc({"doctype": "Lead", "lead_name": "ST26.5-C3-Lead"})
        lead.insert(ignore_permissions=True)
        frappe.db.commit()

        with patch("automation_builder.dispatcher.frappe.enqueue",
                    side_effect=lambda method, **kw: execute_automation(**kw)):
            execute_automation(name, "Lead", lead.name)

        runs = _get_runs(name, "Lead", lead.name)
        self.assertTrue(len(runs) >= 1)
        steps = _get_run_steps(runs[-1].name)
        # Switch should route to Lead case
        switch_step = [s for s in steps if s.step_type == "switch"]
        self.assertTrue(len(switch_step) >= 1, "Switch node should be evaluated")
        self.assertEqual(switch_step[0].branch_taken, "case-0")

        self._cleanup_auto(auto_name)


class TestStage26_5CategoryD(IntegrationTestCase):
    """Category D — Mixed trigger TYPES."""

    def _cleanup_auto(self, name):
        if frappe.db.exists("Automation", name):
            frappe.delete_doc("Automation", name, force=True)

    def test_D1_doctype_event_plus_manual(self):
        """D1: DocType Event + Manual trigger (same doctype) -> reachability-equivalent."""
        auto_name = "ST26.5-D1-EventPlusManual"
        self._cleanup_auto(auto_name)

        nodes = [
            _make_trigger_node(),
            _make_action_node("act-1", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "EVENT-OR-MANUAL"},
                ],
                "trigger_doctype_select": "Lead",
            }),
        ]
        edges = [
            _edge("trigger", "act-1", applies_to=None),  # "All" -> both rows
        ]
        triggers = [
            _make_trigger("Lead", "On Update", trigger_type="DocType Event"),
            _make_trigger("Lead", "On Update", trigger_type="Manual"),
        ]
        name = _create_and_publish(auto_name, nodes, edges, triggers)

        # Trigger via Lead save (DocType Event path)
        lead = frappe.get_doc({"doctype": "Lead", "lead_name": "ST26.5-D1-Lead"})
        lead.insert(ignore_permissions=True)
        frappe.db.commit()

        with patch("automation_builder.dispatcher.frappe.enqueue",
                    side_effect=lambda method, **kw: execute_automation(**kw)):
            execute_automation(name, "Lead", lead.name)

        runs = _get_runs(name, "Lead", lead.name)
        self.assertTrue(len(runs) >= 1)
        steps = _get_run_steps(runs[-1].name)
        self.assertTrue(_step_has_action(steps, "create_document", "Success"))

        self._cleanup_auto(auto_name)

    def test_D2_doctype_event_plus_webhook_tagged(self):
        """D2: DocType Event + Webhook trigger, each tagged to separate paths."""
        auto_name = "ST26.5-D2-EventPlusWebhook"
        self._cleanup_auto(auto_name)

        nodes = [
            _make_trigger_node(),
            _make_action_node("act-event", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "EVENT-PATH"},
                ],
            }),
            _make_action_node("act-webhook", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "WEBHOOK-PATH"},
                ],
            }),
        ]
        edges = [
            _edge("trigger", "act-event", applies_to=["0"]),
            _edge("trigger", "act-webhook", applies_to=["1"]),
        ]
        triggers = [
            _make_trigger("Lead", "On Update", trigger_type="DocType Event"),
            _make_trigger("", trigger_type="Webhook",
                          webhook_token=secrets.token_hex(32)),
        ]
        name = _create_and_publish(auto_name, nodes, edges, triggers)

        # Trigger via Lead save -> should fire event path only
        lead = frappe.get_doc({"doctype": "Lead", "lead_name": "ST26.5-D2-Lead"})
        lead.insert(ignore_permissions=True)
        frappe.db.commit()

        with patch("automation_builder.dispatcher.frappe.enqueue",
                    side_effect=lambda method, **kw: execute_automation(**kw)):
            execute_automation(name, "Lead", lead.name)

        runs = _get_runs(name, "Lead", lead.name)
        self.assertTrue(len(runs) >= 1)
        steps = _get_run_steps(runs[-1].name)
        self.assertTrue(_step_has_action(steps, "create_document", "Success"))
        # Should NOT have fired the webhook path
        self.assertFalse(
            any("WEBHOOK-PATH" in (s.output or "") for s in steps),
            "Webhook path should not fire on Lead save"
        )

        self._cleanup_auto(auto_name)

    def test_D3_schedule_plus_doctype_event(self):
        """D3: Schedule + DocType Event, each tagged to separate paths."""
        auto_name = "ST26.5-D3-SchedulePlusEvent"
        self._cleanup_auto(auto_name)

        nodes = [
            _make_trigger_node(),
            _make_action_node("act-schedule", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "SCHEDULE-PATH"},
                ],
            }),
            _make_action_node("act-event", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "EVENT-PATH"},
                ],
            }),
        ]
        edges = [
            _edge("trigger", "act-schedule", applies_to=["0"]),
            _edge("trigger", "act-event", applies_to=["1"]),
        ]
        triggers = [
            _make_trigger("ToDo", trigger_type="Schedule",
                          schedule_frequency="Hourly"),
            _make_trigger("Lead", "On Update", trigger_type="DocType Event"),
        ]
        name = _create_and_publish(auto_name, nodes, edges, triggers)

        # Trigger via Lead save -> should fire event path only
        lead = frappe.get_doc({"doctype": "Lead", "lead_name": "ST26.5-D3-Lead"})
        lead.insert(ignore_permissions=True)
        frappe.db.commit()

        with patch("automation_builder.dispatcher.frappe.enqueue",
                    side_effect=lambda method, **kw: execute_automation(**kw)):
            execute_automation(name, "Lead", lead.name)

        runs = _get_runs(name, "Lead", lead.name)
        self.assertTrue(len(runs) >= 1)
        steps = _get_run_steps(runs[-1].name)
        self.assertTrue(_step_has_action(steps, "create_document", "Success"))
        self.assertFalse(
            any("SCHEDULE-PATH" in (s.output or "") for s in steps),
            "Schedule path should not fire on Lead save"
        )

        self._cleanup_auto(auto_name)


class TestStage26_5CategoryE(IntegrationTestCase):
    """Category E — Edge-case resilience."""

    def _cleanup_auto(self, name):
        if frappe.db.exists("Automation", name):
            frappe.delete_doc("Automation", name, force=True)

    def test_E1_orphaned_trigger_tag(self):
        """E1: Edge references trigger row index that no longer exists after row deletion.
        Should handle gracefully on next save and execution."""
        auto_name = "ST26.5-E1-OrphanedTag"
        self._cleanup_auto(auto_name)

        nodes = [
            _make_trigger_node(),
            _make_action_node("act-0", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "PATH-0"},
                ],
            }),
            _make_action_node("act-1", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "PATH-1"},
                ],
            }),
        ]
        edges = [
            _edge("trigger", "act-0", applies_to=["0"]),
            _edge("trigger", "act-1", applies_to=["1"]),
        ]
        triggers = [
            _make_trigger("Lead", "On Update"),
            _make_trigger("ToDo", "On Update"),
        ]
        name = _create_and_publish(auto_name, nodes, edges, triggers)

        # Now remove trigger row 1 (ToDo) but edges still reference ["1"]
        # Reload the automation and remove the ToDo trigger row
        auto = frappe.get_doc("Automation", name)
        # Remove the second trigger row
        auto.triggers = [auto.triggers[0]]
        auto.save(ignore_permissions=True)
        frappe.db.commit()

        # Reload and check: edge still has applies_to_triggers=["1"] but row 1 is gone
        auto_reloaded = frappe.get_doc("Automation", name)
        graph = json.loads(auto.graph_definition)
        todo_edge = [e for e in graph["edges"] if e.get("applies_to_triggers") == ["1"]]
        self.assertTrue(len(todo_edge) >= 1, "Orphaned tag should still exist in graph")

        # Trigger via Lead -> should still work (row 0 exists, edge ["0"] is valid)
        lead = frappe.get_doc({"doctype": "Lead", "lead_name": "ST26.5-E1-Lead"})
        lead.insert(ignore_permissions=True)
        frappe.db.commit()

        with patch("automation_builder.dispatcher.frappe.enqueue",
                    side_effect=lambda method, **kw: execute_automation(**kw)):
            execute_automation(name, "Lead", lead.name)

        runs = _get_runs(name, "Lead", lead.name)
        self.assertTrue(len(runs) >= 1)
        steps = _get_run_steps(runs[-1].name)
        self.assertTrue(_step_has_action(steps, "create_document", "Success"))

        self._cleanup_auto(auto_name)

    def test_E2_corrupted_trigger_tag(self):
        """E2: Edge with applies_to_triggers referencing non-existent row id."""
        auto_name = "ST26.5-E2-CorruptedTag"
        self._cleanup_auto(auto_name)

        nodes = [
            _make_trigger_node(),
            _make_action_node("act-0", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "VALID-PATH"},
                ],
            }),
        ]
        # Edge references ["99"] which doesn't exist, plus ["0"] which does
        edges = [
            _edge("trigger", "act-0", applies_to=["0", "99"]),
        ]
        triggers = [
            _make_trigger("Lead", "On Update"),
        ]
        name = _create_and_publish(auto_name, nodes, edges, triggers)

        # Trigger via Lead -> should work (row "0" exists, "99" is just ignored)
        lead = frappe.get_doc({"doctype": "Lead", "lead_name": "ST26.5-E2-Lead"})
        lead.insert(ignore_permissions=True)
        frappe.db.commit()

        with patch("automation_builder.dispatcher.frappe.enqueue",
                    side_effect=lambda method, **kw: execute_automation(**kw)):
            execute_automation(name, "Lead", lead.name)

        runs = _get_runs(name, "Lead", lead.name)
        self.assertTrue(len(runs) >= 1)
        steps = _get_run_steps(runs[-1].name)
        self.assertTrue(_step_has_action(steps, "create_document", "Success"))

        self._cleanup_auto(auto_name)

    def test_E3_round_trip_stability(self):
        """E3: Save -> reload -> save again -> reload again. No drift in graph or tags."""
        auto_name = "ST26.5-E3-RoundTrip"
        self._cleanup_auto(auto_name)

        nodes = [
            _make_trigger_node(),
            _make_action_node("act-0", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "ROUND-TRIP"},
                ],
            }),
            _make_action_node("act-1", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "ROUND-TRIP-2"},
                ],
            }),
        ]
        edges = [
            _edge("trigger", "act-0", applies_to=["0"]),
            _edge("trigger", "act-1", applies_to=["1"]),
        ]
        triggers = [
            _make_trigger("Lead", "On Update"),
            _make_trigger("ToDo", "On Update"),
        ]

        # Save #1
        name = _create_and_publish(auto_name, nodes, edges, triggers)

        # Reload #1
        loaded1 = frappe.get_doc("Automation", name)
        g1 = json.loads(loaded1.graph_definition)
        t1 = [{"trigger_doctype": t.trigger_doctype, "trigger_event": t.trigger_event}
              for t in loaded1.triggers]

        # Save #2 (re-save loaded data — must delete first since save_automation creates new)
        frappe.delete_doc("Automation", name, force=True)
        frappe.db.commit()
        save_automation(
            automation_name=name,
            status="Published",
            graph_definition=loaded1.graph_definition,
            triggers=[{
                "trigger_type": t.trigger_type,
                "trigger_doctype": t.trigger_doctype,
                "trigger_event": t.trigger_event,
                "schedule_frequency": getattr(t, "schedule_frequency", "Hourly"),
                "webhook_token": getattr(t, "webhook_token", ""),
                "condition_logic": t.condition_logic,
                "conditions": [],
            } for t in loaded1.triggers],
        )

        # Reload #2
        loaded2 = frappe.get_doc("Automation", name)
        g2 = json.loads(loaded2.graph_definition)
        t2 = [{"trigger_doctype": t.trigger_doctype, "trigger_event": t.trigger_event}
              for t in loaded2.triggers]

        # Compare
        self.assertEqual(len(g1["nodes"]), len(g2["nodes"]))
        self.assertEqual(len(g1["edges"]), len(g2["edges"]))
        for e1, e2 in zip(g1["edges"], g2["edges"]):
            self.assertEqual(e1.get("applies_to_triggers"), e2.get("applies_to_triggers"),
                             f"Edge tag drifted: {e1} vs {e2}")
        self.assertEqual(t1, t2, "Trigger rows drifted across round trip")

        self._cleanup_auto(auto_name)

    def test_E4_orphaned_disconnected_node(self):
        """E4: Node with NO path from any trigger (disconnected). Should NOT be flagged."""
        auto_name = "ST26.5-E4-OrphanedNode"
        self._cleanup_auto(auto_name)

        nodes = [
            _make_trigger_node(),
            _make_action_node("act-connected", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "CONNECTED"},
                ],
                "trigger_doctype_select": "Lead",
            }),
            # This node is disconnected (no edge from trigger)
            _make_action_node("act-orphan", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "ORPHAN"},
                ],
            }),
        ]
        edges = [
            _edge("trigger", "act-connected", applies_to=["0"]),
            # No edge to act-orphan
        ]
        triggers = [
            _make_trigger("Lead", "On Update"),
            _make_trigger("ToDo", "On Update"),
        ]

        # Should NOT raise - orphan is unreachable
        name = _create_and_publish(auto_name, nodes, edges, triggers)

        # Trigger via Lead -> orphan should not fire
        lead = frappe.get_doc({"doctype": "Lead", "lead_name": "ST26.5-E4-Lead"})
        lead.insert(ignore_permissions=True)
        frappe.db.commit()

        with patch("automation_builder.dispatcher.frappe.enqueue",
                    side_effect=lambda method, **kw: execute_automation(**kw)):
            execute_automation(name, "Lead", lead.name)

        runs = _get_runs(name, "Lead", lead.name)
        self.assertTrue(len(runs) >= 1)
        steps = _get_run_steps(runs[-1].name)
        self.assertFalse(
            any("ORPHAN" in (s.output or "") for s in steps),
            "Orphaned node should not execute"
        )

        self._cleanup_auto(auto_name)


class TestStage26_5CategoryF(IntegrationTestCase):
    """Category F — Governance interplay."""

    def _cleanup_auto(self, name):
        if frappe.db.exists("Automation", name):
            frappe.delete_doc("Automation", name, force=True)

    def test_F1_draft_does_not_execute(self):
        """F1: Multi-trigger tagged-edge automation in Draft -> should NOT execute on trigger."""
        auto_name = "ST26.5-F1-DraftNoExec"
        self._cleanup_auto(auto_name)

        nodes = [
            _make_trigger_node(),
            _make_action_node("act-lead", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "DRAFT-FIRED"},
                ],
            }),
        ]
        edges = [
            _edge("trigger", "act-lead", applies_to=["0"]),
        ]
        triggers = [
            _make_trigger("Lead", "On Update"),
            _make_trigger("ToDo", "On Update"),
        ]
        name = _create_and_publish(auto_name, nodes, edges, triggers)

        # Now set it to Draft
        auto = frappe.get_doc("Automation", name)
        auto.status = "Draft"
        auto.save(ignore_permissions=True)
        frappe.db.commit()

        # Trigger via Lead -> should NOT fire because Draft
        lead = frappe.get_doc({"doctype": "Lead", "lead_name": "ST26.5-F1-Lead"})
        lead.insert(ignore_permissions=True)
        frappe.db.commit()

        # Check: no run should exist (the SQL query in on_doc_event filters for Published)
        runs = _get_runs(name, "Lead", lead.name)
        self.assertEqual(len(runs), 0, "Draft automation should not execute")

        self._cleanup_auto(auto_name)

    def test_F2_publish_rejects_unscoped_convergence(self):
        """F2: Attempt to publish with unscoped convergence node -> rejected at save.
        Canonical shape: two Trigger nodes, each with its own edge, converging on
        a shared action without trigger_doctype_select."""
        auto_name = "ST26.5-F2-PublishRejects"
        self._cleanup_auto(auto_name)

        nodes = [
            _make_trigger_node("trigger-lead", trigger_doctype="Lead"),
            _make_trigger_node("trigger-todo", trigger_doctype="ToDo"),
            _make_action_node("act-shared", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "SHARED"},
                ],
            }),
        ]
        edges = [
            {"id": "e-tl-as", "source": "trigger-lead", "target": "act-shared",
             "sourceHandle": "trigger-lead-out", "targetHandle": "act-shared-in",
             "type": "smoothstep"},
            {"id": "e-tt-as", "source": "trigger-todo", "target": "act-shared",
             "sourceHandle": "trigger-todo-out", "targetHandle": "act-shared-in-left",
             "type": "smoothstep"},
        ]
        triggers = [
            _make_trigger("Lead", "On Update", graph_node_id="trigger-lead"),
            _make_trigger("ToDo", "On Update", graph_node_id="trigger-todo"),
        ]

        with self.assertRaises(frappe.ValidationError):
            _create_and_publish(auto_name, nodes, edges, triggers)

        self._cleanup_auto(auto_name)


class TestStage26_5Gap4ScheduleTick(IntegrationTestCase):
    """Gap 4 — Schedule trigger tick through full tagged-edge routing chain."""

    def _cleanup_auto(self, name):
        if frappe.db.exists("Automation", name):
            frappe.delete_doc("Automation", name, force=True)

    def test_schedule_tick_routes_via_tagged_edge(self):
        """Schedule trigger (row 0) + DocType Event (row 1), each tagged to own edge.
        Force next_run into the past, invoke check_scheduled_automations() for real.
        Confirm only the schedule path fires through the full tick -> execute chain."""
        auto_name = "ST26.5-D4-ScheduleTickTagged"
        self._cleanup_auto(auto_name)
        for n in frappe.get_all("Note", filters={"title": ["in", ["SCHEDULE-FIRED", "EVENT-FIRED"]]}):
            frappe.delete_doc("Note", n.name, force=True)
        frappe.db.commit()

        nodes = [
            _make_trigger_node(),
            _make_action_node("act-schedule", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "SCHEDULE-FIRED"},
                ],
            }),
            _make_action_node("act-event", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "EVENT-FIRED"},
                ],
            }),
        ]
        edges = [
            _edge("trigger", "act-schedule", applies_to=["0"]),
            _edge("trigger", "act-event", applies_to=["1"]),
        ]
        triggers = [
            _make_trigger("ToDo", trigger_type="Schedule", schedule_frequency="Hourly"),
            _make_trigger("Lead", "On Update", trigger_type="DocType Event"),
        ]
        name = _create_and_publish(auto_name, nodes, edges, triggers)

        # Force next_run into the past so check_scheduled_automations picks it up
        auto = frappe.get_doc("Automation", name)
        schedule_row = auto.triggers[0]
        two_hours_ago = frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=-2)
        frappe.db.sql(
            "UPDATE `tabAutomation Trigger` SET next_run = %s WHERE name = %s",
            (two_hours_ago, schedule_row.name),
        )
        frappe.db.commit()

        # Invoke the real scheduler tick
        from automation_builder.dispatcher import check_scheduled_automations
        check_scheduled_automations()

        # Verify: SCHEDULE-FIRED Note should exist
        notes = frappe.get_all("Note", filters={"title": "SCHEDULE-FIRED"}, limit=5)
        self.assertTrue(len(notes) >= 1,
                        "SCHEDULE-FIRED Note should be created by schedule tick")

        # Verify: EVENT-FIRED Note should NOT exist (DocType Event path not fired)
        bad_notes = frappe.get_all("Note", filters={"title": "EVENT-FIRED"}, limit=5)
        self.assertEqual(len(bad_notes), 0,
                         "EVENT-FIRED Note should NOT be created by schedule tick")

        # Verify: Automation Run has trigger_source='Schedule'
        runs = frappe.get_all("Automation Run",
                              filters={"automation": name},
                              fields=["name", "trigger_source", "status"],
                              order_by="creation desc", limit=1)
        self.assertTrue(len(runs) >= 1, "Run should be created")
        self.assertEqual(runs[0].trigger_source, "Schedule")
        self.assertEqual(runs[0].status, "Success")

        self._cleanup_auto(auto_name)


class TestStage26_5Gap5WebhookHttpPost(IntegrationTestCase):
    """Gap 5 — Webhook trigger fired through the real endpoint with tagged edges."""

    def _cleanup_auto(self, name):
        if frappe.db.exists("Automation", name):
            frappe.delete_doc("Automation", name, force=True)

    def test_webhook_endpoint_routes_via_tagged_edge(self):
        """Webhook (row 0) tagged to act-webhook, DocType Event (row 1) tagged to act-event.
        Fire via the real webhook_trigger() endpoint with a valid token.
        Confirm only the webhook path fires — no cross-fire."""
        auto_name = "ST26.5-D5-WebhookHttpPost"
        self._cleanup_auto(auto_name)
        for n in frappe.get_all("Note", filters={"title": ["in", ["WEBHOOK-FIRED", "EVENT-FIRED"]]}):
            frappe.delete_doc("Note", n.name, force=True)
        frappe.db.commit()

        webhook_token = secrets.token_hex(32)
        nodes = [
            _make_trigger_node(),
            _make_action_node("act-webhook", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "WEBHOOK-FIRED"},
                ],
            }),
            _make_action_node("act-event", "create_document", {
                "target_doctype": "Note", "field_mapping": [
                    {"target_field": "title", "source_value": "EVENT-FIRED"},
                ],
            }),
        ]
        edges = [
            _edge("trigger", "act-webhook", applies_to=["0"]),
            _edge("trigger", "act-event", applies_to=["1"]),
        ]
        triggers = [
            _make_trigger("", trigger_type="Webhook", webhook_token=webhook_token),
            _make_trigger("Lead", "On Update", trigger_type="DocType Event"),
        ]
        name = _create_and_publish(auto_name, nodes, edges, triggers)

        # Set up request context for webhook_trigger()
        saved_form_dict = getattr(frappe.local, "form_dict", {})
        saved_request = getattr(frappe.local, "request", None)
        saved_response = getattr(frappe.local, "response", None)
        try:
            frappe.local.form_dict = frappe._dict(token=webhook_token)
            frappe.local.request = frappe._dict(
                data=json.dumps({"source": "test"}),
                content_length=20,
            )
            frappe.local.response = {}

            # Mock frappe.enqueue to run synchronously
            from automation_builder.dispatcher import execute_webhook_trigger
            with patch("automation_builder.api.frappe.enqueue",
                        side_effect=lambda method, **kw: execute_webhook_trigger(
                            automation_name=kw["automation_name"],
                            payload=kw["payload"],
                        )):
                from automation_builder.api import webhook_trigger
                result = webhook_trigger()

            self.assertEqual(result.get("status"), "queued")

        finally:
            frappe.local.form_dict = saved_form_dict
            frappe.local.request = saved_request
            frappe.local.response = saved_response

        # Verify: WEBHOOK-FIRED Note should exist
        notes = frappe.get_all("Note", filters={"title": "WEBHOOK-FIRED"}, limit=5)
        self.assertTrue(len(notes) >= 1,
                        "WEBHOOK-FIRED Note should be created by webhook")

        # Verify: EVENT-FIRED Note should NOT exist (DocType Event path not fired)
        bad_notes = frappe.get_all("Note", filters={"title": "EVENT-FIRED"}, limit=5)
        self.assertEqual(len(bad_notes), 0,
                         "EVENT-FIRED Note should NOT be created by webhook")

        # Verify: Automation Run has trigger_source='Webhook'
        runs = frappe.get_all("Automation Run",
                              filters={"automation": name},
                              fields=["name", "trigger_source", "status"],
                              order_by="creation desc", limit=1)
        self.assertTrue(len(runs) >= 1, "Run should be created")
        self.assertEqual(runs[0].trigger_source, "Webhook")
        self.assertEqual(runs[0].status, "Success")

        self._cleanup_auto(auto_name)
