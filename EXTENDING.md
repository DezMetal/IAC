# Extending IAC

IAC ships general operations — filesystem, web, vision, desktop, datastore.
**Anything beyond that belongs to you, not to IAC.** A CRM integration, your
company's internal API, a robot's motor controller: none of those should ever
land in this repository, and none of them have to.

An extension is a **domain** — a named group of operations, with an owner, a
version, and a declared list of what it needs to touch.

---

## Write one

```python
from IAC.extensions import Domain

crm = Domain("crm",
             description="Our customer database",
             author="Example Co",
             version="1.0",
             homepage="https://example.com/iac-crm",
             requires=["network"])

@crm.op("lookup", "Find a customer by email",
        {"email": {"type": "string", "description": "Their address"}},
        required=["email"])
def lookup(args, context):
    record = my_api.get(args["email"])
    return {"status": "ok", "data": record,
            "message": f"Found {record['name']}."}

crm.register()
```

That is the whole surface. Your operation is now `crm.lookup`, callable from
any IAC plan, and visible to any model driving IAC.

If you already have handler functions, use the imperative form:

```python
crm.add("lookup", my_existing_function, "Find a customer",
        {"email": {"type": "string"}}, ["email"])
```

## The handler contract

```python
def handler(args, context) -> dict
```

| | |
|---|---|
| `args` | The caller's arguments, already matched against your schema |
| `context` | What the host supplied — `payload`, `config`, and anything else it chose to pass |
| returns | `{"status": "ok"｜"error", ...}` |

Return `data` for structured results, `message` for text a human or a model
should read, `error` when `status` is `"error"`. Raising is safe — the registry
catches it and reports a clean error — but returning `{"status": "error",
"error": "..."}` gives a better message than a traceback.

**Write the `description` for a model, not for a colleague.** It is what an LLM
reads when deciding whether to call your operation. Say what it does and when
to use it.

## Ship one

Declare an entry point, and IAC finds your extension after `pip install`:

```toml
# pyproject.toml
[project.entry-points."iac.domains"]
crm = "my_package:crm"
```

The host calls `IAC.discover()` once — `bootstrap()` already does — and your
domain is live. Nothing patches IAC.

A failing extension is logged and skipped. It never takes the host down.

## What the host controls

Because an extension runs with the host's privileges, the host — not the
extension — decides three things.

**Reserved names.** These belong to IAC and cannot be taken over by accident:

```
core  filesystem  web  vision  desktop  datastore  ai  sys  prism  iac
```

Registering over one requires `replace=True`. An extension quietly redefining
`filesystem.write` is an attack, not a feature, so it must never be silent.

**Capabilities.** Declare what your domain touches:

| Capability | Means |
|---|---|
| `network` | Makes outbound connections |
| `filesystem` | Reads or writes files |
| `subprocess` | Launches processes |
| `desktop` | Controls the display, input, or screen capture |
| `credentials` | Handles secrets, tokens, or user accounts |

A host in safe mode loads only what it has allowed:

```python
crm.register(safe_mode=True, allow=["network"])   # loads
crm.register(safe_mode=True, allow=[])            # refused, logged
```

This is what lets someone run an extension they have not audited: your domain
states what it needs, and their host says yes or no.

**Provenance.** `IAC.list_extensions()` reports every loaded domain, its
author, version, declared capabilities, and where it was loaded from — so
*"what can this instance actually do"* always has an answer.

## Registering without packaging

A host application that owns its operations can skip entry points entirely and
register at startup:

```python
from IAC.registry import get_registry
my_domain.register(get_registry())
```

This is how D-Net Lab's own `dnet.*` and `aether.*` domains work. They are not
in this repository — one is a private platform API, the other belongs to a
commercial product — and IAC neither knows nor needs to know they exist. That
is the model working as intended: **IAC stays general, and everything specific
stays with whoever it is specific to.**

## Errors you will see

| Message | Cause |
|---|---|
| `'x' is a core IAC domain` | Pick another name, or pass `replace=True` |
| `domain 'x' is already registered` | Two extensions claiming one name |
| `invalid domain name` | Dots and spaces are not allowed — the dot separates domain from operation |
| `declares unknown capabilities` | Typo, or a capability IAC does not model |
| `Refusing domain 'x' in safe mode` | The host did not allow what you declared |

All of these fire at declaration or registration time, never at call time. A
broken extension should be impossible to build, not merely broken once
something invokes it.

---

*IAC — Integrated Agent Core. Created and maintained by D-Net Lab.*
*"With Accessibility Comes Understanding and Inspiration" · "From Mind To Matter"*
