"""Part 6: Full end-to-end verification of user's exact scenario."""
import frappe
import json
from frappe.tests import IntegrationTestCase
from automation_builder.api import save_automation
from automation_builder.dispatcher import execute_automation


class TestStage29FinalE2E(IntegrationTestCase):

    def test_full_e2e_user_scenario(self):
        auto_name = "ST29-FINAL-UserScenario"
        created_docs = []

        try:
            # Cleanup
            for name in frappe.get_all("Automation", filters={"name": ["like", "ST29-FINAL-%"]}, fields=["name"]):
                frappe.delete_doc("Automation", name.name, force=True)
            for n in frappe.get_all("Note", filters={"title": ["like", "ST29-FINAL-%"]}, fields=["name"]):
                frappe.delete_doc("Note", n.name, force=True)
            for n in frappe.get_all("Lead", filters={"lead_name": ["like", "ST29-FINAL-%"]}, fields=["name"]):
                frappe.delete_doc("Lead", n.name, force=True)
            for n in frappe.get_all("ToDo", filters={"description": ["like", "ST29-FINAL-%"]}, fields=["name"]):
                frappe.delete_doc("ToDo", n.name, force=True)
            frappe.db.commit()

            nodes = [
                {"id": "trigger-lead", "type": "trigger", "position": {"x": 250, "y": 50}, "data": {"trigger_type": "DocType Event", "trigger_doctype": "Lead", "trigger_event": "On Update", "condition_logic": "All must match", "conditions": []}},
                {"id": "trigger-todo", "type": "trigger", "position": {"x": 550, "y": 50}, "data": {"trigger_type": "DocType Event", "trigger_doctype": "ToDo", "trigger_event": "On Update", "condition_logic": "All must match", "conditions": []}},
                {"id": "cond-lead", "type": "condition", "position": {"x": 250, "y": 200}, "data": {"condition_field": "status", "condition_operator": "=", "condition_value": "Open", "trigger_doctype_select": "Lead"}},
                {"id": "cond-todo", "type": "condition", "position": {"x": 550, "y": 200}, "data": {"condition_field": "status", "condition_operator": "=", "condition_value": "Open", "trigger_doctype_select": "ToDo"}},
                {"id": "act-shared", "type": "action", "position": {"x": 400, "y": 350}, "data": {"action_type": "create_document", "trigger_doctype_select": "any", "target_doctype": "Note", "field_mapping": [{"target_field": "title", "source_value": "ST29-FINAL-FIRED"}]}},
            ]
            edges = [
                {"id": "e-tl-cl", "source": "trigger-lead", "target": "cond-lead", "sourceHandle": "trigger-lead-out", "targetHandle": "cond-lead-in", "type": "smoothstep"},
                {"id": "e-cl-as", "source": "cond-lead", "target": "act-shared", "sourceHandle": "cond-lead-out", "targetHandle": "act-shared-in", "type": "smoothstep"},
                {"id": "e-tt-ct", "source": "trigger-todo", "target": "cond-todo", "sourceHandle": "trigger-todo-out", "targetHandle": "cond-todo-in", "type": "smoothstep"},
                {"id": "e-ct-as", "source": "cond-todo", "target": "act-shared", "sourceHandle": "cond-todo-out", "targetHandle": "act-shared-in-left", "type": "smoothstep"},
            ]
            triggers = [
                {"trigger_type": "DocType Event", "trigger_doctype": "Lead", "trigger_event": "On Update", "schedule_frequency": "Hourly", "webhook_token": "", "condition_logic": "All must match", "conditions": [], "graph_node_id": "trigger-lead"},
                {"trigger_type": "DocType Event", "trigger_doctype": "ToDo", "trigger_event": "On Update", "schedule_frequency": "Hourly", "webhook_token": "", "condition_logic": "All must match", "conditions": [], "graph_node_id": "trigger-todo"},
            ]

            graph_def = json.dumps({"nodes": nodes, "edges": edges})
            result = save_automation(automation_name=auto_name, status="Published", graph_definition=graph_def, triggers=triggers)
            auto_name = result["name"]
            print(f"\nCreated automation: {auto_name}")

            # Fire via Lead
            lead = frappe.get_doc({"doctype": "Lead", "lead_name": "ST29-FINAL-LeadSave"})
            lead.status = "Open"
            lead.insert(ignore_permissions=True)
            lead_name = lead.name
            created_docs.append(("Lead", lead_name))
            frappe.db.commit()
            print(f"Created Lead: {lead_name}")

            execute_automation(auto_name, "Lead", lead_name)
            frappe.db.commit()

            # Fire via ToDo
            todo = frappe.get_doc({"doctype": "ToDo", "description": "ST29-FINAL-TodoSave"})
            todo.insert(ignore_permissions=True)
            todo_name = todo.name
            created_docs.append(("ToDo", todo_name))
            frappe.db.commit()
            print(f"Created ToDo: {todo_name}")

            execute_automation(auto_name, "ToDo", todo_name)
            frappe.db.commit()

            # Show results
            runs = frappe.get_all("Automation Run", filters={"automation": auto_name}, fields=["name", "status", "reference_doctype", "reference_name", "log"], order_by="creation asc")
            print(f"\n=== AUTOMATION RUNS ({len(runs)} total) ===")
            for r in runs:
                print(f"\nRun: {r.name}")
                print(f"  Status: {r.status}")
                print(f"  Reference: {r.reference_doctype} {r.reference_name}")
                steps = frappe.get_all("Automation Run Step", filters={"parent": r.name}, fields=["node_id", "node_type", "step_type", "status", "branch_taken", "output", "error"], order_by="idx asc")
                for s in steps:
                    print(f"  Step: node={s.node_id} type={s.node_type} step={s.step_type} status={s.status} branch={s.branch_taken}")
                    print(f"    output: {s.output}")
                    if s.error:
                        print(f"    error: {s.error}")

            self.assertGreaterEqual(len(runs), 2)
            for r in runs:
                self.assertEqual(r.status, "Success")

        finally:
            for dt, dn in created_docs:
                try:
                    frappe.delete_doc(dt, dn, force=True)
                except Exception:
                    pass
            for name in frappe.get_all("Automation", filters={"name": ["like", "ST29-FINAL-%"]}, fields=["name"]):
                frappe.delete_doc("Automation", name.name, force=True)
            for n in frappe.get_all("Note", filters={"title": ["like", "ST29-FINAL-%"]}, fields=["name"]):
                frappe.delete_doc("Note", n.name, force=True)
            frappe.db.commit()
