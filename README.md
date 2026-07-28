# IAC — Integrated Agent Core

**An execution substrate for agents.** A structured, validated way to describe
what should happen — then make it happen, across the filesystem, the web,
vision, and anything you add yourself.

The premise IAC is built on: **the system does the heavy lifting, so that even a
small local model with no native tool-calling and no JSON mode behaves like one
that has both.** Every design choice here favours carrying the model rather than
requiring a better one.

[![Licence: Apache-2.0](https://img.shields.io/badge/licence-Apache--2.0-blue.svg)](LICENSE)
[![Extensible](https://img.shields.io/badge/extensions-iac.domains-brightgreen.svg)](EXTENDING.md)

> Created and maintained by **[D-Net Lab](https://lab.dnet.live)**
> *"With Accessibility Comes Understanding and Inspiration" · "From Mind To Matter"*

---

## Licence and attribution

IAC is free and open source under **[Apache-2.0](LICENSE)**. Use it, modify it,
build commercial products on it — no fee, no permission needed.

**What is asked in return is credit.** Apache-2.0 requires you to keep the
[`NOTICE`](NOTICE) file, retain copyright headers, and mark files you have
changed. The licence does **not** grant rights to the D-Net Lab name
(Apache-2.0 §6): you may say your product is *built on* IAC, but you may not
claim you created it, present a fork as originally your own work, or use
D-Net Lab's name or marks as your own. See **[TRADEMARKS.md](TRADEMARKS.md)** —
it is short, and everything reasonable is permitted.

IAC hands you the notice so you never have to write it yourself:

```python
import IAC
print(IAC.attribution())
# Powered by IAC (Integrated Agent Core) v2.0
# Copyright (c) 2026 D-Net Lab — https://lab.dnet.live
# Licensed under Apache-2.0
```

Open source runs on credit. Taking the name off takes the one thing the
arrangement was built to preserve.

---

## Quick Start

### As a Python Library

```python
import IAC

# Validate a plan
valid, errors = IAC.validate({
    "plan": [
        {"op": "goto", "args": {"url": "https://example.com"}},
        {"op": "snap", "args": {"payload_key": "homepage"}}
    ]
})

# Execute a plan
result = IAC.execute({
    "plan": [
        {"op": "wait", "args": {"ms": 1000}},
        {"op": "log", "args": {"message": "Hello from IAC"}}
    ]
})

# Create an envelope for cross-node communication
envelope = IAC.create(
    plan=[{"op": "ai.analyze", "args": {"prompt": "Describe this."}}],
    source="aether",
    target="webagent"
)
```

### As a CLI Tool (Universal Entry Point)

The `iac.py` script is the primary entry point for the entire system. It automatically bootstraps all operation domains (Web, AI, Core).

```bash
# Execute any plan (Browser or Non-Browser)
python3 iac.py plan.json

# Execute with output override
python3 iac.py plan.json -o results.json

# List all available operations
python3 iac.py --list-ops
```

### Via Aether API

```bash
curl -X POST http://localhost:5050/api/iac/execute \
  -H "Content-Type: application/json" \
  -d '{"plan": [{"op": "log", "args": {"message": "Hello"}}]}'

# Check available operations
curl http://localhost:5050/api/iac/registry

# Validate without executing
curl -X POST http://localhost:5050/api/iac/validate \
  -H "Content-Type: application/json" \
  -d '{"plan": [{"op": "web.goto", "args": {"url": "https://example.com"}}]}'
```

---

## The IAC Payload

Every exchange between D-Net nodes uses this structure:

```json
{
  "iac_version": "2.0",
  "source": "aether",
  "target": "webagent",
  "config": {
    "headless": true,
    "output": "results.json",
    "ai_config": {
      "host": "http://192.168.1.235:11434",
      "model": "qwen3.5:4b",
      "prompt": "Analyze the image."
    },
    "drop": ["encoded"],
    "security": {
      "allow_eval": false
    }
  },
  "plan": [
    {"op": "web.goto", "args": {"url": "https://example.com"}},
    {"op": "web.snap", "args": {"payload_key": "homepage"}},
    {"op": "ai.analyze", "args": {"prompt": "Describe what you see."}}
  ],
  "data_payload": {}
}
```

### `web_agent` Configuration

| Key | Type | Description |
|:---|:---|:---|
| `headless` | `bool` | Run browser without a window (default: `true`) |
| `output` | `string` | Final JSON results destination |
| `record_video` | `bool` | Enable screen recording |
| `record_video_dir`| `string` | Directory for `.webm` artifacts |
| `user_data_dir` | `string` | **Persistent Mode**: Path to browser profile directory |
| `storage_state` | `string` | Path to load cookies/storage from (JSON snapshot) |
| `save_storage_state`| `string`| Path to auto-save state at session end |

### Field Reference

| Field | Required | Description |
|:---|:---|:---|
| `iac_version` | No | Protocol version (default: `"2.0"`) |
| `source` | No | **Node Identifier**: The unique label for the originating node (e.g., `aether`, `cli`, `sitegen`). Used for trust-level routing and history tracking. |
| `target` | No | Destination node (default: local execution) |
| `config` | No | Session-level configuration |
| `plan` | **Yes** | Ordered list of operations to execute |
| `data_payload` | No | Shared state — the Universal Payload |

### Backward Compatibility

Existing IAC v1 plan-configs (with `workflow` key, `ai_core` key, `web_agent` key) are automatically normalized to v2 format. No changes needed to existing plans.

---

## Operations

Operations are organized by domain to ensure architectural clarity and eliminate cross-system confusion.

### Core Operations (Domain: `core`)
Fundamental state and flow control. These are always available and do not require specific runtime providers.

#### `push`
Adds data to the Universal Payload.
- **Args**: `payload_key`, `data`

#### `pull`
Retrieves a value from the payload.
- **Args**: `payload_key`

#### `merge`
Folds a dictionary into the root payload.
- **Args**: `data` (object)

#### `drop`
Removes specific keys from the payload.
- **Args**: `keys` (list)

#### `wait`
Pauses execution.
- **Args**: `ms` (int)

#### `log`
Console output with priority levels.
- **Args**: `message`, `level`

#### `noop`
No-operation placeholder.

### Web Operations (Domain: `web`)
Operations that interact with a browser instance. Requires a `WebAgent` runtime.

| Operation | Description |
|:---|:---|
| `web.goto` | Navigate to a URL |
| `web.click` | Click a DOM element |
| `web.type` | Input text into a field |
| `web.scroll`| Scroll the viewport |
| `web.tour`  | Execute automated waypoint tour |
| `web.snap`  | Capture screenshot to payload |
| `web.probe` | Discover interactive elements |
| `web.eval`  | Execute custom JavaScript |
| `web.stop`  | Finalize and close session |

### AI Operations (Domain: `ai`)
Cognitive and vision processing tasks. Requires an `IntelligenceCore` provider.

| Operation | Description |
|:---|:---|
| `ai.process`| Standard AI inference (vision/text) on a single payload item |
| `ai.brain`  | Cognitive sweep — batch process all `image_*` items via encode + AI |
| `ai.plan`   | Standalone text inference with payload context injection |
| `ai.encode` | Base64 resource encoding |

#### `ai.plan` — Detailed Reference
Standalone AI text generation that injects selected payload keys as context and stores the result.

| Arg | Type | Description |
|:---|:---|:---|
| `prompt` | `string` | The generation prompt |
| `inject_keys` | `string[]` | Payload keys to inject as context (appended to prompt) |
| `output_key` | `string` | Payload key to store the AI response (default: `ai_plan_result`) |
| `model` | `string` | Override model (falls back to `ai_config.model`) |
| `options` | `object` | Ollama options (`temperature`, `think`, etc.) |

```json
{
  "op": "ai.plan",
  "args": {
    "prompt": "Generate a JSON config for {{business_name}}.",
    "inject_keys": ["fb_about", "fb_contact_info", "image_0"],
    "output_key": "generated_config",
    "options": { "temperature": 0.2 }
  }
}
```

> **Note:** `ai.plan` automatically selects `vision_model` when processing items with encoded images. For text-only generation, it uses the standard `model`.

### System Operations (Domain: `sys`)
Host-level interactions and filesystem management.

| Operation | Description |
|:---|:---|
| `sys.exec`  | Execute host shell command |
| `sys.fs_read`| Read file from disk into payload |
| `sys.fs_write`| Write payload data to disk |

### Desktop Operations (Domain: `desktop`)
Host-level GUI automation (requires `pyautogui`). These are optional and only available if the library is installed.

| Operation | Description |
|:---|:---|
| `desktop.snap`  | Take a screenshot of the entire desktop and save to payload |
| `desktop.click` | Click the mouse at specific coordinates or current location |
| `desktop.type`  | Type text simulating keyboard input |
| `desktop.hotkey`| Press a combination of keys (e.g. `["ctrl", "c"]`) |
| `desktop.move`  | Move the mouse cursor to absolute or relative coordinates |

### Vision Operations (Domain: `vision`)
Image processing and visual annotations. Helpful for preparing images before AI inference (requires `Pillow`).

| Operation | Description |
|:---|:---|
| `vision.overlay`| Bake numbered grids, bounding boxes, or points directly onto an image in the payload. Parameters: `payload_key`, `type` (grid/boxes/points), `grid_size`, `color`. |
| `vision.resolve_cells`| Convert AI-provided grid numbers into a pixel bounding box. Takes `cells` array (or `start_cell`/`end_cell`) and outputs `x`, `y`, `w`, `h`, `center_x`, `center_y` to the payload. |

### Filesystem Operations (Domain: `filesystem`)
Native local file system operations.

| Operation | Description |
|:---|:---|
| `filesystem.list`  | Lists files and directories in a given path |
| `filesystem.read`  | Reads the entire content of a specified file |
| `filesystem.write` | Writes content to a specified file |
| `filesystem.grep`  | Searches for a pattern within a file |

### Datastore Operations (Domain: `datastore`)
Manages a persistent key-value store (relies on `datastore` and an auth token injected via execution context).

| Operation | Description |
|:---|:---|
| `datastore.get`   | Retrieves a persistent value from the datastore |
| `datastore.set`   | Sets a value in the datastore |
| `datastore.delete`| Deletes a key-value pair |
| `datastore.list`  | Lists all key-value pairs |

### Prism Operations (Domain: `prism`)
Recursive structural workspace scanner for code and DNA logic extraction.

| Operation | Description |
|:---|:---|
| `prism.scan`      | Recursive structural scan of a workspace dir. Returns project structure and logic anchors. |

### Your own domains

IAC ships the general operations above. Anything specific to your product — an
internal API, a hardware controller, a company database — is added as an
**extension** and never needs to enter this repository:

```python
from IAC.extensions import Domain

crm = Domain("crm", author="Example Co", requires=["network"])

@crm.op("lookup", "Find a customer by email", {"email": {"type": "string"}}, ["email"])
def lookup(args, context):
    return {"status": "ok", "data": {...}}

crm.register()
```

Extensions can declare what they touch, so a host can refuse ones it has not
audited; core domain names are reserved so nothing can silently redefine
`filesystem.write`. See **[EXTENDING.md](EXTENDING.md)**.

D-Net Lab's own `dnet.*` and `aether.*` domains work exactly this way — they
live with the products that own them, not here.

---

## 5. Security & Isolation

## Operation Registry

Operations are extensible. Any node can register new operations at startup:

```python
from IAC import get_registry

registry = get_registry()
registry.register(
    name="my_custom_op",
    domain="myapp",
    description="Does something custom",
    parameters={"type": "object", "properties": {"input": {"type": "string"}}},
    handler=lambda args, ctx: {"result": f"Processed: {args.get('input')}"}
)
```

Operations are discoverable via `GET /api/iac/registry` on Aether or programmatically:

```python
registry.list_operations()          # All operations
registry.list_operations("web")     # Web domain only
registry.to_prompt_block()          # Text listing for LLM prompts
registry.to_ollama_schemas()        # Ollama tool-call format
```

---

## Intelligence Cores

Intelligence Cores are swappable AI backends. Any IC that implements the standard interface can power IAC operations:

```python
from IAC import get_ic_router
from IAC.ic import OllamaIC, GeminiIC

router = get_ic_router()
router.register("ollama", OllamaIC(host="http://192.168.1.235:11434", model="qwen3.5:4b"))
router.register("gemini", GeminiIC(model="gemma-4-31b-it"))
router.set_default("ollama")

# Discovery
router.list_cores()
router.find_for_operation("ai.analyze")
```

### Available ICs

| IC | Requires | Capabilities |
|:---|:---|:---|
| `OllamaIC` | Ollama server | `ai.analyze`, `ai.brain`, `ai.encode` |
| `GeminiIC` | Gemini API key | `ai.analyze`, `ai.brain` |
| `HumanIC` | Human operator | `*` (requires approval) |

### Custom IC

```python
from IAC.ic import IntelligenceCore

class MyIC(IntelligenceCore):
    def process(self, iac_payload):
        # Your inference logic
        return {"status": "ok", "results": {}}
    
    @property
    def capabilities(self):
        return ["ai.analyze", "custom.my_op"]
    
    def is_available(self):
        return True
```

---

## Bridge Adapters

IAC includes bridge adapters that unify existing tool systems without rewriting them.

### Pipeline ↔ IAC

```python
from IAC.bridge import PipelineBridge

# Convert Aether/DNetComm pipeline to IAC plan
pipeline = {"commands": [{"program": "filesystem", "action": "read", "params": {"path": "data.txt"}}]}
iac_plan = PipelineBridge.pipeline_to_iac(pipeline)
# Result: {"plan": [{"op": "filesystem.read", "args": {"path": "data.txt"}}]}

# Convert IAC plan back to pipeline format
pipeline_back = PipelineBridge.iac_to_pipeline(iac_plan)
```

### D-Net FUNC Tools ↔ IAC

```python
from IAC.bridge import FuncBridge

# Register D-Net FUNC tools as IAC operations
func_map = {"search": search_function, "generate": generate_function}
FuncBridge.register_func_tools(registry, func_map, domain="dnet")
```

> **Note:** This does NOT replace the D-Net LIVE Task system. FUNC tools continue to operate through their native pipeline. The bridge enables local/bridge usage only.

### OPAS Tools ↔ IAC

```python
from IAC.bridge import ToolBridge

# Register OPAS BaseTool instances as IAC operations
ToolBridge.register_tools(registry, opas_tool_registry, domain="tool")
```

---

## Sanitization Protocol

The drop protocol prevents buffer bloat in long-running workflows:

```json
{
  "config": {
    "drop": ["encoded", "raw_html"]
  }
}
```

After each AI processing step, keys listed in `drop` are removed from payload items. This is critical for vision workflows where base64-encoded images would otherwise accumulate.

The sanitizer also enforces payload size limits (default: 50MB) by automatically trimming the largest values when the threshold is exceeded.

---

## Security

### Restricted Operations

`eval`, `exec`, `shell.run`, and `shell.pipe` are blocked by default. To enable:

```json
{
  "config": {
    "security": {
      "allow_eval": true
    }
  }
}
```

Even when enabled, these operations only execute when the plan's `source` is trusted (`local`, `cli`, `human`). Plans received from remote sources (`dnet_live`, `api`) never execute restricted operations without explicit human approval.

### Granular Access Control (Allowlist/Denylist)

For tighter environment control (e.g. public D-Net SaaS integrations), you can provide granular security rules in the security context during execution:

```json
{
  "config": {
    "security": {
      "allow_eval": false,
      "allowed_domains": ["core", "ai", "web"],
      "denied_ops": ["sys.exec", "sys.fs_write"]
    }
  }
}
```

- **`allowed_domains`**: If provided, only operations belonging to these domains (or explicit op strings) are permitted.
- **`allowed_ops`**: If provided, ONLY the exact operation strings in this list are permitted.
- **`denied_ops`**: Any operation in this list will be explicitly blocked, overriding any allowlist.

### Submodule Integration

IAC is designed to be maintained as the **single source of truth**. When integrating IAC into other projects (like Aether or DNet Add-ons), include it as a submodule or shared package. 
Do not duplicate the IAC codebase. Instead, use Python imports (e.g. `import sys; sys.path.append('../IAC'); from IAC import execute`) to harness the core logic.

### Source Trust Levels

| Source | Trust Level | Can Execute Restricted Ops |
|:---|:---|:---|
| `local`, `cli`, `human` | Trusted | Yes (when `allow_eval=true`) |
| `aether` | Trusted | Yes (when `allow_eval=true`) |
| `dnet_live`, `api` | Untrusted | No (requires human approval) |
| `pipeline` | Conditional | Depends on pipeline origin |

---

## Module Reference

| Module | Purpose |
|:---|:---|
| `__init__.py` | Public API exports |
| `protocol.py` | Payload envelope, normalization, version handling |
| `schema.py` | JSON Schema validation, security checks, `_phase` support |
| `registry.py` | Extensible operation registration and dispatch |
| `resolver.py` | File/URL/payload reference resolution (`data_payload`, `final_payload`) |
| `sanitizer.py` | Drop protocol, payload size management |
| `encoder.py` | Base64 encoding for vision/binary data |
| `inference.py` | Provider-agnostic AI inference pipeline |
| `ic.py` | Intelligence Core interface and implementations |
| `runner.py` | Plan executor with `{{interpolation}}`, `foreach`/`if`/`repeat` control flow |
| `agent_tools.py`| AI task functions: `task_encode`, `task_ai_process`, `task_ai_plan` |
| `bridge.py` | Adapters for Pipeline, FUNC, and OPAS tool systems |
| `iac.py` | Universal CLI entry point, operation bootstrapping |
| `schemas/iac_payload.json` | Formal JSON Schema definition |

---

## Aether Integration

Aether serves as the primary IAC Gateway with these endpoints:

| Endpoint | Method | Description |
|:---|:---|:---|
| `/api/iac/execute` | POST | Execute an IAC plan |
| `/api/iac/validate` | POST | Validate without executing |
| `/api/iac/registry` | GET | List available operations |
| `/api/iac/status` | GET | Protocol version, IC availability |

---

## Architecture

```
D-Net Cyberspace Ecosystem
├── IAC Protocol (Structured Data Exchange)
│   ├── Schema Validation
│   ├── Operation Registry
│   ├── Intelligence Core Router
│   ├── Security Layer
│   └── Sanitization Protocol
│
├── D-Net Nodes (Each speaks IAC)
│   ├── Aether (Client / Gateway)
│   ├── D-Net LIVE (Cloud / Personas)
│   ├── SiteGen (Site Builder)
│   └── OPAS (Operational Node — rebuild target)
│
└── Intelligence Cores (Swappable Batteries)
    ├── Ollama (Local)
    ├── Gemini (Cloud)
    ├── D-Net Personas (Cloud)
    └── Human (Manual)
```

---

## Control Flow Constructs

IAC plans support declarative control flow for complex orchestration. All constructs are recursive — they can be nested arbitrarily.

### `{{token}}` Interpolation
All string values in `args` are resolved against the live `data_payload` at execution time.

```json
{
  "data_payload": { "source_url": "https://example.com", "slug": "example" },
  "plan": [
    { "op": "web.goto", "args": { "url": "{{fb_url}}/photos" } },
    { "op": "sys.exec", "args": { "cmd": "python3 sitegen.py --build {{slug}}" } }
  ]
}
```

Supports dot notation for nested access: `{{tab.route}}`, `{{image_0.src}}`, `{{tabs.0}}`.

### `foreach`
Iterates over an array in the payload.

```json
{
  "op": "foreach",
  "items": "info_tabs",
  "as": "tab",
  "index": "_index",
  "steps": [
    { "op": "web.goto", "args": { "url": "{{fb_url}}/{{tab.route}}" } },
    { "op": "web.eval", "args": { "code": "...", "payload_key": "{{tab.key}}" } }
  ]
}
```

### `if`
Conditional branching with evaluation operators: `exists`, `not_exists`, `==`, `>`, `<`, `>=`, `<=`.

```json
{
  "op": "if",
  "condition": "fb_about exists",
  "then": [{ "op": "log", "args": { "message": "Got about data" } }],
  "else": [{ "op": "log", "args": { "message": "Missing about data" } }]
}
```

### `repeat`
Numeric iteration.

```json
{ "op": "repeat", "count": 5, "index": "_i", "steps": [ ... ] }
```

### `_phase` Markers
Annotation-only steps for plan readability. Skipped during execution.

```json
{"_phase": "=== PHASE 1: Collect source media ==="}
```

---

## Example pipelines

The `pipelines/` directory holds reusable task configurations — standard IAC
workflow JSON, no additional orchestrator needed.

```bash
python3 iac.py pipelines/sandbox.json
```

Pipelines that drive other D-Net Lab products (SiteGen and similar) ship with
those products rather than here, so this repository stays about IAC itself.

## Recent updates

- Host-supplied path resolver: a host with several named roots and per-root
  permissions can now own containment policy, while IAC keeps its simple
  single-directory fallback.
- Public extension API (`IAC.Domain`) with entry-point discovery, reserved core
  namespaces and capability declarations. See EXTENDING.md.
- Directory listings are filtered through the host's resolver, so a listing
  cannot leak what a read would refuse.

