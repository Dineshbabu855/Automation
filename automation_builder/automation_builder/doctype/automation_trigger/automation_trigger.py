# Copyright (c) 2026, aruvi and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AutomationTrigger(Document):
    def validate(self):
        """Auto-generate webhook_token for Webhook triggers."""
        if self.trigger_type == "Webhook" and not self.webhook_token:
            self.webhook_token = frappe.generate_hash(length=40)
