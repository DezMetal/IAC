# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
"""
Discovery and readiness -- which Cell Apps are here, and which can run.

Discovery is a directory walk, not an index. The manifests are the registry, so
there is nothing to keep in sync and an app that was deleted stops existing
rather than lingering in a list.

READINESS IS SEPARATE FROM VALIDITY

A manifest can be perfect and the app still unable to run -- no Cell reachable,
`piper-tts` not installed, no microphone on this host. Those are different
questions with different fixes, so they are answered separately:

* `Manifest.problems()` -- is the declaration wrong?
* `check()` -- is this host missing something the declaration asks for?

Conflating them produces the unhelpful "app unavailable". Keeping them apart
means every answer names a fix.
"""

import importlib.util
import json
import os
import shutil
import sys
import urllib.error
import urllib.request

from .manifest import KNOWN_CAPABILITIES, MANIFEST_NAME, find_in, load

__all__ = ["Registry", "default_roots", "check"]


_HERE = os.path.dirname(os.path.abspath(__file__))
#: This file lives at <lab>/IAC/apps/, so the lab root is two levels up.
#: It was one level up before this moved into IAC, and getting it wrong made
#: the launcher search inside IAC itself and report no apps at all.
_LAB_ROOT = os.path.dirname(os.path.dirname(_HERE))

#: pip name -> import name, where they differ. Checking the wrong one reports
#: a missing package that is installed, which is worse than not checking.
_IMPORT_NAMES = {
    "openai-whisper": "whisper",
    "piper-tts": "piper",
    "llama-cpp-python": "llama_cpp",
    "onnxruntime-gpu": "onnxruntime",
    "onnxruntime-directml": "onnxruntime",
    "pillow": "PIL",
    "pyyaml": "yaml",
    "python-dotenv": "dotenv",
}


def default_roots():
    """Where Cell Apps are looked for when nobody said.

    The lab's own tree first, then a per-user directory, because an app
    installed from a bundle should not have to live inside the source tree.
    """
    home = os.path.expanduser("~")
    candidates = [
        os.environ.get("CELL_APPS"),
        # The lab's own apps, grouped. Named explicitly rather than relying on
        # the lab root's walk depth, so the grouping can be nested further
        # without the launcher quietly losing sight of them.
        os.path.join(_LAB_ROOT, "CellApps"),
        _LAB_ROOT,
        os.path.join(home, ".cell", "apps"),
    ]
    seen, roots = set(), []
    for candidate in candidates:
        if not candidate or not os.path.isdir(candidate):
            continue
        key = os.path.normcase(os.path.abspath(candidate))
        if key not in seen:
            seen.add(key)
            roots.append(os.path.abspath(candidate))
    return roots


def _installed(package):
    """Whether a package is importable, by its import name."""
    module = _IMPORT_NAMES.get(package.lower(), package.replace("-", "_"))
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _cell_reachable(host=None):
    """(ok, detail) for an inference provider.

    Checked against the app's own config when it has one, because an app that
    points at a remote Cell is not broken just because nothing answers on
    localhost.
    """
    hosts = [host] if host else []
    hosts += ["http://127.0.0.1:8757"]
    for candidate in hosts:
        if not candidate:
            continue
        base = candidate.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        try:
            with urllib.request.urlopen(base + "/health", timeout=3):
                return True, base
        except (urllib.error.URLError, OSError, TimeoutError):
            continue
    return False, None


def _audio(kind):
    """Whether this host has a microphone or a speaker.

    Asks the Transposer's own prober when it is importable, since that is the
    component that already knows how -- rather than reimplementing ALSA
    parsing here.
    """
    try:
        from Transposer import devices
        probe = devices.probe()
        return bool(probe["can_hear"] if kind == "audio-in"
                    else probe["can_speak"])
    except Exception:
        pass
    if kind == "audio-in":
        return bool(shutil.which("arecord")) or _installed("sounddevice")
    return bool(shutil.which("aplay")) or _installed("sounddevice")


def check(manifest):
    """What this host is missing for this app. (ready, findings).

    Findings are dicts: `{need, ok, detail}`. Every `ok: False` carries a
    `detail` that names the fix -- an app that cannot run should say what to
    install, not that it is unavailable.
    """
    findings = []

    wanted = manifest.requires.get("python")
    if wanted:
        import re
        match = re.match(r">=\s*(\d+)\.(\d+)", str(wanted))
        if match:
            needed = (int(match.group(1)), int(match.group(2)))
            have = sys.version_info[:2]
            findings.append({
                "need": "python %s" % wanted, "ok": have >= needed,
                "detail": None if have >= needed else
                          "this is Python %d.%d" % have})

    for package in manifest.packages:
        ok = _installed(package)
        findings.append({
            "need": package, "ok": ok,
            "detail": None if ok else "pip install %s" % package})

    if manifest.needs_cell:
        host = None
        config = manifest.config_path
        # A sibling `config.local.json` wins, because that is the lab's
        # convention for "this machine's settings" and it is what the app
        # itself loads. Checking the shared config instead reported an app as
        # unready because it named a host that is only reachable from home.
        if config:
            local = os.path.join(os.path.dirname(config), "config.local.json")
            if os.path.isfile(local):
                config = local
        if config and os.path.isfile(config):
            try:
                with open(config, encoding="utf-8") as handle:
                    host = (json.load(handle) or {}).get("provider_host")
            except Exception:
                host = None
        ok, where = _cell_reachable(host)
        findings.append({
            "need": "a Cell", "ok": ok,
            "detail": ("reachable at %s" % where) if ok else
                      ("no inference provider answered%s. Start one: "
                       "python3 -m Cell.server"
                       % ((" at %s" % host) if host else ""))})

    for capability in manifest.capabilities:
        if capability in ("audio-in", "audio-out"):
            ok = _audio(capability)
            findings.append({
                "need": KNOWN_CAPABILITIES[capability], "ok": ok,
                "detail": None if ok else
                          "no %s found on this host"
                          % KNOWN_CAPABILITIES[capability]})
        elif capability == "network":
            findings.append({"need": "network", "ok": True, "detail": None})
        else:
            # Declared but not checkable here. Reported as unknown rather
            # than as satisfied -- claiming a capability nobody verified is
            # the thing these checks exist to prevent.
            findings.append({
                "need": KNOWN_CAPABILITIES.get(capability, capability),
                "ok": None,
                "detail": "IAC cannot verify this one; the app will say"})

    ready = all(f["ok"] is not False for f in findings)
    return ready, findings


class Registry:
    """The Cell Apps on this machine."""

    def __init__(self, roots=None):
        self.roots = list(roots) if roots else default_roots()
        self._apps = {}
        self._errors = []
        self.rebuild()

    def rebuild(self):
        self._apps = {}
        self._errors = []
        # The same manifest can be reached through two roots -- CellApps/ is
        # named explicitly AND sits inside the lab root -- and finding it
        # twice is not a name clash. Without this it reported an app as
        # conflicting with itself, naming the identical path on both sides.
        seen_paths = set()
        for root in self.roots:
            for path in find_in(root):
                key = os.path.normcase(os.path.abspath(path))
                if key in seen_paths:
                    continue
                seen_paths.add(key)
                try:
                    manifest = load(path)
                except ValueError as exc:
                    # A broken manifest is reported, not silently skipped: a
                    # typo that makes an app vanish is maddening to diagnose.
                    self._errors.append({"path": path, "error": str(exc)})
                    continue
                if not manifest.name:
                    self._errors.append({
                        "path": path,
                        "error": "no 'name' in the manifest, so Bay has "
                                 "nothing to call it"})
                    continue
                if manifest.name in self._apps:
                    existing = self._apps[manifest.name].path
                    self._errors.append({
                        "path": path,
                        "error": "another app already uses the name %r (%s); "
                                 "names must be unique"
                                 % (manifest.name, existing)})
                    continue
                self._apps[manifest.name] = manifest
        return self

    def __len__(self):
        return len(self._apps)

    def __iter__(self):
        return iter(sorted(self._apps.values(), key=lambda m: m.name))

    def get(self, name):
        return self._apps.get(str(name or "").strip())

    def names(self):
        return sorted(self._apps)

    def errors(self):
        return list(self._errors)

    def to_dict(self, deep=False):
        apps = []
        for manifest in self:
            entry = manifest.to_dict()
            if deep:
                ready, findings = check(manifest)
                entry["ready"] = ready
                entry["findings"] = findings
            apps.append(entry)
        return {"count": len(apps), "roots": self.roots, "apps": apps,
                "errors": self.errors(),
                "manifest_name": MANIFEST_NAME}
