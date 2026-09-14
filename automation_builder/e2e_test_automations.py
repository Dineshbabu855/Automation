"""Test all 5 E2E automations end-to-end.

Calls execute_automation directly since no background worker is running.

Usage:
    bench --site learning.localhost execute automation_builder.e2e_test_automations.run
"""

import frappe
import json
from datetime import timedelta


def run():
    frappe.set_user("Administrator")
    results = []

    # ===================================================================
    # Test 1: Multi-Trigger IF Router
    # ===================================================================
    print("\n=== TEST 1: Multi-Trigger IF Router ===")

    # Test 1a: Insert Lead -> should create "IF-LEAD-FIRED" note
    lead = frappe.new_doc("Lead")
    lead.lead_name = "E2E-Test-Lead-IF"
    lead.status = "Lead"
    lead.insert(ignore_permissions=True)
    frappe.db.commit()

    _call_automation("E2E-IF-Router", lead.doctype, lead.name)
    frappe.db.commit()

    note = frappe.db.get_value("Note", {"title": "IF-LEAD-FIRED"}, ["name"], order_by="creation desc")
    if note:
        results.append(("IF-Router", "Lead -> IF-LEAD-FIRED note", "PASS"))
        print(f"  [PASS] Lead insert created IF-LEAD-FIRED note ({note})")
    else:
        results.append(("IF-Router", "Lead -> IF-LEAD-FIRED note", "FAIL"))
        print("  [FAIL] Lead insert did NOT create IF-LEAD-FIRED note")

    # Verify IF-TODO-FIRED was NOT created
    todo_note = frappe.db.get_value("Note", {"title": "IF-TODO-FIRED"}, ["name"])
    if not todo_note:
        print("  [PASS] IF-TODO-FIRED was NOT created (correct for Lead trigger)")
    else:
        print(f"  [WARN] IF-TODO-FIRED was created ({todo_note}) - unexpected")

    # Test 1b: Insert ToDo -> should create "IF-TODO-FIRED" note
    todo = frappe.new_doc("ToDo")
    todo.description = "E2E-Test-Todo-IF"
    todo.status = "Open"
    todo.insert(ignore_permissions=True)
    frappe.db.commit()

    _call_automation("E2E-IF-Router", todo.doctype, todo.name)
    frappe.db.commit()

    note2 = frappe.db.get_value("Note", {"title": "IF-TODO-FIRED"}, ["name"], order_by="creation desc")
    if note2:
        results.append(("IF-Router", "ToDo -> IF-TODO-FIRED note", "PASS"))
        print(f"  [PASS] ToDo insert created IF-TODO-FIRED note ({note2})")
    else:
        results.append(("IF-Router", "ToDo -> IF-TODO-FIRED note", "FAIL"))
        print("  [FAIL] ToDo insert did NOT create IF-TODO-FIRED note")
        # Debug: check what was actually created
        run_log = frappe.db.get_value("Automation Run",
            {"automation": "E2E-IF-Router"}, ["log"], order_by="creation desc")
        if run_log:
            print(f"  [DEBUG] Latest run log: {run_log[:500]}")

    # Cleanup
    frappe.delete_doc("Lead", lead.name, force=True)
    frappe.delete_doc("ToDo", todo.name, force=True)

    # ===================================================================
    # Test 2: Lead Status Switch
    # ===================================================================
    print("\n=== TEST 2: Lead Status Switch ===")

    # Test 2a: Insert Lead with status=Open -> "STATUS-OPEN"
    lead2 = frappe.new_doc("Lead")
    lead2.lead_name = "E2E-Test-Lead-Switch"
    lead2.status = "Open"
    lead2.insert(ignore_permissions=True)
    frappe.db.commit()

    _call_automation("E2E-Status-Switch", lead2.doctype, lead2.name)
    frappe.db.commit()

    note_open = frappe.db.get_value("Note", {"title": "STATUS-OPEN"}, ["name"], order_by="creation desc")
    if note_open:
        results.append(("Status-Switch", "Open -> STATUS-OPEN", "PASS"))
        print(f"  [PASS] Lead Open created STATUS-OPEN note ({note_open})")
    else:
        results.append(("Status-Switch", "Open -> STATUS-OPEN", "FAIL"))
        print("  [FAIL] Lead Open did NOT create STATUS-OPEN note")

    # Test 2b: Update Lead to Replied -> "STATUS-REPLIED"
    lead2.status = "Replied"
    lead2.save(ignore_permissions=True)
    frappe.db.commit()

    _call_automation("E2E-Status-Switch", lead2.doctype, lead2.name)
    frappe.db.commit()

    note_replied = frappe.db.get_value("Note", {"title": "STATUS-REPLIED"}, ["name"], order_by="creation desc")
    if note_replied:
        results.append(("Status-Switch", "Replied -> STATUS-REPLIED", "PASS"))
        print(f"  [PASS] Lead Replied created STATUS-REPLIED note ({note_replied})")
    else:
        results.append(("Status-Switch", "Replied -> STATUS-REPLIED", "FAIL"))
        print("  [FAIL] Lead Replied did NOT create STATUS-REPLIED note")

    # Test 2c: Update Lead to "Do Not Contact" -> "STATUS-OTHER"
    lead2.status = "Do Not Contact"
    lead2.save(ignore_permissions=True)
    frappe.db.commit()

    _call_automation("E2E-Status-Switch", lead2.doctype, lead2.name)
    frappe.db.commit()

    note_other = frappe.db.get_value("Note", {"title": "STATUS-OTHER"}, ["name"], order_by="creation desc")
    if note_other:
        results.append(("Status-Switch", "Other status -> STATUS-OTHER", "PASS"))
        print(f"  [PASS] Lead other status created STATUS-OTHER note ({note_other})")
    else:
        results.append(("Status-Switch", "Other status -> STATUS-OTHER", "FAIL"))
        print("  [FAIL] Lead other status did NOT create STATUS-OTHER note")

    # Cleanup
    frappe.delete_doc("Lead", lead2.name, force=True)

    # ===================================================================
    # Test 3: Cross-Doctype Tagged Paths
    # ===================================================================
    print("\n=== TEST 3: Cross-Doctype Tagged Paths ===")

    # Test 3a: Insert Lead -> only "LEAD-PATH" note
    lead3 = frappe.new_doc("Lead")
    lead3.lead_name = "E2E-Test-Lead-Tagged"
    lead3.status = "Lead"
    lead3.insert(ignore_permissions=True)
    frappe.db.commit()

    _call_automation("E2E-Tagged-Paths", lead3.doctype, lead3.name)
    frappe.db.commit()

    lead_note = frappe.db.get_value("Note", {"title": "LEAD-PATH"}, ["name"], order_by="creation desc")
    if lead_note:
        results.append(("Tagged-Paths", "Lead -> LEAD-PATH note", "PASS"))
        print(f"  [PASS] Lead insert created LEAD-PATH note ({lead_note})")
    else:
        results.append(("Tagged-Paths", "Lead -> LEAD-PATH note", "FAIL"))
        print("  [FAIL] Lead insert did NOT create LEAD-PATH note")

    # Verify TODO-PATH was NOT created
    todo_path = frappe.db.get_value("Note", {"title": "TODO-PATH"}, ["name"])
    if not todo_path:
        print("  [PASS] TODO-PATH was NOT created (correct for Lead trigger)")
    else:
        print(f"  [WARN] TODO-PATH was created ({todo_path})")

    # Test 3b: Insert ToDo -> only "TODO-PATH" note
    todo3 = frappe.new_doc("ToDo")
    todo3.description = "E2E-Test-Todo-Tagged"
    todo3.status = "Open"
    todo3.insert(ignore_permissions=True)
    frappe.db.commit()

    _call_automation("E2E-Tagged-Paths", todo3.doctype, todo3.name)
    frappe.db.commit()

    todo_note_path = frappe.db.get_value("Note", {"title": "TODO-PATH"}, ["name"], order_by="creation desc")
    if todo_note_path:
        results.append(("Tagged-Paths", "ToDo -> TODO-PATH note", "PASS"))
        print(f"  [PASS] ToDo insert created TODO-PATH note ({todo_note_path})")
    else:
        results.append(("Tagged-Paths", "ToDo -> TODO-PATH note", "FAIL"))
        print("  [FAIL] ToDo insert did NOT create TODO-PATH note")

    # Verify LEAD-PATH was NOT created again for this ToDo trigger
    lead_note2 = frappe.db.get_value("Note", {"title": "LEAD-PATH"}, ["name"], order_by="creation desc")
    if lead_note2 and lead_note2 != lead_note:
        print(f"  [WARN] LEAD-PATH was created again ({lead_note2})")
    else:
        print("  [PASS] LEAD-PATH was NOT duplicated (correct)")

    # Cleanup
    frappe.delete_doc("Lead", lead3.name, force=True)
    frappe.delete_doc("ToDo", todo3.name, force=True)

    # ===================================================================
    # Test 4: Scheduled Note Creator
    # ===================================================================
    print("\n=== TEST 4: Scheduled Note Creator ===")

    from automation_builder.dispatcher import check_scheduled_automations

    # Set next_run to the past
    trigger_name = frappe.db.get_value(
        "Automation Trigger",
        {"parent": "E2E-Schedule-Tick", "trigger_type": "Schedule"},
        "name",
    )
    past = frappe.utils.now_datetime() - timedelta(hours=1)
    frappe.db.sql(
        "UPDATE `tabAutomation Trigger` SET next_run = %s WHERE name = %s",
        (past, trigger_name),
    )
    frappe.db.commit()

    # Record notes count before
    count_before = frappe.db.count("Note", {"title": "SCHEDULE-TICK"})

    # Run schedule checker
    check_scheduled_automations()
    frappe.db.commit()

    count_after = frappe.db.count("Note", {"title": "SCHEDULE-TICK"})
    if count_after > count_before:
        note_sched = frappe.db.get_value("Note", {"title": "SCHEDULE-TICK"}, ["name"], order_by="creation desc")
        results.append(("Schedule-Tick", "Schedule tick -> SCHEDULE-TICK note", "PASS"))
        print(f"  [PASS] Schedule tick created SCHEDULE-TICK note ({note_sched})")
    else:
        results.append(("Schedule-Tick", "Schedule tick -> SCHEDULE-TICK note", "FAIL"))
        print("  [FAIL] Schedule tick did NOT create SCHEDULE-TICK note")

    # Verify next_run was updated
    new_next = frappe.db.get_value("Automation Trigger", trigger_name, "next_run")
    if new_next and new_next > frappe.utils.now_datetime():
        print(f"  [PASS] next_run updated to future: {new_next}")
    else:
        print(f"  [WARN] next_run = {new_next}")

    # ===================================================================
    # Test 5: Webhook Payload Note
    # ===================================================================
    print("\n=== TEST 5: Webhook Payload Note ===")

    from automation_builder.dispatcher import execute_webhook_trigger

    # Get the webhook token
    auto5_doc = frappe.get_doc("Automation", "E2E-Webhook-Note")
    token = auto5_doc.triggers[0].webhook_token
    print(f"  [INFO] Webhook token: {token}")

    # Call execute_webhook_trigger directly
    payload = {"title": "E2E-WEBHOOK-TEST"}
    execute_webhook_trigger("E2E-Webhook-Note", payload)
    frappe.db.commit()

    wh_note = frappe.db.get_value("Note", {"title": "WEBHOOK-E2E-WEBHOOK-TEST"}, ["name"], order_by="creation desc")
    if wh_note:
        results.append(("Webhook-Note", "Webhook -> WEBHOOK-{title} note", "PASS"))
        print(f"  [PASS] Webhook created WEBHOOK-E2E-WEBHOOK-TEST note ({wh_note})")
    else:
        results.append(("Webhook-Note", "Webhook -> WEBHOOK-{title} note", "FAIL"))
        print("  [FAIL] Webhook did NOT create WEBHOOK-E2E-WEBHOOK-TEST note")

    # ===================================================================
    # Summary
    # ===================================================================
    print("\n" + "=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)
    passed = sum(1 for _, _, r in results if r == "PASS")
    failed = sum(1 for _, _, r in results if r == "FAIL")
    for auto, desc, status in results:
        icon = "[PASS]" if status == "PASS" else "[FAIL]"
        print(f"  {icon} {auto}: {desc}")
    print(f"\nTotal: {len(results)} tests, {passed} passed, {failed} failed")
    print("=" * 60)

    # ===================================================================
    # Check Automation Run records
    # ===================================================================
    print("\n=== Automation Run Records ===")
    runs = frappe.get_all(
        "Automation Run",
        filters={"automation": ["in", [
            "E2E-IF-Router", "E2E-Status-Switch", "E2E-Tagged-Paths",
            "E2E-Schedule-Tick", "E2E-Webhook-Note",
        ]]},
        fields=["name", "automation", "status", "trigger_source", "reference_doctype", "reference_name", "started_at"],
        order_by="started_at desc",
        limit_page_length=20,
    )
    for r in runs:
        ref = r.reference_name or ""
        print(f"  {r.status:10s} | {r.automation:25s} | {r.trigger_source:12s} | {r.reference_doctype or '':10s} | {ref}")

    return results


def _call_automation(automation_name, ref_doctype, ref_name):
    """Call execute_automation directly (no background worker)."""
    from automation_builder.dispatcher import execute_automation
    execute_automation(automation_name, ref_doctype, ref_name)
