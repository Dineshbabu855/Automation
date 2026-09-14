app_name = "automation_builder"
app_title = "Automation Builder"
app_publisher = "aruvi"
app_description = "n8n for frappe"
app_email = "aruvi@gmail.com"
app_license = "mit"

# Setup hooks
after_install = "automation_builder.setup.create_automation_roles"

# Includes in <head>
# ------------------
# CSS loaded dynamically in spa_builder.js with cache busting

# Document Events
# ---------------
# Wildcard "*" fires for every DocType site-wide.
# The handler itself filters by trigger_doctype + trigger_event so unrelated
# saves are rejected immediately (indexed query, early return).
doc_events = {
    "*": {
        "after_insert": "automation_builder.dispatcher.on_doc_event",
        "on_update": "automation_builder.dispatcher.on_doc_event",
        "on_submit": "automation_builder.dispatcher.on_doc_event",
        "on_cancel": "automation_builder.dispatcher.on_doc_event",
    },
}

# Scheduler Events
# ----------------
# 15-minute interval for Schedule-type triggers. Standard Frappe granularity
# — per-minute would be over-engineered for this scale.
scheduler_events = {
    "cron": {
        "*/15 * * * *": [
            "automation_builder.dispatcher.check_scheduled_automations"
        ],
    },
}

# Apps Screen
# -----------
add_to_apps_screen = [
    {
        "name": "automation_builder",
        "title": "Automation Builder",
        "route": "/app/spa-builder",
    },
    {
        "name": "automation_builder_settings",
        "title": "Automation Builder Settings",
        "route": "/app/automation-builder-settings",
        "type": "settings",
    },
]
