"""Tests for Stage 18 — IF/Switch branching nodes.

Covers:
- IF branch selection logic (true and false paths)
- Switch branch selection logic (case match and default)
- Full execution with branching nodes through dispatcher
- Automation Run Step logging
- Mid-chain node removal re-check (open risk from Stage 17a)
- Graph save/load roundtrip with branching nodes
"""

import json

import frappe
from frappe.tests import IntegrationTestCase

from automation_builder.action_types import get_action_type, get_all_action_types
from automation_builder.dispatcher import (
    _evaluate_branching_node,
    _walk_graph,
    _extract_actions_from_graph,
    execute_automation,
)


class TestBranchingRegistry(IntegrationTestCase):
    """Verify IF and Switch are registered correctly in the action_types registry."""

    def test_if_registered_as_logic(self):
        at = get_action_type("if_condition")
        self.assertIsNotNone(at)
        self.assertEqual(at["node_category"], "logic")
        self.assertEqual(at["label"], "IF")
        self.assertIn("evaluate_branch", at)

    def test_switch_registered_as_logic(self):
        at = get_action_type("switch_case")
        self.assertIsNotNone(at)
        self.assertEqual(at["node_category"], "logic")
        self.assertEqual(at["label"], "Switch")
        self.assertIn("evaluate_branch", at)
        self.assertIn("get_output_handles", at)

    def test_all_action_types_includes_logic(self):
        types = get_all_action_types()
        categories = {v["node_category"] for v in types.values()}
        self.assertIn("action", categories)
        self.assertIn("logic", categories)
        self.assertEqual(types["if_condition"]["node_category"], "logic")
        self.assertEqual(types["switch_case"]["node_category"], "logic")

    def test_if_output_handles_static(self):
        at = get_action_type("if_condition")
        handles = at["output_handles"]
        self.assertEqual(len(handles), 2)
        self.assertEqual(handles[0]["id"], "if-true")
        self.assertEqual(handles[1]["id"], "if-false")

    def test_switch_output_handles_dynamic(self):
        at = get_action_type("switch_case")
        self.assertEqual(at["output_handles"], "dynamic")

        config = {
            "cases": [
                {"case_value": "New"},
                {"case_value": "Contacted"},
                {"case_value": "Qualified"},
            ]
        }
        handles = at["get_output_handles"](config)
        self.assertEqual(len(handles), 4)  # 3 cases + default
        self.assertEqual(handles[0]["id"], "case-0")
        self.assertEqual(handles[1]["id"], "case-1")
        self.assertEqual(handles[2]["id"], "case-2")
        self.assertEqual(handles[3]["id"], "default")


class TestIFBranchSelection(IntegrationTestCase):
    """Test IF condition evaluation and branch selection."""

    def setUp(self):
        frappe.db.sql("DELETE FROM `tabAutomation Run` WHERE automation LIKE 'TEST-IF%'")
        frappe.db.sql("DELETE FROM `tabAutomation Trigger` WHERE parent LIKE 'TEST-IF%'")
        frappe.db.sql("DELETE FROM `tabAutomation` WHERE name LIKE 'TEST-IF%'")
        frappe.db.commit()

    def tearDown(self):
        frappe.db.sql("DELETE FROM `tabAutomation Run Step` WHERE parent IN (SELECT name FROM `tabAutomation Run` WHERE automation LIKE 'TEST-IF%')")
        frappe.db.sql("DELETE FROM `tabAutomation Run` WHERE automation LIKE 'TEST-IF%'")
        frappe.db.sql("DELETE FROM `tabAutomation Trigger` WHERE parent LIKE 'TEST-IF%'")
        frappe.db.sql("DELETE FROM `tabAutomation` WHERE name LIKE 'TEST-IF%'")
        frappe.db.commit()

    def _make_if_graph(self, field, operator, value):
        return {
            "nodes": [
                {"id": "trigger", "type": "trigger", "position": {"x": 0, "y": 0}, "data": {"trigger_doctype": "ToDo", "trigger_event": "On Update"}},
                {"id": "if-1", "type": "if", "position": {"x": 0, "y": 170}, "data": {"field_to_check": field, "operator": operator, "value": value}},
                {"id": "action-true", "type": "action", "position": {"x": 100, "y": 340}, "data": {"action_type": "telegram", "chat_id": "TEST", "message": "TRUE branch"}},
                {"id": "action-false", "type": "action", "position": {"x": 300, "y": 340}, "data": {"action_type": "telegram", "chat_id": "TEST", "message": "FALSE branch"}},
            ],
            "edges": [
                {"source": "trigger", "target": "if-1", "sourceHandle": "trigger-out", "targetHandle": "if-in"},
                {"source": "if-1", "target": "action-true", "sourceHandle": "if-true", "targetHandle": "action-true-in"},
                {"source": "if-1", "target": "action-false", "sourceHandle": "if-false", "targetHandle": "action-false-in"},
            ],
        }

    def test_if_true_branch(self):
        graph = self._make_if_graph("status", "=", "Open")
        todo = frappe.get_doc({"doctype": "ToDo", "description": "IF true test", "status": "Open"})
        ctx = {"doc": todo, "ref_doctype": "ToDo", "ref_name": todo.name}

        trace = _walk_graph(graph, "trigger", context=ctx)
        node_ids = [e["node_id"] for e in trace if e["type"] != "branch"]
        self.assertIn("action-true", node_ids)
        self.assertNotIn("action-false", node_ids)

        branch_entries = [e for e in trace if e["type"] == "branch"]
        self.assertEqual(len(branch_entries), 1)
        self.assertEqual(branch_entries[0]["branch_taken"], "true")
        self.assertIn("TRUE", branch_entries[0]["output"])

    def test_if_false_branch(self):
        graph = self._make_if_graph("status", "=", "Open")
        todo = frappe.get_doc({"doctype": "ToDo", "description": "IF false test", "status": "Closed"})
        ctx = {"doc": todo, "ref_doctype": "ToDo", "ref_name": todo.name}

        trace = _walk_graph(graph, "trigger", context=ctx)
        node_ids = [e["node_id"] for e in trace if e["type"] != "branch"]
        self.assertIn("action-false", node_ids)
        self.assertNotIn("action-true", node_ids)

        branch_entries = [e for e in trace if e["type"] == "branch"]
        self.assertEqual(len(branch_entries), 1)
        self.assertEqual(branch_entries[0]["branch_taken"], "false")
        self.assertIn("FALSE", branch_entries[0]["output"])

    def test_if_not_equal_operator(self):
        graph = self._make_if_graph("status", "!=", "Open")
        todo = frappe.get_doc({"doctype": "ToDo", "description": "IF neq test", "status": "Closed"})
        ctx = {"doc": todo, "ref_doctype": "ToDo", "ref_name": todo.name}

        trace = _walk_graph(graph, "trigger", context=ctx)
        node_ids = [e["node_id"] for e in trace if e["type"] != "branch"]
        self.assertIn("action-true", node_ids)

    def test_if_gt_operator(self):
        graph = self._make_if_graph("priority", ">", "5")
        todo = frappe.get_doc({"doctype": "ToDo", "description": "IF gt test"})
        todo.priority = "10"
        ctx = {"doc": todo, "ref_doctype": "ToDo", "ref_name": todo.name}

        trace = _walk_graph(graph, "trigger", context=ctx)
        node_ids = [e["node_id"] for e in trace if e["type"] != "branch"]
        self.assertIn("action-true", node_ids)


class TestSwitchBranchSelection(IntegrationTestCase):
    """Test Switch case evaluation and branch selection."""

    def setUp(self):
        frappe.db.sql("DELETE FROM `tabAutomation Run` WHERE automation LIKE 'TEST-Switch%'")
        frappe.db.sql("DELETE FROM `tabAutomation Trigger` WHERE parent LIKE 'TEST-Switch%'")
        frappe.db.sql("DELETE FROM `tabAutomation` WHERE name LIKE 'TEST-Switch%'")
        frappe.db.commit()

    def tearDown(self):
        frappe.db.sql("DELETE FROM `tabAutomation Run Step` WHERE parent IN (SELECT name FROM `tabAutomation Run` WHERE automation LIKE 'TEST-Switch%')")
        frappe.db.sql("DELETE FROM `tabAutomation Run` WHERE automation LIKE 'TEST-Switch%'")
        frappe.db.sql("DELETE FROM `tabAutomation Trigger` WHERE parent LIKE 'TEST-Switch%'")
        frappe.db.sql("DELETE FROM `tabAutomation` WHERE name LIKE 'TEST-Switch%'")
        frappe.db.commit()

    def _make_switch_graph(self, field, cases, default_msg="DEFAULT"):
        nodes = [
            {"id": "trigger", "type": "trigger", "position": {"x": 0, "y": 0}, "data": {"trigger_doctype": "ToDo", "trigger_event": "On Update"}},
            {"id": "switch-1", "type": "switch", "position": {"x": 0, "y": 170}, "data": {"field_to_check": field, "cases": cases}},
        ]
        edges = [
            {"source": "trigger", "target": "switch-1", "sourceHandle": "trigger-out", "targetHandle": "switch-in"},
        ]

        for i, case in enumerate(cases):
            action_id = f"action-case-{i}"
            nodes.append({
                "id": action_id, "type": "action",
                "position": {"x": i * 200, "y": 340},
                "data": {"action_type": "telegram", "chat_id": "TEST", "message": f"Case {i}: {case['case_value']}"},
            })
            edges.append({
                "source": "switch-1", "target": action_id,
                "sourceHandle": f"case-{i}", "targetHandle": f"{action_id}-in",
            })

        # Default action
        nodes.append({
            "id": "action-default", "type": "action",
            "position": {"x": len(cases) * 200, "y": 340},
            "data": {"action_type": "telegram", "chat_id": "TEST", "message": default_msg},
        })
        edges.append({
            "source": "switch-1", "target": "action-default",
            "sourceHandle": "default", "targetHandle": "action-default-in",
        })

        return {"nodes": nodes, "edges": edges}

    def test_switch_case_match(self):
        cases = [{"case_value": "New"}, {"case_value": "Open"}, {"case_value": "Closed"}]
        graph = self._make_switch_graph("status", cases)
        todo = frappe.get_doc({"doctype": "ToDo", "description": "Switch match test", "status": "Open"})
        ctx = {"doc": todo, "ref_doctype": "ToDo", "ref_name": todo.name}

        trace = _walk_graph(graph, "trigger", context=ctx)
        node_ids = [e["node_id"] for e in trace if e["type"] != "branch"]
        self.assertIn("action-case-1", node_ids)  # "Open" is case index 1
        self.assertNotIn("action-case-0", node_ids)
        self.assertNotIn("action-case-2", node_ids)
        self.assertNotIn("action-default", node_ids)

        branch_entries = [e for e in trace if e["type"] == "branch"]
        self.assertEqual(branch_entries[0]["branch_taken"], "case-1")

    def test_switch_default_when_no_match(self):
        cases = [{"case_value": "New"}, {"case_value": "Open"}]
        graph = self._make_switch_graph("status", cases)
        todo = frappe.get_doc({"doctype": "ToDo", "description": "Switch default test", "status": "Qualified"})
        ctx = {"doc": todo, "ref_doctype": "ToDo", "ref_name": todo.name}

        trace = _walk_graph(graph, "trigger", context=ctx)
        node_ids = [e["node_id"] for e in trace if e["type"] != "branch"]
        self.assertIn("action-default", node_ids)
        self.assertNotIn("action-case-0", node_ids)
        self.assertNotIn("action-case-1", node_ids)

        branch_entries = [e for e in trace if e["type"] == "branch"]
        self.assertEqual(branch_entries[0]["branch_taken"], "default")

    def test_switch_first_case_match(self):
        cases = [{"case_value": "New"}, {"case_value": "Open"}]
        graph = self._make_switch_graph("status", cases)
        todo = frappe.get_doc({"doctype": "ToDo", "description": "Switch first case test", "status": "New"})
        ctx = {"doc": todo, "ref_doctype": "ToDo", "ref_name": todo.name}

        trace = _walk_graph(graph, "trigger", context=ctx)
        node_ids = [e["node_id"] for e in trace if e["type"] != "branch"]
        self.assertIn("action-case-0", node_ids)

    def test_switch_empty_field_matches_default(self):
        cases = [{"case_value": "New"}]
        graph = self._make_switch_graph("status", cases)
        todo = frappe.get_doc({"doctype": "ToDo", "description": "Switch empty test"})
        ctx = {"doc": todo, "ref_doctype": "ToDo", "ref_name": todo.name}

        trace = _walk_graph(graph, "trigger", context=ctx)
        node_ids = [e["node_id"] for e in trace if e["type"] != "branch"]
        self.assertIn("action-default", node_ids)


class TestBranchingExecution(IntegrationTestCase):
    """Test full execution with branching nodes through execute_automation."""

    def setUp(self):
        frappe.db.sql("DELETE FROM `tabAutomation Run Step` WHERE parent IN (SELECT name FROM `tabAutomation Run` WHERE automation LIKE 'TEST-Exec%')")
        frappe.db.sql("DELETE FROM `tabAutomation Run` WHERE automation LIKE 'TEST-Exec%'")
        frappe.db.sql("DELETE FROM `tabAutomation Trigger` WHERE parent LIKE 'TEST-Exec%'")
        frappe.db.sql("DELETE FROM `tabAutomation` WHERE name LIKE 'TEST-Exec%'")
        frappe.db.commit()

    def tearDown(self):
        frappe.db.sql("DELETE FROM `tabAutomation Run Step` WHERE parent IN (SELECT name FROM `tabAutomation Run` WHERE automation LIKE 'TEST-Exec%')")
        frappe.db.sql("DELETE FROM `tabAutomation Run` WHERE automation LIKE 'TEST-Exec%'")
        frappe.db.sql("DELETE FROM `tabAutomation Trigger` WHERE parent LIKE 'TEST-Exec%'")
        frappe.db.sql("DELETE FROM `tabAutomation` WHERE name LIKE 'TEST-Exec%'")
        frappe.db.commit()

    def test_if_execution_creates_run_and_steps(self):
        graph = {
            "nodes": [
                {"id": "trigger", "type": "trigger", "position": {"x": 0, "y": 0}, "data": {"trigger_doctype": "ToDo", "trigger_event": "On Update"}},
                {"id": "if-1", "type": "if", "position": {"x": 0, "y": 170}, "data": {"field_to_check": "status", "operator": "=", "value": "Open"}},
                {"id": "action-true", "type": "action", "position": {"x": 100, "y": 340}, "data": {"action_type": "telegram", "chat_id": "TEST", "message": "Status is Open"}},
                {"id": "action-false", "type": "action", "position": {"x": 300, "y": 340}, "data": {"action_type": "telegram", "chat_id": "TEST", "message": "Status is NOT Open"}},
            ],
            "edges": [
                {"source": "trigger", "target": "if-1", "sourceHandle": "trigger-out", "targetHandle": "if-in"},
                {"source": "if-1", "target": "action-true", "sourceHandle": "if-true", "targetHandle": "action-true-in"},
                {"source": "if-1", "target": "action-false", "sourceHandle": "if-false", "targetHandle": "action-false-in"},
            ],
        }

        auto = frappe.get_doc({
            "doctype": "Automation",
            "automation_name": "TEST-Exec-IF",
            "status": "Published",
            "enabled": 1,
            "triggers": [{"trigger_doctype": "ToDo", "trigger_event": "On Update"}],
            "graph_definition": json.dumps(graph),
        })
        auto.insert(ignore_permissions=True)
        frappe.db.commit()

        # Create a ToDo that matches the IF condition
        todo = frappe.get_doc({"doctype": "ToDo", "description": "IF exec test", "status": "Open"})
        todo.insert(ignore_permissions=True)
        frappe.db.commit()

        # Execute directly (bypassing enqueue)
        execute_automation(auto.name, "ToDo", todo.name)

        # Check Automation Run was created
        runs = frappe.get_all("Automation Run", filters={"automation": auto.name}, fields=["name", "status", "log"])
        self.assertTrue(len(runs) > 0)
        run = runs[0]
        # Status may be "Failed" if Telegram sends to non-existent chat — that's OK for testing branch selection
        # The key assertion is that the CORRECT branch was taken

        # Check log contains branch decision
        log = json.loads(run.log)
        branch_steps = [s for s in log if s.get("step_type") == "if"]
        self.assertTrue(len(branch_steps) > 0)
        self.assertEqual(branch_steps[0]["branch_taken"], "true")

        # Check Automation Run Step records exist
        steps = frappe.get_all("Automation Run Step", filters={"parent": run.name}, fields=["node_id", "node_type", "step_type", "branch_taken", "status"])
        self.assertTrue(len(steps) > 0)
        branch_step_rec = [s for s in steps if s.node_type == "if"]
        self.assertTrue(len(branch_step_rec) > 0)
        self.assertEqual(branch_step_rec[0].branch_taken, "true")
        self.assertEqual(branch_step_rec[0].status, "Success")

    def test_if_execution_false_branch(self):
        graph = {
            "nodes": [
                {"id": "trigger", "type": "trigger", "position": {"x": 0, "y": 0}, "data": {"trigger_doctype": "ToDo", "trigger_event": "On Update"}},
                {"id": "if-1", "type": "if", "position": {"x": 0, "y": 170}, "data": {"field_to_check": "status", "operator": "=", "value": "Open"}},
                {"id": "action-true", "type": "action", "position": {"x": 100, "y": 340}, "data": {"action_type": "telegram", "chat_id": "TEST", "message": "TRUE"}},
                {"id": "action-false", "type": "action", "position": {"x": 300, "y": 340}, "data": {"action_type": "telegram", "chat_id": "TEST", "message": "FALSE"}},
            ],
            "edges": [
                {"source": "trigger", "target": "if-1", "sourceHandle": "trigger-out", "targetHandle": "if-in"},
                {"source": "if-1", "target": "action-true", "sourceHandle": "if-true", "targetHandle": "action-true-in"},
                {"source": "if-1", "target": "action-false", "sourceHandle": "if-false", "targetHandle": "action-false-in"},
            ],
        }

        auto = frappe.get_doc({
            "doctype": "Automation",
            "automation_name": "TEST-Exec-IF-False",
            "status": "Published",
            "enabled": 1,
            "triggers": [{"trigger_doctype": "ToDo", "trigger_event": "On Update"}],
            "graph_definition": json.dumps(graph),
        })
        auto.insert(ignore_permissions=True)
        frappe.db.commit()

        todo = frappe.get_doc({"doctype": "ToDo", "description": "IF false exec test", "status": "Closed"})
        todo.insert(ignore_permissions=True)
        frappe.db.commit()

        execute_automation(auto.name, "ToDo", todo.name)

        runs = frappe.get_all("Automation Run", filters={"automation": auto.name}, fields=["name", "status", "log"])
        self.assertTrue(len(runs) > 0)
        run = runs[0]
        # Status may be "Failed" if Telegram sends to non-existent chat — that's OK

        log = json.loads(run.log)
        # Should have branch step + action step (telegram on false branch)
        step_types = [s["step_type"] for s in log]
        self.assertIn("if", step_types)
        self.assertIn("telegram", step_types)

        branch_step = [s for s in log if s["step_type"] == "if"][0]
        self.assertEqual(branch_step["branch_taken"], "false")


class TestMidChainRemovalRecheck(IntegrationTestCase):
    """Re-check mid-chain node removal — flagged as open risk since Stage 17a.

    This test was NOT manually re-confirmed after the graph schema migration
    in 17a/17b. We are testing it properly here.
    """

    def test_mid_chain_removal_three_node_linear(self):
        """Remove middle node from: trigger → condition → action-1 → action-2
        Expected: trigger → action-2 (edges reconnect)
        """
        graph = {
            "nodes": [
                {"id": "trigger", "type": "trigger", "position": {"x": 0, "y": 0}, "data": {}},
                {"id": "condition", "type": "condition", "position": {"x": 0, "y": 170}, "data": {}},
                {"id": "action-1", "type": "action", "position": {"x": 0, "y": 340}, "data": {"action_type": "create_document"}},
                {"id": "action-2", "type": "action", "position": {"x": 0, "y": 510}, "data": {"action_type": "send_email"}},
            ],
            "edges": [
                {"source": "trigger", "target": "condition", "sourceHandle": "trigger-out", "targetHandle": "condition-in"},
                {"source": "condition", "target": "action-1", "sourceHandle": "condition-out", "targetHandle": "action-1-in"},
                {"source": "action-1", "target": "action-2", "sourceHandle": "action-1-out", "targetHandle": "action-2-in"},
            ],
        }

        removed_id = "condition"

        # Simulate removal: find prev/next edges, remove node and its edges, reconnect
        prev_edge = next((e for e in graph["edges"] if e["target"] == removed_id), None)
        next_edge = next((e for e in graph["edges"] if e["source"] == removed_id), None)

        graph["nodes"] = [n for n in graph["nodes"] if n["id"] != removed_id]
        graph["edges"] = [e for e in graph["edges"] if e["source"] != removed_id and e["target"] != removed_id]

        if prev_edge and next_edge:
            graph["edges"].append({
                "source": prev_edge["source"],
                "target": next_edge["target"],
                "sourceHandle": prev_edge["sourceHandle"],
                "targetHandle": next_edge["targetHandle"],
                "type": "smoothstep",
            })

        actions = _extract_actions_from_graph(graph)
        self.assertEqual(len(actions), 2)
        self.assertEqual(actions[0]["type"], "create_document")
        self.assertEqual(actions[1]["type"], "send_email")

    def test_mid_chain_removal_middle_of_five(self):
        """Remove middle node from a 5-node chain."""
        graph = {
            "nodes": [
                {"id": "trigger", "type": "trigger", "position": {"x": 0, "y": 0}, "data": {}},
                {"id": "action-1", "type": "action", "position": {"x": 0, "y": 170}, "data": {"action_type": "create_document"}},
                {"id": "action-2", "type": "action", "position": {"x": 0, "y": 340}, "data": {"action_type": "send_email"}},
                {"id": "action-3", "type": "action", "position": {"x": 0, "y": 510}, "data": {"action_type": "telegram"}},
                {"id": "action-4", "type": "action", "position": {"x": 0, "y": 680}, "data": {"action_type": "http_request"}},
            ],
            "edges": [
                {"source": "trigger", "target": "action-1", "sourceHandle": "trigger-out", "targetHandle": "action-1-in"},
                {"source": "action-1", "target": "action-2", "sourceHandle": "action-1-out", "targetHandle": "action-2-in"},
                {"source": "action-2", "target": "action-3", "sourceHandle": "action-2-out", "targetHandle": "action-3-in"},
                {"source": "action-3", "target": "action-4", "sourceHandle": "action-3-out", "targetHandle": "action-4-in"},
            ],
        }

        removed_id = "action-2"

        prev_edge = next((e for e in graph["edges"] if e["target"] == removed_id), None)
        next_edge = next((e for e in graph["edges"] if e["source"] == removed_id), None)

        graph["nodes"] = [n for n in graph["nodes"] if n["id"] != removed_id]
        graph["edges"] = [e for e in graph["edges"] if e["source"] != removed_id and e["target"] != removed_id]

        if prev_edge and next_edge:
            graph["edges"].append({
                "source": prev_edge["source"],
                "target": next_edge["target"],
                "sourceHandle": prev_edge["sourceHandle"],
                "targetHandle": next_edge["targetHandle"],
                "type": "smoothstep",
            })

        actions = _extract_actions_from_graph(graph)
        self.assertEqual(len(actions), 3)
        self.assertEqual(actions[0]["type"], "create_document")
        self.assertEqual(actions[1]["type"], "telegram")
        self.assertEqual(actions[2]["type"], "http_request")

    def test_mid_chain_removal_with_branching(self):
        """Remove an IF node from a branching graph — should disconnect both branches."""
        graph = {
            "nodes": [
                {"id": "trigger", "type": "trigger", "position": {"x": 0, "y": 0}, "data": {}},
                {"id": "if-1", "type": "if", "position": {"x": 0, "y": 170}, "data": {"field_to_check": "status", "operator": "=", "value": "Open"}},
                {"id": "action-true", "type": "action", "position": {"x": 100, "y": 340}, "data": {"action_type": "send_email"}},
                {"id": "action-false", "type": "action", "position": {"x": 300, "y": 340}, "data": {"action_type": "telegram"}},
            ],
            "edges": [
                {"source": "trigger", "target": "if-1", "sourceHandle": "trigger-out", "targetHandle": "if-in"},
                {"source": "if-1", "target": "action-true", "sourceHandle": "if-true", "targetHandle": "action-true-in"},
                {"source": "if-1", "target": "action-false", "sourceHandle": "if-false", "targetHandle": "action-false-in"},
            ],
        }

        removed_id = "if-1"

        # For branching node, there's a single incoming edge but multiple outgoing
        prev_edge = next((e for e in graph["edges"] if e["target"] == removed_id), None)
        next_edges = [e for e in graph["edges"] if e["source"] == removed_id]

        graph["nodes"] = [n for n in graph["nodes"] if n["id"] != removed_id]
        graph["edges"] = [e for e in graph["edges"] if e["source"] != removed_id and e["target"] != removed_id]

        # After removing IF, both action nodes become disconnected (orphaned)
        # This is expected — user would need to reconnect manually
        actions = _extract_actions_from_graph(graph)
        self.assertEqual(len(actions), 0, "Both action nodes should be orphaned after IF removal")


class TestGraphSaveLoadWithBranching(IntegrationTestCase):
    """Verify branching nodes survive JSON roundtrip."""

    def test_if_roundtrip(self):
        graph = {
            "nodes": [
                {"id": "trigger", "type": "trigger", "position": {"x": 0, "y": 0}, "data": {}},
                {"id": "if-1", "type": "if", "position": {"x": 0, "y": 170}, "data": {"field_to_check": "status", "operator": "=", "value": "Open"}},
                {"id": "action-true", "type": "action", "position": {"x": 100, "y": 340}, "data": {"action_type": "send_email"}},
                {"id": "action-false", "type": "action", "position": {"x": 300, "y": 340}, "data": {"action_type": "telegram"}},
            ],
            "edges": [
                {"source": "trigger", "target": "if-1", "sourceHandle": "trigger-out", "targetHandle": "if-in"},
                {"source": "if-1", "target": "action-true", "sourceHandle": "if-true", "targetHandle": "action-true-in"},
                {"source": "if-1", "target": "action-false", "sourceHandle": "if-false", "targetHandle": "action-false-in"},
            ],
        }

        saved = json.dumps(graph)
        loaded = json.loads(saved)

        self.assertEqual(len(loaded["nodes"]), 4)
        if_node = [n for n in loaded["nodes"] if n["type"] == "if"][0]
        self.assertEqual(if_node["data"]["field_to_check"], "status")
        self.assertEqual(if_node["data"]["operator"], "=")
        self.assertEqual(if_node["data"]["value"], "Open")

        if_edges = [e for e in loaded["edges"] if e["source"] == "if-1"]
        self.assertEqual(len(if_edges), 2)
        handles = {e["sourceHandle"] for e in if_edges}
        self.assertEqual(handles, {"if-true", "if-false"})

    def test_switch_roundtrip(self):
        graph = {
            "nodes": [
                {"id": "trigger", "type": "trigger", "position": {"x": 0, "y": 0}, "data": {}},
                {"id": "switch-1", "type": "switch", "position": {"x": 0, "y": 170}, "data": {"field_to_check": "status", "cases": [{"case_value": "New"}, {"case_value": "Open"}]}},
                {"id": "action-0", "type": "action", "position": {"x": 0, "y": 340}, "data": {"action_type": "send_email"}},
                {"id": "action-1", "type": "action", "position": {"x": 200, "y": 340}, "data": {"action_type": "telegram"}},
                {"id": "action-default", "type": "action", "position": {"x": 400, "y": 340}, "data": {"action_type": "http_request"}},
            ],
            "edges": [
                {"source": "trigger", "target": "switch-1", "sourceHandle": "trigger-out", "targetHandle": "switch-in"},
                {"source": "switch-1", "target": "action-0", "sourceHandle": "case-0", "targetHandle": "action-0-in"},
                {"source": "switch-1", "target": "action-1", "sourceHandle": "case-1", "targetHandle": "action-1-in"},
                {"source": "switch-1", "target": "action-default", "sourceHandle": "default", "targetHandle": "action-default-in"},
            ],
        }

        saved = json.dumps(graph)
        loaded = json.loads(saved)

        switch_node = [n for n in loaded["nodes"] if n["type"] == "switch"][0]
        self.assertEqual(len(switch_node["data"]["cases"]), 2)
        self.assertEqual(switch_node["data"]["cases"][0]["case_value"], "New")
        self.assertEqual(switch_node["data"]["cases"][1]["case_value"], "Open")

        switch_edges = [e for e in loaded["edges"] if e["source"] == "switch-1"]
        self.assertEqual(len(switch_edges), 3)
        handles = {e["sourceHandle"] for e in switch_edges}
        self.assertEqual(handles, {"case-0", "case-1", "default"})


class TestTriggerDoctypePseudoField(IntegrationTestCase):
    """Test the __trigger_doctype__ pseudo-field in IF and Switch nodes.

    Both tests exercise the REAL dispatch path:
        doc.insert() -> hooks.py -> on_doc_event() -> SQL dispatch query
        -> frappe.enqueue (patched synchronous) -> execute_automation()
        -> graph walk -> evaluate_branch()
    """

    def setUp(self):
        frappe.db.sql("DELETE FROM `tabAutomation Run Step` WHERE parent IN (SELECT name FROM `tabAutomation Run` WHERE automation LIKE 'TEST-PseudoField%')")
        frappe.db.sql("DELETE FROM `tabAutomation Run` WHERE automation LIKE 'TEST-PseudoField%'")
        frappe.db.sql("DELETE FROM `tabAutomation Trigger Condition` WHERE parent IN (SELECT name FROM `tabAutomation Trigger` WHERE parent LIKE 'TEST-PseudoField%')")
        frappe.db.sql("DELETE FROM `tabAutomation Trigger` WHERE parent LIKE 'TEST-PseudoField%'")
        frappe.db.sql("DELETE FROM `tabAutomation` WHERE name LIKE 'TEST-PseudoField%'")
        frappe.db.commit()

    def tearDown(self):
        frappe.db.sql("DELETE FROM `tabAutomation Run Step` WHERE parent IN (SELECT name FROM `tabAutomation Run` WHERE automation LIKE 'TEST-PseudoField%')")
        frappe.db.sql("DELETE FROM `tabAutomation Run` WHERE automation LIKE 'TEST-PseudoField%'")
        frappe.db.sql("DELETE FROM `tabAutomation Trigger Condition` WHERE parent IN (SELECT name FROM `tabAutomation Trigger` WHERE parent LIKE 'TEST-PseudoField%')")
        frappe.db.sql("DELETE FROM `tabAutomation Trigger` WHERE parent LIKE 'TEST-PseudoField%'")
        frappe.db.sql("DELETE FROM `tabAutomation` WHERE name LIKE 'TEST-PseudoField%'")
        frappe.db.commit()

    def test_if_trigger_doctype_branch(self):
        """FULL REAL PATH: IF node with __trigger_doctype__ branches on triggering doctype.

        Path: doc.insert() -> hooks -> on_doc_event -> enqueue -> execute_automation
              -> _walk_graph -> if_condition.evaluate_branch -> checks __trigger_doctype__
        """
        from unittest.mock import patch

        auto = frappe.get_doc({
            "doctype": "Automation",
            "automation_name": "TEST-PseudoField-IF",
            "status": "Published",
            "enabled": 1,
            "triggers": [
                {"trigger_doctype": "Lead", "trigger_event": "After Insert"},
                {"trigger_doctype": "ToDo", "trigger_event": "After Insert"},
            ],
            "graph_definition": json.dumps({
                "nodes": [
                    {"id": "trigger", "type": "trigger", "position": {"x": 0, "y": 0}, "data": {}},
                    {"id": "if-1", "type": "if", "position": {"x": 0, "y": 150},
                     "data": {"field_to_check": "__trigger_doctype__", "operator": "=", "value": "Lead"}},
                    {"id": "action-lead", "type": "action", "position": {"x": -100, "y": 300},
                     "data": {"action_type": "telegram", "chat_id": "TEST", "message": "LEAD BRANCH"}},
                    {"id": "action-todo", "type": "action", "position": {"x": 100, "y": 300},
                     "data": {"action_type": "telegram", "chat_id": "TEST", "message": "TODO BRANCH"}},
                ],
                "edges": [
                    {"source": "trigger", "target": "if-1", "sourceHandle": "trigger-out", "targetHandle": "if-in"},
                    {"source": "if-1", "target": "action-lead", "sourceHandle": "if-true", "targetHandle": "action-lead-in"},
                    {"source": "if-1", "target": "action-todo", "sourceHandle": "if-false", "targetHandle": "action-todo-in"},
                ],
            }),
        })
        auto.insert(ignore_permissions=True)
        frappe.db.commit()

        def _capture_enqueue(method, queue="short", **kwargs):
            from automation_builder.dispatcher import execute_automation
            execute_automation(**kwargs)

        # --- Lead triggers -> IF __trigger_doctype__ = "Lead" -> TRUE -> action-lead ---
        with patch("automation_builder.dispatcher.frappe.enqueue", side_effect=_capture_enqueue), \
             patch("automation_builder.action_types.telegram._get_bot_token", return_value=None):
            lead = frappe.get_doc({"doctype": "Lead", "lead_name": "PseudoField Test Lead"})
            lead.insert(ignore_permissions=True)
            frappe.db.commit()

        runs_lead = frappe.get_all(
            "Automation Run",
            filters={"automation": auto.name, "reference_doctype": "Lead"},
            fields=["status", "log"],
        )
        self.assertEqual(len(runs_lead), 1, "Lead should produce exactly one run")
        self.assertEqual(runs_lead[0].status, "Success")
        log_lead = json.loads(runs_lead[0].log)
        # Expect: branch entry (IF) + action entry (telegram)
        statuses_lead = [e["status"] for e in log_lead]
        self.assertTrue(all(s == "Success" for s in statuses_lead), f"All steps should succeed: {log_lead}")
        # The IF branch should have been taken (branch entry present)
        branch_entries = [e for e in log_lead if e.get("step_type") == "if"]
        self.assertEqual(len(branch_entries), 1)
        self.assertIn("TRUE", branch_entries[0].get("output", ""))

        # --- ToDo triggers -> IF __trigger_doctype__ = "Lead" -> FALSE -> action-todo ---
        with patch("automation_builder.dispatcher.frappe.enqueue", side_effect=_capture_enqueue), \
             patch("automation_builder.action_types.telegram._get_bot_token", return_value=None):
            todo = frappe.get_doc({"doctype": "ToDo", "description": "PseudoField Test ToDo"})
            todo.insert(ignore_permissions=True)
            frappe.db.commit()

        runs_todo = frappe.get_all(
            "Automation Run",
            filters={"automation": auto.name, "reference_doctype": "ToDo"},
            fields=["status", "log"],
        )
        self.assertEqual(len(runs_todo), 1, "ToDo should produce exactly one run")
        self.assertEqual(runs_todo[0].status, "Success")
        log_todo = json.loads(runs_todo[0].log)
        branch_entries_todo = [e for e in log_todo if e.get("step_type") == "if"]
        self.assertEqual(len(branch_entries_todo), 1)
        self.assertIn("FALSE", branch_entries_todo[0].get("output", ""))

    def test_switch_trigger_doctype_branch(self):
        """FULL REAL PATH: Switch node with __trigger_doctype__ branches per doctype.

        Path: doc.insert() -> hooks -> on_doc_event -> enqueue -> execute_automation
              -> _walk_graph -> switch_case.evaluate_branch -> checks __trigger_doctype__
        """
        from unittest.mock import patch

        auto = frappe.get_doc({
            "doctype": "Automation",
            "automation_name": "TEST-PseudoField-Switch",
            "status": "Published",
            "enabled": 1,
            "triggers": [
                {"trigger_doctype": "Lead", "trigger_event": "After Insert"},
                {"trigger_doctype": "ToDo", "trigger_event": "After Insert"},
                {"trigger_doctype": "Note", "trigger_event": "After Insert"},
            ],
            "graph_definition": json.dumps({
                "nodes": [
                    {"id": "trigger", "type": "trigger", "position": {"x": 0, "y": 0}, "data": {}},
                    {"id": "switch-1", "type": "switch", "position": {"x": 0, "y": 150},
                     "data": {"field_to_check": "__trigger_doctype__", "cases": [
                         {"case_value": "Lead"},
                         {"case_value": "ToDo"},
                     ]}},
                    {"id": "action-lead", "type": "action", "position": {"x": -200, "y": 300},
                     "data": {"action_type": "telegram", "chat_id": "TEST", "message": "LEAD CASE"}},
                    {"id": "action-todo", "type": "action", "position": {"x": 0, "y": 300},
                     "data": {"action_type": "telegram", "chat_id": "TEST", "message": "TODO CASE"}},
                    {"id": "action-default", "type": "action", "position": {"x": 200, "y": 300},
                     "data": {"action_type": "telegram", "chat_id": "TEST", "message": "DEFAULT CASE"}},
                ],
                "edges": [
                    {"source": "trigger", "target": "switch-1", "sourceHandle": "trigger-out", "targetHandle": "switch-in"},
                    {"source": "switch-1", "target": "action-lead", "sourceHandle": "case-0", "targetHandle": "action-lead-in"},
                    {"source": "switch-1", "target": "action-todo", "sourceHandle": "case-1", "targetHandle": "action-todo-in"},
                    {"source": "switch-1", "target": "action-default", "sourceHandle": "default", "targetHandle": "action-default-in"},
                ],
            }),
        })
        auto.insert(ignore_permissions=True)
        frappe.db.commit()

        def _capture_enqueue(method, queue="short", **kwargs):
            from automation_builder.dispatcher import execute_automation
            execute_automation(**kwargs)

        # --- Lead triggers -> case-0 ("Lead") ---
        with patch("automation_builder.dispatcher.frappe.enqueue", side_effect=_capture_enqueue), \
             patch("automation_builder.action_types.telegram._get_bot_token", return_value=None):
            lead = frappe.get_doc({"doctype": "Lead", "lead_name": "Switch PseudoField Lead"})
            lead.insert(ignore_permissions=True)
            frappe.db.commit()

        runs_lead = frappe.get_all(
            "Automation Run",
            filters={"automation": auto.name, "reference_doctype": "Lead"},
            fields=["status", "log"],
        )
        self.assertEqual(len(runs_lead), 1)
        self.assertEqual(runs_lead[0].status, "Success")
        log_lead = json.loads(runs_lead[0].log)
        branch_entries = [e for e in log_lead if e.get("step_type") == "switch"]
        self.assertEqual(len(branch_entries), 1)
        self.assertIn("case-0", branch_entries[0].get("branch_taken", ""))

        # --- ToDo triggers -> case-1 ("ToDo") ---
        with patch("automation_builder.dispatcher.frappe.enqueue", side_effect=_capture_enqueue), \
             patch("automation_builder.action_types.telegram._get_bot_token", return_value=None):
            todo = frappe.get_doc({"doctype": "ToDo", "description": "Switch PseudoField ToDo"})
            todo.insert(ignore_permissions=True)
            frappe.db.commit()

        runs_todo = frappe.get_all(
            "Automation Run",
            filters={"automation": auto.name, "reference_doctype": "ToDo"},
            fields=["status", "log"],
        )
        self.assertEqual(len(runs_todo), 1)
        self.assertEqual(runs_todo[0].status, "Success")
        log_todo = json.loads(runs_todo[0].log)
        branch_entries_todo = [e for e in log_todo if e.get("step_type") == "switch"]
        self.assertEqual(len(branch_entries_todo), 1)
        self.assertIn("case-1", branch_entries_todo[0].get("branch_taken", ""))

        # --- Note triggers -> default (no case matches "Note") ---
        with patch("automation_builder.dispatcher.frappe.enqueue", side_effect=_capture_enqueue), \
             patch("automation_builder.action_types.telegram._get_bot_token", return_value=None):
            note = frappe.get_doc({"doctype": "Note", "title": "Switch PseudoField Note", "content": "test"})
            note.insert(ignore_permissions=True)
            frappe.db.commit()

        runs_note = frappe.get_all(
            "Automation Run",
            filters={"automation": auto.name, "reference_doctype": "Note"},
            fields=["status", "log"],
        )
        self.assertEqual(len(runs_note), 1)
        self.assertEqual(runs_note[0].status, "Success")
        log_note = json.loads(runs_note[0].log)
        branch_entries_note = [e for e in log_note if e.get("step_type") == "switch"]
        self.assertEqual(len(branch_entries_note), 1)
        self.assertIn("default", branch_entries_note[0].get("branch_taken", ""))
