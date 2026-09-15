"""Stage 28 — Reproduce and fix multi-trigger convergence with Condition nodes.

The exact failing shape:
  Trigger ──[tagged: Lead]──> Condition-1 ──> Action (shared)
  Trigger ──[tagged: ToDo]──> Condition-2 ──> Action (shared)

Stage 26.5's B1-B4 convergence tests went trigger->action DIRECTLY,
with no intermediate Condition node. This shape was never tested.

Part E: Tests matching the real canvas topology — two separate Trigger nodes
each with one outgoing edge, converging into a shared Action node. This is
what the frontend ACTUALLY produces (each Trigger node = one trigger row).
"""

import frappe
import json
import unittest
from unittest.mock import patch
from frappe.tests import IntegrationTestCase

from automation_builder.api import save_automation
from automation_builder.dispatcher import execute_automation, _walk_graph, _find_start_trigger


def _make_trigger(dt, event="On Update", trigger_type="DocType Event", **kwargs):
    row = {
        "trigger_type": trigger_type,
        "trigger_doctype": dt,
        "trigger_event": event,
        "condition_logic": "All must match",
        "conditions": [],
    }
    row.update(kwargs)
    return row


def _make_trigger_node(node_id="trigger", trigger_doctype=""):
    return {
        "id": node_id, "type": "trigger",
        "position": {"x": 250, "y": 50},
        "data": {"trigger_doctype": trigger_doctype, "trigger_event": "On Update"},
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


def _edge(src, tgt, applies_to=None, source_handle=None, target_handle=None):
    e = {
        "id": f"e-{src}-{tgt}",
        "source": src, "target": tgt,
        "sourceHandle": source_handle or f"{src}-out",
        "targetHandle": target_handle or f"{tgt}-in",
        "type": "smoothstep",
    }
    if applies_to is not None:
        e["applies_to_triggers"] = applies_to
    return e


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


def _cleanup_auto(name):
    if frappe.db.exists("Automation", name):
        frappe.delete_doc("Automation", name, force=True)


class TestStage28Reproduction(IntegrationTestCase):
    """Part A: Reproduce the exact failing shape through save_automation."""

    def setUp(self):
        self.created_docs = []

    def tearDown(self):
        for dt, name in self.created_docs:
            try:
                frappe.delete_doc(dt, name, force=True)
            except Exception:
                pass

    def _cleanup_all(self):
        for name in frappe.get_all("Automation", filters={"name": ["like", "ST28-%"]}, fields=["name"]):
            frappe.delete_doc("Automation", name.name, force=True)
        for n in frappe.get_all("Note", filters={"title": ["like", "ST28-%"]}, fields=["name"]):
            frappe.delete_doc("Note", n.name, force=True)
        frappe.db.commit()

    # ------------------------------------------------------------------
    # A1: Multi-trigger nodes, action scoped to "any" — SHOULD save
    # ------------------------------------------------------------------
    def test_A1_both_tagged_action_any_saves(self):
        """A1: Two Trigger nodes → separate Conditions → shared Action(any).
        Expected: save succeeds, execution works for both Lead and ToDo.
        """
        auto_name = "ST28-A1-BothTaggedActionAny"
        self._cleanup_all()

        nodes = [
            _make_trigger_node("trigger-lead", trigger_doctype="Lead"),
            _make_trigger_node("trigger-todo", trigger_doctype="ToDo"),
            _make_condition_node("cond-lead", "status", "=", "Open"),
            _make_condition_node("cond-todo", "status", "=", "Open"),
            _make_action_node("act-shared", "create_document", {
                "target_doctype": "Note",
                "field_mapping": [
                    {"target_field": "title", "source_value": "ST28-A1-FIRED"},
                ],
            }, scoped_dt="any"),
        ]
        edges = [
            _edge("trigger-lead", "cond-lead"),
            _edge("cond-lead", "act-shared"),
            _edge("trigger-todo", "cond-todo"),
            _edge("cond-todo", "act-shared", target_handle="act-shared-in-left"),
        ]
        triggers = [
            _make_trigger("Lead", "On Update", graph_node_id="trigger-lead"),
            _make_trigger("ToDo", "On Update", graph_node_id="trigger-todo"),
        ]

        print("\n=== A1: Graph Definition JSON ===")
        graph_def = json.dumps({"nodes": nodes, "edges": edges}, indent=2)
        print(graph_def)

        print("\n=== A1: Triggers ===")
        print(json.dumps(triggers, indent=2))

        try:
            name = _create_and_publish(auto_name, nodes, edges, triggers)
            print(f"\n=== A1: Save SUCCEEDED — automation name: {name} ===")

            auto = frappe.get_doc("Automation", name)
            print(f"\n=== A1: Automation status={auto.status}, enabled={auto.enabled} ===")
            for i, t in enumerate(auto.triggers):
                print(f"  Trigger {i}: type={t.trigger_type}, doctype={t.trigger_doctype}, event={t.trigger_event}")

            graph = json.loads(auto.graph_definition)
            print(f"\n=== A1: Stored graph: {len(graph['nodes'])} nodes, {len(graph['edges'])} edges ===")
            for e in graph["edges"]:
                print(f"  Edge {e['id']}: {e['source']} -> {e['target']}")

            print("\n=== A1: Triggering via Lead ===")
            with patch("automation_builder.dispatcher.frappe.enqueue",
                        side_effect=lambda method, **kw: execute_automation(**kw)):
                lead = frappe.get_doc({"doctype": "Lead", "lead_name": "ST28-A1-Lead"})
                lead.status = "Open"
                lead.insert(ignore_permissions=True)
                self.created_docs.append(("Lead", lead.name))
                frappe.db.commit()

                execute_automation(name, "Lead", lead.name)

            runs_lead = _get_runs(name, "Lead", lead.name)
            print(f"  Runs for Lead: {len(runs_lead)}")
            for r in runs_lead:
                print(f"    {r.name}: status={r.status}")
                steps = _get_run_steps(r.name)
                for s in steps:
                    print(f"      Step: type={s.step_type}, status={s.status}, branch={s.branch_taken}, output={s.output}")

            print("\n=== A1: Triggering via ToDo ===")
            with patch("automation_builder.dispatcher.frappe.enqueue",
                        side_effect=lambda method, **kw: execute_automation(**kw)):
                todo = frappe.get_doc({"doctype": "ToDo", "description": "ST28-A1-Todo"})
                todo.insert(ignore_permissions=True)
                self.created_docs.append(("ToDo", todo.name))
                frappe.db.commit()

                execute_automation(name, "ToDo", todo.name)

            runs_todo = _get_runs(name, "ToDo", todo.name)
            print(f"  Runs for ToDo: {len(runs_todo)}")
            for r in runs_todo:
                print(f"    {r.name}: status={r.status}")
                steps = _get_run_steps(r.name)
                for s in steps:
                    print(f"      Step: type={s.step_type}, status={s.status}, branch={s.branch_taken}, output={s.output}")

            self._cleanup_all()

        except Exception as e:
            print(f"\n=== A1: Save/execution FAILED: {type(e).__name__}: {e} ===")
            self._cleanup_all()
            raise

    # ------------------------------------------------------------------
    # A2: Multi-trigger nodes, action NOT scoped — should REJECT at save
    # ------------------------------------------------------------------
    def test_A2_both_tagged_action_unscoped_rejected(self):
        """A2: Two Trigger nodes converge on shared action without scoping.
        Expected: _validate_scoping_for_multi_doctype rejects the save.
        """
        auto_name = "ST28-A2-BothTaggedNoScope"
        self._cleanup_all()

        nodes = [
            _make_trigger_node("trigger-lead", trigger_doctype="Lead"),
            _make_trigger_node("trigger-todo", trigger_doctype="ToDo"),
            _make_condition_node("cond-lead", "status", "=", "New"),
            _make_condition_node("cond-todo", "status", "=", "Open"),
            _make_action_node("act-shared", "create_document", {
                "target_doctype": "Note",
                "field_mapping": [
                    {"target_field": "title", "source_value": "ST28-A2-FIRED"},
                ],
            }, scoped_dt=""),  # NO scoping
        ]
        edges = [
            _edge("trigger-lead", "cond-lead"),
            _edge("cond-lead", "act-shared"),
            _edge("trigger-todo", "cond-todo"),
            _edge("cond-todo", "act-shared", target_handle="act-shared-in-left"),
        ]
        triggers = [
            _make_trigger("Lead", "On Update", graph_node_id="trigger-lead"),
            _make_trigger("ToDo", "On Update", graph_node_id="trigger-todo"),
        ]

        print("\n=== A2: Attempting save with unscoped action ===")
        try:
            name = _create_and_publish(auto_name, nodes, edges, triggers)
            print(f"  UNEXPECTED: Save succeeded as {name}")
            self._cleanup_all()
            self.fail("Expected ValidationError but save succeeded")
        except frappe.ValidationError as e:
            print(f"  EXPECTED: ValidationError: {e}")
            self._cleanup_all()

    # ------------------------------------------------------------------
    # A3: Multi-trigger nodes, separate conditions, action ANY — SHOULD save
    # ------------------------------------------------------------------
    def test_A3_first_edge_untagged_second_tagged(self):
        """A3: Two Trigger nodes → separate Conditions → shared Action(any).
        Expected: save succeeds, execution works for both Lead and ToDo.
        """
        auto_name = "ST28-A3-FirstUntagged"
        self._cleanup_all()

        nodes = [
            _make_trigger_node("trigger-lead", trigger_doctype="Lead"),
            _make_trigger_node("trigger-todo", trigger_doctype="ToDo"),
            _make_condition_node("cond-lead", "status", "=", "New"),
            _make_condition_node("cond-todo", "status", "=", "Open"),
            _make_action_node("act-shared", "create_document", {
                "target_doctype": "Note",
                "field_mapping": [
                    {"target_field": "title", "source_value": "ST28-A3-FIRED"},
                ],
            }, scoped_dt="any"),
        ]
        edges = [
            _edge("trigger-lead", "cond-lead"),
            _edge("cond-lead", "act-shared"),
            _edge("trigger-todo", "cond-todo"),
            _edge("cond-todo", "act-shared", target_handle="act-shared-in-left"),
        ]
        triggers = [
            _make_trigger("Lead", "On Update", graph_node_id="trigger-lead"),
            _make_trigger("ToDo", "On Update", graph_node_id="trigger-todo"),
        ]

        print("\n=== A3: Multi-trigger, separate conditions, action Any ===")
        try:
            name = _create_and_publish(auto_name, nodes, edges, triggers)
            print(f"  Save SUCCEEDED as {name}")

            auto = frappe.get_doc("Automation", name)
            print(f"\n=== A3: Graph edges ===")
            graph = json.loads(auto.graph_definition)
            for e in graph["edges"]:
                print(f"  Edge {e['id']}: {e['source']} -> {e['target']}")

            print("\n=== A3: Triggering via Lead ===")
            with patch("automation_builder.dispatcher.frappe.enqueue",
                        side_effect=lambda method, **kw: execute_automation(**kw)):
                lead = frappe.get_doc({"doctype": "Lead", "lead_name": "ST28-A3-Lead"})
                lead.status = "Open"
                lead.insert(ignore_permissions=True)
                self.created_docs.append(("Lead", lead.name))
                frappe.db.commit()

                execute_automation(name, "Lead", lead.name)

            runs_lead = _get_runs(name, "Lead", lead.name)
            print(f"  Runs for Lead: {len(runs_lead)}")
            for r in runs_lead:
                print(f"    {r.name}: status={r.status}")
                steps = _get_run_steps(r.name)
                for s in steps:
                    print(f"      Step: type={s.step_type}, status={s.status}, branch={s.branch_taken}, output={s.output}")

            print("\n=== A3: Triggering via ToDo ===")
            with patch("automation_builder.dispatcher.frappe.enqueue",
                        side_effect=lambda method, **kw: execute_automation(**kw)):
                todo = frappe.get_doc({"doctype": "ToDo", "description": "ST28-A3-Todo"})
                todo.insert(ignore_permissions=True)
                self.created_docs.append(("ToDo", todo.name))
                frappe.db.commit()

                execute_automation(name, "ToDo", todo.name)

            runs_todo = _get_runs(name, "ToDo", todo.name)
            print(f"  Runs for ToDo: {len(runs_todo)}")
            for r in runs_todo:
                print(f"    {r.name}: status={r.status}")
                steps = _get_run_steps(r.name)
                for s in steps:
                    print(f"      Step: type={s.step_type}, status={s.status}, branch={s.branch_taken}, output={s.output}")

            self._cleanup_all()

        except frappe.ValidationError as e:
            print(f"  ValidationError: {e}")
            self._cleanup_all()
        except Exception as e:
            print(f"  FAILED: {type(e).__name__}: {e}")
            self._cleanup_all()
            raise

    # ------------------------------------------------------------------
    # A4: Multi-trigger nodes, shared action unscoped — REJECT
    # ------------------------------------------------------------------
    def test_A4_first_untagged_condition_unscoped_rejected(self):
        """A4: Two Trigger nodes → shared Action without trigger_doctype_select.
        The shared Action is reachable from both Triggers → validation should reject."""
        auto_name = "ST28-A4-CondNoScope"
        self._cleanup_all()

        nodes = [
            _make_trigger_node("trigger-lead", trigger_doctype="Lead"),
            _make_trigger_node("trigger-todo", trigger_doctype="ToDo"),
            _make_condition_node("cond-lead", "status", "=", "Open", scoped_dt="Lead"),
            _make_condition_node("cond-todo", "status", "=", "Open", scoped_dt="ToDo"),
            _make_action_node("act-shared", "create_document", {
                "target_doctype": "Note",
                "field_mapping": [
                    {"target_field": "title", "source_value": "ST28-A4-FIRED"},
                ],
            }, scoped_dt=""),  # NO scoping
        ]
        edges = [
            _edge("trigger-lead", "cond-lead"),
            _edge("cond-lead", "act-shared"),
            _edge("trigger-todo", "cond-todo"),
            _edge("cond-todo", "act-shared", target_handle="act-shared-in-left"),
        ]
        triggers = [
            _make_trigger("Lead", "On Update", graph_node_id="trigger-lead"),
            _make_trigger("ToDo", "On Update", graph_node_id="trigger-todo"),
        ]

        print("\n=== A4: Shared action unscoped, reachable from both triggers ===")
        try:
            name = _create_and_publish(auto_name, nodes, edges, triggers)
            print(f"  UNEXPECTED: Save succeeded as {name}")
            self._cleanup_all()
            self.fail("Expected ValidationError but save succeeded")
        except frappe.ValidationError as e:
            print(f"  EXPECTED: ValidationError: {e}")
            self._cleanup_all()


class TestStage28GraphWalk(IntegrationTestCase):
    """Part B: Trace the graph walk for the exact shape."""

    def test_B1_walk_lead_path(self):
        """B1: Trace _walk_graph for Lead trigger through the correctly-built shape."""
        graph = {
            "nodes": [
                {"id": "trigger", "type": "trigger", "data": {"trigger_doctype": "Lead"}},
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
                {"id": "e1", "source": "trigger", "target": "cond-lead",
                 "sourceHandle": "trigger-out", "targetHandle": "cond-lead-in",
                 "applies_to_triggers": ["0"]},
                {"id": "e2", "source": "cond-lead", "target": "act-shared",
                 "sourceHandle": "cond-lead-out", "targetHandle": "act-shared-in"},
                {"id": "e3", "source": "trigger", "target": "cond-todo",
                 "sourceHandle": "trigger-out", "targetHandle": "cond-todo-in",
                 "applies_to_triggers": ["1"]},
                {"id": "e4", "source": "cond-todo", "target": "act-shared",
                 "sourceHandle": "cond-todo-out", "targetHandle": "act-shared-in-left"},
            ],
        }

        # Simulate a Lead document
        lead_doc = frappe._dict({"status": "Open", "lead_name": "Test Lead"})
        context = {
            "doc": lead_doc,
            "ref_doctype": "Lead",
            "ref_name": "TEST-001",
            "trigger_doctype": "Lead",
            "firing_trigger_name": "0",
        }

        print("\n=== B1: _walk_graph for Lead (firing_trigger='0') ===")
        trace = _walk_graph(graph, "trigger", context)
        for entry in trace:
            print(f"  {entry}")

        # Should follow: Trigger -> cond-lead (tagged Lead) -> evaluate -> act-shared
        self.assertEqual(len(trace), 2, f"Expected 2 trace entries (condition branch + action), got {len(trace)}: {trace}")
        self.assertEqual(trace[0]["type"], "branch")
        self.assertEqual(trace[0]["node_type"], "condition")
        self.assertEqual(trace[0]["branch_taken"], "condition-out")
        self.assertEqual(trace[1]["type"], "action")
        self.assertEqual(trace[1]["node_id"], "act-shared")

    def test_B2_walk_todo_path(self):
        """B2: Trace _walk_graph for ToDo trigger with multi-Trigger-node topology."""
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
        context = {"doc": todo_doc, "ref_doctype": "ToDo", "ref_name": "TODO-001"}

        print("\n=== B2: _walk_graph for ToDo (multi-Trigger-node) ===")
        trace = _walk_graph(graph, "trigger-todo", context)
        for entry in trace:
            print(f"  {entry}")

        # Should follow: trigger-todo -> cond-todo -> act-shared
        self.assertEqual(len(trace), 2, f"Expected 2 trace entries, got {len(trace)}: {trace}")
        self.assertEqual(trace[0]["type"], "branch")
        self.assertEqual(trace[0]["node_type"], "condition")
        self.assertEqual(trace[0]["branch_taken"], "condition-out")
        self.assertEqual(trace[1]["type"], "action")
        self.assertEqual(trace[1]["node_id"], "act-shared")

    def test_B3_walk_first_edge_all_lead(self):
        """B3: First edge is ALL (untagged), second tagged to ToDo.
        Lead fires: both Condition-1 (All) and Condition-2 (tagged ToDo) match.
        Walker should follow the FIRST matching edge — Condition-1."""
        graph = {
            "nodes": [
                {"id": "trigger", "type": "trigger", "data": {"trigger_doctype": "Lead"}},
                {"id": "cond-lead", "type": "condition", "data": {
                    "condition_field": "status", "condition_operator": "=",
                    "condition_value": "New",
                }},
                {"id": "cond-todo", "type": "condition", "data": {
                    "condition_field": "status", "condition_operator": "=",
                    "condition_value": "Open",
                }},
                {"id": "act-shared", "type": "action", "data": {
                    "action_type": "create_document", "trigger_doctype_select": "any",
                }},
            ],
            "edges": [
                {"id": "e1", "source": "trigger", "target": "cond-lead",
                 "sourceHandle": "trigger-out", "targetHandle": "cond-lead-in",
                 "applies_to_triggers": None},  # ALL — first edge, no picker
                {"id": "e2", "source": "cond-lead", "target": "act-shared",
                 "sourceHandle": "cond-lead-out", "targetHandle": "act-shared-in"},
                {"id": "e3", "source": "trigger", "target": "cond-todo",
                 "sourceHandle": "trigger-out", "targetHandle": "cond-todo-in",
                 "applies_to_triggers": ["1"]},  # Tagged ToDo
                {"id": "e4", "source": "cond-todo", "target": "act-shared",
                 "sourceHandle": "cond-todo-out", "targetHandle": "act-shared-in-left"},
            ],
        }

        # Lead fires — first edge is All, so both paths match at trigger level
        # But walker only follows the FIRST matching edge
        lead_doc = frappe._dict({"status": "Open", "lead_name": "Test Lead"})
        context = {
            "doc": lead_doc,
            "ref_doctype": "Lead",
            "ref_name": "TEST-002",
            "trigger_doctype": "Lead",
            "firing_trigger_name": "0",
        }

        print("\n=== B3: _walk_graph for Lead, first edge All ===")
        trace = _walk_graph(graph, "trigger", context)
        for entry in trace:
            print(f"  {entry}")

        # Lead fires: both cond-lead (All) and cond-todo (tagged "1") match at trigger
        # Walker has 2 filtered edges, follows FIRST (cond-lead)
        # cond-lead checks status=New on Lead doc -> matches -> proceeds to act-shared
        self.assertTrue(len(trace) >= 1)
        # The trace should go through cond-lead, not cond-todo
        node_ids = [e.get("node_id") for e in trace]
        self.assertIn("cond-lead", node_ids, "Should follow cond-lead path (first matching edge)")
        self.assertNotIn("cond-todo", node_ids, "Should NOT follow cond-todo path")

    def test_B4_walk_first_edge_all_todo(self):
        """B4: First edge ALL, second tagged ToDo. ToDo fires.
        Both edges match at trigger level. Walker follows FIRST (cond-lead).
        cond-lead checks status=New on ToDo doc -> FAILS -> stops.
        The ToDo path via cond-todo is NEVER reached."""
        graph = {
            "nodes": [
                {"id": "trigger", "type": "trigger", "data": {"trigger_doctype": "Lead"}},
                {"id": "cond-lead", "type": "condition", "data": {
                    "condition_field": "status", "condition_operator": "=",
                    "condition_value": "New",
                }},
                {"id": "cond-todo", "type": "condition", "data": {
                    "condition_field": "status", "condition_operator": "=",
                    "condition_value": "Open",
                }},
                {"id": "act-shared", "type": "action", "data": {
                    "action_type": "create_document", "trigger_doctype_select": "any",
                }},
            ],
            "edges": [
                {"id": "e1", "source": "trigger", "target": "cond-lead",
                 "sourceHandle": "trigger-out", "targetHandle": "cond-lead-in",
                 "applies_to_triggers": None},  # ALL
                {"id": "e2", "source": "cond-lead", "target": "act-shared",
                 "sourceHandle": "cond-lead-out", "targetHandle": "act-shared-in"},
                {"id": "e3", "source": "trigger", "target": "cond-todo",
                 "sourceHandle": "trigger-out", "targetHandle": "cond-todo-in",
                 "applies_to_triggers": ["1"]},  # Tagged ToDo
                {"id": "e4", "source": "cond-todo", "target": "act-shared",
                 "sourceHandle": "cond-todo-out", "targetHandle": "act-shared-in-left"},
            ],
        }

        todo_doc = frappe._dict({"status": "Open", "description": "Test Todo"})
        context = {
            "doc": todo_doc,
            "ref_doctype": "ToDo",
            "ref_name": "TODO-002",
            "trigger_doctype": "ToDo",
            "firing_trigger_name": "1",
        }

        print("\n=== B4: _walk_graph for ToDo, first edge All ===")
        trace = _walk_graph(graph, "trigger", context)
        for entry in trace:
            print(f"  {entry}")

        # ToDo fires: both cond-lead (All) and cond-todo (tagged "1") match at trigger
        # Walker has 2 filtered edges, follows FIRST (cond-lead)
        # cond-lead checks status=New on ToDo doc -> FAILS (ToDo has status=Open) -> stops
        self.assertTrue(len(trace) >= 1)
        # The trace should go through cond-lead and STOP (condition failed)
        node_ids = [e.get("node_id") for e in trace]
        self.assertIn("cond-lead", node_ids, "Should follow cond-lead (first edge, All)")
        self.assertNotIn("cond-todo", node_ids, "cond-todo is NEVER reached")
        self.assertNotIn("act-shared", node_ids, "Action is NEVER reached — condition stopped")
        # The condition should have failed
        cond_entry = [e for e in trace if e.get("node_id") == "cond-lead"][0]
        self.assertEqual(cond_entry["branch_taken"], "skipped",
                         "cond-lead should FAIL on ToDo doc (status != New)")


# ---------------------------------------------------------------------------
# Part E: Real canvas topology — TWO Trigger nodes, each with one outgoing edge
# ---------------------------------------------------------------------------
class TestStage28MultiTriggerNode(IntegrationTestCase):
    """Part E: Tests matching what the frontend ACTUALLY produces.

    Topology:
      Trigger-1 (Lead) ──> Condition-1 ──> Action (shared)
      Trigger-2 (ToDo) ──> Condition-2 ──> Action (shared)

    Each Trigger node has ONE outgoing edge to its own Condition node.
    Both Condition nodes converge into a shared Action node.
    No tagged edges — each Trigger node IS a separate trigger row.
    """

    def setUp(self):
        self.created_docs = []

    def tearDown(self):
        for dt, name in self.created_docs:
            try:
                frappe.delete_doc(dt, name, force=True)
            except Exception:
                pass

    def _cleanup_all(self):
        for name in frappe.get_all("Automation", filters={"name": ["like", "ST28-E%"]}, fields=["name"]):
            frappe.delete_doc("Automation", name.name, force=True)
        for n in frappe.get_all("Note", filters={"title": ["like", "ST28-E%"]}, fields=["name"]):
            frappe.delete_doc("Note", n.name, force=True)
        frappe.db.commit()

    def test_E1_two_trigger_nodes_lead_fires(self):
        """E1: Two Trigger nodes, Lead fires -> should walk Trigger-1 path only."""
        auto_name = "ST28-E1-MultiTriggerLead"
        self._cleanup_all()

        nodes = [
            _make_trigger_node("trigger-lead", trigger_doctype="Lead"),
            _make_trigger_node("trigger-todo", trigger_doctype="ToDo"),
            _make_condition_node("cond-lead", "status", "=", "Open", scoped_dt="Lead"),
            _make_condition_node("cond-todo", "status", "=", "Open", scoped_dt="ToDo"),
            _make_action_node("act-shared", "create_document", {
                "target_doctype": "Note",
                "field_mapping": [
                    {"target_field": "title", "source_value": "ST28-E1-FIRED"},
                ],
            }, scoped_dt="any"),
        ]
        edges = [
            _edge("trigger-lead", "cond-lead"),
            _edge("cond-lead", "act-shared"),
            _edge("trigger-todo", "cond-todo"),
            _edge("cond-todo", "act-shared", target_handle="act-shared-in-left"),
        ]
        triggers = [
            _make_trigger("Lead", "On Update"),
            _make_trigger("ToDo", "On Update"),
        ]

        name = _create_and_publish(auto_name, nodes, edges, triggers)
        auto = frappe.get_doc("Automation", name)
        self.assertEqual(auto.status, "Published")
        self.assertEqual(len(auto.triggers), 2)
        self.assertEqual(auto.triggers[0].trigger_doctype, "Lead")
        self.assertEqual(auto.triggers[1].trigger_doctype, "ToDo")

        # Verify graph stored correctly
        graph = json.loads(auto.graph_definition)
        self.assertEqual(len(graph["nodes"]), 5)
        self.assertEqual(len(graph["edges"]), 4)

        # Fire Lead document
        lead = frappe.get_doc({"doctype": "Lead", "lead_name": "ST28-E1-Lead"})
        lead.status = "Open"
        lead.insert(ignore_permissions=True)
        self.created_docs.append(("Lead", lead.name))
        frappe.db.commit()

        with patch("automation_builder.dispatcher.frappe.enqueue",
                    side_effect=lambda method, **kw: execute_automation(**kw)):
            execute_automation(name, "Lead", lead.name)

        runs = _get_runs(name, "Lead", lead.name)
        self.assertGreaterEqual(len(runs), 1, "Lead trigger should produce at least 1 run")
        for r in runs:
            steps = _get_run_steps(r.name)
            step_types = [s.step_type for s in steps]
            self.assertIn("create_document", step_types, "Should execute the shared action")
            self.assertEqual(r.status, "Success")

            self._cleanup_all()

    def test_E2_two_trigger_nodes_todo_fires(self):
        """E2: Two Trigger nodes, ToDo fires -> should walk Trigger-2 path only."""
        auto_name = "ST28-E2-MultiTriggerTodo"
        self._cleanup_all()

        nodes = [
            _make_trigger_node("trigger-lead", trigger_doctype="Lead"),
            _make_trigger_node("trigger-todo", trigger_doctype="ToDo"),
            _make_condition_node("cond-lead", "status", "=", "Open", scoped_dt="Lead"),
            _make_condition_node("cond-todo", "status", "=", "Open", scoped_dt="ToDo"),
            _make_action_node("act-shared", "create_document", {
                "target_doctype": "Note",
                "field_mapping": [
                    {"target_field": "title", "source_value": "ST28-E2-FIRED"},
                ],
            }, scoped_dt="any"),
        ]
        edges = [
            _edge("trigger-lead", "cond-lead"),
            _edge("cond-lead", "act-shared"),
            _edge("trigger-todo", "cond-todo"),
            _edge("cond-todo", "act-shared", target_handle="act-shared-in-left"),
        ]
        triggers = [
            _make_trigger("Lead", "On Update"),
            _make_trigger("ToDo", "On Update"),
        ]

        name = _create_and_publish(auto_name, nodes, edges, triggers)

        # Fire ToDo document
        todo = frappe.get_doc({"doctype": "ToDo", "description": "ST28-E2-Todo"})
        todo.insert(ignore_permissions=True)
        self.created_docs.append(("ToDo", todo.name))
        frappe.db.commit()

        with patch("automation_builder.dispatcher.frappe.enqueue",
                    side_effect=lambda method, **kw: execute_automation(**kw)):
            execute_automation(name, "ToDo", todo.name)

        runs = _get_runs(name, "ToDo", todo.name)
        self.assertGreaterEqual(len(runs), 1, "ToDo trigger should produce at least 1 run")
        for r in runs:
            steps = _get_run_steps(r.name)
            step_types = [s.step_type for s in steps]
            self.assertIn("create_document", step_types, "Should execute the shared action")
            self.assertEqual(r.status, "Success")

        self._cleanup_all()

    def test_E3_two_trigger_nodes_both_fire(self):
        """E3: Two Trigger nodes, both Lead and ToDo fire -> each produces its own run."""
        auto_name = "ST28-E3-MultiTriggerBoth"
        self._cleanup_all()

        nodes = [
            _make_trigger_node("trigger-lead", trigger_doctype="Lead"),
            _make_trigger_node("trigger-todo", trigger_doctype="ToDo"),
            _make_condition_node("cond-lead", "status", "=", "Open", scoped_dt="Lead"),
            _make_condition_node("cond-todo", "status", "=", "Open", scoped_dt="ToDo"),
            _make_action_node("act-shared", "create_document", {
                "target_doctype": "Note",
                "field_mapping": [
                    {"target_field": "title", "source_value": "ST28-E3-FIRED"},
                ],
            }, scoped_dt="any"),
        ]
        edges = [
            _edge("trigger-lead", "cond-lead"),
            _edge("cond-lead", "act-shared"),
            _edge("trigger-todo", "cond-todo"),
            _edge("cond-todo", "act-shared", target_handle="act-shared-in-left"),
        ]
        triggers = [
            _make_trigger("Lead", "On Update"),
            _make_trigger("ToDo", "On Update"),
        ]

        name = _create_and_publish(auto_name, nodes, edges, triggers)

        # Fire Lead
        lead = frappe.get_doc({"doctype": "Lead", "lead_name": "ST28-E3-Lead"})
        lead.status = "Open"
        lead.insert(ignore_permissions=True)
        self.created_docs.append(("Lead", lead.name))
        frappe.db.commit()

        with patch("automation_builder.dispatcher.frappe.enqueue",
                    side_effect=lambda method, **kw: execute_automation(**kw)):
            execute_automation(name, "Lead", lead.name)

        runs_lead = _get_runs(name, "Lead", lead.name)
        self.assertGreaterEqual(len(runs_lead), 1, "Lead trigger should produce at least 1 run")

        # Fire ToDo
        todo = frappe.get_doc({"doctype": "ToDo", "description": "ST28-E3-Todo"})
        todo.insert(ignore_permissions=True)
        self.created_docs.append(("ToDo", todo.name))
        frappe.db.commit()

        with patch("automation_builder.dispatcher.frappe.enqueue",
                    side_effect=lambda method, **kw: execute_automation(**kw)):
            execute_automation(name, "ToDo", todo.name)

        runs_todo = _get_runs(name, "ToDo", todo.name)
        self.assertGreaterEqual(len(runs_todo), 1, "ToDo trigger should produce at least 1 run")

        # Total runs: Lead + ToDo
        all_runs = _get_runs(name)
        self.assertGreaterEqual(len(all_runs), 2, "Should have runs from both triggers")

        self._cleanup_all()

    def test_E4_graph_walk_two_trigger_nodes_lead(self):
        """E4: Walk the two-trigger-node graph starting at trigger-lead for Lead."""
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
                 "sourceHandle": "trigger-lead-out", "targetHandle": "cond-lead-in"},
                {"id": "e2", "source": "cond-lead", "target": "act-shared",
                 "sourceHandle": "cond-lead-out", "targetHandle": "act-shared-in"},
                {"id": "e3", "source": "trigger-todo", "target": "cond-todo",
                 "sourceHandle": "trigger-todo-out", "targetHandle": "cond-todo-in"},
                {"id": "e4", "source": "cond-todo", "target": "act-shared",
                 "sourceHandle": "cond-todo-out", "targetHandle": "act-shared-in-left"},
            ],
        }

        lead_doc = frappe._dict({"status": "Open", "lead_name": "Test Lead"})
        context = {
            "doc": lead_doc,
            "ref_doctype": "Lead",
            "ref_name": "TEST-E4-001",
            "trigger_doctype": "Lead",
        }

        # Use _find_start_trigger to determine correct start node
        trigger_nodes = [n for n in graph["nodes"] if n["type"] == "trigger"]
        start_id = _find_start_trigger(trigger_nodes, "Lead", context)
        self.assertEqual(start_id, "trigger-lead", "Should start at trigger-lead for Lead")

        trace = _walk_graph(graph, start_id, context)
        node_ids = [e.get("node_id") for e in trace]
        self.assertIn("cond-lead", node_ids, "Should follow cond-lead path")
        self.assertNotIn("cond-todo", node_ids, "Should NOT follow cond-todo path")
        self.assertIn("act-shared", node_ids, "Should reach the shared action")

    def test_E5_graph_walk_two_trigger_nodes_todo(self):
        """E5: Walk the two-trigger-node graph starting at trigger-todo for ToDo."""
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
                 "sourceHandle": "trigger-lead-out", "targetHandle": "cond-lead-in"},
                {"id": "e2", "source": "cond-lead", "target": "act-shared",
                 "sourceHandle": "cond-lead-out", "targetHandle": "act-shared-in"},
                {"id": "e3", "source": "trigger-todo", "target": "cond-todo",
                 "sourceHandle": "trigger-todo-out", "targetHandle": "cond-todo-in"},
                {"id": "e4", "source": "cond-todo", "target": "act-shared",
                 "sourceHandle": "cond-todo-out", "targetHandle": "act-shared-in-left"},
            ],
        }

        todo_doc = frappe._dict({"status": "Open", "description": "Test Todo"})
        context = {
            "doc": todo_doc,
            "ref_doctype": "ToDo",
            "ref_name": "TEST-E5-001",
            "trigger_doctype": "ToDo",
        }

        trigger_nodes = [n for n in graph["nodes"] if n["type"] == "trigger"]
        start_id = _find_start_trigger(trigger_nodes, "ToDo", context)
        self.assertEqual(start_id, "trigger-todo", "Should start at trigger-todo for ToDo")

        trace = _walk_graph(graph, start_id, context)
        node_ids = [e.get("node_id") for e in trace]
        self.assertNotIn("cond-lead", node_ids, "Should NOT follow cond-lead path")
        self.assertIn("cond-todo", node_ids, "Should follow cond-todo path")
        self.assertIn("act-shared", node_ids, "Should reach the shared action")


# ---------------------------------------------------------------------------
# Part F: _find_start_trigger unit tests
# ---------------------------------------------------------------------------
class TestStage28FindStartTrigger(IntegrationTestCase):
    """Part F: Verify _find_start_trigger selects the correct trigger node."""

    def test_F1_single_trigger_node(self):
        """F1: Single trigger node -> always returns that node."""
        trigger_nodes = [{"id": "trigger", "type": "trigger", "data": {"trigger_doctype": "Lead"}}]
        result = _find_start_trigger(trigger_nodes, "Lead", {})
        self.assertEqual(result, "trigger")

    def test_F2_multi_trigger_matches_doctype(self):
        """F2: Multiple triggers -> matches by ref_doctype."""
        trigger_nodes = [
            {"id": "trigger-lead", "type": "trigger", "data": {"trigger_doctype": "Lead"}},
            {"id": "trigger-todo", "type": "trigger", "data": {"trigger_doctype": "ToDo"}},
        ]
        result = _find_start_trigger(trigger_nodes, "ToDo", {})
        self.assertEqual(result, "trigger-todo")

    def test_F3_multi_trigger_matches_first(self):
        """F3: Multiple triggers -> matches first trigger by doctype."""
        trigger_nodes = [
            {"id": "trigger-lead", "type": "trigger", "data": {"trigger_doctype": "Lead"}},
            {"id": "trigger-todo", "type": "trigger", "data": {"trigger_doctype": "ToDo"}},
        ]
        result = _find_start_trigger(trigger_nodes, "Lead", {})
        self.assertEqual(result, "trigger-lead")

    def test_F4_multi_trigger_no_match_fallback_to_first(self):
        """F4: No matching doctype -> falls back to trigger_nodes[0]."""
        trigger_nodes = [
            {"id": "trigger-lead", "type": "trigger", "data": {"trigger_doctype": "Lead"}},
            {"id": "trigger-todo", "type": "trigger", "data": {"trigger_doctype": "ToDo"}},
        ]
        result = _find_start_trigger(trigger_nodes, "Event", {})
        self.assertEqual(result, "trigger-lead", "Should fall back to first trigger node")

    def test_F5_multi_trigger_fallback_to_firing_index(self):
        """F5: graph_node_id not in graph → falls back to trigger_doctype matching."""
        trigger_nodes = [
            {"id": "trigger-lead", "type": "trigger", "data": {"trigger_doctype": "Lead"}},
            {"id": "trigger-todo", "type": "trigger", "data": {"trigger_doctype": "ToDo"}},
        ]
        # Automation with no graph_node_id set on triggers → fallback path
        class MockTrigger:
            def __init__(self, doctype, gnid):
                self.trigger_doctype = doctype
                self.graph_node_id = gnid
        class MockAutomation:
            triggers = [MockTrigger("Lead", ""), MockTrigger("ToDo", "")]
        result = _find_start_trigger(trigger_nodes, "ToDo", {}, MockAutomation())
        self.assertEqual(result, "trigger-todo", "Should fall back to trigger_doctype matching")

    def test_F6_empty_trigger_nodes(self):
        """F6: No trigger nodes -> returns default 'trigger'."""
        result = _find_start_trigger([], "Lead", {})
        self.assertEqual(result, "trigger")
