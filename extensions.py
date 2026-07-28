# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
"""
IAC Extensions -- the supported way to add your own domains and operations.

WHY THIS EXISTS
---------------
IAC ships a set of general operations: filesystem, web, vision, desktop,
datastore. Anything beyond that belongs to whoever is using IAC, not to IAC.
A CRM integration, a company's internal API, a robot's motor controller -- none
of those should ever land in this repository, and none of them should have to.

Before this module, adding a domain meant reaching into `get_registry()` and
calling `register()` directly. That works, and still does, but it left three
things undefined: who owns a domain name, what a handler is allowed to assume,
and how a host decides whether an extension is safe to load at all. Those gaps
are exactly what makes a plugin system unsafe to deploy anywhere.

So an extension is now a declared thing with an owner, a version, and a stated
list of what it needs to touch.

WRITING ONE
-----------
    from IAC.extensions import Domain

    crm = Domain("crm", description="Our customer database",
                 author="Example Co", version="1.0", requires=["network"])

    @crm.op("lookup", "Find a customer by email",
            {"email": {"type": "string", "description": "Their address"}},
            required=["email"])
    def lookup(args, context):
        return {"status": "ok", "data": {...}, "message": "Found 1 customer."}

    crm.register()

That is the whole surface. `args` is the caller's arguments, `context` is the
execution context the host supplied, and the return value is a dict with
`status` plus whichever of `data`, `message` or `error` apply.

SHIPPING ONE
------------
Declare an entry point and IAC will find it after `pip install`:

    [project.entry-points."iac.domains"]
    crm = "my_package:crm"

Then the host calls `IAC.discover()` once. Nothing has to edit IAC.

WHAT A HOST CONTROLS
--------------------
- **Reserved names.** An extension cannot silently take over `filesystem` or
  `web`. Shadowing a core domain requires `replace=True` and says so in the log,
  because an extension quietly redefining `filesystem.write` is an attack, not
  a feature.
- **Capabilities.** A domain declares what it needs (`network`, `filesystem`,
  `subprocess`, `desktop`, `credentials`). A host running in safe mode refuses
  the ones it does not want, without having to know what the domain does.
- **Provenance.** `list_extensions()` reports every loaded domain and where it
  came from, so "what is this instance actually able to do" has an answer.
"""

import importlib
import logging
import sys
from typing import Callable, Dict, List, Optional

try:
    from .registry import get_registry
except ImportError:  # direct import, not as a package
    from registry import get_registry

logger = logging.getLogger("IAC.extensions")

__all__ = [
    "Domain", "ExtensionError", "register_domain", "discover", "load",
    "list_extensions", "CORE_DOMAINS", "CAPABILITIES",
]

# Shipped with IAC. An extension may not take these over by accident.
CORE_DOMAINS = frozenset({
    "core", "filesystem", "web", "vision", "desktop", "datastore",
    "ai", "sys", "prism", "iac",
})

# What a domain may declare it needs. A host in safe mode can refuse any of
# them without knowing anything else about the extension.
CAPABILITIES = frozenset({
    "network",      # makes outbound connections
    "filesystem",   # reads or writes files
    "subprocess",   # launches processes
    "desktop",      # controls the local display, input, or screen capture
    "credentials",  # handles secrets, tokens, or user accounts
})

# Populated as domains register, so a host can answer "what can this instance
# do, and who supplied it".
_loaded: Dict[str, dict] = {}


class ExtensionError(Exception):
    """An extension could not be registered."""


class Domain:
    """A named group of operations contributed by something other than IAC."""

    def __init__(self, name: str, description: str = "", author: str = "",
                 version: str = "", homepage: str = "",
                 requires: Optional[List[str]] = None):
        name = str(name or "").strip()
        if not name:
            raise ExtensionError("a domain needs a name")
        if "." in name or " " in name:
            raise ExtensionError(
                f"invalid domain name '{name}': no dots or spaces -- the dot "
                f"separates domain from operation in 'domain.operation'")

        unknown = set(requires or []) - CAPABILITIES
        if unknown:
            raise ExtensionError(
                f"domain '{name}' declares unknown capabilities: "
                f"{', '.join(sorted(unknown))}. "
                f"Known: {', '.join(sorted(CAPABILITIES))}")

        self.name = name
        self.description = description
        self.author = author
        self.version = version
        self.homepage = homepage
        self.requires = list(requires or [])
        self._ops = {}

    # ── declaring operations ──────────────────────────────────────────────
    def op(self, name: str, description: str = "",
           properties: Optional[dict] = None,
           required: Optional[List[str]] = None) -> Callable:
        """Decorator form. The function becomes the handler.

            @crm.op("lookup", "Find a customer", {"email": {...}}, ["email"])
            def lookup(args, context): ...
        """
        def decorator(fn):
            self.add(name, fn, description, properties, required)
            return fn
        return decorator

    def add(self, name: str, handler: Callable, description: str = "",
            properties: Optional[dict] = None,
            required: Optional[List[str]] = None) -> "Domain":
        """Imperative form, for handlers you already have."""
        name = str(name or "").strip()
        if not name:
            raise ExtensionError(f"[{self.name}] an operation needs a name")
        if "." in name:
            raise ExtensionError(
                f"[{self.name}] operation '{name}' may not contain a dot")
        if not callable(handler):
            raise ExtensionError(
                f"[{self.name}.{name}] handler is not callable")
        if name in self._ops:
            raise ExtensionError(
                f"[{self.name}] '{name}' is already defined in this domain")

        self._ops[name] = {
            "description": description or f"{self.name}.{name}",
            "parameters": {
                "type": "object",
                "properties": properties or {},
                "required": list(required or []),
            },
            "handler": handler,
        }
        return self

    def operations(self) -> List[str]:
        return sorted(self._ops)

    # ── registering ───────────────────────────────────────────────────────
    def register(self, registry=None, replace: bool = False,
                 safe_mode: bool = False,
                 allow: Optional[List[str]] = None) -> int:
        """Put this domain's operations into a registry. Returns the count."""
        return register_domain(self, registry=registry, replace=replace,
                               safe_mode=safe_mode, allow=allow)

    def info(self) -> dict:
        return {
            "domain": self.name,
            "description": self.description,
            "author": self.author,
            "version": self.version,
            "homepage": self.homepage,
            "requires": list(self.requires),
            "operations": self.operations(),
        }

    def __len__(self):
        return len(self._ops)

    def __repr__(self):
        return (f"<Domain {self.name} ({len(self._ops)} ops"
                + (f", by {self.author}" if self.author else "") + ")>")


def register_domain(domain: Domain, registry=None, replace: bool = False,
                    safe_mode: bool = False, source: str = "direct",
                    allow: Optional[List[str]] = None) -> int:
    """Register a Domain, enforcing the host's rules about who may do what.

    safe_mode:
        Refuse any domain declaring a capability outside `allow`. A host that
        does not trust an extension can load it without having to audit it --
        the domain says what it touches, and the host says yes or no.
    """
    if not isinstance(domain, Domain):
        raise ExtensionError(f"not a Domain: {domain!r}")
    if not len(domain):
        logger.warning("Domain '%s' declares no operations.", domain.name)
        return 0

    # `registry or get_registry()` would be wrong: OperationRegistry defines
    # __len__, so an EMPTY registry is falsy and a host passing a fresh one
    # would have its operations silently land in the global singleton instead.
    registry = get_registry() if registry is None else registry

    if domain.name in CORE_DOMAINS and not replace:
        raise ExtensionError(
            f"'{domain.name}' is a core IAC domain. Registering over it would "
            f"silently redefine built-in operations; pass replace=True if that "
            f"is genuinely what you intend.")

    if safe_mode:
        permitted = set(allow if allow is not None else [])
        refused = sorted(set(domain.requires) - permitted)
        if refused:
            logger.warning(
                "Refusing domain '%s' in safe mode: it needs %s.",
                domain.name, ", ".join(refused))
            return 0

    existing = _loaded.get(domain.name)
    if existing and not replace:
        raise ExtensionError(
            f"domain '{domain.name}' is already registered (from "
            f"{existing['source']}). Pass replace=True to override it.")

    count = 0
    for op_name, spec in domain._ops.items():
        try:
            registry.register(name=op_name, domain=domain.name,
                              description=spec["description"],
                              parameters=spec["parameters"],
                              handler=spec["handler"])
            count += 1
        except Exception as e:
            logger.warning("Could not register %s.%s: %s",
                           domain.name, op_name, e)

    _loaded[domain.name] = {**domain.info(), "source": source,
                            "registered": count}
    logger.info("Registered domain '%s' (%d ops) from %s",
                domain.name, count, source)
    return count


def load(target, registry=None, replace: bool = False,
         safe_mode: bool = False, allow: Optional[List[str]] = None) -> int:
    """Load an extension by import path, e.g. "my_package:crm".

    Accepts "module:attribute", a bare module (which must expose `domain`, or a
    `register(registry)` function), or a Domain instance.
    """
    if isinstance(target, Domain):
        return register_domain(target, registry, replace, safe_mode,
                               "direct", allow)

    # Same trap as in register_domain: an empty registry is falsy.
    registry = get_registry() if registry is None else registry
    spec = str(target)
    mod_name, _, attr = spec.partition(":")
    try:
        module = importlib.import_module(mod_name)
    except Exception as e:
        raise ExtensionError(f"could not import '{mod_name}': {e}")

    if attr:
        obj = getattr(module, attr, None)
        if obj is None:
            raise ExtensionError(f"'{mod_name}' has no attribute '{attr}'")
    else:
        obj = getattr(module, "domain", None)

    if isinstance(obj, Domain):
        return register_domain(obj, registry, replace, safe_mode, spec, allow)

    # A plain register(registry) function -- the older style, still supported
    # so existing integrations keep working unchanged.
    fn = obj if callable(obj) else getattr(module, "register", None)
    if callable(fn):
        before = len(registry)
        fn(registry)
        after = len(registry)
        _loaded[mod_name] = {"domain": mod_name, "source": spec,
                             "registered": after - before, "requires": [],
                             "operations": [], "author": "", "version": "",
                             "description": "(legacy register function)",
                             "homepage": ""}
        return after - before

    raise ExtensionError(
        f"'{spec}' is neither a Domain nor a register(registry) function")


def _entry_points(group: str):
    """Entry points for a group, across the importlib.metadata API changes."""
    try:
        from importlib import metadata
    except ImportError:  # pragma: no cover -- Python < 3.8
        return []
    try:
        eps = metadata.entry_points()
        if hasattr(eps, "select"):          # 3.10+
            return list(eps.select(group=group))
        return list(eps.get(group, []))     # 3.8 / 3.9
    except Exception as e:
        logger.debug("Entry point lookup failed: %s", e)
        return []


def discover(registry=None, safe_mode: bool = False,
             allow: Optional[List[str]] = None, group: str = "iac.domains") -> int:
    """Find and load every installed extension advertising `iac.domains`.

    This is what makes an extension a thing you `pip install` rather than a
    thing you patch IAC to support. One failing extension is logged and skipped
    -- it must not take the host down with it.
    """
    total = 0
    for ep in _entry_points(group):
        try:
            obj = ep.load()
            if isinstance(obj, Domain):
                total += register_domain(obj, registry, False, safe_mode,
                                         f"entry_point:{ep.name}", allow)
            elif callable(obj):
                total += load(obj, registry, False, safe_mode, allow) \
                    if isinstance(obj, str) else _call_legacy(obj, registry)
            else:
                logger.warning("Entry point '%s' is not a Domain.", ep.name)
        except Exception as e:
            logger.warning("Extension '%s' failed to load: %s", ep.name, e)
    return total


def _call_legacy(fn, registry):
    reg = get_registry() if registry is None else registry
    before = len(reg)
    fn(reg)
    return len(reg) - before


def list_extensions() -> List[dict]:
    """Every domain registered through this module, and where it came from."""
    return sorted(_loaded.values(), key=lambda d: d["domain"])


def forget(domain_name: str) -> bool:
    """Drop the provenance record for a domain. Tests and hot-reload use this;
    it does not unregister the operations themselves."""
    return _loaded.pop(domain_name, None) is not None
