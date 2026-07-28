# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
"""
IAC — Integrated Agent Core
D-Net Node Protocol for structured data exchange.

Public API:
    validate(plan)     — Validate an IAC plan-config
    execute(source)    — Execute an IAC plan from file or dict
    create(plan, ...)  — Create an IAC envelope
    resolve(source)    — Resolve file pointers and normalize
    get_registry()     — Access the operation registry
    get_ic_router()    — Access the Intelligence Core router

Extending IAC with your own operations:
    Domain             — declare a domain of operations you own
    extend(domain)     — register it
    discover()         — load every installed `iac.domains` extension
    list_extensions()  — what is loaded, and where it came from

    Anything specific to your product belongs in an extension, not in IAC.
    See EXTENDING.md.

Attribution:
    ATTRIBUTION        — the notice to reproduce when redistributing
    attribution()      — the same, as text

    IAC is Apache-2.0: use it, modify it, sell what you build with it. The
    licence does not grant rights to the D-Net Lab name (Apache-2.0 §6) — see
    TRADEMARKS.md. Attribution is exposed here so nobody has to hand-write it.
"""

from .schema import validate_plan as validate
from .schema import validate_and_check
from .protocol import create_envelope as create
from .protocol import normalize_envelope as normalize
from .protocol import IAC_VERSION
from .resolver import resolve_full_envelope as resolve
from .runner import execute
from .registry import get_registry
from .ic import get_ic_router
from .extensions import (Domain, ExtensionError, discover, list_extensions,
                         register_domain as extend, load as load_extension)

__version__ = IAC_VERSION

__author__ = "D-Net Lab"
__license__ = "Apache-2.0"
__copyright__ = "Copyright (c) 2026 D-Net Lab"
__homepage__ = "https://lab.dnet.live"

# The notice to reproduce when redistributing IAC or shipping something built
# on it. Exposed at runtime, not only in NOTICE, so attribution travels with a
# running instance and nobody has to retype it. See TRADEMARKS.md.
ATTRIBUTION = (
    f"Powered by IAC (Integrated Agent Core) v{IAC_VERSION}\n"
    f"{__copyright__} — {__homepage__}\n"
    f"Licensed under {__license__}"
)


def attribution() -> str:
    """The attribution notice, as text. Put this in your about/credits screen."""
    return ATTRIBUTION


__all__ = [
    "validate", "validate_and_check", "execute", "create",
    "normalize", "resolve", "get_registry", "get_ic_router",
    "Domain", "ExtensionError", "extend", "discover", "load_extension",
    "list_extensions", "IAC_VERSION", "ATTRIBUTION", "attribution",
]
