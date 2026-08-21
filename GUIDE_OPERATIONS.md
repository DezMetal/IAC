# IAC Operations Guide

This guide provides a detailed breakdown of all default operations registered within the Integrated Agent Core (IAC), as well as examples for utilizing the declarative control flow constructs (`foreach`, `repeat`, `if`).

## Control Flow Constructs

IAC supports recursive, declarative control flow constructs that allow for complex orchestration without needing an external script.

### 1. `{{token}}` Interpolation
All string values in `args` are resolved against the live `data_payload` at execution time.
Supports dot notation for nested access: `{{tab.route}}`, `{{image_0.src}}`, `{{tabs.0}}`.

```json
{
  "op": "web.goto",
  "args": { "url": "{{target_url}}/dashboard" }
}
```

The `config` block is interpolated too, against `data_payload`, so a per-run
output path works: `"output": "pipelines/{{slug}}_results.json"`.

**Filters** — `{{key|filter}}`, chainable. A closed set, deliberately:
interpolation is not a template language.

| Filter | Effect |
|---|---|
| `qsep` | `?` or `&`, whichever correctly appends a query to that URL (empty if it already ends in one) |
| `slug` | lowercase, non-alphanumerics collapsed to `-` |
| `upper` / `lower` / `trim` | the obvious string transforms |
| `json` | serialise the value as JSON |
| `count` | length of a list, string or dict |

`qsep` exists because a hardcoded separator is wrong for half of all inputs:
`https://site.com/name` needs `?sk=photos` while
`https://site.com/profile.php?id=1` needs `&sk=photos`. Getting it wrong does
not raise — the page loads, the scrape returns nothing, and the failure only
surfaces much later as missing data.

```json
{ "op": "web.goto", "args": { "url": "{{fb_url}}{{fb_url|qsep}}sk=photos" } }
```

### 2. `foreach`
Iterates over an array present in the payload. The current item and its index are temporarily injected into the payload during the loop.

```json
{
  "op": "foreach",
  "items": "info_tabs",
  "as": "tab",
  "index": "_index",
  "steps": [
    { "op": "web.goto", "args": { "url": "https://example.com/{{tab.route}}" } },
    { "op": "log", "args": { "message": "Visiting {{tab.route}} at index {{_index}}" } }
  ]
}
```

### 3. `if`
Conditional branching based on payload evaluation.
Operators supported: `exists`, `not_exists`, `==`, `>`, `<`, `>=`, `<=`.

```json
{
  "op": "if",
  "condition": "user_data exists",
  "then": [
    { "op": "log", "args": { "message": "User data found!" } }
  ],
  "else": [
    { "op": "log", "args": { "message": "No user data." } }
  ]
}
```

### 4. `repeat`
Numeric iteration.

```json
{
  "op": "repeat",
  "count": 3,
  "index": "_i",
  "steps": [
    { "op": "log", "args": { "message": "Iteration {{_i}}" } }
  ]
}
```

---

## Finding an operation: `iac.search`

Describe what you are trying to do and get back operations that exist ON THIS
HOST, plus the chain such tasks usually take:

    { "op": "iac.search", "args": { "goal": "write a script and run it" } }

    MATCHES (no order -- ranked by wording, NOT the sequence to run them in):
      sys.exec(cmd, cwd, timeout) -- Run a shell command
      filesystem.write(path, content) -- Writes content to a specified file
    SUGGESTED ORDER (this one IS a sequence): filesystem.write -> sys.exec
      exec runs in the workspace, so a file written there is runnable by name.

The two lists are labelled apart on purpose. Matches are ranked by how closely
the words fit, so reading them top to bottom would have you run a script before
writing it. Only a chain is ordered.

No model is involved -- it is string matching over the loaded registry, so it
can only ever name operations that are really there, and a suggested chain
naming something this host lacks is dropped rather than offered.

It exists because agents that cannot find an operation invent one: `execute_shell`
was called and refused while `sys.exec` sat available, and `web.search` was
emitted before it existed. Every result is labelled GUIDANCE, not instruction --
the caller knows what the task needs; this only knows which words look similar.

## Default Operations by Domain

### Core Operations (`core`)
Fundamental operations available on every IAC node without needing specific runtimes.

- **`push`**
  - Adds data to the Universal Payload.
  - Args: `payload_key` (string), `data` (any)
- **`pull`**
  - Retrieves a value from the payload (useful when checking return statuses programmatically).
  - Args: `payload_key` (string)
- **`merge`**
  - Folds a dictionary into the root of the payload.
  - Args: `data` (object)
- **`drop`**
  - Removes specific keys from the payload to free memory.
  - Args: `keys` (list of strings)
- **`wait`**
  - Pauses execution.
  - Args: `ms` (integer, milliseconds)
- **`log`**
  - Outputs to the console with priority levels.
  - Args: `message` (string), `level` (string: "info", "warn", "error")
- **`set`**
  - Assigns one payload key to a literal value.
  - Args: `payload_key` (string), `value` (any)
- **`append`**
  - Accumulates onto a payload key. Appends to a list if the key holds one,
    otherwise concatenates as text with `sep` (default newline).
  - Args: `payload_key` (string), `from_key` (string) or `value` (any), `sep` (string)
  - This is what makes generating a document in pieces possible: inside a
    `foreach`, every iteration writes the same `output_key`, so without an
    accumulator a ten-section page arrives as its final section.
- **`parse_json`**
  - Parses a payload key holding JSON text into a real object or list,
    tolerating markdown fences. Needed to `foreach` over an AI-generated list.
  - Args: `from_key` (string), `into` (string, defaults to `from_key`)
- **`dump_payload`**
  - Writes the whole payload to disk as `{"final_payload": {...}}`, for handing
    state to an external script mid-plan.
  - Args: `path` (string)

### System Operations (`sys`)
Host-level interactions.

- **`sys.exec`**
  - Executes a command on the host shell. *(Restricted operation)*
  - Args: `cmd` (string), `cwd` (string), `timeout` (int), `payload_key` (string)
  - With `payload_key`, stores `{stdout, stderr, exit_code}` — reachable by dot
    path, e.g. `{{result.stdout}}`. Stored regardless of exit code.

> `sys.fs_read` and `sys.fs_write` were removed. They were a second door into
> the same room as `filesystem.read`/`filesystem.write` that skipped the host's
> path policy entirely. Use the `filesystem` domain below.

### Filesystem Operations (`filesystem`)
Subject to the host's workspace boundary and any staging hook it supplies.

- **`filesystem.read`**
  - Args: `path` (string), `payload_key` (string)
  - `payload_key` is how the content becomes addressable. Without it the value
    is only reachable as `{{_output}}`, which the very next operation overwrites.
- **`filesystem.write`**
  - Args: `path` (string), and exactly one of `content` (literal) or
    `from_key` (payload key, dot paths allowed).
  - `from_key` is the normal choice for anything a previous step generated —
    interpolating 6KB of HTML through `content` would mean pasting the whole
    document into the plan file.
  - A missing `from_key` is an **error**, never an empty file. Silently writing
    `""` turns a failed generation step into a 0-byte artifact that the rest of
    the pipeline then builds on top of.
  - `expect`: `json` | `object` | `array` validates before touching the file.
    A model that hits its token cap returns a document truncated mid-string;
    without this the truncation overwrites the good copy and only surfaces
    several steps later as a parse error somewhere else.

> **Never ask a model to echo back a large document in order to edit it.** Give
> it a reduced view, take a patch, and merge the patch yourself — the pieces it
> must not touch then cannot be lost, and its reply stays far below the cap.
> SiteGen's `compose.py config-view` / `merge-config` are this pattern.
- **`filesystem.copy`**
  - Args: `src` (string), `dst` (string). Creates parent directories.
  - Prefer this over `sys.exec` with `cp`: plans run on Windows as often as
    not, and `cmd.exe` has no `cp`.

### Choosing where inference runs — provider profiles

One pipeline routinely wants more than one backend: something cheap and local
to describe forty screenshots, something stronger for the handful of calls that
make real decisions. A **profile** is that bundle of settings, named once in
`config.ai_config.providers`:

```json
"ai_config": {
  "providers": {
    "vision":    { "provider": "ollama", "host": "http://10.0.0.5:11434",
                   "model": "Gemma4:E4B",
                   "options": { "num_ctx": 8192, "think": false } },
    "architect": { "provider": "gemini", "model": "gemini-2.5-pro",
                   "api_key_env": "GEMINI_API_KEY" }
  },
  "default_provider": "architect",
  "vision_provider": "vision"
}
```

Any AI step then selects one with `"provider": "vision"`. A step that says
nothing gets `default_provider` — or `vision_provider` when the call carries
images, which is what makes "vision goes to the local box" a single line rather
than a per-step annotation.

Name profiles for the **role they play** in the pipeline, not for where they
happen to run. Whether a slot resolves to a machine on the LAN or a hosted API
is a property of the slot; repoint it and every step using it moves with it,
with no edit to any operation.

Precedence, highest first: **step args → profile → base `ai_config`**.
`provider` also accepts a bare backend name (`ollama`, `gemini`, `openai`,
`anthropic`). Keys come from `api_key_env` so they stay out of the plan file.
A plan with no `providers` block behaves exactly as it always did.

### Failing a step deliberately — `on_error`

Steps are advisory by default: a failure is logged and the plan continues. A
step that is load-bearing says so, at step level (not inside `args`):

```json
{ "op": "sys.exec", "on_error": "abort",
  "args": { "cmd": "python3 inject_media.py ..." } }
```

`abort` stops the run, still writes the results file, and reports the step
index so `--resume` can pick up from there. `sys.exec` is judged on its exit
code as well as its status. Use it where continuing would mean generating
confidently against missing inputs.

### AI Operations (`ai`)
Requires an `IntelligenceCore` provider (Ollama, Gemini, Persona).

- **`ai.process`**
  - Standard AI inference (vision or text) on a single payload item.
  - Args: `payload_key` (string), `prompt` (string), `output_key` (string)
  - Batch mode: pass `prefix` (default `image_`) to sweep every matching key.
  - Aliases: `ai.analyze`, `ai.vision`, `ai.analyze_data`, `analyze_data`.
    Use these freely — they all resolve to `ai.process`. (`analyze` on its own
    maps to `web.analyze`, which operates on the live browser view instead.)
- **`ai.brain`**
  - Batch cognitive sweep of all `image_*` keys via encoding + AI.
- **`ai.plan`**
  - Standalone text inference with selective payload context injection.
  - Args: `prompt` (string), `inject_keys` (list of strings), `output_key` (string),
    `format` (string), `model`/`provider` (overrides), `options` (object)
  - **`format`** — `json` | `html` | `css` | `text` | `auto` (default).
    Pin this whenever the output is not JSON. Under `auto`, output that opens
    with `{` or `[` is parsed and normalised as JSON; anything else is returned
    verbatim. Pinning `html`/`css`/`text` disables JSON extraction entirely.

    > This matters: an earlier revision ran a greedy `{...}` scan over *every*
    > `ai.plan` result. A generated HTML page containing a JSON-LD block could
    > have its entire body replaced by that fragment. Pin the format when
    > generating markup.
  - **`model` / `provider`**: do not set these unless the user explicitly asked
    for a specific override. Leave them out and the pipeline's `ai_config`
    defaults apply.
- **`ai.encode`**
  - Base64 encodes an image resource for vision processing.
  - Args: `file_path` (string), `payload_key` (string)

### Web Operations (`web`)
Requires a `WebAgent` browser runtime.

- **`web.goto`**: Navigate to `url`. Returns `status`, `url`, `title`, `chars`
  and a `preview` of the opening text, so a single navigation is answerable
  without a second call. Use `web.extract` when more than the opening is
  wanted.
- **`web.search`**: Search the web for `query` (optional `limit`). Runs in the
  browser carrying the saved session from `IAC/auth/session.json`, because a
  cold client gets a human-verification challenge instead of results. Returns
  `title` / `url` / `snippet` per result, read structurally rather than by CSS
  class. The engine comes from `search_url` (`{q}` is the encoded query), so it
  can be pointed at a search API without changing the operation. If a challenge
  is served it returns an ERROR saying so — an empty list would read as "the
  web has nothing about this".
- **`web.click`**: Click element by `selector`
- **`web.type`**: Type `text` into `selector`
- **`web.snap`**: Capture screenshot and store in `payload_key`
- **`web.eval`**: Execute custom JS `code`
- **`web.modify`**: Set text, value or an attribute on an element
- **`web.brain`**: AI sweep over every matching payload item
- **`web.stop`**: Terminate the browser session
- **`web.sandbox`**: Hand the browser to a human, with the IAC HUD attached
- **`web.save_state`**: Write cookies and local storage to a JSON file

#### Sessions: `storage_state` and `save_storage_state`

`config.storage_state` loads a saved session at launch;
`config.save_storage_state` writes it back when the browser closes — through
whichever entry point ran the plan, not just `webagent.py` directly.

`web.save_state`'s `path` falls back to `save_storage_state`, then
`storage_state`, then `auth/session.json`; a path naming a *directory* gets
`session.json` appended. Writes are atomic, and an empty session will not
overwrite a non-empty one unless you pass `allow_empty` — losing a login that
took a human to obtain costs far more than a skipped save.

A storage state file that is missing, malformed, or not actually a Playwright
state is reported and ignored rather than being fatal. It must never block the
sandbox launch, because that launch is how you produce a good one.

**Refreshing an expired login:**

```bash
python3 iac.py pipelines/sandbox.json
```

Log in by hand, then exit the HUD. The session lands in `auth/session.json`,
and every pipeline pointing at that file picks it up on its next run. Every
operation the sandbox HUD offers is a registered operation, so a chain exported
from it replays unchanged through `python3 iac.py <plan>`.

### Skill Operations (`skill`)

Registered by the host application (Aether), not by IAC itself — IAC stays a
generic execution substrate. Present only when a skill library is attached.

- **`skill.list`**: List installed skills and when to use each.
- **`skill.load`**: Read a skill's full instructions before performing its task.
  - Args: `name` (string), e.g. `sitegen`
- **`skill.reload`**: Rescan skill directories from disk.

Skills follow a two-stage economy matching the tool router: a one-line catalog
rides along with the capability injection, and the full body is fetched only
when `skill.load` is called. Per-turn cost stays flat as the library grows.

---


> **The browser lives on ONE thread.** Playwright's sync API may only be
> driven from the thread that started it, so every `web.*` operation is
> marshalled onto a single dedicated worker (see `web/ops.py`). Hosts that run
> turns on a thread pool would otherwise start the browser on one worker and
> touch it from another, failing with "cannot switch to a different thread
> (which happens to have exited)".

> **`web.analyze` needs a loaded page.** A fresh browser sits on `about:blank`;
> analysing it sends a white rectangle to a vision model and costs ~12s to be
> told it is white. It now refuses immediately and says to `goto` or `search`
> first.

> **`web.brain` sweeps `prefix` (default `image_`).** `web.extract` writes
> `data_0`, `data_1`, so an explicit prefix matters; with no match and no
> explicit prefix it falls back to sweeping whatever the payload holds, which
> is what makes extract → brain work as an agent expects.

## Writing Reliable Pipelines

Patterns that separate a pipeline that completes from one that produces good
output. Drawn from rebuilding `sitegen_auto.json`.

1. **Give the model the contract before asking it to decide.** A prompt that
   says "include relevant addons" without listing them yields invented names.
   Emit the tool's own capability output into the payload and inject it.

2. **Validate before anything downstream depends on it.** Generate config →
   validate → repair → *then* design against it. Discovering a bad config after
   the HTML was written around it wastes the whole run.

3. **Feed validation reports back as repair input.** A structured error report
   is the highest-signal prompt context available. Instruct the model to return
   its input unchanged when the report is clean — that makes the repair step
   idempotent and safe to run unconditionally.

4. **Pin `ai.plan` `format`.** See `ai.plan` above.

5. **Verify ops resolve before shipping a plan.** A typo'd op name is a silent
   no-op:

   ```python
   from iac import bootstrap
   from registry import get_registry
   bootstrap(safe_mode=False)
   reg = get_registry()
   print([o for o in my_ops if not reg.has(o)])
   ```

   `ai.analyze` sat unresolved in a production pipeline for months, so the final
   review step never ran once.
