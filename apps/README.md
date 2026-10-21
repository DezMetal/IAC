# IAC Launcher

**Find, inspect, run and stop Cell Apps.**

A **Cell App** is a program that gets its intelligence from a
[Cell](https://lab.dnet.live), speaks IAC, and declares what it is in a
`cellapp.json` rather than being configured into working. The launcher is what
holds them.

It lives inside IAC rather than in a component of its own, and that was a
deliberate decision not to name something: IAC already has an operation
registry, a runner and pipelines, so running a declared program is the same job
at a larger grain. A separate launcher would have been a third surface over one
idea. See [DESIGN.md](DESIGN.md) §1.

Apache-2.0, as part of IAC — so anyone can launch and manage the apps they
build. The apps themselves need not be open.

---

## Use it

```bash
python3 -m IAC.apps                      # what is installed, and what is up
python3 -m IAC.apps check transposer     # can it run here, and what is missing
python3 -m IAC.apps run cell serve
python3 -m IAC.apps run transposer text "Notwithstanding the aforementioned caveats..."
python3 -m IAC.apps status
python3 -m IAC.apps stop cell
```

Carry one to another machine:

```bash
python3 -m IAC.apps export transposer
python3 -m IAC.apps inspect ./transposer-0.1.cellapp
python3 -m IAC.apps import ./transposer-0.1.cellapp
```

Everything above is the `apps.*` IAC domain with argument parsing in front, so
a person and an agent do the same thing through the same code.

## `apps.*`

| Operation | Does |
|---|---|
| `apps.list` | Every Cell App found, `deep` to check each against this host |
| `apps.info` | One app's manifest in full |
| `apps.check` | Whether it can run here, and what is missing, by name |
| `apps.run` | Start a command. Refuses when the host is missing something, `force` to override |
| `apps.stop` | Stop a command, or everything an app is running |
| `apps.status` | What is running, and what exited — with its last output |
| `apps.export` | Write a portable `.cellapp` |
| `apps.inspect` | What is in a bundle, without unpacking it |
| `apps.import` | Unpack a bundle so this machine can run it |
| `apps.domains` | Register installed apps' own IAC domains, so an agent can use them directly |

For a public provider: `allow=["list", "info", "check", "status", "inspect"]`.
The rest start processes and write files.

## A manifest

```jsonc
{
  "cellapp": "1.0",
  "name": "transposer",
  "title": "Transposer",
  "summary": "Speech in, transformed speech out.",
  "version": "0.1",

  "domain": "transpose",
  "register": "Transposer.iac_domain:register",

  "commands": {
    "serve":  {"run": "Transposer.service:main", "service": true, "port": 8771},
    "text":   {"run": "Transposer.app:main", "args": ["text"]},
    "stream": {"run": "Transposer.app:main", "args": ["stream"]}
  },

  "requires": {
    "cell": true,
    "python": ">=3.9",
    "packages": ["openai-whisper", "piper-tts"],
    "capabilities": ["audio-in", "audio-out"]
  },

  "config": "config.json",
  "compute": {"mode": "local"}
}
```

A directory with that file is a Cell App. Nothing in it is launcher-specific —
it describes the app, and the launcher reads it. Unknown keys are carried and
reported, never dropped: the manifest is the author's statement of intent, and
the launcher's job is to say which parts it acted on.

## What it will not do

- **Install anything.** `apps.check` reports a missing package with the exact
  command. A launcher that silently pip-installs is how a machine becomes
  unexplainable.
- **Carry configs or models in a bundle.** A config names hosts, ports and
  keys that are wrong — or secret — elsewhere; a model is hundreds of
  megabytes the receiver may already have. Config files are excluded **by
  name**, not only when the manifest declares one, because the undeclared case
  is the dangerous one.
- **Pretend an app is running.** A process that exited is reported as exited,
  with its exit code and last output. A launcher listing three running apps,
  none of which are, is worse than no launcher.
- **Trust a bundle.** A tar entry may be named `../../anything`, be absolute,
  or be a symlink out of the tree. All three are refused, and the count is
  reported.

## Why subprocesses

An app that crashes does not take the launcher with it; two apps wanting
different versions of something is their problem rather than an import-order
puzzle; and stopping actually stops — a thread holding a model cannot be
reliably killed, a process can.

The exception is `apps.*` itself, which registers in-process: an agent calling
`apps.list` wants an answer, not a subprocess.

## Tests

```bash
python3 IAC/apps/tests/test_apps.py
```

50 checks, on temporary trees — the suite neither needs nor touches the apps
installed on this machine. It covers manifest validation, discovery, duplicate
names, bundle contents, **bundle path-escape refusal**, a command that exits
immediately not being reported as started, and that printing an app's title
cannot crash on a Windows console.

## State

| Piece | |
|---|---|
| `manifest.py` | Reading, validating, describing a `cellapp.json` | ✅ |
| `registry.py` | Discovery and host readiness | ✅ |
| `runner.py` | Run, stop, status, output capture | ✅ |
| `bundle.py` | `.cellapp` export and import | ✅ |
| `console.py` | Printing that cannot fail the thing it describes | ✅ |
| `../apps_ops.py` | `apps.*` | ✅ |
| `__main__.py` | The CLI | ✅ |
| A UI | Not built. `IAC/web/` and Aether's dashboard are the candidates — see DESIGN.md §8 | ❌ |
| Pipelines as apps | Not built. A pipeline is a command with no code of its own | ❌ |
| Remote run | Not built. `Cell.deploy` is the shape for that | ❌ |
