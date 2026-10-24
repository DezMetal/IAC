# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
"""
`apps.*` -- the Cell App launcher.

So an agent can do what a person does at the CLI: see what is installed, check
whether it can run here, start it, stop it, and say what is up. Registered through `IAC/extensions.py`.

This is also the seam that makes the convergence in DESIGN.md §1 cheap.
Aether's dashboard already lists and launches things; if it ever becomes Bay's
front end, it does so by calling these operations rather than by either side
being rewritten. Nothing here owns a UI, deliberately.
"""

import os

from .apps.bundle import export_app, import_bundle, inspect_bundle
from .apps.registry import Registry, check
from .apps.scaffold import SCAFFOLD_KINDS, scaffold
from .apps.runner import RunError, Runner

__all__ = ["build_domain", "register", "get_registry", "get_runner"]


_registry = None
_runner = None


def get_registry(config=None, refresh=False):
    global _registry
    if _registry is None or refresh:
        _registry = Registry((config or {}).get("roots"))
    return _registry


def get_runner(config=None):
    global _runner
    if _runner is None:
        _runner = Runner()
    return _runner


def build_domain(config=None):
    """The `apps` domain, ready to register."""
    from IAC.extensions import Domain

    apps = Domain(
        "apps",
        description="Find, inspect, run and stop Cell Apps",
        author="D-Net Lab",
        version="0.1",
        homepage="https://lab.dnet.live",
        requires=["filesystem", "subprocess", "network"],
    )

    @apps.op("list",
            "Every Cell App on this machine, with what it needs",
            {"deep": {"type": "boolean",
                      "description": "Also check whether each one can run "
                                     "here. Costs a probe per app."}}, readonly=True)
    def _list(args, context=None):
        args = args or {}
        registry = get_registry(config, refresh=True)
        data = registry.to_dict(deep=bool(args.get("deep")))
        running = {r["app"] for r in get_runner(config).running()}
        for entry in data["apps"]:
            entry["running"] = entry["name"] in running
        message = "%d app%s" % (data["count"],
                                "" if data["count"] == 1 else "s")
        if data["errors"]:
            message += ", %d manifest problem(s)" % len(data["errors"])
        if not data["count"]:
            message = ("no Cell Apps found in %s -- an app is a directory "
                       "with a cellapp.json" % ", ".join(data["roots"]))
        return {"status": "ok", "data": data, "message": message}

    @apps.op("info",
            "One app's manifest, in full",
            {"app": {"type": "string", "description": "The app's name."}},
            required=["app"], readonly=True)
    def _info(args, context=None):
        name = str((args or {}).get("app") or "").strip()
        manifest = get_registry(config).get(name)
        if manifest is None:
            return _unknown(name)
        return {"status": "ok", "data": manifest.to_dict(),
                "message": "%s v%s -- %s" % (manifest.title, manifest.version,
                                             manifest.summary or "no summary")}

    @apps.op("check",
            "Whether an app can run on this host, and what is missing",
            {"app": {"type": "string"}},
            required=["app"], readonly=True)
    def _check(args, context=None):
        name = str((args or {}).get("app") or "").strip()
        manifest = get_registry(config).get(name)
        if manifest is None:
            return _unknown(name)
        ready, findings = check(manifest)
        missing = [f for f in findings if f["ok"] is False]
        return {"status": "ok",
                "data": {"app": name, "ready": ready, "findings": findings,
                         "manifest_problems": manifest.problems()},
                "message": ("ready" if ready else
                            "not ready: " + "; ".join(
                                f["detail"] or f["need"] for f in missing))}

    @apps.op("run",
            "Start one of an app's commands",
            {"app": {"type": "string"},
             "command": {"type": "string",
                         "description": "Which command. Defaults to the only "
                                        "one, or to a service if there is "
                                        "exactly one."},
             "args": {"type": "array",
                      "description": "Extra arguments for the command."},
             "force": {"type": "boolean",
                       "description": "Start even if this host is missing "
                                      "something the app asked for."}},
            required=["app"])
    def _run(args, context=None):
        args = args or {}
        name = str(args.get("app") or "").strip()
        manifest = get_registry(config).get(name)
        if manifest is None:
            return _unknown(name)

        command = args.get("command") or _default_command(manifest)
        if command is None:
            return {"status": "error",
                    "error": "which command? %s has: %s"
                             % (name, ", ".join(sorted(manifest.commands)))}

        if not args.get("force"):
            ready, findings = check(manifest)
            if not ready:
                missing = [f["detail"] or f["need"]
                           for f in findings if f["ok"] is False]
                return {"status": "error",
                        "error": "%s cannot run here: %s"
                                 % (name, "; ".join(missing)),
                        "data": {"findings": findings},
                        "hint": "pass force to start it anyway"}

        try:
            record = get_runner(config).start(manifest, command,
                                              args.get("args"))
        except RunError as exc:
            return {"status": "error", "error": str(exc)}
        return {"status": "ok", "data": record,
                "message": "%s:%s started (pid %s)%s"
                           % (name, command, record["pid"],
                              " on port %s" % record["port"]
                              if record.get("port") else "")}

    @apps.op("stop",
            "Stop an app, or one of its commands",
            {"app": {"type": "string"},
             "command": {"type": "string",
                         "description": "Omit to stop everything this app is "
                                        "running."}},
            required=["app"])
    def _stop(args, context=None):
        args = args or {}
        name = str(args.get("app") or "").strip()
        stopped = get_runner(config).stop(name, args.get("command"))
        if not stopped:
            return {"status": "ok", "data": {"stopped": []},
                    "message": "%s was not running" % name}
        return {"status": "ok", "data": {"stopped": stopped},
                "message": "; ".join("%s %s" % (s["key"], s["was"])
                                     for s in stopped)}

    @apps.op("status",
            "What is running, and what exited",
            {}, readonly=True)
    def _status(args, context=None):
        entries = get_runner(config).status()
        alive = [e for e in entries if e["alive"]]
        return {"status": "ok",
                "data": {"running": alive,
                         "exited": [e for e in entries if not e["alive"]]},
                "message": ("%d running" % len(alive)) if alive
                           else "nothing is running"}

    @apps.op("export",
            "Write an app to a portable .cellapp bundle",
            {"app": {"type": "string"},
             "path": {"type": "string",
                      "description": "Where to write it."},
             "include_config": {"type": "boolean",
                                "description": "Carry the app's config. Off "
                                               "by default: a config names "
                                               "hosts and keys that are wrong "
                                               "or secret elsewhere."}},
            required=["app"])
    def _export(args, context=None):
        args = args or {}
        name = str(args.get("app") or "").strip()
        manifest = get_registry(config).get(name)
        if manifest is None:
            return _unknown(name)
        try:
            result = export_app(manifest, args.get("path"),
                                include_config=bool(
                                    args.get("include_config")))
        except (ValueError, OSError) as exc:
            return {"status": "error", "error": str(exc)}
        return {"status": "ok", "data": result,
                "message": "wrote %s (%d KB)"
                           % (result["path"], result["bytes"] // 1024)}

    @apps.op("inspect",
            "What is inside a .cellapp bundle, without unpacking it",
            {"path": {"type": "string"}},
            required=["path"], readonly=True)
    def _inspect(args, context=None):
        path = str((args or {}).get("path") or "")
        try:
            data = inspect_bundle(path)
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}
        return {"status": "ok", "data": data,
                "message": "%s v%s, %d files"
                           % (data["manifest"]["name"],
                              data["manifest"]["version"], data["files"])}

    @apps.op("import",
            "Unpack a .cellapp bundle so this machine can run it",
            {"path": {"type": "string"},
             "into": {"type": "string",
                      "description": "Where to put it. Defaults to "
                                     "~/.cell/apps."},
             "overwrite": {"type": "boolean"}},
            required=["path"])
    def _import(args, context=None):
        args = args or {}
        destination = args.get("into") or os.path.join(
            os.path.expanduser("~"), ".cell", "apps")
        try:
            result = import_bundle(args.get("path"), destination,
                                   overwrite=bool(args.get("overwrite")))
        except (ValueError, OSError) as exc:
            return {"status": "error", "error": str(exc)}
        get_registry(config, refresh=True)
        return {"status": "ok", "data": result,
                "message": "imported %s to %s. %s"
                           % (result["app"], result["path"], result["next"])}

    @apps.op("new",
             "Create a new Cell App: a manifest, a service, an IAC domain, "
             "and optionally a DSS-styled GUI",
             {"name": {"type": "string",
                       "description": "Lowercase id, e.g. 'note-taker'."},
              "kind": {"type": "string",
                       "description": "service | ui | tool. 'ui' adds a web "
                                      "GUI styled by DSS."},
              "title": {"type": "string"},
              "summary": {"type": "string",
                          "description": "One sentence on what it DOES. Not "
                                         "what it will do."},
              "needs_cell": {"type": "boolean",
                             "description": "Does it need an intelligence "
                                            "core?"},
              "into": {"type": "string",
                       "description": "Where to create it. Defaults to the "
                                      "first configured root."},
              "port": {"type": "integer"}},
             required=["name"])
    def _new(args, context=None):
        args = args or {}
        registry = get_registry(config)
        destination = args.get("into") or (registry.roots[0]
                                           if registry.roots else os.getcwd())
        try:
            result = scaffold(
                args["name"], destination,
                kind=args.get("kind") or "service",
                title=args.get("title"), summary=args.get("summary"),
                needs_cell=bool(args.get("needs_cell")),
                port=int(args.get("port") or 8800))
        except (ValueError, OSError) as exc:
            return {"status": "error", "error": str(exc),
                    "kinds": SCAFFOLD_KINDS}
        get_registry(config, refresh=True)
        return {"status": "ok", "data": result,
                "message": "created %s at %s. %s"
                           % (result["app"], result["path"], result["next"])}

    @apps.op("domains",
            "Register the IAC domains of every installed app, so an agent can "
            "use them directly",
            {"app": {"type": "string",
                     "description": "Just this one. Omit for all."}})
    def _domains(args, context=None):
        args = args or {}
        from IAC.registry import OperationRegistry

        registry = get_registry(config)
        wanted = [registry.get(args["app"])] if args.get("app") else list(registry)
        landed, failed = {}, {}
        for manifest in wanted:
            if manifest is None or not manifest.register:
                continue
            module, _, func = str(manifest.register).partition(":")
            _importable(manifest, module)
            try:
                imported = __import__(module, fromlist=[func])
                count = getattr(imported, func)(
                    registry=OperationRegistry(),
                    config=_app_config(manifest))
                landed[manifest.name] = {"domain": manifest.domain,
                                         "operations": count}
            except Exception as exc:
                # An app whose domain will not load is reported by name: the
                # alternative is an agent silently missing capability.
                failed[manifest.name] = str(exc)[:200]
        return {"status": "ok", "data": {"registered": landed,
                                         "failed": failed},
                "message": "registered %d domain(s)%s"
                           % (len(landed),
                              ", %d failed" % len(failed) if failed else "")}

    return apps


def _importable(manifest, module):
    """Make an app's `register` module importable in THIS process.

    The runner gives a subprocess the lab root and the app's parent on
    PYTHONPATH; `apps.domains` imports in-process and did neither, so every
    app outside an already-importable location failed with "No module named"
    -- the Transposer and the Dash both. The package is found where it
    actually is: the app directory IS the package (Transposer), or the
    package sits inside it (`labdash` inside DNetLab). Only the directory
    that holds the package is added, so an app's loose top-level files never
    shadow anything.
    """
    import sys

    root = manifest.root
    if not root:
        return
    top = module.split(".")[0]
    parent = os.path.dirname(root)
    if os.path.basename(root) == top:
        holder = parent
    elif os.path.isdir(os.path.join(root, top)):
        holder = root
    else:
        return
    if holder not in sys.path:
        sys.path.insert(0, holder)


def _app_config(manifest):
    import json
    path = manifest.config_path
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return {}
    return {}


def _default_command(manifest):
    """The command to run when the caller did not say.

    Only guesses when there is one obvious answer: a single command, or a
    single service. Guessing between several would start the wrong thing.
    """
    if len(manifest.commands) == 1:
        return next(iter(manifest.commands))
    services = manifest.services()
    if len(services) == 1:
        return next(iter(services))
    return None


def _unknown(name):
    registry = get_registry()
    return {"status": "error",
            "error": "no Cell App called %r. Installed: %s"
                     % (name, ", ".join(registry.names()) or "none")}


def register_apps_operations(registry=None, config=None,
                             allow=None, replace=False):
    """Put `apps.*` into an IAC registry. Returns how many operations landed.

    `allow` narrows it: `list`, `info`, `check`, `status` and `inspect` are
    read-only; `run`, `stop`, `import` and `export` start processes and write
    files, and have no business on a shared surface.
    """
    return build_domain(config).register(registry=registry, replace=replace,
                                         allow=allow)


#: Kept so `register` reads naturally from outside.
register = register_apps_operations
