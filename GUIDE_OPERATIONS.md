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

### System Operations (`sys`)
Host-level interactions.

- **`sys.exec`**
  - Executes a command on the host shell. *(Restricted operation)*
  - Args: `cmd` (string)
- **`sys.fs_read`**
  - Reads a file from disk into the payload.
  - Args: `path` (string), `payload_key` (string)
- **`sys.fs_write`**
  - Writes data from the payload to disk.
  - Args: `path` (string), `content` (string or object)

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

- **`web.goto`**: Navigate to `url`
- **`web.click`**: Click element by `selector`
- **`web.type`**: Type `text` into `selector`
- **`web.snap`**: Capture screenshot and store in `payload_key`
- **`web.eval`**: Execute custom JS `code`
- **`web.stop`**: Terminate the browser session

### Runner-Level Operations

Handled directly by the runner rather than the registry, so they will not
appear in `registry.list_operations()`:

- **`dump_payload`**: Write the entire current payload to disk.
  - Args: `path` (string)
  - Useful for handing payload state to an external script mid-plan.

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
