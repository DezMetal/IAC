---
name: cell-app
description: "Build a great, reliable Cell App to D-Net Lab standards: a program that declares itself in a cellapp.json (discoverable by the lab, the Dash and every IAC host), speaks IAC, gets its intelligence from a Cell or a spec'd agent, and optionally has a DSS web GUI. Use for make an app, build a tool, add a GUI, package this, make it discoverable, run it without Aether, deploy it to another machine."
triggers: cell app, cellapp, make an app, build an app, new app, launcher, apps.new, scaffold, package app, ship app, app gui, deploy app
---

A **Cell App** is a directory with a `cellapp.json`. That is the whole
contract. It declares what it is, what it needs, and what can be run; the
IAC Launcher does the rest.

## Before anything: is there already one?

**No second mechanism.** A Cell App is how the lab finds, launches and
connects programs, so it is never a reason to build a launcher, a registry,
a settings store or a view framework of its own. Before writing a file, ask
who already answers each question the app has, and use theirs:

| The app needs | Its one home |
|---|---|
| to be found, listed, launched, stopped | this manifest; `apps.list / run / status` |
| its operations in another host | `domain` + `register` here (never a config list of import paths) |
| a GUI shell, styles | `IAC.apps.ui.shell` and DSS (below) |
| a mind | a Cell (`requires.cell`), or an agent spec `personas/<key>/agent.json` run by `Aether/agent_host.py` |
| model tuning | config.json's model profiles and `llm.tasks` -- never in the app |
| identity, memory | Ghost, by subject key -- never a private memory file |
| a view of data another program owns | that program's component, mounted (the agents view: `agent_host.blueprint()` + `agents_view.js`) |

Grep for the noun, read the folder's `.dnetlab` card (`provides`), and read
the module docstrings. Measured 2026-11-15: four second mechanisms in one
day, each one a copy that would have drifted silently.

## Make one

**Never write the files by hand first.** Scaffold, then replace:

```
apps.new {"name": "note-taker", "kind": "ui", "needs_cell": true,
          "summary": "Keeps short notes and finds them again."}
```

`kind` is `service` (HTTP + JSON API), `ui` (that plus a DSS web GUI), or
`tool` (command line only). It writes a manifest, an IAC domain, a CLI, a
service, a GUI, and a config — all running before you change anything.

`summary` must say what it **does**, not what it will do. It is what a person
sees in `apps.list`, and "an app for notes, coming soon" is noise there
forever.

## Then

```
apps.check note-taker       what this host is missing, by name, with the fix
apps.run   note-taker serve
apps.status                 what is up, and what exited with its last output
apps.stop  note-taker
```

Carry it elsewhere:

```
apps.export note-taker          writes a .cellapp bundle
apps.import {"path": "..."}     unpacks it on another machine
```

A bundle carries code and the manifest. It does **not** carry configs or
models: a config names hosts and keys that are wrong or secret elsewhere, and
a model is hundreds of megabytes the receiver may already have.

## What you replace

| File | Replace |
|---|---|
| `cellapp.json` | the summary, and `requires` — ask only for what it uses |
| `iac_domain.py` | `_do` — its real operations |
| `app.py` | `cmd_check` — a real readiness report |
| `service.py` | `_answer` — what it actually does |
| `web.py` | `_BODY` and `_SCRIPT` — markup and fetch calls |

Keep the CLI and the IAC domain calling **the same functions**. A person and
an agent should get identical behaviour; two code paths drift and only one
gets tested.

## Rules that are not style

**Declare only what it uses.** `requires.packages` and `requires.capabilities`
are checked against the host before it runs. Asking for a microphone it never
opens makes it unrunnable on a server for no reason.

**Say why, not that.** Every refusal names the fix: a missing package with its
pip command, a missing Cell with `python3 -m IAC.apps run cell serve`. "App
unavailable" is the failure mode this whole launcher exists to avoid.

**Never claim a capability you have not checked.** If a stage, device or
dependency might be absent, probe it and report it — with the reason. A node
that lies about what it can do makes every plan built against it wrong, and
the failure lands somewhere else entirely.

**Thinking goes through a Cell.** Set `requires.cell: true` and read
`provider_host` from config. Do not embed an API key or call a cloud provider
directly — that bypasses metering and the compute declaration, and it breaks
the app on a machine configured differently.

**One attempt for live work.** Anything interactive should pass a flag that
caps retries. Quality costs latency, and a conversation will not wait.

## If it has a GUI

The shell, DSS, header and footer come from `IAC.apps.ui`. You write a markup
fragment and the fetch calls. **No CSS.**

Every class must exist in `DSS/dss.css` — check the file, do not trust memory:

```
dss-container  dss-card  dss-card--interactive  dss-card--flat
dss-deck  dss-grid  dss-button  dss-button--secondary  dss-button--sm
dss-field  dss-label  dss-input  dss-select  dss-textarea
dss-tag  dss-chip  dss-status  dss-dot  dss-dot--warning  dss-dot--error
dss-eyebrow  dss-display  dss-rule-iri  dss-bar
```

There is **no** `dss-btn`, `dss-callout`, `dss-muted` or `dss-check`. Those
were invented once and the page rendered unstyled while looking finished in
the source. For muted text use `color: var(--dss-ink-soft)`.

Three rules from `DNetLab/WEB_STANDARDS.md` that apply to an app interface:

- **It must say something without JavaScript.** A GUI is a client for an API,
  so it needs JS for live data — but it must state that, not sit on the word
  "loading" forever. The shell writes the `<noscript>`; your static markup
  should describe the app before any script runs.
- **No small bare text in a wide space.** Secondary detail goes in a
  `.dss-bar` or behind a `.dss-rule-iri` keyline, not in shrunken grey type.
- **The tagline is not decoration.** "From Mind To Matter" belongs to the nav
  lockup and to one place where its meaning is explained. An app GUI explains
  nothing, so it does not use it. The shell already handles this.

Then **look at it** at desktop and 375px, in both themes. Measurements prove a
layout is correct; they cannot tell you it looks right.

## Where it runs

`compute.mode` says where the thinking happens: `home` (point at a Cell
elsewhere), `local` (carry one), `prefer-local` (local if genuinely present,
else home). A host that cannot realistically run a core — a Pi Zero, anything
under a gigabyte of RAM — should declare `home` and carry no model. That is
the normal arrangement, not a degraded one.

## D-Net Lab standards that make it reliable

**It runs on its own.** An app is usable without Aether, the Dash or any
other app running: its IAC domain is in-process and its `serve` command is
its own. A dependency that may be absent (a Cell, Weave, the Dash) is probed
and reported by name, never assumed; the app still starts and says what is
missing.

**Silence is the bug.** Every refusal names its fix. A setting it cannot
honour is reported on every answer, not dropped. A failed source shows as
stale with the reason, not as the last good value.

**Loopback and write guards by default.** `serve` binds 127.0.0.1. Writes
need a header or PIN check (the Dash's `X-Dash: 1` and PIN rule is the
pattern); reaching beyond the machine is the operator's switch.

**Live data is polite.** Poll slowly by default, per source; back off when a
source fails or rate-limits; external sources slowest of all. A refresh
updates content IN PLACE and never resets scroll, camera, selection or a
half-typed input (Weave's live cards did, and work became impossible).
One notice per distinct fault, not one per poll.

**Config is the operator's.** Read it; never rewrite it wholesale. A change
is a sparse write of the paths that changed, through the host's one writer.
Secrets are never printed, logged or bundled.

**Agent-shaped work goes through specs.** An app that needs agents to do
things declares them as entities (`personas/<key>/agent.json`) and runs them
through `agent_host` -- the grant, the worker floor, staged writes, the
breaker, the Boundary and the run record come with it.

**Describe it so it survives extraction** (`DNetLab/DISCOVERY.md`): the
manifest `summary`, the README's first sentence and the domain's description
say what it DOES, in plain words, standing alone.

**Give its folder a `.dnetlab` card** with `launch:` lines for its gates, and
tests that can FAIL ON THE REAL BUG (the real route, real library objects;
a test that writes data refuses to run anywhere that does not look local).
`python3`, never `python`. No AI attribution in code, commits or docs.

**Prove it before you say it works:** `python3 -m IAC.apps check <name>`,
`apps.list` shows it, `apps.domains` registers it, its `serve` page renders
with no console errors at desktop and 375px, and its gates pass.

## Do not

- Do not install dependencies for the user. Report them with the command.
- Do not write a config into a bundle.
- Do not invent a manifest field; unknown keys are carried but never acted on.
- Do not build a second UI framework. The shell is shared so every Cell App
  looks like one family.
- Do not build a second anything (see the top): a launcher, registry,
  settings store, memory, agent loop or copy of another program's view.
