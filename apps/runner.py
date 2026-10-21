# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
"""
Running Cell Apps -- start, stop, and tell the truth about what is up.

Each app command runs as its own process. It costs a little startup and buys
three things worth more: an app that crashes does not take the launcher with it, two
apps wanting different versions of something is their problem rather than an
import-order puzzle, and **stopping actually stops** -- a thread holding a
model cannot be reliably killed, a process can.

THE RULE THAT MATTERS

**A process that exited is reported as exited.** A launcher that lists three
running apps, none of which are, is worse than no launcher: every decision made
from that list is wrong. So `status()` polls, and a dead process carries its
exit code and its last output, because "it stopped" is useless without "and
here is what it said".

State lives in one JSON file so a `apps.list` in a new shell sees what an
earlier one started. A recorded process whose pid is gone is reported as such
and cleaned up -- the file is a cache of reality, never the authority.
"""

import json
import os
import signal
import subprocess
import sys
import time

__all__ = ["Runner", "RunError"]


_STATE_DIR = os.path.join(os.path.expanduser("~"), ".cell", "apps")
_STATE_FILE = os.path.join(_STATE_DIR, "running.json")
_LOG_DIR = os.path.join(_STATE_DIR, "logs")


class RunError(Exception):
    """An app could not be started or stopped. Carries a sentence."""


def _alive(pid):
    """Whether this pid is still running. Best effort, never raises."""
    if not pid:
        return False
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid],
                                 capture_output=True, text=True, timeout=20)
            return str(pid) in (out.stdout or "")
        os.kill(pid, 0)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


class Runner:
    """Starts and stops app commands, and remembers which are up."""

    def __init__(self, state_file=None, log_dir=None):
        self.state_file = state_file or _STATE_FILE
        self.log_dir = log_dir or _LOG_DIR
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        os.makedirs(self.log_dir, exist_ok=True)

    # ── state ─────────────────────────────────────────────────────────────

    def _read(self):
        try:
            with open(self.state_file, encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _write(self, data):
        tmp = self.state_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        os.replace(tmp, self.state_file)

    def _key(self, app, command):
        return "%s:%s" % (app, command)

    # ── running ───────────────────────────────────────────────────────────

    def start(self, manifest, command, extra_args=None, python=None):
        """Start one of an app's commands. Returns its record.

        Refuses to start a second copy of the same command: two instances of a
        service fight over a port and two instances of a model-loading app
        fight over memory, and neither failure explains itself.
        """
        spec = manifest.commands.get(command)
        if spec is None:
            raise RunError("app %r has no command %r. It has: %s"
                           % (manifest.name, command,
                              ", ".join(sorted(manifest.commands)) or "none"))

        key = self._key(manifest.name, command)
        state = self._read()
        existing = state.get(key)
        if existing and _alive(existing.get("pid")):
            raise RunError("%s is already running (pid %s). Stop it first."
                           % (key, existing["pid"]))

        target = str(spec.get("run") or "")
        module, _, callable_name = target.partition(":")
        if not module or not callable_name:
            raise RunError("command %r has a bad 'run' (%r); it must be "
                           "\"module:callable\"" % (command, target))

        argv = [python or sys.executable, "-c",
                "import sys; from %s import %s as _m; sys.exit(_m() or 0)"
                % (module, callable_name)]
        argv += [str(a) for a in (spec.get("args") or [])]
        argv += [str(a) for a in (extra_args or [])]

        # The lab root, so `from Transposer...` and `from Cell...` resolve
        # without the app needing to be installed.
        env = dict(os.environ)
        # <lab>/IAC/apps/runner.py -> the lab root is three levels up, so
        # an app can `from Transposer...` without being installed.
        lab_root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        env["PYTHONPATH"] = os.pathsep.join(
            [p for p in (lab_root, env.get("PYTHONPATH")) if p])

        log_path = os.path.join(self.log_dir, "%s-%s.log"
                                % (manifest.name, command))
        try:
            handle = open(log_path, "ab")
        except OSError as exc:
            raise RunError("could not open a log at %s: %s" % (log_path, exc))

        try:
            process = subprocess.Popen(
                argv, cwd=manifest.root, env=env,
                stdout=handle, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP
                               if os.name == "nt" else 0))
        except Exception as exc:
            handle.close()
            raise RunError("could not start %s: %s" % (key, exc))
        finally:
            try:
                handle.close()
            except Exception:
                pass

        record = {"app": manifest.name, "command": command,
                  "pid": process.pid, "started_at": time.time(),
                  "service": bool(spec.get("service")),
                  "port": spec.get("port"), "log": log_path,
                  "argv": argv[2:], "root": manifest.root}
        state[key] = record
        self._write(state)

        # A command that dies immediately is the common case when something is
        # missing, and reporting "started" for it would be a lie. Give it a
        # moment and check.
        time.sleep(0.6)
        if process.poll() is not None:
            state.pop(key, None)
            self._write(state)
            raise RunError("%s exited immediately (code %s). Last output:\n%s"
                           % (key, process.returncode,
                              self.tail(log_path, 12) or "(nothing)"))
        return record

    def stop(self, app, command=None, timeout=10):
        """Stop one command, or every command of an app. Returns what stopped."""
        state = self._read()
        keys = [k for k in state
                if k == self._key(app, command)] if command else \
               [k for k in state if k.startswith(app + ":")]
        if not keys:
            return []

        stopped = []
        for key in keys:
            record = state.get(key) or {}
            pid = record.get("pid")
            if not _alive(pid):
                stopped.append({"key": key, "was": "already gone"})
                state.pop(key, None)
                continue
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                   capture_output=True, timeout=30)
                else:
                    os.kill(pid, signal.SIGTERM)
                    deadline = time.time() + timeout
                    while time.time() < deadline and _alive(pid):
                        time.sleep(0.2)
                    if _alive(pid):
                        os.kill(pid, signal.SIGKILL)
                stopped.append({"key": key, "was": "stopped", "pid": pid})
            except Exception as exc:
                stopped.append({"key": key, "was": "could not stop: %s" % exc})
                continue
            state.pop(key, None)
        self._write(state)
        return stopped

    # ── reporting ─────────────────────────────────────────────────────────

    @staticmethod
    def tail(path, lines=20):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                return "".join(handle.readlines()[-lines:]).strip()
        except OSError:
            return ""

    def status(self):
        """Every recorded process, with whether it is actually alive.

        Polls rather than trusting the file. A record whose process is gone is
        reported as exited -- with its log tail -- and then forgotten.
        """
        state = self._read()
        out = []
        changed = False
        for key, record in sorted(state.items()):
            alive = _alive(record.get("pid"))
            entry = dict(record)
            entry["key"] = key
            entry["alive"] = alive
            entry["uptime_seconds"] = (
                int(time.time() - record.get("started_at", time.time()))
                if alive else None)
            if not alive:
                entry["last_output"] = self.tail(record.get("log", ""), 8)
                state.pop(key, None)
                changed = True
            out.append(entry)
        if changed:
            self._write(state)
        return out

    def running(self):
        return [e for e in self.status() if e["alive"]]
