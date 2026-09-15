"""Stage 29 — Canonical multi-Trigger-node design tests.

All payloads match the EXACT shape produced by AutomationBuilder.vue save():
- graph_definition: { nodes: [{id, type, position, data}], edges: [{id, source, target, sourceHandle, targetHandle, type}] }
- triggers: [{ trigger_type, trigger_doctype, trigger_event, condition_logic, conditions, graph_node_id }]

No applies_to_triggers on edges. Each Trigger node has ONE outgoing edge.
graph_node_id links each trigger row to its canvas Trigger node.
"""

import frappe
import json
import unittest
from unittest.mock import patch
from frappe.tests import IntegrationTestCase

from automation_builder.api import save_automation
from automation_builder.dispatcher import execute_automation, _walk_graph, _find_start_trigger


# ---------------------------------------------------------------------------
# Helpers — payload shape matches real save() output exactly
# ---------------------------------------------------------------------------

def _make_trigger(dt, event="On Update", graph_node_id="trigger"):
    """Real save() trigger row shape."""
    return {
        "trigger_type": "DocType Event",
        "trigger_doctype": dt,
        "trigger_event": event,
        "schedule_frequency": "Hourly",
        "webhook_token": "",
        "condition_logic": "All must match",
        "conditions": [],
        "graph_node_id": graph_node_id,
    }


def _make_trigger_node(node_id, trigger_doctype=""):
    """Real save() trigger node shape."""
    return {
        "id": node_id, "type": "trigger",
        "position": {"x": 250, "y": 50},
        "data": {
            "trigger_type": "DocType Event",
            "trigger_doctype": trigger_doctype,
            "trigger_event": "On Update",
            "schedule_frequency": "Hourly",
            "condition_logic": "All must match",
            "conditions": [],
        },
    }


def _make_condition_node(node_id, field, op="=", val="", scoped_dt=""):
    return {
        "id": node_id, "type": "condition",
        "position": {"x": 0, "y": 150},
        "data": {
            "condition_field": field, "condition_operator": op,
            "condition_value": val, "trigger_doctype_select": scoped_dt,
        },
    }


def _make_action_node(node_id, action_type, config=None, scoped_dt=""):
    data = {"action_type": action_type, "trigger_doctype_select": scoped_dt}
    if config:
        data.update(config)
    return {"id": node_id, "type": "action", "position": {"x": 0, "y": 300}, "data": data}


def _edge(src, tgt, target_handle=None):
    """Real save() edge shape — no applies_to_triggers."""
    return {
        "id": f"e-{src}-{tgt}",
        "source": src, "target": tgt,
        "sourceHandle": f"{src}-out",
        "targetHandle": target_handle or f"{tgt}-in",
        "type": "smoothstep",
    }


def _create_and_publish(name, graph_nodes, graph_edges, triggers):
    graph_def = json.dumps({"nodes": graph_nodes, "edges": graph_edges})
    result = save_automation(
        automation_name=name,
        status="Published",
        graph_definition=graph_def,
        triggers=triggers,
    )
    return result["name"]


def _get_runs(auto_name, ref_doctype=None, ref_name=None):
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
    return frappe.get_all(
        "Automation Run Step",
        filters={"parent": run_name},
        fields=["node_id", "node_type", "step_type", "status", "branch_taken", "output", "error"],
        order_by="idx asc",
    )


def _cleanup(name):
    if frappe.db.exists("Automation", name):
        frappe.delete_doc("Automation", name, force=True)


# ---------------------------------------------------------------------------
# Part G: User's exact scenario — Lead + ToDo, separate conditions,
#          converging shared action
# ---------------------------------------------------------------------------
class TestStage29UserScenario(IntegrationTestCase):
    """User's exact scenario: two Trigger nodes, each with its own Condition,
    both converging into a shared Action node."""

    def setUp(self):
        self.created_docs = []

    def tearDown(self):
        for dt, name in self.created_docs:
            try:
                frappe.delete_doc(dt, name, force=True)
            except Exception:
                pass

    def _cleanup_all(self):
        for name in frappe.get_all("Automation", filters={"name": ["like", "ST29-%"]}, fields=["name"]):
            frappe.delete_doc("Automation", name.name, force=True)
        for n in frappe.get_all("Note", filters={"title": ["like", "ST29-%"]}, fields=["name"]):
            frappe.delete_doc("Note", n.name, force=True)
        frappe.db.commit()

    def test_G1_user_scenario_saves(self):
        """G1: User's exact scenario saves without error."""
        auto_name = "ST29-G1-UserScenario"
        self._cleanup_all()

        nodes = [
            _make_trigger_node("trigger-lead", trigger_doctype="Lead"),
            _make_trigger_node("trigger-todo", trigger_doctype="ToDo"),
            _make_condition_node("cond-lead", "status", "=", "Open"),
            _make_condition_node("cond-todo", "status", "=", "Open"),
            _make_action_node("act-shared", "create_document", {
                "target_doctype": "Note",
                "field_mapping": [{"target_field": "title", "source_value": "ST29-G1-FIRED"}],
            }, scoped_dt="any"),
        ]
        edges = [
            _edge("trigger-lead", "cond-lead"),
            _edge("cond-lead", "act-shared"),
            _edge("trigger-todo", "cond-todo"),
            _edge("cond-todo", "act-shared", target_handle="act-shared-in-left"),
        ]
        triggers = [
            _make_trigger("Lead", graph_node_id="trigger-lead"),
            _make_trigger("ToDo", graph_node_id="trigger-todo"),
        ]

        name = _create_and_publish(auto_name, nodes, edges, triggers)
        auto = frappe.get_doc("Automation", name)
        self.assertEqual(auto.status, "Published")
        self.assertEqual(len(auto.triggers), 2)

        # Verify graph_node_id linkage
        self.assertEqual(auto.triggers[0].graph_node_id, "trigger-lead")
        self.assertEqual(auto.triggers[1].graph_node_id, "trigger-todo")

        # Verify graph has no applies_to_triggers
        graph = json.loads(auto.graph_definition)
        for e in graph["edges"]:
            self.assertIsNone(e.get("applies_to_triggers"),
                              f"Edge {e['id']} should not have applies_to_triggers")

        self._cleanup_all()

    def test_G2_lead_fires_creates_run(self):
        """G2: Lead fires → walks trigger-lead path → creates Note."""
        auto_name = "ST29-G2-LeadFires"
        self._cleanup_all()

        nodes = [
            _make_trigger_node("trigger-lead", trigger_doctype="Lead"),
            _make_trigger_node("trigger-todo", trigger_doctype="ToDo"),
            _make_condition_node("cond-lead", "status", "=", "Open"),
            _make_condition_node("cond-todo", "status", "=", "Open"),
            _make_action_node("act-shared", "create_document", {
                "target_doctype": "Note",
                "field_mapping": [{"target_field": "title", "source_value": "ST29-G2-LEAD"}],
            }, scoped_dt="any"),
        ]
        edges = [
            _edge("trigger-lead", "cond-lead"),
            _edge("cond-lead", "act-shared"),
            _edge("trigger-todo", "cond-todo"),
            _edge("cond-todo", "act-shared", target_handle="act-shared-in-left"),
        ]
        triggers = [
            _make_trigger("Lead", graph_node_id="trigger-lead"),
            _make_trigger("ToDo", graph_node_id="trigger-todo"),
        ]

        name = _create_and_publish(auto_name, nodes, edges, triggers)

        lead = frappe.get_doc({"doctype": "Lead", "lead_name": "ST29-G2-Lead"})
        lead.status = "Open"
        lead.insert(ignore_permissions=True)
        self.created_docs.append(("Lead", lead.name))
        frappe.db.commit()

        execute_automation(name, "Lead", lead.name)

        runs = _get_runs(name, "Lead", lead.name)
        self.assertGreaterEqual(len(runs), 1)
        for r in runs:
            self.assertEqual(r.status, "Success")
            steps = _get_run_steps(r.name)
            step_types = [s.step_type for s in steps]
            self.assertIn("create_document", step_types)

        self._cleanup_all()

    def test_G3_todo_fires_creates_run(self):
        """G3: ToDo fires → walks trigger-todo path → creates Note."""
        auto_name = "ST29-G3-TodoFires"
        self._cleanup_all()

        nodes = [
            _make_trigger_node("trigger-lead", trigger_doctype="Lead"),
            _make_trigger_node("trigger-todo", trigger_doctype="ToDo"),
            _make_condition_node("cond-lead", "status", "=", "Open"),
            _make_condition_node("cond-todo", "status", "=", "Open"),
            _make_action_node("act-shared", "create_document", {
                "target_doctype": "Note",
                "field_mapping": [{"target_field": "title", "source_value": "ST29-G3-TODO"}],
            }, scoped_dt="any"),
        ]
        edges = [
            _edge("trigger-lead", "cond-lead"),
            _edge("cond-lead", "act-shared"),
            _edge("trigger-todo", "cond-todo"),
            _edge("cond-todo", "act-shared", target_handle="act-shared-in-left"),
        ]
        triggers = [
            _make_trigger("Lead", graph_node_id="trigger-lead"),
            _make_trigger("ToDo", graph_node_id="trigger-todo"),
        ]

        name = _create_and_publish(auto_name, nodes, edges, triggers)

        todo = frappe.get_doc({"doctype": "ToDo", "description": "ST29-G3-Todo"})
        todo.insert(ignore_permissions=True)
        self.created_docs.append(("ToDo", todo.name))
        frappe.db.commit()

        execute_automation(name, "ToDo", todo.name)

        runs = _get_runs(name, "ToDo", todo.name)
        self.assertGreaterEqual(len(runs), 1)
        for r in runs:
            self.assertEqual(r.status, "Success")
            steps = _get_run_steps(r.name)
            step_types = [s.step_type for s in steps]
            self.assertIn("create_document", step_types)

        self._cleanup_all()

    def test_G4_both_fire_each_produces_run(self):
        """G4: Both Lead and ToDo fire → each produces its own run."""
        auto_name = "ST29-G4-BothFire"
        self._cleanup_all()

        nodes = [
            _make_trigger_node("trigger-lead", trigger_doctype="Lead"),
            _make_trigger_node("trigger-todo", trigger_doctype="ToDo"),
            _make_condition_node("cond-lead", "status", "=", "Open"),
            _make_condition_node("cond-todo", "status", "=", "Open"),
            _make_action_node("act-shared", "create_document", {
                "target_doctype": "Note",
                "field_mapping": [{"target_field": "title", "source_value": "ST29-G4-FIRED"}],
            }, scoped_dt="any"),
        ]
        edges = [
            _edge("trigger-lead", "cond-lead"),
            _edge("cond-lead", "act-shared"),
            _edge("trigger-todo", "cond-todo"),
            _edge("cond-todo", "act-shared", target_handle="act-shared-in-left"),
        ]
        triggers = [
            _make_trigger("Lead", graph_node_id="trigger-lead"),
            _make_trigger("ToDo", graph_node_id="trigger-todo"),
        ]

        name = _create_and_publish(auto_name, nodes, edges, triggers)

        # Fire Lead
        lead = frappe.get_doc({"doctype": "Lead", "lead_name": "ST29-G4-Lead"})
        lead.status = "Open"
        lead.insert(ignore_permissions=True)
        self.created_docs.append(("Lead", lead.name))
        frappe.db.commit()
        execute_automation(name, "Lead", lead.name)

        # Fire ToDo
        todo = frappe.get_doc({"doctype": "ToDo", "description": "ST29-G4-Todo"})
        todo.insert(ignore_permissions=True)
        self.created_docs.append(("ToDo", todo.name))
        frappe.db.commit()
        execute_automation(name, "ToDo", todo.name)

        all_runs = _get_runs(name)
        self.assertGreaterEqual(len(all_runs), 2)

        lead_runs = _get_runs(name, "Lead", lead.name)
        todo_runs = _get_runs(name, "ToDo", todo.name)
        self.assertGreaterEqual(len(lead_runs), 1)
        self.assertGreaterEqual(len(todo_runs), 1)

        self._cleanup_all()


# ---------------------------------------------------------------------------
# Graph walk tests — verify _find_start_trigger uses graph_node_id
# ---------------------------------------------------------------------------
class TestStage29GraphWalk(IntegrationTestCase):

    def test_H1_find_start_uses_graph_node_id(self):
        """H1: _find_start_trigger uses graph_node_id for direct lookup."""
        trigger_nodes = [
            {"id": "trigger-lead", "type": "trigger", "data": {"trigger_doctype": "Lead"}},
            {"id": "trigger-todo", "type": "trigger", "data": {"trigger_doctype": "ToDo"}},
        ]

        # Mock automation with graph_node_id set
        class MockTrigger:
            def __init__(self, doctype, gnid):
                self.trigger_doctype = doctype
                self.graph_node_id = gnid

        class MockAutomation:
            triggers = [MockTrigger("Lead", "trigger-lead"), MockTrigger("ToDo", "trigger-todo")]

        result = _find_start_trigger(trigger_nodes, "ToDo", {}, MockAutomation())
        self.assertEqual(result, "trigger-todo")

    def test_H2_find_start_fallback_to_doctype(self):
        """H2: Falls back to trigger_doctype matching when graph_node_id is empty."""
        trigger_nodes = [
            {"id": "trigger-lead", "type": "trigger", "data": {"trigger_doctype": "Lead"}},
            {"id": "trigger-todo", "type": "trigger", "data": {"trigger_doctype": "ToDo"}},
        ]

        class MockTrigger:
            def __init__(self, doctype, gnid):
                self.trigger_doctype = doctype
                self.graph_node_id = gnid

        class MockAutomation:
            triggers = [MockTrigger("Lead", ""), MockTrigger("ToDo", "")]

        result = _find_start_trigger(trigger_nodes, "Lead", {}, MockAutomation())
        self.assertEqual(result, "trigger-lead")

    def test_H3_walk_two_trigger_nodes_lead(self):
        """H3: Walk two-trigger-node graph starting at trigger-lead for Lead."""
        graph = {
            "nodes": [
                {"id": "trigger-lead", "type": "trigger", "data": {"trigger_doctype": "Lead"}},
                {"id": "trigger-todo", "type": "trigger", "data": {"trigger_doctype": "ToDo"}},
                {"id": "cond-lead", "type": "condition", "data": {
                    "condition_field": "status", "condition_operator": "=",
                    "condition_value": "Open", "trigger_doctype_select": "Lead",
                }},
                {"id": "cond-todo", "type": "condition", "data": {
                    "condition_field": "status", "condition_operator": "=",
                    "condition_value": "Open", "trigger_doctype_select": "ToDo",
                }},
                {"id": "act-shared", "type": "action", "data": {
                    "action_type": "create_document", "trigger_doctype_select": "any",
                    "target_doctype": "Note",
                }},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-lead", "target": "cond-lead",
                 "sourceHandle": "trigger-lead-out", "targetHandle": "cond-lead-in", "type": "smoothstep"},
                {"id": "e2", "source": "cond-lead", "target": "act-shared",
                 "sourceHandle": "cond-lead-out", "targetHandle": "act-shared-in", "type": "smoothstep"},
                {"id": "e3", "source": "trigger-todo", "target": "cond-todo",
                 "sourceHandle": "trigger-todo-out", "targetHandle": "cond-todo-in", "type": "smoothstep"},
                {"id": "e4", "source": "cond-todo", "target": "act-shared",
                 "sourceHandle": "cond-todo-out", "targetHandle": "act-shared-in-left", "type": "smoothstep"},
            ],
        }

        lead_doc = frappe._dict({"status": "Open", "lead_name": "Test Lead"})
        context = {"doc": lead_doc, "ref_doctype": "Lead", "ref_name": "T"}

        trace = _walk_graph(graph, "trigger-lead", context)
        node_ids = [e.get("node_id") for e in trace]
        self.assertIn("cond-lead", node_ids)
        self.assertNotIn("cond-todo", node_ids)
        self.assertIn("act-shared", node_ids)

    def test_H4_walk_two_trigger_nodes_todo(self):
        """H4: Walk two-trigger-node graph starting at trigger-todo for ToDo."""
        graph = {
            "nodes": [
                {"id": "trigger-lead", "type": "trigger", "data": {"trigger_doctype": "Lead"}},
                {"id": "trigger-todo", "type": "trigger", "data": {"trigger_doctype": "ToDo"}},
                {"id": "cond-lead", "type": "condition", "data": {
                    "condition_field": "status", "condition_operator": "=",
                    "condition_value": "Open", "trigger_doctype_select": "Lead",
                }},
                {"id": "cond-todo", "type": "condition", "data": {
                    "condition_field": "status", "condition_operator": "=",
                    "condition_value": "Open", "trigger_doctype_select": "ToDo",
                }},
                {"id": "act-shared", "type": "action", "data": {
                    "action_type": "create_document", "trigger_doctype_select": "any",
                    "target_doctype": "Note",
                }},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-lead", "target": "cond-lead",
                 "sourceHandle": "trigger-lead-out", "targetHandle": "cond-lead-in", "type": "smoothstep"},
                {"id": "e2", "source": "cond-lead", "target": "act-shared",
                 "sourceHandle": "cond-lead-out", "targetHandle": "act-shared-in", "type": "smoothstep"},
                {"id": "e3", "source": "trigger-todo", "target": "cond-todo",
                 "sourceHandle": "trigger-todo-out", "targetHandle": "cond-todo-in", "type": "smoothstep"},
                {"id": "e4", "source": "cond-todo", "target": "act-shared",
                 "sourceHandle": "cond-todo-out", "targetHandle": "act-shared-in-left", "type": "smoothstep"},
            ],
        }

        todo_doc = frappe._dict({"status": "Open", "description": "Test Todo"})
        context = {"doc": todo_doc, "ref_doctype": "ToDo", "ref_name": "T"}

        trace = _walk_graph(graph, "trigger-todo", context)
        node_ids = [e.get("node_id") for e in trace]
        self.assertNotIn("cond-lead", node_ids)
        self.assertIn("cond-todo", node_ids)
        self.assertIn("act-shared", node_ids)

    def test_H5_find_start_single_trigger(self):
        """H5: Single trigger node → always returns that node."""
        trigger_nodes = [{"id": "trigger", "type": "trigger", "data": {"trigger_doctype": "Lead"}}]
        result = _find_start_trigger(trigger_nodes, "Lead", {})
        self.assertEqual(result, "trigger")

    def test_H6_find_start_no_automation_fallback(self):
        """H6: No automation provided → falls back to doctype matching."""
        trigger_nodes = [
            {"id": "trigger-lead", "type": "trigger", "data": {"trigger_doctype": "Lead"}},
            {"id": "trigger-todo", "type": "trigger", "data": {"trigger_doctype": "ToDo"}},
        ]
        result = _find_start_trigger(trigger_nodes, "ToDo", {})
        self.assertEqual(result, "trigger-todo")


# ---------------------------------------------------------------------------
# Scoping validation tests — forward-walk BFS without tagged edges
# ---------------------------------------------------------------------------
class TestStage29Scoping(IntegrationTestCase):

    def test_I1_shared_action_needs_scoping(self):
        """I1: Two triggers → shared action without trigger_doctype_select → rejected."""
        auto_name = "ST29-I1-NeedsScoping"
        if frappe.db.exists("Automation", auto_name):
            frappe.delete_doc("Automation", auto_name, force=True)

        nodes = [
            _make_trigger_node("trigger-lead", trigger_doctype="Lead"),
            _make_trigger_node("trigger-todo", trigger_doctype="ToDo"),
            _make_action_node("act-shared", "create_document", {
                "target_doctype": "Note",
                "field_mapping": [{"target_field": "title", "source_value": "X"}],
            }, scoped_dt=""),  # NO scoping
        ]
        edges = [
            _edge("trigger-lead", "act-shared"),
            _edge("trigger-todo", "act-shared", target_handle="act-shared-in-left"),
        ]
        triggers = [
            _make_trigger("Lead", graph_node_id="trigger-lead"),
            _make_trigger("ToDo", graph_node_id="trigger-todo"),
        ]

        try:
            _create_and_publish(auto_name, nodes, edges, triggers)
            self.fail("Expected ValidationError")
        except frappe.ValidationError:
            pass
        finally:
            if frappe.db.exists("Automation", auto_name):
                frappe.delete_doc("Automation", auto_name, force=True)

    def test_I2_shared_action_with_scoping_saves(self):
        """I2: Two triggers → shared action WITH trigger_doctype_select='any' → saves."""
        auto_name = "ST29-I2-ScopedAny"
        if frappe.db.exists("Automation", auto_name):
            frappe.delete_doc("Automation", auto_name, force=True)

        nodes = [
            _make_trigger_node("trigger-lead", trigger_doctype="Lead"),
            _make_trigger_node("trigger-todo", trigger_doctype="ToDo"),
            _make_action_node("act-shared", "create_document", {
                "target_doctype": "Note",
                "field_mapping": [{"target_field": "title", "source_value": "X"}],
            }, scoped_dt="any"),
        ]
        edges = [
            _edge("trigger-lead", "act-shared"),
            _edge("trigger-todo", "act-shared", target_handle="act-shared-in-left"),
        ]
        triggers = [
            _make_trigger("Lead", graph_node_id="trigger-lead"),
            _make_trigger("ToDo", graph_node_id="trigger-todo"),
        ]

        name = _create_and_publish(auto_name, nodes, edges, triggers)
        self.assertTrue(frappe.db.exists("Automation", name))
        if frappe.db.exists("Automation", auto_name):
            frappe.delete_doc("Automation", auto_name, force=True)

    def test_I3_separate_paths_no_scoping_needed(self):
        """I3: Two triggers → separate paths, each action scoped → saves."""
        auto_name = "ST29-I3-SeparatePaths"
        if frappe.db.exists("Automation", auto_name):
            frappe.delete_doc("Automation", auto_name, force=True)

        nodes = [
            _make_trigger_node("trigger-lead", trigger_doctype="Lead"),
            _make_trigger_node("trigger-todo", trigger_doctype="ToDo"),
            _make_action_node("act-lead", "create_document", {
                "target_doctype": "Note",
                "field_mapping": [{"target_field": "title", "source_value": "LEAD"}],
            }, scoped_dt="Lead"),
            _make_action_node("act-todo", "create_document", {
                "target_doctype": "Note",
                "field_mapping": [{"target_field": "title", "source_value": "TODO"}],
            }, scoped_dt="ToDo"),
        ]
        edges = [
            _edge("trigger-lead", "act-lead"),
            _edge("trigger-todo", "act-todo"),
        ]
        triggers = [
            _make_trigger("Lead", graph_node_id="trigger-lead"),
            _make_trigger("ToDo", graph_node_id="trigger-todo"),
        ]

        name = _create_and_publish(auto_name, nodes, edges, triggers)
        self.assertTrue(frappe.db.exists("Automation", name))
        if frappe.db.exists("Automation", auto_name):
            frappe.delete_doc("Automation", auto_name, force=True)

    def test_I4_condition_convergence_needs_scoping(self):
        """I4: Two triggers → converging conditions → shared unscoped action → rejected."""
        auto_name = "ST29-I4-ConvergenceReject"
        if frappe.db.exists("Automation", auto_name):
            frappe.delete_doc("Automation", auto_name, force=True)

        nodes = [
            _make_trigger_node("trigger-lead", trigger_doctype="Lead"),
            _make_trigger_node("trigger-todo", trigger_doctype="ToDo"),
            _make_condition_node("cond-lead", "status", "=", "Open"),
            _make_condition_node("cond-todo", "status", "=", "Open"),
            _make_action_node("act-shared", "create_document", {
                "target_doctype": "Note",
                "field_mapping": [{"target_field": "title", "source_value": "X"}],
            }, scoped_dt=""),  # NO scoping
        ]
        edges = [
            _edge("trigger-lead", "cond-lead"),
            _edge("cond-lead", "act-shared"),
            _edge("trigger-todo", "cond-todo"),
            _edge("cond-todo", "act-shared", target_handle="act-shared-in-left"),
        ]
        triggers = [
            _make_trigger("Lead", graph_node_id="trigger-lead"),
            _make_trigger("ToDo", graph_node_id="trigger-todo"),
        ]

        try:
            _create_and_publish(auto_name, nodes, edges, triggers)
            self.fail("Expected ValidationError")
        except frappe.ValidationError:
            pass
        finally:
            if frappe.db.exists("Automation", auto_name):
                frappe.delete_doc("Automation", auto_name, force=True)

    def test_I5_condition_convergence_with_scoping_saves(self):
        """I5: Same as I4 but action scoped to 'any' → saves."""
        auto_name = "ST29-I5-ConvergenceScoped"
        if frappe.db.exists("Automation", auto_name):
            frappe.delete_doc("Automation", auto_name, force=True)

        nodes = [
            _make_trigger_node("trigger-lead", trigger_doctype="Lead"),
            _make_trigger_node("trigger-todo", trigger_doctype="ToDo"),
            _make_condition_node("cond-lead", "status", "=", "Open"),
            _make_condition_node("cond-todo", "status", "=", "Open"),
            _make_action_node("act-shared", "create_document", {
                "target_doctype": "Note",
                "field_mapping": [{"target_field": "title", "source_value": "X"}],
            }, scoped_dt="any"),
        ]
        edges = [
            _edge("trigger-lead", "cond-lead"),
            _edge("cond-lead", "act-shared"),
            _edge("trigger-todo", "cond-todo"),
            _edge("cond-todo", "act-shared", target_handle="act-shared-in-left"),
        ]
        triggers = [
            _make_trigger("Lead", graph_node_id="trigger-lead"),
            _make_trigger("ToDo", graph_node_id="trigger-todo"),
        ]

        name = _create_and_publish(auto_name, nodes, edges, triggers)
        self.assertTrue(frappe.db.exists("Automation", name))
        if frappe.db.exists("Automation", auto_name):
            frappe.delete_doc("Automation", auto_name, force=True)
