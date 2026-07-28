# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
"""
IAC Schema — JSON Schema validation for IAC payloads.

Validates plan-config structures before execution to ensure
every node in the ecosystem receives well-formed data.
"""

import json

IAC_PLAN_SCHEMA = {
    "type": "object",
    "required": ["plan"],
    "properties": {
        "iac_version": {"type": "string"},
        "source": {"type": "string"},
        "target": {"type": "string"},
        "config": {
            "type": "object",
            "properties": {
                "headless": {"type": "boolean"},
                "record_video": {"type": "boolean"},
                "record_video_dir": {"type": "string"},
                "output": {"type": "string"},
                "ai_config": {"type": "object"},
                "retries": {"type": "integer", "minimum": 0},
                "data_payload": {},
                "drop": {"type": "array", "items": {"type": "string"}},
                "security": {
                    "type": "object",
                    "properties": {
                        "allow_eval": {"type": "boolean"},
                        "allow_shell": {"type": "boolean"},
                        "allowed_domains": {"type": "array", "items": {"type": "string"}},
                        "allowed_paths": {"type": "array", "items": {"type": "string"}}
                    }
                }
            }
        },
        "plan": {
            "type": "array",
            "items": {
                "type": "object"
            },
            "minItems": 1
        },
        "data_payload": {}
    }
}

OPERATION_SCHEMA = {
    "type": "object",
    "required": ["op"],
    "properties": {
        "op": {"type": "string", "minLength": 1},
        "args": {"type": "object"}
    }
}

RESTRICTED_OPS = {"eval", "exec", "shell.run", "shell.pipe", "sys.exec"}


def _validate_type(value, schema):
    expected = schema.get("type")
    if expected is None:
        return []

    if isinstance(value, str) and "{{" in value and "}}" in value:
        return []

    type_map = {
        "string": str, "integer": int, "boolean": bool,
        "number": (int, float), "array": list, "object": dict
    }
    expected_type = type_map.get(expected)
    if expected_type and not isinstance(value, expected_type):
        return [f"Expected {expected}, got {type(value).__name__}"]

    errors = []
    if expected == "string" and "minLength" in schema:
        if len(value) < schema["minLength"]:
            errors.append(f"String too short (min {schema['minLength']})")

    if expected == "integer" and "minimum" in schema:
        if value < schema["minimum"]:
            errors.append(f"Value below minimum ({schema['minimum']})")

    if expected == "array" and "items" in schema:
        for i, item in enumerate(value):
            sub_errors = _validate_type(item, schema["items"])
            errors.extend([f"[{i}]: {e}" for e in sub_errors])

    if expected == "object" and "properties" in schema:
        errors.extend(_validate_object(value, schema))

    return errors


def _validate_object(obj, schema):
    errors = []
    required = schema.get("required", [])
    properties = schema.get("properties", {})

    for req in required:
        if req not in obj:
            errors.append(f"Missing required field: '{req}'")

    for key, value in obj.items():
        if key in properties:
            sub_errors = _validate_type(value, properties[key])
            errors.extend([f"{key}: {e}" for e in sub_errors])

    return errors


def validate_plan(plan_data: dict) -> tuple:
    if not isinstance(plan_data, dict):
        return False, ["Plan must be a JSON object"]

    errors = _validate_object(plan_data, IAC_PLAN_SCHEMA)

    plan = plan_data.get("plan", [])
    if not isinstance(plan, list) or len(plan) == 0:
        errors.append("'plan' must be a non-empty array of operations")
    else:
        for i, step in enumerate(plan):
            if not isinstance(step, dict):
                errors.append(f"plan[{i}]: Must be an object")
                continue
            if "_phase" in step:
                continue
            if "op" not in step:
                errors.append(f"plan[{i}]: Missing 'op' field")
            elif not isinstance(step["op"], str) or not step["op"].strip():
                errors.append(f"plan[{i}]: 'op' must be a non-empty string")

    return (len(errors) == 0), errors


def validate_operation(op_dict: dict) -> tuple:
    if not isinstance(op_dict, dict):
        return False, ["Operation must be a JSON object"]
    errors = _validate_object(op_dict, OPERATION_SCHEMA)
    return (len(errors) == 0), errors


def check_security(plan_data: dict, security_context: dict = None) -> tuple:
    if security_context is None:
        security_context = {}

    import os
    import json
    policy_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "security_policy.json")
    policy = {}
    if os.path.exists(policy_path):
        try:
            with open(policy_path, "r", encoding="utf-8") as f:
                policy = json.load(f)
        except Exception:
            pass

    allow_eval = policy.get("allow_eval", security_context.get("allow_eval", False))
    source_trusted = security_context.get("source_trusted", False)
    allowed_domains = policy.get("allowed_domains", security_context.get("allowed_domains"))
    allowed_ops = policy.get("allowed_ops", security_context.get("allowed_ops"))
    denied_ops = list(set(list(policy.get("denied_ops", [])) + list(security_context.get("denied_ops", []))))
    warnings = []

    for i, step in enumerate(plan_data.get("plan", [])):
        op = step.get("op", "")
        # Core restriction check
        if op in RESTRICTED_OPS or op.startswith("eval") or op.startswith("shell"):
            if not allow_eval and not source_trusted:
                warnings.append(
                    f"plan[{i}]: Operation '{op}' is restricted. "
                    f"Set config.security.allow_eval=true or use a trusted source."
                )
        
        # Denylist check
        if op in denied_ops:
            warnings.append(f"plan[{i}]: Operation '{op}' is explicitly denied by security context.")

        # Allowlist check (domains)
        if allowed_domains is not None:
            domain = op.split(".")[0] if "." in op else "core"
            if domain not in allowed_domains and op not in allowed_domains:
                warnings.append(f"plan[{i}]: Domain '{domain}' (op: '{op}') is not in allowed_domains.")

        # Allowlist check (ops)
        if allowed_ops is not None:
            if op not in allowed_ops:
                warnings.append(f"plan[{i}]: Operation '{op}' is not in allowed_ops.")

    return (len(warnings) == 0), warnings


def validate_and_check(plan_data: dict, security_context: dict = None) -> tuple:
    valid, errors = validate_plan(plan_data)
    if not valid:
        return False, errors

    safe, warnings = check_security(plan_data, security_context)
    return safe, warnings
