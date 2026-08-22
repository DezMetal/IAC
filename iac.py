#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
"""
Integrated Agent Core (IAC) — Universal Entry Point
Standardized interface for executing IAC plans across all domains (Core, AI, Web).
"""

import os
import sys
import argparse
import json
from pathlib import Path

# Ensure IAC root is in path
ROOT = Path(__file__).parent.absolute()
sys.path.append(str(ROOT))

try:
    from .registry import get_registry
    from .runner import execute
    from . import agent_tools
except ImportError:
    from registry import get_registry
    from runner import execute
    import agent_tools

def search(goal, limit=6, quiet=False):
    """Find operations that fit a goal. Usable from a REPL or any script.

    The discovery OPERATION (iac.search) is for an agent inside a plan. This
    is the same thing for whoever is at a keyboard:

        >>> import iac
        >>> iac.search("create a script to read these csv")

    Bootstraps on first use, because requiring that first is exactly the kind
    of knowledge this function exists to stop needing. Prints the readable
    form and returns the structured one, so it is useful typed and useful
    imported.
    """
    registry = get_registry()
    try:
        loaded = bool(registry.list_operations())
    except Exception:
        loaded = False
    if not loaded:
        bootstrap()
        registry = get_registry()

    try:
        from .discover import suggest
    except ImportError:
        from discover import suggest

    found = suggest(goal, registry, limit=limit)
    if not quiet:
        print("MATCHES (no order -- ranked by wording, NOT a sequence):")
        for item in found["operations"]:
            hint = ", ".join(item["args"][:6]) or "no arguments"
            print("  %s(%s)" % (item["op"], hint))
            if item["description"]:
                print("      %s" % item["description"])
        if not found["operations"]:
            print("  (nothing matched -- try plainer words)")
        for chain in found["chains"]:
            print("SUGGESTED ORDER (this one IS a sequence): %s"
                  % " -> ".join(chain["steps"]))
            print("      %s" % chain["why"])
        print(found["note"])
    return found


def bootstrap(safe_mode=False):
    """Initialize the global registry with all available operations."""
    registry = get_registry()
    
    # 0. Register Core Operations
    core_schemas = {
        "push": {
            "type": "object",
            "properties": {
                "payload_key": {"type": "string", "description": "Key to store under"},
                "data": {"type": "object", "description": "JSON object data to push"}
            },
            "required": ["payload_key", "data"]
        },
        "pull": {
            "type": "object",
            "properties": {
                "payload_key": {"type": "string", "description": "Key to retrieve"}
            },
            "required": ["payload_key"]
        },
        "merge": {
            "type": "object",
            "properties": {
                "data": {"type": "object", "description": "JSON object to fold into root payload"}
            },
            "required": ["data"]
        },
        "drop": {
            "type": "object",
            "properties": {
                "keys": {"type": "array", "items": {"type": "string"}, "description": "List of keys to remove"}
            },
            "required": ["keys"]
        },
        "wait": {
            "type": "object",
            "properties": {
                "ms": {"type": "integer", "description": "Milliseconds to pause", "default": 1000}
            },
            "required": ["ms"]
        },
        "log": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Message to output"},
                "level": {"type": "string", "description": "Log level (info/warn/error)", "default": "info"},
                "dump_key": {"type": "string", "description": "Optional payload key to dump alongside log"}
            },
            "required": ["message"]
        },
        "noop": {
            "type": "object",
            "properties": {}
        },
        "set": {
            "type": "object",
            "properties": {
                "payload_key": {"type": "string", "description": "Key to assign"},
                "value": {"description": "Value to store (any JSON type)"}
            },
            "required": ["payload_key"]
        },
        "append": {
            "type": "object",
            "properties": {
                "payload_key": {"type": "string", "description": "Key to accumulate into. Appends to a list if it holds one, otherwise concatenates as text."},
                "from_key": {"type": "string", "description": "Payload key holding the piece to append"},
                "value": {"description": "Literal piece to append, instead of from_key"},
                "sep": {"type": "string", "description": "Separator used for text accumulation", "default": "\n"}
            },
            "required": ["payload_key"]
        },
        "parse_json": {
            "type": "object",
            "properties": {
                "from_key": {"type": "string", "description": "Payload key holding JSON text (markdown fences tolerated)"},
                "into": {"type": "string", "description": "Key to store the parsed object under. Defaults to from_key."}
            },
            "required": ["from_key"]
        },
        "dump_payload": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File to write the whole payload to, as {\"final_payload\": {...}}"}
            },
            "required": ["path"]
        }
    }

    core_ops = [
        ("push", "Add data to the Universal Payload"),
        ("pull", "Retrieve data from the payload"),
        ("merge", "Fold a dictionary into the root payload"),
        ("set", "Assign a single payload key to a literal value"),
        ("append", "Accumulate a value onto a payload key across loop iterations"),
        ("parse_json", "Parse a payload key holding JSON text into a real object or list"),
        ("dump_payload", "Write the entire payload to a file for another process to read"),
        ("drop", "Remove specific keys from the payload"),
        ("wait", "Pause execution for N milliseconds"),
        ("log", "Output a message to the console"),
        ("noop", "No-operation placeholder")
    ]
    try:
        from .runner import _fallback_execute
    except ImportError:
        from runner import _fallback_execute

    for name, desc in core_ops:
        def make_core_handler(op_name):
            return lambda args, ctx: _fallback_execute(op_name, args, ctx.get("payload", {}), ctx.get("config", {}))
        registry.register(name, "core", desc, core_schemas.get(name, {}), make_core_handler(name))
        registry.alias(name, f"core.{name}")

    # 1. Register AI Operations (from agent_tools)
    def _ai_handler(op_func):
        def handler(args, ctx):
            payload = ctx.get("payload", {})
            
            # Robust configuration resolution
            base_config = ctx.get("config", {})
            ai_config = base_config.get("ai_config", base_config.get("ai_core", {}))
            final_cfg = {**base_config, **ai_config, **args}
            # Keep the step's OWN arguments distinguishable after the merge.
            # Provider profiles need to know which settings the step asked for
            # and which merely came from the plan-wide config -- flattened
            # together, a plan-level `model` outranks the model belonging to
            # the profile the step selected, and the step silently runs on the
            # wrong one.
            final_cfg["_step_args"] = dict(args)

            # AUTO-BATCHING: If no payload_key is provided and it's ai.process, hit everything
            if op_func == agent_tools.task_ai_process and not args.get("payload_key") and not args.get("key"):
                count = agent_tools.task_ai_batch(payload, final_cfg)
                return {"status": "ok", "count": count, "message": f"Processed {count} items"}

            key = args.get("payload_key", args.get("key", "default"))
            
            # Use payload item as info if it exists, otherwise use args
            info = payload.get(key, args)
            if not isinstance(info, dict):
                info = {"data": info}
            
            success = op_func(key, info, final_cfg)
            
            # Ensure the processed info is back in the payload
            if key not in payload or payload[key] is not info:
                payload[key] = info
            
            # Promote AI text output for $OUTPUT / {{_output}} access
            if success:
                out_k = args.get("output_key", "raw_output")
                text_out = info.get(out_k) or info.get("raw_output") or info.get("message") or ""
                if text_out:
                    payload["_output"] = text_out
                return {"status": "ok", "key": key, "message": text_out, "data": text_out}
            else:
                return {"status": "error", "key": key, "error": "AI process failed"}
        return handler

    registry.register(
        name="encode",
        domain="ai",
        description="Encode image or resource to base64",
        parameters={
            "type": "object",
            "properties": {
                "payload_key": {"type": "string", "description": "Payload key of image to encode"}
            },
            "required": ["payload_key"]
        },
        handler=_ai_handler(agent_tools.task_encode)
    )
    
    registry.register(
        name="process",
        domain="ai",
        description="Process data/vision via AI inference",
        parameters={
            "type": "object",
            "properties": {
                "payload_key": {"type": "string", "description": "Payload key to analyze (live_view if blank)"},
                "prompt": {"type": "string", "description": "Prompt for the AI model"},
                "model": {"type": "string", "description": "DO NOT fill unless user explicitly requests a specific model override"},
                "provider": {"type": "string", "description": "DO NOT fill unless user explicitly requests a specific provider override"},
                "api_key": {"type": "string", "description": "Override Cloud API Key"},
                "host": {"type": "string", "description": "Override Ollama host"},
                "temperature": {"type": "number", "description": "Temperature (e.g. 0.2)", "default": 0.2},
                "think": {"type": "boolean", "description": "Enable deep thinking/reasoning", "default": False},
                "options": {"type": "object", "description": "Ollama options (e.g. max_tokens, etc.)"}
            },
            "required": ["prompt"]
        },
        handler=_ai_handler(agent_tools.task_ai_process)
    )
    
    # Make aliases clear and intuitive
    registry.alias("ai.analyze_data", "ai.process")
    registry.alias("analyze_data", "ai.process")
    # ai.analyze is the name agents and pipelines reach for when analysing an
    # image or blob already sitting in the payload. Without this it silently
    # fails to resolve and the step is a no-op.
    registry.alias("ai.analyze", "ai.process")
    registry.alias("ai.vision", "ai.process")
    # 'analyze' by default maps to 'web.analyze' since it operates on the live view/page
    registry.alias("analyze", "web.analyze")
    # Remove ai.brain and unify in ai.process

    # ai.plan — Standalone text inference with payload injection
    def _ai_plan_handler(args, context):
        payload = context.get("payload", {})
        base_config = context.get("config", {})
        ai_cfg = base_config.get("ai_config", base_config.get("ai_core", {}))
        # args stay unmerged: task_ai_plan already falls back to config for
        # everything a step does not state, and it needs the two separable to
        # rank step > profile > plan config.
        return agent_tools.task_ai_plan(args, payload, ai_cfg)

    registry.register(
        name="plan",
        domain="ai",
        description="Standalone AI text inference with payload context injection",
        parameters={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Instruction for AI plan generation"},
                "inject_keys": {"type": "array", "items": {"type": "string"}, "description": "Payload keys to inject as context"},
                "output_key": {"type": "string", "description": "Payload key to store results", "default": "plan_result"},
                "format": {"type": "string", "description": "Expected output format: json|html|css|text (default auto). Pin this when generating HTML/CSS so JSON extraction is not applied to it."},
                "model": {"type": "string", "description": "DO NOT fill unless user explicitly requests a specific model override"},
                "provider": {"type": "string", "description": "DO NOT fill unless user explicitly requests a specific provider override"},
                "api_key": {"type": "string", "description": "Override Cloud API Key"},
                "options": {"type": "object", "description": "Ollama options temperature, think, etc."}
            },
            "required": ["prompt"]
        },
        handler=_ai_plan_handler
    )

    # 2. Register Web Operations
    try:
        try:
            from .web.ops import register_web_operations
        except ImportError:
            from web.ops import register_web_operations
        register_web_operations(registry)
    except ImportError as e:
        print(f"[WARN] Web operations not available: {e}")
        
    # 2.5 Register Desktop Operations
    if not safe_mode:
        try:
            try:
                from .desktop_ops import register_desktop_operations
            except ImportError:
                from desktop_ops import register_desktop_operations
            register_desktop_operations(registry)
        except ImportError as e:
            print(f"[WARN] Desktop operations not available: {e}")
            
        # 2.6 Register Vision Operations
        try:
            try:
                from .vision_ops import register_vision_operations
            except ImportError:
                from vision_ops import register_vision_operations
            register_vision_operations(registry)
        except ImportError as e:
            print(f"[WARN] Vision operations not available: {e}")

        # 2.65 Register Sense Operations
        try:
            try:
                from .sense_ops import register_sense_operations
            except ImportError:
                from sense_ops import register_sense_operations
            register_sense_operations(registry)
        except ImportError as e:
            print(f"[WARN] Sense operations not available: {e}")

        # 2.7 Register Filesystem Operations
        try:
            try:
                from .filesystem_ops import register_filesystem_operations
            except ImportError:
                from filesystem_ops import register_filesystem_operations
            register_filesystem_operations(registry)
        except ImportError as e:
            print(f"[WARN] Filesystem operations not available: {e}")

    # 2.9 Register Datastore Operations
    try:
        try:
            from .datastore_ops import register_datastore_operations
        except ImportError:
            from datastore_ops import register_datastore_operations
        register_datastore_operations(registry)
    except ImportError as e:
        print(f"[WARN] Datastore operations not available: {e}")

    # 2.10 Register Prism Operations
    if not safe_mode:
        try:
            try:
                from .prism_ops import register_prism_operations
            except ImportError:
                from prism_ops import register_prism_operations
            register_prism_operations(registry)
        except ImportError as e:
            print(f"[WARN] Prism operations not available: {e}")

    # 2.11 Third-party domains.
    # Anything product-specific -- a company's internal API, a host application's
    # own operations -- lives in an extension rather than in this repository.
    # Installed packages advertising `iac.domains` are picked up here; a host
    # can also register its domains directly (see EXTENDING.md). In safe mode
    # an extension is loaded only if the capabilities it declares are allowed.
    try:
        try:
            from .extensions import discover
        except ImportError:
            from extensions import discover
        found = discover(registry, safe_mode=safe_mode,
                         allow=[] if safe_mode else None)
        if found:
            print(f"[IAC] Loaded {found} operation(s) from installed extensions")
    except Exception as e:
        print(f"[WARN] Extension discovery skipped: {e}")

    # 3. Register System/Utility Operations
    if not safe_mode:
        def _make_sys_handler(op_name):
            def handler(args, context):
                try:
                    from .web.ops import get_web_agent
                except ImportError:
                    from web.ops import get_web_agent
                agent = get_web_agent(context)
                
                # CRITICAL: Sync runner payload to agent
                if context and "payload" in context:
                    agent.payload = context["payload"]

                # A script written to the workspace has to be runnable from
                # the workspace. Without this the shell inherits the HOST's
                # working directory, so `python3 script.py` fails on a file
                # that was just created successfully -- which reads as the
                # write having failed, and sends the agent back to rewrite a
                # file that was already there.
                if op_name == "exec" and not args.get("cwd"):
                    where = (context or {}).get("workspace_dir")
                    if where:
                        args = {**args, "cwd": where}

                if op_name in agent.registry:
                    return agent.registry[op_name](args)
                return {"status": "error", "error": f"Unknown sys operation: {op_name}"}
            return handler

        sys_schemas = {
            "exec": {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "Shell command to execute"},
                    "cwd": {"type": "string", "description": "Current working directory"},
                    "timeout": {"type": "integer", "description": "Command execution timeout in seconds", "default": 120},
                    "payload_key": {"type": "string", "description": "Optional key to store output under"}
                },
                "required": ["cmd"]
            }
        }

        # sys.exec only. fs_read/fs_write were a second door into the same
        # room as filesystem.read/write, and the second door skipped the host's
        # path policy entirely -- sys.fs_write would happily write outside every
        # configured root while filesystem.write refused the identical path.
        # One way to touch a file, and it is the guarded one.
        registry.register("exec", "sys", "Run a shell command",
                          sys_schemas["exec"], _make_sys_handler("exec"))

    # Add common aliases for backward compatibility with v1 plans
    for op in ["goto", "click", "type", "wait", "scroll", "tour", "snap", "analyze", "extract", "probe", "eval", "stop", "sandbox"]:
        registry.alias(op, f"web.{op}")
    
    # Capability discovery. Registered for EVERY host, safe mode included:
    # it only reads the registry and runs no operation, and an agent that
    # cannot find out what it can do is the failure this whole module exists
    # to stop.
    try:
        try:
            from .discover import register_discovery_operations
        except ImportError:
            from discover import register_discovery_operations
        register_discovery_operations(registry)
    except Exception as _e:
        print(f"    [!] Discovery unavailable: {_e}")

    if not safe_mode:
        registry.alias("exec", "sys.exec")
        # The names a model reaches for when it wants a shell. It asked for
        # `execute_shell`, got "not in the allowed set", and concluded it had
        # no permission to run anything -- then said so, out loud, twice. The
        # operation was there the whole time under a different name. Teaching
        # the registry the synonyms is cheaper than teaching every model the
        # vocabulary, and it fails loudly if the target ever disappears.
        for _spoken in ("execute_shell", "run_command", "shell", "bash",
                        "run_shell", "terminal", "sys.execute", "sys.shell"):
            registry.alias(_spoken, "sys.exec")

    # 4. Register Control Flow Operations (handled natively by runner)
    flow_noop = lambda a, c: {"status": "ok", "note": "handled by runner"}
    flow_schemas = {
        "foreach": {
            "type": "object",
            "properties": {
                "items": {"type": "string", "description": "Payload key pointing to the iterable array"},
                "as": {"type": "string", "description": "Variable name for current item", "default": "_item"},
                "index": {"type": "string", "description": "Variable name for loop index", "default": "_index"},
                "steps": {"type": "array", "description": "Operations to run on each iteration", "default": []}
            },
            "required": ["items"]
        },
        "repeat": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "Number of iterations (or payload key)", "default": 1},
                "index": {"type": "string", "description": "Variable name for loop index", "default": "_i"},
                "steps": {"type": "array", "description": "Operations to repeat", "default": []}
            },
            "required": ["count"]
        },
        "if": {
            "type": "object",
            "properties": {
                "condition": {"type": "string", "description": "Condition expression (e.g. 'key == value', 'key exists')"},
                "then": {"type": "array", "description": "Steps to run if condition is true", "default": []},
                "else": {"type": "array", "description": "Steps to run if condition is false", "default": []}
            },
            "required": ["condition"]
        }
    }
    flow_ops = [
        ("foreach", "Loop over a payload array"),
        ("repeat", "Repeat steps N times"),
        ("if", "Conditional branching based on payload state")
    ]
    for name, desc in flow_ops:
        registry.register(name, "flow", desc, flow_schemas[name], flow_noop)

def main():
    parser = argparse.ArgumentParser(description="IAC Universal Agent — All-in-one execution core")
    parser.add_argument("plan", nargs="?", help="Path to IAC plan (JSON) or raw JSON string")
    parser.add_argument("-o", "--output", help="Override output path")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("--list-ops", action="store_true", help="List all registered operations and exit")
    parser.add_argument("--resume", action="store_true", help="Resume previous execution from output file if it exists")
    parser.add_argument("--resume-from", help="Specify step index, operation name, or phase name to force-resume execution from")
    parser.add_argument("--search", metavar="GOAL",
                        help="Describe what you are trying to do and get "
                             "operations that fit, plus the usual order. "
                             "Guidance, not instruction.")
    parser.add_argument("--limit", type=int, default=6,
                        help="How many matches --search returns (default 6)")
    
    args = parser.parse_args()

    bootstrap()
    registry = get_registry()

    # Discovery before execution: someone reaching for the CLI to ask "what
    # can this thing do about X" should not have to read the whole operation
    # index and match it themselves.
    if args.search:
        search(args.search, limit=args.limit)
        sys.exit(0)

    if args.list_ops:
        print(registry.to_prompt_block())
        sys.exit(0)

    if not args.plan:
        parser.print_help()
        sys.exit(1)

    # Resolve plan
    plan_source = args.plan
    if os.path.isfile(plan_source):
        with open(plan_source, 'r', encoding='utf-8') as f:
            plan_data = json.load(f)
    else:
        try:
            plan_data = json.loads(plan_source)
        except:
            if plan_source.endswith('.json'):
                print(f"[ERROR] File not found: {plan_source}")
            else:
                print(f"[ERROR] Plan must be a valid file path or JSON string.")
            sys.exit(1)

    if args.output:
        plan_data.setdefault("config", {})["output"] = args.output

    # Execute via standardized runner
    exit_code = 0
    try:
        results = execute(plan_data, resume=args.resume or (args.resume_from is not None), resume_from=args.resume_from)
        
        if "error" in results:
            print(f"\n[!] Plan Execution Failed: {results['error']}")
            exit_code = 1
        else:
            print(f"\n[OK] Plan completed successfully.")
    except KeyboardInterrupt:
        print("\n[!] Execution interrupted by user.")
        exit_code = 1
    except Exception as e:
        print(f"\n[!] Fatal Error: {e}")
        import traceback
        if args.verbose:
            traceback.print_exc()
        exit_code = 1
    finally:
        # Snapshot the session on THIS thread, before teardown moves to a
        # worker. Playwright's sync API is bound to the thread that created it,
        # so a save attempted from the cleanup thread below cannot work -- it
        # raises "Cannot switch to a different thread" after the run has
        # otherwise finished. A plan with save_storage_state set but no
        # explicit save_state step would quietly never refresh its session.
        try:
            try:
                from .web.ops import _agent_instance as _agent
            except ImportError:
                from web.ops import _agent_instance as _agent
        except Exception:
            _agent = None

        if _agent and not getattr(_agent, "closed", False):
            # Best effort. Playwright's sync connection may already be parked
            # by this point, in which case the snapshot cannot be taken from
            # here at all -- which is why plans that care put an explicit
            # web.save_state step on the main path instead of relying on this.
            try:
                _target = _agent.web_cfg.get("save_storage_state")
                if _target and not getattr(_agent, "_state_saved", None):
                    _agent._cmd_save_state({"path": _target})
            except Exception as e:
                print(f"[WARN] Session snapshot skipped during shutdown: "
                      f"{str(e).splitlines()[0]}")

            # Close on THIS thread first. The driver is a child node process;
            # os._exit() below kills Python without letting it shut down, and
            # it reports that as an EPIPE traceback long after the run has
            # finished. Closing properly here usually avoids that entirely.
            try:
                _agent.close()
            except Exception:
                pass

        # Backstop: if the main-thread close above hung or never ran, do it on
        # a worker so a wedged browser cannot hold the process open.
        import threading
        def safe_close():
            try:
                try:
                    from .web.ops import _agent_instance
                except ImportError:
                    from web.ops import _agent_instance
                if _agent_instance:
                    _agent_instance.close()
            except: pass
        
        cleanup_thread = threading.Thread(target=safe_close, daemon=True)
        cleanup_thread.start()
        # Budget covers finalizing the recording AND writing save_storage_state.
        # 2s was enough for a video flush but could cut off a session snapshot,
        # and losing a login that took a human to obtain costs far more than a
        # few seconds of shutdown.
        cleanup_thread.join(timeout=15.0)

        # Force terminate — Playwright sync threads can prevent clean exit
        os._exit(exit_code)

if __name__ == "__main__":
    main()
