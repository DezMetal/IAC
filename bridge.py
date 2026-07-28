# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
"""
IAC Bridge — Adapters that unify existing tool systems into IAC operations.

Translates between:
  - pipeline-style command lists <-> IAC operations
  - plain function maps <-> IAC operations
  - class-based tool objects <-> IAC operations

No existing tools are rewritten. Bridges wrap them.
"""

import inspect
import json
from typing import Any, Callable, Dict, List, Optional


class PipelineBridge:
    """Bidirectional translation between pipeline-style command lists and IAC plans.

    A "pipeline" here is any dict of the shape
    {"commands": [{"program": ..., "action": ..., "params": {...}}]}.
    """

    @staticmethod
    def pipeline_to_iac(pipeline: dict, source: str = "pipeline") -> dict:
        plan = []
        for cmd in pipeline.get("commands", []):
            program = cmd.get("program", "core")
            action = cmd.get("action", "execute")
            op = f"{program}.{action}"
            args = cmd.get("params", {})
            plan.append({"op": op, "args": args})

        return {
            "iac_version": "2.0",
            "source": source,
            "plan": plan,
            "config": {},
            "data_payload": {}
        }

    @staticmethod
    def iac_to_pipeline(iac_plan: dict) -> dict:
        commands = []
        for step in iac_plan.get("plan", []):
            op = step.get("op", "")
            parts = op.split(".", 1)
            program = parts[0] if len(parts) > 1 else "core"
            action = parts[1] if len(parts) > 1 else parts[0]
            commands.append({
                "program": program,
                "action": action,
                "params": step.get("args", {})
            })

        return {
            "name": "IAC Generated Pipeline",
            "trigger": {"type": "manual", "value": ""},
            "enabled": True,
            "commands": commands
        }

    @staticmethod
    def register_pipeline_programs(registry, program_modules: dict):
        for prog_name, module in program_modules.items():
            if not hasattr(module, 'execute'):
                continue

            doc = getattr(module, '__doc__', '') or ''
            actions = _extract_yaml_actions(doc)

            for action_name, action_info in actions.items():
                def make_handler(mod, act):
                    def handler(args, context=None):
                        params = {
                            "token": (context or {}).get("token", ""),
                            "action": act,
                            "parameters": args
                        }
                        dnet_core = (context or {}).get("dnet_core")
                        return mod.execute(dnet_core, params)
                    return handler

                registry.register(
                    name=action_name,
                    domain=prog_name,
                    description=action_info.get("description", f"{prog_name}.{action_name}"),
                    parameters=action_info.get("parameters", {}),
                    handler=make_handler(module, action_name)
                )


class FuncBridge:
    """Wraps a map of plain Python functions as IAC operations.

    IMPORTANT: Does NOT replace the D-Net Task system. These adapters
    Useful when you already have a body of callables and want them reachable
    from an IAC plan without rewriting them as a Domain. For anything new,
    prefer the extension API in extensions.py -- it carries schemas, ownership
    and capability declarations that a bare function map cannot.
    """

    @staticmethod
    def func_to_operation(func_name: str, func_ref: Callable,
                          domain: str = "dnet") -> dict:
        sig = inspect.signature(func_ref)
        params = {"type": "object", "properties": {}, "required": []}

        skip_params = {"ai_name", "user_id", "kwargs", "tid", "mid"}
        for name, param in sig.parameters.items():
            if name in skip_params:
                continue
            if param.kind == param.VAR_KEYWORD:
                continue

            p_type = "string"
            if param.annotation != inspect.Parameter.empty:
                ann = param.annotation
                if ann == int:
                    p_type = "integer"
                elif ann == bool:
                    p_type = "boolean"
                elif ann == float:
                    p_type = "number"
                elif ann == list or ann == List:
                    p_type = "array"

            params["properties"][name] = {"type": p_type}
            if param.default is inspect.Parameter.empty:
                params["required"].append(name)

        return {
            "op": f"{domain}.{func_name}",
            "description": (func_ref.__doc__ or "").strip().split("\n")[0],
            "parameters": params,
            "handler": func_ref
        }

    @staticmethod
    def register_func_tools(registry, func_map: dict, domain: str = "tools"):
        for name, func in func_map.items():
            op_info = FuncBridge.func_to_operation(name, func, domain)

            def make_handler(fn, fn_name):
                def handler(args, context=None):
                    ctx = context or {}
                    call_args = {
                        "ai_name": ctx.get("ai_name", "IAC"),
                        "user_id": ctx.get("user_id", 0),
                        **args
                    }
                    try:
                        return fn(**call_args)
                    except Exception as e:
                        return {"error": f"{fn_name} failed: {e}"}
                return handler

            registry.register(
                name=name,
                domain=domain,
                description=op_info["description"],
                parameters=op_info["parameters"],
                handler=make_handler(func, name)
            )


class ToolBridge:
    """Wraps class-based tool objects as IAC operations.

    Expects objects exposing `name`, `description` and a `run(**kwargs)` or
    `execute(**kwargs)` method -- the shape most tool frameworks converge on.
    """

    @staticmethod
    def tool_to_operation(tool, domain: str = "tool") -> dict:
        return {
            "op": f"{domain}.{tool.name}",
            "description": tool.description,
            "parameters": tool.parameters,
            "handler": lambda args, ctx=None: tool.execute(**args)
        }

    @staticmethod
    def register_tools(registry, tool_registry, domain: str = "tool"):
        count = 0
        for name in tool_registry.list_tools():
            tool = tool_registry.get(name)
            op_info = ToolBridge.tool_to_operation(tool, domain)

            def make_handler(t):
                def handler(args, context=None):
                    try:
                        return t.execute(**args)
                    except Exception as e:
                        return {"error": f"Tool.{t.name} failed: {e}"}
                return handler

            registry.register(
                name=name,
                domain=domain,
                description=op_info["description"],
                parameters=op_info["parameters"],
                handler=make_handler(tool)
            )
            count += 1
        return count


def _extract_yaml_actions(docstring: str) -> dict:
    actions = {}
    if not docstring:
        return actions

    in_actions = False
    current_action = None

    for line in docstring.split("\n"):
        stripped = line.strip()
        if stripped.lower().startswith("actions:"):
            in_actions = True
            continue
        if in_actions:
            if not ":" in stripped:
                continue
            
            # If line is exactly `key:` (and not `- key:`)
            if stripped.endswith(":") and not stripped.startswith("- ") and " " not in stripped:
                current_action = stripped.rstrip(":")
                actions[current_action] = {"description": current_action, "parameters": {}}
            elif stripped.startswith("- "):
                current_action = stripped[2:].strip().rstrip(":")
                actions[current_action] = {"description": current_action, "parameters": {}}
            elif current_action and ":" in stripped:
                key, val = stripped.split(":", 1)
                key = key.strip()
                val = val.strip()
                if key == "description":
                    actions[current_action]["description"] = val
            elif not stripped:
                in_actions = False
                current_action = None

    return actions
