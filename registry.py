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
import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


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
    def provider(self) -> str:
        """Who serves this: "iac", or "mcp:<server>" for anything from outside.

        The handler already carries it -- `handler.external` is what the
        boundary reads to know an operation came from outside and does not
        know its roots. Naming it here is what lets the registry see that two
        different things are trying to be the same operation.
        """
        return str(getattr(self.handler, "external", "") or "iac")

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

    # Moves on every change to what is registered. Anything that caches a
    # view of the registry -- an index, a schema table, a prompt block --
    # compares this rather than guessing when to rebuild. A host that
    # rendered its operation index at startup and cached it kept offering
    # a domain for the rest of the session after an MCP server had replaced
    # it; nothing told the cache, because nothing could.
    _version = 0

    @property
    def version(self) -> int:
        return self._version

    def _changed(self):
        self._version = self._version + 1

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
        self._changed()

    def is_excluded(self, op_name: str) -> bool:
        if not self._excluded or not op_name:
            return False
        low = str(op_name).strip().lower()
        return any(fnmatch.fnmatch(low, pat) for pat in self._excluded)

    def register(self, name: str, domain: str, description: str,
                 parameters: dict, handler: Callable) -> Optional[Operation]:
        op = Operation(name, domain, description, parameters, handler)
        if self.is_excluded(op.qualified_name) or self._owned_elsewhere(op):
            return None
        self._note_clash(op)
        self._ops[op.qualified_name] = op
        self._changed()
        return op

    def _owned_elsewhere(self, op: Operation) -> bool:
        """True when another provider has claimed this operation's domain.

        A CLAIM HAS TO SURVIVE THE NEXT REGISTRATION, or it is not a claim.
        Displacing the built-in browser when a browser server connects worked
        exactly until something imported the module that registers built-ins
        again -- and the registry is a singleton, so the second bootstrap
        quietly put half of them back, on top. The result was one `web`
        domain holding two browsers: `web.navigate` drove the server's,
        `web.click` drove the built-in one's, and clicking never landed on
        the page that had been navigated.

        Order of arrival cannot be relied on for this. Ownership can.
        """
        owner = getattr(self, "_owners", {}).get((op.domain or "").lower())
        if owner is None or owner == op.provider:
            return False
        if op.qualified_name not in getattr(self, "_refused", ()):
            self._refused = set(getattr(self, "_refused", ())) | {op.qualified_name}
            logger.info("Not registering %s from %s: '%s' is answered for by %s.",
                        op.qualified_name, op.provider, op.domain, owner)
        return True

    def _note_clash(self, op: Operation):
        """Two different providers answering to one name is not an update.

        A LATE REGISTRATION USED TO WIN IN SILENCE. An MCP browser server and
        the built-in browser both put their tools in `web`, so `web.type` was
        the server's and `web.click` was the built-in one's -- two different
        browsers behind one name. Driving a page then meant navigating in one
        and clicking in the other, and the only symptom was that nothing
        worked and the agent looked incompetent.

        Replacing an operation with another from the SAME provider is an
        ordinary update (a server reconnecting, a module reloading) and is
        not a clash.
        """
        held = self._ops.get(op.qualified_name)
        if held is None or held.provider == op.provider:
            return
        row = {"op": op.qualified_name, "was": held.provider, "now": op.provider}
        self._clashes = [c for c in getattr(self, "_clashes", [])
                         if c["op"] != op.qualified_name] + [row]
        logger.warning(
            "%s now comes from %s, replacing %s. Two providers in one domain "
            "means a name whose meaning depends on start-up order.",
            op.qualified_name, op.provider, held.provider)

    def clashes(self) -> list:
        """Names two providers have both claimed, most recent last."""
        return list(getattr(self, "_clashes", []))

    def owners(self) -> dict:
        """{domain: provider} -- domains one provider answers for."""
        return dict(getattr(self, "_owners", {}))

    def release_domain(self, domain: str, provider: str = "") -> bool:
        """Give a domain back, so what it displaced may register again.

        A server that goes away must not keep its claim: the built-in
        operations it replaced are the fallback, and a claim nobody is
        serving is a domain with nothing in it.
        """
        domain = (domain or "").strip().lower()
        owners = dict(getattr(self, "_owners", {}))
        if domain not in owners or (provider and owners[domain] != provider):
            return False
        del owners[domain]
        self._owners = owners
        self._refused = {q for q in getattr(self, "_refused", ())
                         if not q.lower().startswith(domain + ".")}
        logger.info("'%s' is no longer claimed; anything that registers there "
                    "may have it again.", domain)
        return True

    def providers_by_domain(self) -> dict:
        """{domain: {provider: [op, ...]}} -- who serves what, where.

        A domain with more than one provider in it is the real fault: a
        single clashing NAME is visible, but a domain quietly half-served by
        each is the one that cannot be reasoned about at all.
        """
        out = {}
        for qualified, op in self._ops.items():
            out.setdefault(op.domain or "", {}).setdefault(op.provider, []).append(qualified)
        return out

    def mixed_domains(self) -> dict:
        """Only the domains served by more than one provider."""
        return {d: p for d, p in self.providers_by_domain().items() if len(p) > 1}

    def claim_domain(self, domain: str, provider: str) -> list:
        """One provider answers for a whole domain. Returns what was retired.

        Reporting a mixed domain is not enough, because the mix is not a
        warning about a future problem -- it IS the problem, from the moment
        it exists. `web` half-served by a browser server and half by the
        built-in browser is not two browsers you can choose between: it is
        one name that drives whichever of them registered last, so navigating
        and clicking land in different windows.

        So a domain gets ONE owner and the rest of it steps aside. Nothing is
        hidden: what was retired is returned and logged by name, and the
        operations still exist in their own module for a host that wants them
        instead.
        """
        domain = (domain or "").strip().lower()
        if not domain:
            return []
        # Recorded, so a later registration from anyone else is refused
        # rather than quietly winning. See _owned_elsewhere.
        self._owners = dict(getattr(self, "_owners", {}))
        self._owners[domain] = provider
        retired = []
        for qualified in [q for q, op in self._ops.items()
                          if (op.domain or "").lower() == domain
                          and op.provider != provider]:
            retired.append(qualified)
            del self._ops[qualified]
        if retired:
            self._aliases = {a: t for a, t in self._aliases.items() if t not in retired}
            self._changed()
            logger.warning("%s now answers for the whole '%s' domain; retired %d "
                           "operation(s) that came from elsewhere: %s",
                           provider, domain, len(retired), ", ".join(sorted(retired)))
        return retired

    def register_operation(self, op: Operation):
        if self.is_excluded(op.qualified_name) or self._owned_elsewhere(op):
            return
        self._note_clash(op)
        self._ops[op.qualified_name] = op
        self._changed()

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
        self._changed()
        return True

    def alias(self, alias_name: str, target_name: str):
        if self.is_excluded(target_name) or self.is_excluded(alias_name):
            return
        self._aliases[alias_name] = target_name
        self._changed()

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
        self._changed()

    def __len__(self):
        return len(self._ops)

    def __repr__(self):
        return f"OperationRegistry({len(self._ops)} ops across {len(self.list_domains())} domains)"


def get_registry() -> OperationRegistry:
    return OperationRegistry()
