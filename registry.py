# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
"""
IAC Operation Registry — Extensible operation registration and dispatch.

Any D-Net node can register operations organized by domain.
The registry provides discovery, validation, and execution.
"""

import fnmatch
import json
from typing import Any, Callable, Dict, List, Optional


class Operation:
    __slots__ = ("name", "domain", "description", "parameters", "handler")

    def __init__(self, name: str, domain: str, description: str,
                 parameters: dict, handler: Callable):
        self.name = name
        self.domain = domain
        self.description = description
        self.parameters = parameters
        self.handler = handler

    @property
    def qualified_name(self) -> str:
        return f"{self.domain}.{self.name}" if self.domain else self.name

    def to_schema(self) -> dict:
        return {
            "op": self.qualified_name,
            "name": self.name,
            "domain": self.domain,
            "description": self.description,
            "parameters": self.parameters
        }

    def __repr__(self):
        return f"<Op: {self.qualified_name}>"


class OperationRegistry:
    _instance = None
    # Reassigned by `exclude`, never mutated, so a class default is safe --
    # and a subclass that skips `__new__` for isolation still has one.
    _excluded = ()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._ops: Dict[str, Operation] = {}
            cls._instance._aliases: Dict[str, str] = {}
        return cls._instance

    def exclude(self, patterns):
        """Operations this host will never carry, as glob patterns.

        A policy that DENIES an operation at call time still advertises it in
        the index, and an agent will keep reaching for what it can see. When
        the host has replaced a whole domain -- a browser served by an MCP
        server instead of the built-in one, say -- the built-in should not be
        in the index at all. Anything already registered that matches is
        removed; anything registered later that matches is dropped silently.
        The host decides the patterns; this module knows nothing about where
        they came from.
        """
        self._excluded = [str(p).strip().lower() for p in (patterns or [])
                          if str(p).strip()]
        for qn in [qn for qn in list(self._ops) if self.is_excluded(qn)]:
            self.unregister(qn)

    def is_excluded(self, op_name: str) -> bool:
        if not self._excluded or not op_name:
            return False
        low = str(op_name).strip().lower()
        return any(fnmatch.fnmatch(low, pat) for pat in self._excluded)

    def register(self, name: str, domain: str, description: str,
                 parameters: dict, handler: Callable) -> Optional[Operation]:
        op = Operation(name, domain, description, parameters, handler)
        if self.is_excluded(op.qualified_name):
            return None
        self._ops[op.qualified_name] = op
        return op

    def register_operation(self, op: Operation):
        if self.is_excluded(op.qualified_name):
            return
        self._ops[op.qualified_name] = op

    def unregister(self, op_name: str) -> bool:
        """Remove an operation, and any alias that pointed at it.

        Capability can now arrive at runtime -- an MCP server added while the
        app is running can be removed while it is running too. An operation
        left in the index after its provider is gone is worse than one that
        was never there: it advertises something that will fail.
        """
        op = self.resolve(op_name)
        if op is None:
            return False
        self._ops.pop(op.qualified_name, None)
        for alias_name, target in list(self._aliases.items()):
            if target in (op.qualified_name, op_name):
                self._aliases.pop(alias_name, None)
        return True

    def alias(self, alias_name: str, target_name: str):
        if self.is_excluded(target_name) or self.is_excluded(alias_name):
            return
        self._aliases[alias_name] = target_name

    def resolve(self, op_name: str) -> Optional[Operation]:
        if op_name in self._ops:
            return self._ops[op_name]
        if op_name in self._aliases:
            return self._ops.get(self._aliases[op_name])
        for qn, op in self._ops.items():
            if op.name == op_name:
                return op
        return None

    def has(self, op_name: str) -> bool:
        return self.resolve(op_name) is not None

    def execute(self, op_name: str, args: dict, context: dict = None) -> Any:
        op = self.resolve(op_name)
        if not op:
            return {"error": f"Unknown operation: '{op_name}'", "status": "error"}

        # Optional policy gate supplied by the host. This is the ONE place every
        # operation passes through, which is exactly why the check belongs here:
        # containment enforced inside individual operations only ever covers the
        # ones that remember to enforce it. A host that guards its filesystem
        # operations and forgets its shell operations has no boundary at all.
        #
        # IAC stays generic. It asks a yes/no question and knows nothing about
        # what the policy is or how the answer was reached.
        guard = (context or {}).get("guard")
        if guard is not None:
            try:
                verdict = guard.check(op_name, args, context)
            except Exception as e:
                # A guard that cannot answer must not fail open.
                return {"status": "error", "refused_by": "guard",
                        "error": f"[{op_name}] policy check failed: {e}"}
            if verdict is not None and not getattr(verdict, "allowed", True):
                return {"status": "error", "refused_by": "guard",
                        "verdict": getattr(verdict, "kind", "deny"),
                        "error": getattr(verdict, "reason",
                                         f"'{op_name}' is not permitted here.")}

        try:
            return op.handler(args, context or {})
        except Exception as e:
            return {"error": f"[{op_name}] {e}", "status": "error"}

    def list_operations(self, domain: str = None) -> List[dict]:
        ops = []
        for qn, op in self._ops.items():
            if domain and op.domain != domain:
                continue
            ops.append(op.to_schema())
        return ops

    def list_domains(self) -> List[str]:
        return sorted(set(op.domain for op in self._ops.values() if op.domain))

    def to_prompt_block(self, domain: str = None) -> str:
        lines = ["Available IAC Operations:"]
        for schema in self.list_operations(domain):
            params_str = ", ".join(
                f"{k}: {v.get('type', 'any')}"
                for k, v in schema.get("parameters", {}).get("properties", {}).items()
            )
            lines.append(f"  {schema['op']}({params_str}) — {schema['description']}")
        return "\n".join(lines)

    def to_ollama_schemas(self, domain: str = None) -> List[dict]:
        schemas = []
        for op in self._ops.values():
            if domain and op.domain != domain:
                continue
            schemas.append({
                "type": "function",
                "function": {
                    "name": op.qualified_name.replace(".", "_"),
                    "description": op.description,
                    "parameters": op.parameters
                }
            })
        return schemas

    def get_summary(self) -> dict:
        domains = {}
        for op in self._ops.values():
            d = op.domain or "core"
            domains.setdefault(d, []).append(op.name)
        return {"total": len(self._ops), "domains": domains}

    def clear(self):
        self._ops.clear()
        self._aliases.clear()

    def __len__(self):
        return len(self._ops)

    def __repr__(self):
        return f"OperationRegistry({len(self._ops)} ops across {len(self.list_domains())} domains)"


def get_registry() -> OperationRegistry:
    return OperationRegistry()
