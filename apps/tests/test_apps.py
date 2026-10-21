# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
"""
Tests for the IAC Launcher.

Everything here works on a temporary tree, so the suite neither needs nor
touches the apps actually installed on this machine. The parts worth testing
are the ones that are easy to get wrong and quiet when wrong: manifest
validation, bundle safety, and whether a dead process is reported as dead.

Run:  python3 IAC/apps/tests/test_apps.py
"""

import io
import json
import os
import shutil
import sys
import tarfile
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from IAC.apps import (Manifest, Registry, Runner, check,  # noqa: E402
                      export_app, import_bundle, inspect_bundle, load)
from IAC.apps.console import safe, say  # noqa: E402
from IAC.apps.manifest import MANIFEST_NAME, find_in  # noqa: E402

_passed, _failed, _skipped = [], [], []


def ok(name, condition, detail=""):
    if condition:
        _passed.append(name)
        print("  ok    %s" % name)
    else:
        _failed.append((name, detail))
        print("  FAIL  %s %s" % (name, "-- " + detail if detail else ""))


def skip(name, why):
    _skipped.append((name, why))
    print("  skip  %s -- %s" % (name, why))


GOOD = {
    "cellapp": "1.0", "name": "demo-app", "title": "Demo",
    "summary": "A test app.", "version": "1.2",
    "domain": "demo", "register": "demo.ops:register",
    "commands": {"serve": {"run": "demo.service:main", "service": True,
                           "port": 9999},
                 "once": {"run": "demo.cli:main", "args": ["once"]}},
    "requires": {"cell": True, "python": ">=3.9",
                 "packages": ["definitely-not-installed-xyz"],
                 "capabilities": ["audio-in"]},
    "compute": {"mode": "local"},
}


def _tree(data=None, name="demo-app", with_config=False):
    """A throwaway app directory."""
    root = tempfile.mkdtemp(prefix="apps-test-")
    app = os.path.join(root, name)
    os.makedirs(app)
    with io.open(os.path.join(app, MANIFEST_NAME), "w",
                 encoding="utf-8") as handle:
        json.dump(data if data is not None else GOOD, handle)
    with io.open(os.path.join(app, "code.py"), "w", encoding="utf-8") as h:
        h.write("# app code\n")
    os.makedirs(os.path.join(app, "__pycache__"), exist_ok=True)
    with io.open(os.path.join(app, "__pycache__", "x.pyc"), "w") as h:
        h.write("cached")
    os.makedirs(os.path.join(app, "models"), exist_ok=True)
    with io.open(os.path.join(app, "models", "big.gguf"), "w") as h:
        h.write("weights" * 100)
    if with_config:
        with io.open(os.path.join(app, "config.json"), "w",
                     encoding="utf-8") as h:
            json.dump({"provider_host": "http://secret-host:8757/v1",
                       "api_key": "a-secret"}, h)
    return root, app


# ── manifests ─────────────────────────────────────────────────────────────

def test_manifest():
    print("\nmanifest")
    good = Manifest(GOOD, path="/x/demo-app/cellapp.json")
    ok("a good manifest is valid", good.valid, str(good.problems()))
    ok("reads its name and version",
       good.name == "demo-app" and good.version == "1.2")
    ok("lists commands", set(good.commands) == {"serve", "once"})
    ok("knows which are services", set(good.services()) == {"serve"})
    ok("reads requirements",
       good.needs_cell and good.capabilities == ["audio-in"])
    ok("reads the compute mode", good.compute_mode == "local")

    cases = (
        ({k: v for k, v in GOOD.items() if k != "cellapp"}, "cellapp"),
        ({**GOOD, "name": "Bad Name"}, "lowercase"),
        ({**GOOD, "name": ""}, "name"),
        ({**GOOD, "commands": {}}, "nothing to run"),
        ({**GOOD, "commands": {"x": {"run": "no-colon"}}}, "module:callable"),
        ({**GOOD, "register": "no-colon"}, "module:callable"),
        ({**GOOD, "compute": {"mode": "sideways"}}, "home, local"),
        ({**GOOD, "requires": {"capabilities": ["telepathy"]}}, "unknown"),
    )
    for data, expected in cases:
        manifest = Manifest(data)
        problems = " ".join(manifest.problems())
        ok("rejects %s" % expected, expected in problems,
           "problems were: %s" % problems[:110])

    unknown = Manifest({**GOOD, "wat": 1})
    ok("an unknown key is carried, not dropped",
       "wat" in unknown.unknown_keys() and unknown.valid,
       "the operator's file is the authority on what they meant")

    root, _app = _tree()
    try:
        found = find_in(root)
        ok("discovery finds a manifest", len(found) == 1, str(found))
        manifest = load(found[0])
        ok("loading gives the same app", manifest.name == "demo-app")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    bad = tempfile.mkdtemp(prefix="apps-bad-")
    try:
        path = os.path.join(bad, MANIFEST_NAME)
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        try:
            load(path)
            ok("bad JSON is refused with a line number", False)
        except ValueError as exc:
            ok("bad JSON is refused with a line number", "line" in str(exc),
               str(exc))
    finally:
        shutil.rmtree(bad, ignore_errors=True)


def test_registry():
    print("\nregistry")
    root, _ = _tree()
    try:
        registry = Registry([root])
        ok("finds the app", registry.names() == ["demo-app"])
        ok("get() works", registry.get("demo-app") is not None)
        ok("get() on an unknown name is None",
           registry.get("nope") is None)

        ready, findings = check(registry.get("demo-app"))
        ok("a missing package makes it not ready", not ready)
        missing = [f for f in findings if f["ok"] is False]
        ok("every gap names a fix",
           all(f["detail"] for f in missing), str(missing))
        ok("the missing package is named",
           any("definitely-not-installed-xyz" in (f["detail"] or "")
               for f in missing))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # Two apps with one name: reported, not silently shadowed.
    root2 = tempfile.mkdtemp(prefix="apps-dup-")
    try:
        for sub in ("one", "two"):
            app = os.path.join(root2, sub)
            os.makedirs(app)
            with io.open(os.path.join(app, MANIFEST_NAME), "w",
                         encoding="utf-8") as handle:
                json.dump(GOOD, handle)
        registry = Registry([root2])
        ok("a duplicate name is reported",
           any("already uses the name" in e["error"]
               for e in registry.errors()), str(registry.errors()))
        ok("and one of them still loads", len(registry) == 1)
    finally:
        shutil.rmtree(root2, ignore_errors=True)


# ── bundles ───────────────────────────────────────────────────────────────

def test_bundles():
    print("\nbundles")
    root, app = _tree(with_config=True)
    out = tempfile.mkdtemp(prefix="apps-out-")
    try:
        manifest = load(os.path.join(app, MANIFEST_NAME))
        result = export_app(manifest, os.path.join(out, "demo.cellapp"))
        ok("export writes a file", os.path.isfile(result["path"]))

        with tarfile.open(result["path"], "r:gz") as tar:
            names = [m.name for m in tar.getmembers()]
        ok("the manifest travels",
           any(n.endswith(MANIFEST_NAME) for n in names))
        ok("the code travels", any(n.endswith("code.py") for n in names))
        ok("a config does NOT travel",
           not any(n.endswith("config.json") for n in names),
           "a config names hosts and keys that are wrong or secret elsewhere")
        ok("models do NOT travel",
           not any(n.endswith(".gguf") for n in names),
           "hundreds of MB the receiver may already have")
        ok("caches do NOT travel", not any(".pyc" in n for n in names))
        ok("bundle metadata is included",
           any(n.endswith(".bundle.json") for n in names))

        info = inspect_bundle(result["path"])
        ok("inspect reads it without unpacking",
           info["manifest"]["name"] == "demo-app")

        into = tempfile.mkdtemp(prefix="apps-in-")
        try:
            landed = import_bundle(result["path"], into)
            ok("import unpacks it", os.path.isdir(landed["path"]))
            ok("the imported app is discoverable",
               Registry([into]).names() == ["demo-app"])
            ok("import says nothing is configured yet",
               "not carry" in landed["next"] or "configured" in landed["next"])
            try:
                import_bundle(result["path"], into)
                ok("a second import refuses without overwrite", False)
            except ValueError as exc:
                ok("a second import refuses without overwrite",
                   "already exists" in str(exc))
        finally:
            shutil.rmtree(into, ignore_errors=True)

        # Carrying a config is possible but must be explicit and reported.
        with_config = export_app(manifest,
                                 os.path.join(out, "demo-cfg.cellapp"),
                                 include_config=True)
        with tarfile.open(with_config["path"], "r:gz") as tar:
            names = [m.name for m in tar.getmembers()]
        ok("include_config carries it when asked",
           any(n.endswith("config.json") for n in names))
        ok("and the bundle records that it did",
           inspect_bundle(with_config["path"])["meta"]["includes_config"])
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


def test_bundle_safety():
    """A bundle is untrusted input: a tar entry may try to escape."""
    print("\nbundle safety")
    out = tempfile.mkdtemp(prefix="apps-evil-")
    into = tempfile.mkdtemp(prefix="apps-dest-")
    try:
        path = os.path.join(out, "evil.cellapp")
        with tarfile.open(path, "w:gz") as tar:
            blob = json.dumps(GOOD).encode("utf-8")
            info = tarfile.TarInfo("demo-app/" + MANIFEST_NAME)
            info.size = len(blob)
            tar.addfile(info, io.BytesIO(blob))
            # The attack: a path that climbs out of the destination.
            escape = b"owned"
            info = tarfile.TarInfo("../escaped.txt")
            info.size = len(escape)
            tar.addfile(info, io.BytesIO(escape))

        result = import_bundle(path, into)
        ok("the escaping member was refused",
           result["refused_members"] >= 1, str(result))
        parent = os.path.dirname(os.path.abspath(into))
        ok("nothing was written outside the destination",
           not os.path.exists(os.path.join(parent, "escaped.txt")),
           "python extracts ../ paths happily unless stopped")

        # A tar with no manifest is not a Cell App.
        plain = os.path.join(out, "plain.cellapp")
        with tarfile.open(plain, "w:gz") as tar:
            info = tarfile.TarInfo("x/readme.txt")
            info.size = 2
            tar.addfile(info, io.BytesIO(b"hi"))
        try:
            inspect_bundle(plain)
            ok("a tar with no manifest is refused", False)
        except ValueError as exc:
            ok("a tar with no manifest is refused",
               "not a Cell App" in str(exc) or MANIFEST_NAME in str(exc))
    finally:
        shutil.rmtree(out, ignore_errors=True)
        shutil.rmtree(into, ignore_errors=True)


# ── running ───────────────────────────────────────────────────────────────

def test_runner():
    print("\nrunner")
    state = tempfile.mkdtemp(prefix="apps-state-")
    root, app = _tree(data={
        "cellapp": "1.0", "name": "tiny", "title": "Tiny",
        "summary": "sleeps", "version": "1",
        "commands": {
            "up": {"run": "time:sleep", "args": [], "service": True},
            "dies": {"run": "sys:exit"},
        },
        "requires": {"cell": False},
    }, name="tiny")
    try:
        runner = Runner(state_file=os.path.join(state, "running.json"),
                        log_dir=os.path.join(state, "logs"))
        manifest = load(os.path.join(app, MANIFEST_NAME))

        ok("nothing is running to begin with", runner.status() == [])

        from IAC.apps.runner import RunError
        try:
            runner.start(manifest, "nope")
            ok("an unknown command is refused", False)
        except RunError as exc:
            ok("an unknown command is refused", "has no command" in str(exc))
            ok("and lists the real ones", "up" in str(exc))

        # A command that exits at once must not be reported as started.
        try:
            runner.start(manifest, "dies")
            ok("a command that exits immediately is not 'started'", False,
               "it reported success for a dead process")
        except RunError as exc:
            ok("a command that exits immediately is not 'started'",
               "exited immediately" in str(exc), str(exc)[:90])
        ok("and it is not left in the state file",
           not any(e["key"] == "tiny:dies" for e in runner.status()))
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(state, ignore_errors=True)


def test_console():
    print("\nconsole")

    class Cp1252:
        encoding = "cp1252"

        def write(self, text):
            text.encode("cp1252")   # faithful: a real cp1252 console raises

        def flush(self):
            pass

    try:
        say("plain", stream=Cp1252())
        say("box ─ emoji \U0001f600 cjk 京", stream=Cp1252())
        ok("printing cannot fail on an unencodable character", True,
           "app titles and log tails are not text the launcher wrote")
    except Exception as exc:
        ok("printing cannot fail on an unencodable character", False, str(exc))
    ok("safe() returns something encodable",
       isinstance(safe("café ─ \U0001f600", stream=Cp1252()), str))


def main():
    print("IAC Launcher tests")
    test_manifest()
    test_registry()
    test_bundles()
    test_bundle_safety()
    test_runner()
    test_console()

    print("\n%d passed, %d failed, %d skipped"
          % (len(_passed), len(_failed), len(_skipped)))
    for name, detail in _failed:
        print("  FAILED %s %s" % (name, detail))
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
