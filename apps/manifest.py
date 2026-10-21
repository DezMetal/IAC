# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
"""
The Cell App manifest -- `cellapp.json`, read and checked.

A Cell App is a directory with a manifest. That is the entire contract, and it
is deliberately the smallest one that works: the manifests *are* the registry,
so there is no index file to drift out of sync with what is on disk.

WHAT VALIDATION IS FOR HERE

Not schema purity. A manifest is written by hand, so the failure modes are
typos and omissions, and the job is to say which -- by name, with what the
field should have looked like. `Manifest.problems()` returns sentences a person
can act on, because the alternative is a launcher that reports an app as
"invalid" and leaves them diffing JSON.

A manifest that is merely *unusual* is not rejected. Unknown keys are kept and
reported, never dropped: the operator's file is the authority on what they
meant, and a launcher's job is to say which parts it acted on.
"""

import json
import os
import re

__all__ = ["Manifest", "MANIFEST_NAME", "SPEC_VERSION", "load", "find_in"]

MANIFEST_NAME = "cellapp.json"
SPEC_VERSION = "1.0"

_NAME_OK = re.compile(r"^[a-z][a-z0-9_-]{1,40}$")

#: Keys IAC understands. Anything else is carried and reported.
_KNOWN = {
    "cellapp", "name", "title", "summary", "version", "domain", "register",
    "commands", "requires", "config", "compute", "homepage", "author",
    "icon", "tags",
}

#: Hardware or environment an app can ask for, and who can answer.
KNOWN_CAPABILITIES = {
    "audio-in": "a microphone",
    "audio-out": "a speaker or headphone output",
    "display": "a screen",
    "gpio": "GPIO pins",
    "network": "outbound network access",
    "camera": "a camera",
}


class Manifest:
    """One Cell App's declaration."""

    def __init__(self, data, path=None):
        self.data = dict(data or {})
        self.path = path
        self.root = os.path.dirname(os.path.abspath(path)) if path else None

    # ── fields ────────────────────────────────────────────────────────────

    @property
    def name(self):
        return str(self.data.get("name") or "").strip()

    @property
    def title(self):
        return self.data.get("title") or self.name or "untitled"

    @property
    def summary(self):
        return self.data.get("summary") or ""

    @property
    def version(self):
        return str(self.data.get("version") or "0")

    @property
    def domain(self):
        return self.data.get("domain")

    @property
    def register(self):
        """`module:callable` that registers this app's IAC domain, or None."""
        return self.data.get("register")

    @property
    def commands(self):
        value = self.data.get("commands")
        return value if isinstance(value, dict) else {}

    @property
    def requires(self):
        value = self.data.get("requires")
        return value if isinstance(value, dict) else {}

    @property
    def needs_cell(self):
        return bool(self.requires.get("cell"))

    @property
    def packages(self):
        return [str(p) for p in (self.requires.get("packages") or [])]

    @property
    def capabilities(self):
        return [str(c) for c in (self.requires.get("capabilities") or [])]

    @property
    def compute_mode(self):
        compute = self.data.get("compute")
        if isinstance(compute, dict):
            return compute.get("mode")
        return None

    @property
    def config_path(self):
        name = self.data.get("config")
        if not name or not self.root:
            return None
        return os.path.join(self.root, name)

    def services(self):
        """Commands that are long-running surfaces rather than one-shot runs."""
        return {name: spec for name, spec in self.commands.items()
                if isinstance(spec, dict) and spec.get("service")}

    # ── checking ──────────────────────────────────────────────────────────

    def problems(self):
        """Everything wrong with this manifest, as actionable sentences."""
        issues = []

        spec = str(self.data.get("cellapp") or "")
        if not spec:
            issues.append("missing 'cellapp': set it to \"%s\" -- it is how "
                          "IAC knows this is a Cell App manifest"
                          % SPEC_VERSION)
        elif spec.split(".")[0] != SPEC_VERSION.split(".")[0]:
            issues.append("'cellapp' is %r, but IAC understands %s.x"
                          % (spec, SPEC_VERSION.split(".")[0]))

        if not self.name:
            issues.append("missing 'name': a short lowercase id, e.g. "
                          "\"transposer\"")
        elif not _NAME_OK.match(self.name):
            issues.append("'name' %r must be lowercase letters, digits, "
                          "hyphen or underscore, starting with a letter"
                          % self.name)

        if not self.commands:
            issues.append("no 'commands': an app with nothing to run cannot "
                          "be launched. Add at least one, e.g. "
                          "{\"serve\": {\"run\": \"Pkg.service:main\"}}")

        for label, spec_dict in self.commands.items():
            if not isinstance(spec_dict, dict):
                issues.append("command %r must be an object with a 'run'"
                              % label)
                continue
            target = spec_dict.get("run")
            if not target:
                issues.append("command %r has no 'run': use "
                              "\"module:callable\"" % label)
            elif ":" not in str(target):
                issues.append("command %r 'run' is %r; it must be "
                              "\"module:callable\"" % (label, target))

        if self.register and ":" not in str(self.register):
            issues.append("'register' is %r; it must be \"module:callable\""
                          % self.register)

        unknown_caps = [c for c in self.capabilities
                        if c not in KNOWN_CAPABILITIES]
        if unknown_caps:
            issues.append("unknown capabilities %s; known: %s"
                          % (", ".join(unknown_caps),
                             ", ".join(sorted(KNOWN_CAPABILITIES))))

        mode = self.compute_mode
        if mode and mode not in ("home", "local", "prefer-local"):
            issues.append("compute mode %r must be home, local or "
                          "prefer-local" % mode)

        if self.config_path and not os.path.isfile(self.config_path):
            # Not fatal: setup may be expected to write it.
            issues.append("'config' names %s, which does not exist yet "
                          "(run the app's setup, or remove the key)"
                          % os.path.basename(self.config_path))
        return issues

    @property
    def valid(self):
        """Whether this manifest can be launched at all.

        A missing config is reported but not disqualifying -- an app whose
        setup has not run yet is a normal state, not a broken manifest.
        """
        fatal = [p for p in self.problems() if "does not exist yet" not in p]
        return not fatal

    def unknown_keys(self):
        return sorted(k for k in self.data
                      if k not in _KNOWN and not k.startswith("_"))

    # ── reporting ─────────────────────────────────────────────────────────

    def to_dict(self):
        return {
            "name": self.name, "title": self.title, "summary": self.summary,
            "version": self.version, "domain": self.domain,
            "commands": sorted(self.commands),
            "services": sorted(self.services()),
            "requires": {"cell": self.needs_cell,
                         "packages": self.packages,
                         "capabilities": self.capabilities,
                         "python": self.requires.get("python")},
            "compute": self.compute_mode,
            "root": self.root, "path": self.path,
            "valid": self.valid, "problems": self.problems(),
            "unknown_keys": self.unknown_keys(),
        }

    def __repr__(self):
        return "Manifest(%s v%s, %d command(s))" % (
            self.name or "?", self.version, len(self.commands))


def load(path):
    """A `Manifest` from a file. Raises `ValueError` with a usable sentence."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        raise ValueError("no manifest at %s" % path)
    except json.JSONDecodeError as exc:
        raise ValueError("%s is not valid JSON: line %d, %s"
                         % (os.path.basename(path), exc.lineno, exc.msg))
    if not isinstance(data, dict):
        raise ValueError("%s must contain a JSON object"
                         % os.path.basename(path))
    return Manifest(data, path)


def find_in(root, max_depth=2):
    """Manifest paths under `root`. Shallow: apps live near the top.

    Depth-limited because a Cell App's manifest sits in its own directory, and
    walking a whole tree to find one would pick up vendored copies inside
    dependencies.
    """
    found = []
    if not root or not os.path.isdir(root):
        return found
    root = os.path.abspath(root)
    base = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".")
                       and d not in ("__pycache__", "node_modules", "venv",
                                     ".venv", "site-packages", "dist",
                                     "build", "models", "tests")]
        if dirpath.count(os.sep) - base >= max_depth:
            dirnames[:] = []
        if MANIFEST_NAME in filenames:
            found.append(os.path.join(dirpath, MANIFEST_NAME))
            # An app does not contain another app.
            dirnames[:] = []
    return found
