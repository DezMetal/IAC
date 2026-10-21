# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
"""
Making a new Cell App -- the programmatic half of authoring one.

An agent, a person at a terminal, or Aether can call this and get a working
app: a manifest, a service that answers, an optional DSS-styled GUI, and an
IAC domain. It runs before anything is written, so the first thing the author
sees is something that already starts.

WHY A SCAFFOLD RATHER THAN A TEMPLATE REPOSITORY

Because the thing being taught is the *shape*, and a shape is easier to learn
from a file that runs than from documentation about a file that would run. The
generated app is deliberately small enough to read in one sitting and already
does the four things a Cell App does: declares itself, serves something,
registers a domain, and says what it needs.

WHAT IT WILL NOT DO

* **Overwrite.** An existing directory is refused. Generating over someone's
  work to save them a flag is not a trade worth making.
* **Invent requirements.** The manifest it writes asks for a Cell only if the
  app was asked for one. A scaffold that declared dependencies nobody wanted
  would teach the wrong habit on the first file.
* **Pretend to be finished.** Every generated file says what to replace, and
  the summary is the author's words or an honest placeholder -- never a
  plausible-sounding description of an app that does nothing yet.
"""

import json
import os
import re

__all__ = ["scaffold", "SCAFFOLD_KINDS"]

#: What a new app can start as. Deliberately few: these are starting points,
#: not a framework of options to choose between.
SCAFFOLD_KINDS = {
    "service": "an HTTP service with a JSON API and an IAC domain",
    "ui": "a service plus a DSS-styled web GUI",
    "tool": "a command-line tool with an IAC domain, no server",
}

_NAME_OK = re.compile(r"^[a-z][a-z0-9_-]{1,40}$")


def _module_name(name):
    """A Python package name from an app name: `my-app` -> `my_app`."""
    return name.replace("-", "_")


def _manifest(name, title, summary, kind, needs_cell, port):
    data = {
        "cellapp": "1.0",
        "name": name,
        "title": title,
        "summary": summary,
        "version": "0.1",
        "author": "",
        "commands": {},
        "requires": {
            "cell": bool(needs_cell),
            "python": ">=3.9",
            "packages": [],
            "capabilities": [],
        },
        "config": "config.json",
    }
    if kind in ("service", "ui"):
        data["commands"]["serve"] = {
            "run": "%s.service:main" % _module_name(name),
            "service": True,
            "port": port,
        }
    if kind == "tool":
        data["commands"]["run"] = {"run": "%s.app:main" % _module_name(name)}
    data["commands"]["check"] = {
        "run": "%s.app:main" % _module_name(name), "args": ["check"]}
    data["domain"] = name.replace("-", "_")
    data["register"] = "%s.iac_domain:register" % _module_name(name)
    if kind == "ui":
        data["ui"] = {"route": "/", "command": "serve", "title": title}
    return data


_SERVICE = '''"""%(title)s -- the HTTP surface.

Standard library only, like every other Cell App service. Replace `_answer`
with whatever this app actually does.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

__all__ = ["serve", "main"]

_HERE = os.path.dirname(os.path.abspath(__file__))


def load_config():
    path = os.path.join(_HERE, "config.json")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    return {}


def _answer(payload, config):
    """What this app does. REPLACE THIS."""
    return {"echo": payload, "note": "replace _answer in service.py"}


class _Handler(BaseHTTPRequestHandler):
    server_version = "%(title)s"
    sys_version = ""

    def log_message(self, fmt, *args):
        if self.server.config.get("access_log"):
            super().log_message(fmt, *args)

    def _json(self, code, payload):
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
%(ui_get)s
        if path in ("/", "/status"):
            return self._json(200, {"app": "%(name)s", "status": "ok"})
        return self._json(404, {"error": "no route %%s" %% path})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
        except ValueError as exc:
            return self._json(400, {"error": "body is not JSON: %%s" %% exc})
        return self._json(200, _answer(payload, self.server.config))


def serve(config=None, block=True):
    config = config or load_config()
    host = config.get("host", "127.0.0.1")
    port = int(config.get("port", %(port)s))

    # Refuse a port already in use: on Windows a second bind is allowed and
    # the ORIGINAL process keeps answering, so a restart silently does nothing.
    import socket
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(1.0)
    try:
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise OSError("something is already listening on port %%s" %% port)
    finally:
        probe.close()

    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.daemon_threads = True
    httpd.config = config
    if not block:
        import threading
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd
    print("%(title)s on http://%%s:%%s" %% (host, port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return httpd


def main(argv=None):
    serve()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
'''

_UI_GET = '''
        # The optional GUI, styled by DSS through the shared shell.
        try:
            from .web import handle as handle_ui
            handled = handle_ui(self.path)
        except Exception:
            handled = None
        if handled:
            status, content_type, body = handled
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
'''

_WEB = '''"""%(title)s -- the web GUI.

The shell, DSS, the header and the footer come from `IAC.apps.ui`. What is
here is a fragment of markup and the script that talks to this app's own JSON
API.

Every class used must exist in `DSS/dss.css`. Check it rather than trusting
memory: `dss-button`, `dss-card`, `dss-field`, `dss-input`, `dss-select`,
`dss-textarea`, `dss-tag`, `dss-status`, `dss-dot`, `dss-eyebrow`,
`dss-deck`, `dss-grid`, `dss-container`, `dss-rule-iri`, `dss-bar`.
There is no `dss-btn`, `dss-callout` or `dss-muted`.
"""

from IAC.apps.ui import UI_ROUTE_PREFIX, serve_ui_request, shell

__all__ = ["page", "handle"]

_BODY = """
<section class="dss-card">
  <p class="dss-eyebrow">Replace me</p>
  <h2 style="margin:.25rem 0 .75rem">%(title)s</h2>
  <p style="color:var(--dss-ink-soft); max-width:52ch">
    This is the generated GUI. Edit <code>web.py</code>: put your markup in
    <code>_BODY</code> and your fetch calls in <code>_SCRIPT</code>.
  </p>
  <button id="go" class="dss-button">Call the API</button>
  <div id="out" class="dss-card dss-card--flat" hidden
       style="margin-top:1.25rem"></div>
</section>
"""

_SCRIPT = """
document.getElementById('go').addEventListener('click', async () => {
  const out = document.getElementById('out');
  out.hidden = false;
  out.textContent = 'calling\\\\u2026';
  try {
    const response = await fetch('', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({hello: 'world'})
    });
    out.textContent = JSON.stringify(await response.json(), null, 2);
  } catch (error) {
    out.textContent = error.message;
  }
});
"""


def page():
    return shell(
        "%(title)s",
        body=_BODY,
        subtitle="%(summary)s",
        links=(("Status", "status"),),
        scripts=(UI_ROUTE_PREFIX + "/app.js",),
    )


def handle(path):
    route = (path or "").split("?", 1)[0].rstrip("/")
    shared = serve_ui_request(path)
    if shared:
        return shared
    if route in ("", "/ui"):
        return 200, "text/html; charset=utf-8", page().encode("utf-8")
    if route == UI_ROUTE_PREFIX + "/app.js":
        return 200, "application/javascript; charset=utf-8", \\
            _SCRIPT.encode("utf-8")
    return None
'''

_DOMAIN = '''"""`%(domain)s.*` -- %(title)s as an IAC domain.

Registered through `IAC/extensions.py`, so IAC is not modified. This is what
lets an agent use this app without being taught anything about it.
"""

__all__ = ["build_domain", "register"]


def build_domain(config=None):
    from IAC.extensions import Domain

    domain = Domain(
        "%(domain)s",
        description="%(summary)s",
        author="",
        version="0.1",
        requires=["network"],
    )

    @domain.op("status", "Whether this app is ready, and why not", {})
    def _status(args, context=None):
        return {"status": "ok", "data": {"ready": True},
                "message": "%(name)s is ready"}

    @domain.op("do", "The thing this app does. REPLACE THIS.",
               {"input": {"type": "string", "description": "What to act on."}},
               required=["input"])
    def _do(args, context=None):
        value = str((args or {}).get("input") or "").strip()
        if not value:
            return {"status": "error", "error": "'input' is required"}
        return {"status": "ok", "data": {"echo": value},
                "message": "replace _do in iac_domain.py"}

    return domain


def register(registry=None, config=None, allow=None, replace=False):
    return build_domain(config).register(registry=registry, replace=replace,
                                         allow=allow)
'''

_APP = '''"""%(title)s -- the command line.

`check` reports readiness; add your own subcommands beside it. Keep this and
the IAC domain thin: both should call the same functions, so a person and an
agent get the same behaviour.
"""

import argparse
import sys

__all__ = ["main"]


def cmd_check(args):
    print("%(title)s")
    print("  ready: yes")
    print("  replace cmd_check in app.py with a real readiness report")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="%(name)s",
                                     description="%(summary)s")
    sub = parser.add_subparsers(dest="action")
    sub.add_parser("check")
    args = parser.parse_args(argv)
    if (args.action or "check") == "check":
        return cmd_check(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
'''

_README = '''# %(title)s

%(summary)s

A **Cell App**: it declares itself in `cellapp.json`, speaks IAC, and gets its
intelligence from a Cell if it needs one.

```bash
python3 -m IAC.apps check %(name)s
python3 -m IAC.apps run %(name)s %(first_command)s
python3 -m IAC.apps status
```

## What to replace

| File | |
|---|---|
| `cellapp.json` | What this app is and needs. The summary should say what it does, not what it will do. |
%(files_table)s

## Its IAC domain

`%(domain)s.status` and `%(domain)s.do`. Both are placeholders. Keep the CLI
and the domain calling the same functions so a person and an agent get the
same behaviour.
'''


def scaffold(name, directory, kind="service", title=None, summary=None,
             needs_cell=False, port=8800):
    """Create a new Cell App. Returns what was written.

    Raises `ValueError` with a usable sentence rather than writing a partial
    app: a scaffold that half-succeeds leaves someone debugging generated
    code, which is the worst code to debug.
    """
    name = str(name or "").strip().lower()
    if not _NAME_OK.match(name):
        raise ValueError(
            "app name %r must be lowercase letters, digits, hyphen or "
            "underscore, starting with a letter -- e.g. 'note-taker'" % name)
    if kind not in SCAFFOLD_KINDS:
        raise ValueError("kind must be one of: %s"
                         % ", ".join("%s (%s)" % (k, v)
                                     for k, v in SCAFFOLD_KINDS.items()))

    # The DIRECTORY is the module name, not the app name. An app may
    # sensibly be called "note-taker", and Python cannot import a hyphen --
    # the first version created note-taker/ with a manifest pointing at
    # note_taker.service, which could never import. The launcher finds apps
    # by their manifest, so the directory name costs nothing.
    root = os.path.join(os.path.abspath(directory), _module_name(name))
    if os.path.exists(root):
        raise ValueError("%s already exists. Generating over existing work to "
                         "save a flag is not a trade worth making." % root)

    title = title or name.replace("-", " ").replace("_", " ").title()
    summary = summary or ("REPLACE: say what %s does, in one sentence."
                          % title)
    module = _module_name(name)

    fields = {"name": name, "title": title, "summary": summary,
              "domain": name.replace("-", "_"), "port": port,
              "ui_get": _UI_GET if kind == "ui" else "",
              "first_command": "serve" if kind in ("service", "ui") else "run"}

    os.makedirs(root)
    written = []

    def write(filename, text):
        path = os.path.join(root, filename)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        written.append(filename)

    write("cellapp.json",
          json.dumps(_manifest(name, title, summary, kind, needs_cell, port),
                     indent=2) + "\n")
    write("__init__.py", '"""%s"""\n' % summary)
    write("app.py", _APP % fields)
    write("iac_domain.py", _DOMAIN % fields)
    if kind in ("service", "ui"):
        write("service.py", _SERVICE % fields)
    if kind == "ui":
        write("web.py", _WEB % fields)

    config = {"host": "127.0.0.1", "port": port,
              "_note": "This file is NOT carried in a .cellapp bundle -- it "
                       "names hosts and may hold keys. Each machine makes "
                       "its own."}
    if needs_cell:
        config["provider_host"] = "http://127.0.0.1:8757/v1"
        config["provider_model"] = "REPLACE: the core to ask for"
    write("config.json", json.dumps(config, indent=2) + "\n")

    rows = []
    for filename, what in (("app.py", "The command line."),
                           ("iac_domain.py", "Its IAC operations."),
                           ("service.py", "The HTTP surface and `_answer`."),
                           ("web.py", "The GUI markup and script.")):
        if filename in written:
            rows.append("| `%s` | %s |" % (filename, what))
    write("README.md", _README % dict(fields, files_table="\n".join(rows)))

    return {"app": name, "path": root, "kind": kind, "files": written,
            "module": module,
            "next": ("python3 -m IAC.apps check %s -- then replace the "
                     "placeholders the README lists." % name)}
