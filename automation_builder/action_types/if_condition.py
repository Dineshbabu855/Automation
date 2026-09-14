"""IF Condition logic type — evaluates a condition and routes to True/False branch.

Unlike action types (which have side effects and always continue to the next node),
logic types determine WHICH edge to follow next based on evaluating their config
against the run context.

Two fixed output handles: 'if-true' and 'if-false'.
"""

import operator as op

from automation_builder.action_types import register_action_type
from automation_builder.action_types._helpers import resolve_value, TRIGGER_DOCTYPE_FIELD

CONFIG_SCHEMA = [
    {"name": "field_to_check", "type": "field_select", "label": "Field to Check"},
    {
        "name": "operator",
        "type": "select",
        "label": "Operator",
        "options": ["=", "!=", ">", "<", ">=", "<="],
    },
    {
        "name": "value",
        "type": "data",
        "label": "Value",
        "description": "Expected value. Supports {{trigger.fieldname}} tokens.",
    },
]

OUTPUT_HANDLES = [
    {"id": "if-true", "label": "True"},
    {"id": "if-false", "label": "False"},
]

OPERATORS = {
    "=": op.eq,
    "!=": op.ne,
    ">": op.gt,
    "<": op.lt,
    ">=": op.ge,
    "<=": op.le,
}


def evaluate_branch(config, context):
    """Evaluate IF condition and return (source_handle, log_message).

    config: dict with field_to_check, operator, value
    context: dict with 'doc' (the trigger document)
    """
    doc = context.get("doc")
    field = config.get("field_to_check", "")
    operator_str = config.get("operator", "=")
    expected_raw = config.get("value", "")
    expected = resolve_value(expected_raw, context)

    if field == TRIGGER_DOCTYPE_FIELD:
        actual = context.get("trigger_doctype", "")
    else:
        actual = doc.get(field) if doc else None
    comparator = OPERATORS.get(operator_str)

    if comparator:
        if operator_str in (">", "<", ">=", "<="):
            try:
                actual = float(actual)
                expected = float(expected)
            except (TypeError, ValueError):
                pass
        result = comparator(actual, expected)
    else:
        result = str(actual) == str(expected)

    handle = "true" if result else "false"
    log_msg = (
        f"IF {field} {operator_str} {expected!r} -> "
        f"{'TRUE' if result else 'FALSE'} (actual: {actual!r})"
    )
    return handle, log_msg


register_action_type(
    key="if_condition",
    label="IF",
    config_schema=CONFIG_SCHEMA,
    node_category="logic",
    output_handles=OUTPUT_HANDLES,
    evaluate_branch=evaluate_branch,
)
