import frappe
from frappe.model.document import Document


class Automation(Document):
    def validate(self):
        """Auto-generate webhook_token for Webhook trigger rows that lack one."""
        for trigger in self.triggers:
            if trigger.trigger_type == "Webhook" and not trigger.webhook_token:
                trigger.webhook_token = frappe.generate_hash(length=40)
