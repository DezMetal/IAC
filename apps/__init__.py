# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
"""
Cell Apps -- find, inspect, run and stop them.

A **Cell App** is a D-Net program that gets its intelligence from a Cell,
speaks IAC, and declares what it is rather than being configured into working.
A directory with a `cellapp.json` is a Cell App; that is the whole contract.

This lives in IAC rather than in a component of its own because IAC already
has a registry, a runner and pipelines -- launching a declared program is the
same job, and a separate launcher would have been a third surface over one
idea. See DESIGN.md.

    python3 -m IAC.apps            what is installed, and what is running
    python3 -m IAC.apps run transposer serve

and over IAC, as `apps.*`.
"""

from .bundle import export_app, import_bundle, inspect_bundle
from .manifest import MANIFEST_NAME, SPEC_VERSION, Manifest, load
from .registry import Registry, check, default_roots
from .scaffold import SCAFFOLD_KINDS, scaffold
from .runner import RunError, Runner

__all__ = ["Manifest", "load", "MANIFEST_NAME", "SPEC_VERSION", "Registry",
           "check", "default_roots", "Runner", "RunError", "export_app",
           "import_bundle", "inspect_bundle", "scaffold", "SCAFFOLD_KINDS"]
