# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
"""
`python3 -m IAC.apps` -- the Cell App launcher, from a terminal.

    python3 -m IAC.apps                      what is installed, and what is running
    python3 -m IAC.apps info transposer
    python3 -m IAC.apps check transposer     can it run here, and what is missing
    python3 -m IAC.apps run transposer serve
    python3 -m IAC.apps stop transposer
    python3 -m IAC.apps status
    python3 -m IAC.apps export transposer
    python3 -m IAC.apps inspect ./transposer-0.1.cellapp
    python3 -m IAC.apps import ./transposer-0.1.cellapp

Everything here is the `apps.*` IAC domain with argument parsing in front, so a
person and an agent are doing the same thing through the same code. There is no
behaviour in the CLI that an agent cannot reach.
"""

import argparse
import os
import sys

from .bundle import export_app, import_bundle, inspect_bundle
from .console import say
from .registry import Registry, check
from .scaffold import SCAFFOLD_KINDS, scaffold
from .runner import RunError, Runner


def _registry(args):
    return Registry(args.root or None)


def _find(registry, name):
    manifest = registry.get(name)
    if manifest is None:
        say("  no Cell App called %r" % name)
        say("  installed: %s" % (", ".join(registry.names()) or "none"))
        return None
    return manifest


def cmd_list(args):
    registry = _registry(args)
    runner = Runner()
    running = {}
    for entry in runner.running():
        running.setdefault(entry["app"], []).append(entry["command"])

    say("Cell Apps")
    say("  roots: %s" % ", ".join(registry.roots))
    say("")
    if not len(registry):
        say("  None found. A Cell App is a directory with a cellapp.json.")
        say("  See IAC/apps/DESIGN.md section 2 for the shape.")
    for manifest in registry:
        ready, findings = check(manifest)
        missing = [f for f in findings if f["ok"] is False]
        mark = "running" if manifest.name in running else (
            "ready" if ready else "needs setup")
        say("  %-14s %-12s %s" % (manifest.name, mark,
                                  manifest.summary[:46]))
        if manifest.name in running:
            say("                 up: %s" % ", ".join(running[manifest.name]))
        elif missing:
            say("                 %s" % (missing[0]["detail"]
                                          or missing[0]["need"]))
    if registry.errors():
        say("")
        say("  Manifest problems:")
        for error in registry.errors():
            say("    %s" % error["path"])
            say("      %s" % error["error"])
    return 0


def cmd_info(args):
    registry = _registry(args)
    manifest = _find(registry, args.app)
    if manifest is None:
        return 1
    data = manifest.to_dict()
    say("%s  v%s" % (data["title"], data["version"]))
    if data["summary"]:
        say("  %s" % data["summary"])
    say("")
    say("  name      : %s" % data["name"])
    say("  root      : %s" % data["root"])
    say("  domain    : %s" % (data["domain"] or "none"))
    say("  commands  : %s" % ", ".join(data["commands"]))
    if data["services"]:
        say("  services  : %s" % ", ".join(data["services"]))
    say("  compute   : %s" % (data["compute"] or "unspecified"))
    say("  needs cell: %s" % data["requires"]["cell"])
    if data["requires"]["packages"]:
        say("  packages  : %s" % ", ".join(data["requires"]["packages"]))
    if data["requires"]["capabilities"]:
        say("  hardware  : %s" % ", ".join(data["requires"]["capabilities"]))
    if data["unknown_keys"]:
        say("  unknown keys (carried, not acted on): %s"
            % ", ".join(data["unknown_keys"]))
    for problem in data["problems"]:
        say("  ! %s" % problem)
    return 0


def cmd_check(args):
    registry = _registry(args)
    manifest = _find(registry, args.app)
    if manifest is None:
        return 1
    ready, findings = check(manifest)
    say("%s on this host" % manifest.title)
    say("")
    for finding in findings:
        mark = {True: "ok ", False: "GAP", None: "  ?"}[finding["ok"]]
        say("  [%s] %s" % (mark, finding["need"]))
        if finding["detail"]:
            say("        %s" % finding["detail"])
    for problem in manifest.problems():
        say("  [GAP] manifest: %s" % problem)
    say("")
    say("  %s" % ("ready to run" if ready else "not ready"))
    return 0 if ready else 1


def cmd_run(args):
    registry = _registry(args)
    manifest = _find(registry, args.app)
    if manifest is None:
        return 1

    command = args.command
    if command is None:
        if len(manifest.commands) == 1:
            command = next(iter(manifest.commands))
        else:
            services = manifest.services()
            if len(services) == 1:
                command = next(iter(services))
    if command is None:
        say("  which command? %s has: %s"
            % (manifest.name, ", ".join(sorted(manifest.commands))))
        return 1

    if not args.force:
        ready, findings = check(manifest)
        if not ready:
            say("  %s cannot run here:" % manifest.name)
            for finding in findings:
                if finding["ok"] is False:
                    say("    %s" % (finding["detail"] or finding["need"]))
            say("  --force to start it anyway")
            return 1

    try:
        record = Runner().start(manifest, command, args.extra)
    except RunError as exc:
        say("  %s" % exc)
        return 1
    say("  %s:%s started (pid %s)%s"
        % (manifest.name, command, record["pid"],
           " on port %s" % record["port"] if record.get("port") else ""))
    say("  log: %s" % record["log"])
    return 0


def cmd_stop(args):
    stopped = Runner().stop(args.app, args.command)
    if not stopped:
        say("  %s was not running" % args.app)
        return 0
    for entry in stopped:
        say("  %s %s" % (entry["key"], entry["was"]))
    return 0


def cmd_status(args):
    entries = Runner().status()
    if not entries:
        say("  nothing is running")
        return 0
    for entry in entries:
        if entry["alive"]:
            say("  %-24s pid %-7s up %ds%s"
                % (entry["key"], entry["pid"], entry["uptime_seconds"],
                   " port %s" % entry["port"] if entry.get("port") else ""))
        else:
            say("  %-24s EXITED" % entry["key"])
            if entry.get("last_output"):
                for line in entry["last_output"].splitlines()[-5:]:
                    say("      %s" % line)
    return 0


def cmd_export(args):
    registry = _registry(args)
    manifest = _find(registry, args.app)
    if manifest is None:
        return 1
    try:
        result = export_app(manifest, args.path,
                            include_config=args.include_config)
    except (ValueError, OSError) as exc:
        say("  %s" % exc)
        return 1
    say("  wrote %s" % result["path"])
    say("  %d KB, %d path(s) left out" % (result["bytes"] // 1024,
                                          result["skipped"]))
    if not result["includes_config"]:
        say("  No config or models inside -- the receiver runs setup.")
    return 0


def cmd_inspect(args):
    try:
        data = inspect_bundle(args.path)
    except ValueError as exc:
        say("  %s" % exc)
        return 1
    manifest = data["manifest"]
    say("%s  v%s" % (manifest["title"], manifest["version"]))
    say("  %s" % (manifest["summary"] or ""))
    say("")
    say("  files    : %d" % data["files"])
    say("  size     : %d KB" % (data["bytes"] // 1024))
    say("  commands : %s" % ", ".join(manifest["commands"]))
    if manifest["requires"]["packages"]:
        say("  packages : %s" % ", ".join(manifest["requires"]["packages"]))
    meta = data.get("meta") or {}
    if meta.get("includes_config"):
        say("  ! this bundle carries a config from the exporting machine")
    for problem in data["problems"]:
        say("  ! %s" % problem)
    return 0


def cmd_import(args):
    destination = args.into or os.path.join(os.path.expanduser("~"),
                                            ".cell", "apps")
    try:
        result = import_bundle(args.path, destination,
                               overwrite=args.overwrite)
    except (ValueError, OSError) as exc:
        say("  %s" % exc)
        return 1
    say("  imported %s v%s" % (result["app"], result["version"]))
    say("  into %s" % result["path"])
    if result["refused_members"]:
        say("  %d bundle path(s) refused as unsafe"
            % result["refused_members"])
    for problem in result["problems"]:
        say("  ! %s" % problem)
    say("")
    say("  %s" % result["next"])
    return 0


def cmd_new(args):
    registry = _registry(args)
    destination = args.into or (registry.roots[0] if registry.roots
                                else os.getcwd())
    try:
        result = scaffold(args.name, destination, kind=args.kind,
                          title=args.title, summary=args.summary,
                          needs_cell=args.needs_cell, port=args.port)
    except (ValueError, OSError) as exc:
        say("  %s" % exc)
        return 1
    say("  created %s (%s)" % (result["app"], result["kind"]))
    say("  at %s" % result["path"])
    for filename in result["files"]:
        say("    %s" % filename)
    say("")
    say("  %s" % result["next"])
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python3 -m IAC.apps",
        description="Find, inspect, run and stop Cell Apps.")
    parser.add_argument("--root", action="append",
                        help="where to look for apps (repeatable)")
    subparsers = parser.add_subparsers(dest="action")

    subparsers.add_parser("list")

    for name in ("info", "check"):
        sub = subparsers.add_parser(name)
        sub.add_argument("app")

    run = subparsers.add_parser("run")
    run.add_argument("app")
    run.add_argument("command", nargs="?")
    run.add_argument("extra", nargs="*")
    run.add_argument("--force", action="store_true")

    stop = subparsers.add_parser("stop")
    stop.add_argument("app")
    stop.add_argument("command", nargs="?")

    subparsers.add_parser("status")

    export = subparsers.add_parser("export")
    export.add_argument("app")
    export.add_argument("path", nargs="?")
    export.add_argument("--include-config", action="store_true",
                        dest="include_config")

    new = subparsers.add_parser("new")
    new.add_argument("name")
    new.add_argument("--kind", default="service",
                     choices=sorted(SCAFFOLD_KINDS))
    new.add_argument("--title")
    new.add_argument("--summary")
    new.add_argument("--needs-cell", action="store_true", dest="needs_cell")
    new.add_argument("--into")
    new.add_argument("--port", type=int, default=8800)

    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("path")

    importer = subparsers.add_parser("import")
    importer.add_argument("path")
    importer.add_argument("--into")
    importer.add_argument("--overwrite", action="store_true")

    args = parser.parse_args(argv)
    action = args.action or "list"

    handlers = {"list": cmd_list, "info": cmd_info, "check": cmd_check,
                "run": cmd_run, "stop": cmd_stop, "status": cmd_status,
                "export": cmd_export, "inspect": cmd_inspect,
                "import": cmd_import, "new": cmd_new}
    return handlers[action](args)


if __name__ == "__main__":
    sys.exit(main())
