---
name: cell-app
description: build a Cell App -- a D-Net program that declares itself in a cellapp.json, speaks IAC, gets its intelligence from a Cell, and optionally has a DSS web GUI. Use for "make an app", "build a tool", "add a GUI", "package this", "deploy it to another machine".
triggers: cell app, cellapp, make an app, build an app, new app, launcher, apps.new, scaffold, package app, ship app, app gui, deploy app
---

A **Cell App** is a directory with a `cellapp.json`. That is the whole
contract. It declares what it is, what it needs, and what can be run; the
IAC Launcher does the rest.

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

## Do not

- Do not install dependencies for the user. Report them with the command.
- Do not write a config into a bundle.
- Do not invent a manifest field; unknown keys are carried but never acted on.
- Do not build a second UI framework. The shell is shared so every Cell App
  looks like one family.
