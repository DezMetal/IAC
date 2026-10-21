# IAC Launcher — Design

**Where Cell Apps live, and what runs them. The IAC Launcher.**

A **Cell App** is a D-Net program that gets its intelligence from a
[Cell](../Cell), speaks [IAC](../IAC), and declares what it is rather than
being configured into working. The Transposer is the first one.

**`IAC/apps`** is the thing that holds them: find, inspect, run, stop, import,
export. One place, one vocabulary, reachable from a terminal or from an agent
over `apps.*`.

**Status: phase 1.** Local apps, run and stop, import and export, the IAC
domain. See §7.

**Apache-2.0**, as part of IAC. This is open infrastructure; the apps it launches may not be.

---

## 1. The naming decision

*Settled 2026-10-21, because the operator asked for a word and the risk of not
choosing was three words for one thing.*

| Word | Means | Scope |
|---|---|---|
| **Cell** | The inference provider. The socket an intelligence core plugs into. | unchanged |
| **Cell App** | A D-Net program powered by a Cell. The artifact: a directory with a manifest. | **the new word** |
| **Node** | *What a Cell App is made of* — the four declarations. Architecture, not an artifact. | clarified |
| *(no new word)* | Where Cell Apps are kept and run is `IAC/apps`, because IAC already runs declared things. | **deliberately unnamed** |

**One addition, and deliberately only one.**

**Why "Cell App".** The operator reached for it unprompted — "D-Net
Cyberspace/Cell Apps", "Cell App Launcher" — and a name people already use is
worth more than a better one they have to learn. It also says the true thing:
these are apps whose intelligence comes from a Cell, the way an iOS app's
frameworks come from iOS. That makes Cell a platform, which it had already
quietly become.

**Why "Node" is not a third artifact.** `D-NET-LAB.md` says a D-Net Cyberspace
Node is four declarations. That is a *description of composition*, not a
separate thing to package — like "program" versus "executable". A Cell App is
a node; node is what it is made of. One artifact word, one architecture word.

**Why "`IAC/apps`".** Short, unflashy, and it reads as an IAC domain, which was the
operator's stated test when Cell was named: `apps.list`, `apps.run`,
`apps.export`. The metaphor follows Cell without straining — a bay is where
cells are racked.

### The convergence, noted and not acted on

The operator spotted it first: this resembles IAC pipelines and Aether's
dashboard, and he is right. A pipeline is a declared sequence Aether can run;
a Cell App is a declared program `IAC/apps` can run; Aether's dashboard already lists
and launches things. Three surfaces over the same idea.

That is alignment showing up as duplication, which is what happens when
separate good ideas grow toward each other. **The response here is to make
convergence cheap rather than to force it now:** the manifest is plain JSON, the
operations are an IAC domain, and nothing in `IAC/apps` owns a UI. So Aether's
dashboard can become `IAC/apps`'s front end by calling `apps.*`, and a pipeline can
become an app entry point, whenever that is worth doing. No refactor is
required today and none is prevented later.

## 2. What a Cell App is

A directory with a `cellapp.json`. That is the whole contract.

```jsonc
{
  "cellapp": "1.0",
  "name": "transposer",            // the id, lowercase, no spaces
  "title": "Transposer",
  "summary": "Speech in, transformed speech out.",
  "version": "0.1",

  "domain": "transpose",           // its IAC domain, if it has one
  "register": "Transposer.iac_domain:register",

  "commands": {                    // what a person can run
    "text":   {"run": "Transposer.app:main", "args": ["text"]},
    "listen": {"run": "Transposer.app:main", "args": ["listen"]},
    "serve":  {"run": "Transposer.service:main", "port": 8771,
               "service": true}
  },

  "requires": {
    "cell": true,                  // needs an inference provider
    "python": ">=3.9",
    "packages": ["openai-whisper", "piper-tts"],
    "capabilities": ["audio-in", "audio-out"]
  },

  "config": "config.json",
  "compute": {"mode": "local"}     // home | local | prefer-local
}
```

Nothing in that file is `IAC/apps`-specific. It describes the app, and `IAC/apps` reads it.
An app with no manifest is not a Cell App — it is just a directory, and `IAC/apps`
says so rather than guessing.

## 3. What `IAC/apps` does

| | |
|---|---|
| **Finds** | Scans for `cellapp.json` beside itself and in configured roots. No registry file to keep in sync — the manifests *are* the registry. |
| **Inspects** | Reports what an app needs and whether this host has it, before it is run. |
| **Runs** | Starts a command or a service as a subprocess, tracks it, stops it. |
| **Imports / exports** | A portable `.cellapp` bundle: the manifest, the code, no configs, no caches, no models. |
| **Declares** | `apps.*` over IAC, so an agent can do all of the above. |

## 4. What `IAC/apps` refuses to do

* **It does not install anything.** `apps.check` reports a missing package with
  the command to get it. An app launcher that silently pip-installs is how a
  machine becomes unexplainable.
* **It does not carry configs or models in a bundle.** A config from one
  machine is wrong on another — it names hosts, ports, paths and keys — and a
  model is hundreds of megabytes that the receiving host may already have.
  Export carries code and a manifest; the receiver runs `setup`.
* **It does not pretend an app is running.** A process that exited is reported
  as exited, with its last output. The alternative is a launcher that lists
  three running apps, none of which are.
* **It does not hide why an app cannot run.** Missing Cell, missing package,
  missing microphone — each by name, with the fix. Same rule as Cell's probe
  and the Transposer's stages.

## 5. Why subprocesses and not imports

`IAC/apps` runs an app's command in its own process. It costs a little startup and
buys three things worth more:

* **An app that crashes does not take `IAC/apps` with it**, and a launcher that dies
  with its worst app is not a launcher.
* **Apps do not share a Python namespace**, so two of them wanting different
  versions of something is their problem rather than an import-order puzzle.
* **Stopping actually stops.** A thread holding a model cannot be reliably
  killed; a process can.

The exception is the IAC domain: `apps.*` registers in-process, because an agent
calling `apps.list` wants an answer rather than a subprocess.

## 6. Monetisation, kept in view

`D-NET-LAB.md` is explicit that commercial work is the means and the lab
monetises what consistently serves. Two properties here exist for that reason
and cost nothing now:

* **A bundle is a unit of delivery.** `.cellapp` is already the shape of
  something that could be handed to a client, installed on their hardware, and
  run against their own Cell.
* **Cell meters.** An app's cognition goes through a Cell, which counts tokens
  per identity. Whatever is eventually charged for, the measurement is already
  where it needs to be.

Neither is built toward speculatively. They are noted because the architecture
happens to allow them, and a note is cheaper than a retrofit.

## 7. State

| Piece | |
|---|---|
| `manifest.py` | Reading, validating and describing a `cellapp.json` | working |
| `registry.py` | Discovery across roots, requirement checking | working |
| `runner.py` | Run, stop, status, output capture | working |
| `bundle.py` | `.cellapp` import and export | working |
| `iac_domain.py` | `apps.*` | working |
| `__main__.py` | The CLI | working |
| A UI | **not built.** Aether's dashboard is the candidate, via `apps.*` |
| Remote apps | **not built.** `apps.run` on another host is a `Cell.deploy` job |
| Dependency install | **deliberately not built.** See §4 |

---

## 8. What this absorbs

*Direction set by the operator 2026-10-21.* The launcher is not only for Cell
Apps — it is where several existing surfaces over the same idea converge. IAC
already runs declared things in three different ways, and that is the
duplication worth removing rather than adding a fourth to:

| Exists today | What it is | How it folds in |
|---|---|---|
| **Pipelines** (`IAC/pipelines/*.json`) | A declared sequence of operations, run by `IAC/runner.py`. | A pipeline is a command with no code of its own. A manifest can name one as its `run`, so a pipeline becomes a launchable app without being rewritten. |
| **The node web app** (`IAC/web/`) | A browser UI over IAC operations. Needs polish. | The launcher deliberately owns no UI. This becomes its front end by calling `apps.*`, which is also what finally gives that UI a reason to exist. |
| **The sandbox** (`SANDBOX_GUIDE.md`, `pipelines/sandbox.json`) | A constrained place to try operations. | A sandbox is a run with a narrower allowlist. `apps.run` already passes `allow` through to domain registration; a sandboxed run is the same call with less permitted. |
| **Aether's dashboard** | Lists and launches things for a person. | The logical adopter: Aether is the IAC Gateway and the human-facing client. It calls `apps.*` rather than either side being rewritten. |

**None of that is built yet, and none of it is urgent.** It is written down
because the shape of this component was chosen to make the convergence cheap:
the manifest is plain JSON, every operation is in the IAC registry, and nothing
here renders anything. Those three properties are what let each of the above
move in later without a refactor — and they are the reason not to grow a
feature that would break them.

The operator's framing, worth keeping: *"bubbles begin to merge; refactoring
and optimization, and cleaning of legacy/redundancies may come soon after…
good ideas grow too."* That is the pattern this section exists to make
possible rather than to pre-empt.

## 9. Being open changes who this is for

The launcher is Apache-2.0, because it lives in IAC. That is not incidental:
it means **anyone can launch and manage the apps they build**, not just the
lab. A Cell App is a directory with a manifest and a bundle is a tar — neither
requires anything of the lab's that is not public.

The apps themselves need not be open. Cell and the Transposer are
licence-undecided and launch perfectly well from here, which is the seam
working as intended: open substrate, whatever you like on top.
