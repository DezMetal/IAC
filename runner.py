# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
"""
IAC Runner — Unified plan executor.

Validates, resolves, and executes IAC plans step by step.
Routes operations to appropriate handlers via the registry.
Produces structured history + final_payload output.
"""

import copy
import json
import os
import re
import time
from typing import Any, Dict, List, Optional

try:
    from .protocol import normalize_envelope, get_security_context
    from .schema import validate_plan, check_security
    from .resolver import resolve_full_envelope, resolve_data_payload
    from .sanitizer import sanitize_payload, sanitize_step_result, get_payload_stats
    from .registry import get_registry
except ImportError:
    from protocol import normalize_envelope, get_security_context
    from schema import validate_plan, check_security
    from resolver import resolve_full_envelope, resolve_data_payload
    from sanitizer import sanitize_payload, sanitize_step_result, get_payload_stats
    from registry import get_registry


class PlanAborted(Exception):
    """A step marked `on_error: abort` failed.

    Most plan steps are advisory -- a scroll that found nothing, a validation
    that reported warnings -- and stopping on them would make plans brittle,
    so the default stays "log it and carry on". But some steps are load-bearing:
    if media injection fails there is nothing to build a site around, and every
    expensive generation step after it produces a confident, complete, wrong
    result. Those steps say so, and this is how they stop the run.
    """

    def __init__(self, op: str, index: int, detail: str):
        self.op, self.index, self.detail = op, index, detail
        super().__init__(f"step {index} '{op}' failed: {detail}")


def execute(source, context: dict = None, resume: bool = False, resume_from: Any = None) -> dict:
    if isinstance(source, str) and os.path.isfile(source):
        envelope = resolve_full_envelope(source)
    elif isinstance(source, dict):
        envelope = normalize_envelope(copy.deepcopy(source))
        dp = envelope.get("data_payload", envelope.get("config", {}).get("data_payload", {}))
        envelope["data_payload"] = resolve_data_payload(dp) if isinstance(dp, str) else (dp or {})
    else:
        return {"error": "Invalid source: must be a file path or dict", "history": []}

    valid, errors = validate_plan(envelope)
    if not valid:
        return {"error": f"Validation failed: {errors}", "history": []}

    sec_ctx = get_security_context(envelope)
    if context:
        sec_ctx.update(context)

    safe, warnings = check_security(envelope, sec_ctx)
    if not safe:
        return {"error": f"Security check failed: {warnings}", "history": []}

    return _execute_plan(envelope, sec_ctx, resume=resume, resume_from=resume_from)


def _resolve_ref(key: str, payload: dict):
    """Resolve a dotted key path against the payload. Supports array indexing (e.g. tabs.0)."""
    parts = key.split(".")
    current = payload
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, (list, tuple)):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def _qsep(value) -> str:
    """"?" or "&" -- whichever correctly appends a query parameter to this URL.

    A plan that hardcodes one of them works for exactly half of its inputs.
    `https://facebook.com/profile.php?id=123` needs "&"; `.../nws1733` needs
    "?", and getting it wrong does not error -- the page loads, the scrape
    finds nothing, and the site gets built around media that never arrived.
    """
    s = str(value or "")
    if not s:
        return "?"
    if s.endswith(("?", "&")):
        return ""
    return "&" if "?" in s else "?"


def _slugify(value) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return s or "item"


# Pure, side-effect-free transforms usable as {{key|filter}} inside any
# interpolated string. Deliberately a closed set: interpolation is not a
# template language and must never become one.
_FILTERS = {
    "qsep":  _qsep,
    "upper": lambda v: str(v).upper(),
    "lower": lambda v: str(v).lower(),
    "slug":  _slugify,
    "trim":  lambda v: str(v).strip(),
    "json":  lambda v: json.dumps(v, ensure_ascii=False),
    "count": lambda v: str(len(v)) if hasattr(v, "__len__") else "0",
}


def _resolve_expr(expr: str, payload: dict):
    """Resolve `key`, `key.path`, or `key|filter[|filter]` against the payload."""
    if "|" not in expr:
        return _resolve_ref(expr.strip(), payload)

    parts = [p.strip() for p in expr.split("|")]
    value = _resolve_ref(parts[0], payload)
    for name in parts[1:]:
        fn = _FILTERS.get(name)
        if fn is None:
            # Unknown filter: leave the token untouched rather than guessing.
            return None
        try:
            value = fn(value)
        except Exception:
            return None
    return value


def _interpolate_value(value, payload: dict):
    """Recursively resolve {{key}}, $OUTPUT, and $STORE{key} tokens against the live payload."""
    if isinstance(value, str):
        # Legacy full-value: $OUTPUT -> last step's meaningful output
        if value.strip() == "$OUTPUT":
            resolved = payload.get("_output")
            return resolved if resolved is not None else value

        # Legacy full-value: $STORE{key} -> payload key lookup
        store_full = re.fullmatch(r"\$STORE\{([^}]+)\}", value.strip())
        if store_full:
            resolved = _resolve_ref(store_full.group(1), payload)
            return resolved if resolved is not None else value

        # IAC full-value: {{key}} -> payload key lookup
        full_match = re.fullmatch(r"\{\{([^{}]+)\}\}", value.strip())
        if full_match:
            resolved = _resolve_expr(full_match.group(1), payload)
            return resolved if resolved is not None else value

        # Inline replacements (legacy then IAC)
        if "$OUTPUT" in value:
            out_val = payload.get("_output", "")
            value = value.replace("$OUTPUT", str(out_val) if out_val is not None else "")
        if "$STORE{" in value:
            value = re.sub(r"\$STORE\{([^}]+)\}", lambda m: str(_resolve_ref(m.group(1), payload) or m.group(0)), value)

        def _replacer(m):
            resolved = _resolve_expr(m.group(1), payload)
            return str(resolved) if resolved is not None else m.group(0)
        return re.sub(r"\{\{([^{}]+)\}\}", _replacer, value)

    if isinstance(value, list):
        return [_interpolate_value(item, payload) for item in value]

    if isinstance(value, dict):
        return {k: _interpolate_value(v, payload) for k, v in value.items()}

    return value


def _interpolate_args(args, payload: dict):
    """Walk an entire args dict/list and resolve all {{key}} references."""
    return _interpolate_value(args, payload)


def _eval_condition(condition: str, payload: dict) -> bool:
    """Evaluate a condition string against the payload.
    
    Supported forms:
      "key"                   -> truthy check on payload[key]
      "key == value"          -> equality (string comparison)
      "key != value"          -> inequality
      "key > value"           -> numeric greater than
      "key < value"           -> numeric less than
      "key >= value"          -> numeric gte
      "key <= value"          -> numeric lte
      "key exists"            -> key is present in payload
      "key not_exists"        -> key is NOT present in payload
    """
    condition = condition.strip()

    for op_token, op_fn in [
        ("!=", lambda a, b: str(a) != str(b)),
        (">=", lambda a, b: float(a) >= float(b)),
        ("<=", lambda a, b: float(a) <= float(b)),
        ("==", lambda a, b: str(a) == str(b)),
        (">",  lambda a, b: float(a) > float(b)),
        ("<",  lambda a, b: float(a) < float(b)),
    ]:
        if op_token in condition:
            left, right = condition.split(op_token, 1)
            left_val = _resolve_ref(left.strip(), payload)
            right_val = _resolve_ref(right.strip(), payload)
            if right_val is None:
                right_val = right.strip()
            if left_val is None:
                return False
            try:
                return op_fn(left_val, right_val)
            except (ValueError, TypeError):
                return False

    if condition.endswith(" exists"):
        key = condition.rsplit(" ", 1)[0].strip()
        return _resolve_ref(key, payload) is not None

    if condition.endswith(" not_exists"):
        key = condition.rsplit(" ", 2)[0].strip()
        return _resolve_ref(key, payload) is None

    val = _resolve_ref(condition, payload)
    return bool(val)


def align_scraped_images(payload: dict):
    img_keys = []
    for k in list(payload.keys()):
        if k.startswith("image_") and k[6:].isdigit():
            img_keys.append((int(k[6:]), k))
            
    if not img_keys:
        return

    img_keys.sort()
    
    new_images = {}
    for idx, (_, old_key) in enumerate(img_keys):
        new_key = f"image_{idx}"
        new_images[new_key] = payload[old_key]
        
    for _, old_key in img_keys:
        if old_key in payload:
            del payload[old_key]
            
    payload.update(new_images)

def _entry_failed(entry: dict) -> bool:
    """Did a recorded history entry fail? Recurses into control-flow children.

    A foreach reports its own status, not its body's. Judging the loop alone
    means resume happily skips an iteration set in which every single web.eval
    errored -- which looks like a fast resume and is actually a silent hole in
    the payload.
    """
    data = entry.get("data")
    if not isinstance(data, dict):
        return False
    if data.get("status") in ("error", "timeout") or "error" in data:
        return True
    if data.get("exit_code") not in (None, 0):
        return True
    return any(_entry_failed(child) for child in (data.get("steps") or [])
               if isinstance(child, dict))


def _execute_steps(steps: list, payload: dict, config: dict, security_context: dict,
                   registry, history: list, depth: int = 0, existing_history: list = None,
                   resume_from_idx: int = None):
    """Shared step executor used by the main plan loop and all control flow constructs."""
    prefix = "  " * (depth + 1)
    
    for i, step in enumerate(steps):
        align_scraped_images(payload)
        op = step.get("op", "")

        # Phase markers do no work, but they still occupy a slot in the plan.
        # Dropping them from history silently shifts every later entry left,
        # and resume matches history to plan BY INDEX -- so a plan with one
        # bare marker resumes each step into its neighbour's slot.
        if not op and "_phase" in step:
            history.append({"op": None, "data": {"status": "marker",
                                                 "phase": step.get("_phase")},
                            "elapsed": 0})
            continue

        # Top-level resume skip
        if depth == 0 and existing_history and i < len(existing_history):
            if resume_from_idx is not None and i >= resume_from_idx:
                # Do NOT skip, force execution for this and all subsequent steps
                pass
            else:
                old_entry = existing_history[i]
                if old_entry.get("op") == op:
                    if not _entry_failed(old_entry):
                        print(f"{prefix}[RESUMED] Skipping successful step {i+1}: {op} ({old_entry.get('elapsed', 0)}s)")
                        history.append(old_entry)
                        continue

        # --- Control Flow: foreach ---
        if op in ("foreach", "flow.foreach"):
            op_args = step.get("args", step)
            items_key = op_args.get("items", "")
            as_var = op_args.get("as", "_item")
            index_var = op_args.get("index", "_index")
            body = op_args.get("steps", step.get("steps", []))
            
            items = _resolve_ref(items_key, payload)
            if not isinstance(items, (list, tuple)):
                print(f"{prefix}[FOREACH] Error: '{items_key}' is not iterable")
                history.append({"op": "foreach", "data": {"status": "error", "error": f"'{items_key}' not iterable"}, "elapsed": 0})
                continue
            
            print(f"{prefix}[FOREACH] Iterating '{items_key}' ({len(items)} items) as '{as_var}'")
            s = time.time()
            # Child steps record into their OWN list, nested under this entry.
            # Appending them to the parent made top-level history longer than
            # the plan, and since resume pairs history[i] with plan[i], every
            # step after the first loop was compared against the wrong entry --
            # which is why resuming re-ran work that had plainly succeeded.
            child_history = []
            for idx, item in enumerate(items):
                payload[as_var] = item
                payload[index_var] = idx
                _execute_steps(body, payload, config, security_context, registry,
                               child_history, depth + 1)

            # Cleanup loop variables
            payload.pop(as_var, None)
            payload.pop(index_var, None)
            elapsed = round(time.time() - s, 3)
            history.append({"op": op, "data": {"status": "ok", "items": items_key,
                                               "count": len(items),
                                               "steps": child_history},
                            "elapsed": elapsed})
            print(f"{prefix}[FOREACH] Complete ({elapsed:.2f}s)")
            continue

        # --- Control Flow: repeat ---
        if op in ("repeat", "flow.repeat"):
            op_args = step.get("args", step)
            count = op_args.get("count", 0)
            index_var = op_args.get("index", "_i")
            body = op_args.get("steps", step.get("steps", []))
            
            # Resolve count from payload if it's a string reference
            if isinstance(count, str):
                resolved = _resolve_ref(count, payload)
                count = int(resolved) if resolved is not None else 0
            
            print(f"{prefix}[REPEAT] {count} iterations, index as '{index_var}'")
            s = time.time()
            child_history = []
            for idx in range(int(count)):
                payload[index_var] = idx
                _execute_steps(body, payload, config, security_context, registry,
                               child_history, depth + 1)

            payload.pop(index_var, None)
            elapsed = round(time.time() - s, 3)
            history.append({"op": op, "data": {"status": "ok", "count": count,
                                               "steps": child_history},
                            "elapsed": elapsed})
            print(f"{prefix}[REPEAT] Complete ({elapsed:.2f}s)")
            continue

        # --- Control Flow: if ---
        if op in ("if", "flow.if"):
            op_args = step.get("args", step)
            condition = op_args.get("condition", "")
            then_steps = op_args.get("then", step.get("then", []))
            else_steps = op_args.get("else", step.get("else", []))
            
            # Interpolate condition string so {{key}} refs resolve
            condition = _interpolate_value(condition, payload) if isinstance(condition, str) else condition
            result = _eval_condition(str(condition), payload)
            
            branch = "then" if result else "else"
            target = then_steps if result else else_steps
            
            print(f"{prefix}[IF] '{condition}' -> {result} (taking {branch})")
            s = time.time()
            child_history = []
            if target:
                _execute_steps(target, payload, config, security_context, registry,
                               child_history, depth + 1)
            elapsed = round(time.time() - s, 3)
            history.append({"op": op, "data": {"status": "ok", "condition": str(condition),
                                               "result": result, "branch": branch,
                                               "steps": child_history},
                            "elapsed": elapsed})
            continue

        # --- Standard Operation ---
        raw_args = step.get("args", {})
        args = _interpolate_args(raw_args, payload)

        s_time = time.time()

        if registry.has(op):
            ctx = {
                "payload": payload,
                "config": config,
                "security": security_context,
                "step_index": i,
                "envelope": {}
            }
            for sk, sv in security_context.items():
                if sk not in ctx:
                    ctx[sk] = sv
            result = registry.execute(op, args, ctx)
        else:
            result = _fallback_execute(op, args, payload, config)

        if isinstance(result, dict):
            result = sanitize_step_result(result, config.get("drop", ["encoded"]))

        elapsed = time.time() - s_time
        history.append({
            "op": op,
            "data": copy.deepcopy(result) if isinstance(result, dict) else result,
            "elapsed": round(elapsed, 3)
        })

        payload["_last_result"] = copy.deepcopy(result) if isinstance(result, dict) else result
        if isinstance(result, dict) and result.get("data") is not None:
            payload["_output"] = result["data"]
        elif isinstance(result, dict) and result.get("message"):
            payload["_output"] = result["message"]

        print(f"{prefix}[{i+1}] {op} ({elapsed:.2f}s)")

        # A step may declare itself load-bearing. sys.exec is judged on its
        # exit code as well as its status, because a script that fails is the
        # usual way a pipeline step fails.
        if str(step.get("on_error", "")).lower() == "abort" and isinstance(result, dict):
            failed = result.get("status") in ("error", "timeout") or "error" in result
            if not failed and result.get("exit_code") not in (None, 0):
                failed = True
            if failed:
                detail = result.get("error") or result.get("stderr") \
                    or f"exit code {result.get('exit_code')}"
                print(f"{prefix}[ABORT] {op}: {str(detail)[:400]}")
                raise PlanAborted(op, i + 1, str(detail)[:400])


def _execute_plan(envelope: dict, security_context: dict, resume: bool = False, resume_from: Any = None) -> dict:
    registry = get_registry()
    plan = envelope.get("plan", [])
    config = envelope.get("config", {})
    payload = envelope.get("data_payload", {})

    # The config block gets the same {{key}} treatment as step arguments.
    # Without this an `output` of "pipelines/{{slug}}_results.json" is taken
    # literally and every project's run overwrites one absurdly-named file --
    # the per-client results the path was clearly written to produce never
    # exist. Only data_payload is in scope here: nothing has run yet, so
    # referencing a step result would resolve to nothing anyway.
    config = _interpolate_value(config, payload)
    envelope["config"] = config

    history = []
    output_path = config.get("output")
    
    existing_history = []
    if resume and output_path and os.path.exists(output_path):
        try:
            with open(output_path, "r", encoding="utf-8") as f:
                old_data = json.load(f)
                old_payload = old_data.get("final_payload", {})
                existing_history = old_data.get("history", [])
                payload.update(old_payload)
                print(f"[SYSTEM] Resuming from existing results. Pre-loaded {len(payload)} keys into payload.")
        except Exception as e:
            print(f"[WARN] Failed to load resume data: {e}")

    resume_from_idx = None
    if resume_from is not None and existing_history:
        rf_str = str(resume_from).strip()
        # 1. Try step number index (1-indexed for CLI users)
        try:
            val = int(rf_str)
            if 1 <= val <= len(plan):
                resume_from_idx = val - 1
                print(f"[SYSTEM] Force-resuming execution starting at step {val}: '{plan[resume_from_idx].get('op', 'marker')}'")
        except ValueError:
            pass
        
        # 2. Try phase/operation name match
        if resume_from_idx is None:
            for idx, step in enumerate(plan):
                if "_phase" in step and rf_str.lower() in str(step["_phase"]).lower():
                    resume_from_idx = idx
                    print(f"[SYSTEM] Force-resuming execution starting at phase match: '{step['_phase']}' (step {idx+1})")
                    break
                elif "op" in step and (rf_str.lower() == str(step["op"]).lower() or rf_str.lower() == str(step["op"].split('.')[-1]).lower()):
                    resume_from_idx = idx
                    print(f"[SYSTEM] Force-resuming execution starting at operation match: '{step['op']}' (step {idx+1})")
                    break

    start_time = time.time()
    print(f"[SYSTEM] IAC Runner v2.0 | {len(plan)} operations | Source: {envelope.get('source', 'local')}")

    aborted = None
    try:
        _execute_steps(plan, payload, config, security_context, registry, history,
                       existing_history=existing_history, resume_from_idx=resume_from_idx)
    except PlanAborted as e:
        # Everything up to the failure is still worth keeping: the results file
        # is what `--resume` reads, so a run that dies at step 14 can be picked
        # up at step 14 instead of re-scraping and re-describing from scratch.
        aborted = e

    drop_list = config.get("drop", ["encoded"])
    sanitize_payload(payload, drop_list)

    total_time = round(time.time() - start_time, 2)
    print(f"[SYSTEM] Complete in {total_time}s | Payload: {get_payload_stats(payload)}")

    output = {
        "history": history,
        "final_payload": payload,
        "meta": {
            "iac_version": envelope.get("iac_version", "2.0"),
            "source": envelope.get("source", "local"),
            "total_time": total_time,
            "operations": len(plan)
        }
    }

    if aborted is not None:
        output["error"] = str(aborted)
        output["meta"]["aborted_at_step"] = aborted.index

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=4, default=str)
        print(f"[SYSTEM] Saved to {output_path}")

    if aborted is not None:
        print(f"[SYSTEM] Plan aborted at step {aborted.index} ({aborted.op}). "
              f"Fix the cause and re-run with --resume to continue from there.")

    return output


def _fallback_execute(op: str, args: dict, payload: dict, config: dict) -> dict:
    if op == "push":
        key = args.get("payload_key", f"data_{len(payload)}")
        payload[key] = args.get("data", {})
        return {"status": "ok", "key": key}

    if op == "dump_payload":
        path = args.get("path")
        if path:
            # No `import json` here: a function-local import makes `json` a
            # local name for the WHOLE function, so every other branch that
            # touches the module-level json raises UnboundLocalError.
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"final_payload": payload}, f, indent=4, default=str)
            return {"status": "ok", "path": path}
        return {"status": "error", "error": "No path provided"}

    if op == "pull":
        key = args.get("payload_key", "")
        if key in payload:
            return {"status": "ok", "data": payload[key]}
        return {"status": "error", "error": f"Key '{key}' not in payload"}

    if op == "merge":
        data = args.get("data", {})
        if isinstance(data, dict):
            payload.update(data)
        return {"status": "ok", "merged_keys": list(data.keys()) if isinstance(data, dict) else []}

    if op == "set":
        key = args.get("payload_key") or args.get("key")
        if not key:
            return {"status": "error", "error": "set requires 'payload_key'"}
        payload[key] = args.get("value", "")
        return {"status": "ok", "key": key}

    if op == "append":
        # Accumulate across loop iterations. Without this, a foreach that
        # generates one section of a document per pass has nowhere to put the
        # results: every iteration writes the same output_key and the last one
        # wins, so a ten-section page arrives as its final section.
        key = args.get("payload_key") or args.get("key")
        if not key:
            return {"status": "error", "error": "append requires 'payload_key'"}
        if "value" in args:
            piece = args["value"]
        elif args.get("from_key"):
            piece = _resolve_ref(args["from_key"], payload)
            if piece is None:
                return {"status": "error",
                        "error": f"Payload key not found: '{args['from_key']}'"}
        else:
            return {"status": "error", "error": "append requires 'value' or 'from_key'"}

        existing = payload.get(key)
        if isinstance(existing, list):
            existing.append(piece)
            return {"status": "ok", "key": key, "count": len(existing)}

        sep = args.get("sep", "\n")
        base = "" if existing is None else str(existing)
        payload[key] = (base + sep + str(piece)) if base else str(piece)
        return {"status": "ok", "key": key, "length": len(payload[key])}

    if op == "parse_json":
        # An AI step stores its output as text. flow.foreach needs a real list,
        # and a plan should not have to round-trip through a file to get one.
        src = args.get("from_key") or args.get("payload_key")
        dest = args.get("into") or args.get("payload_key") or src
        raw = _resolve_ref(src, payload) if src else None
        if raw is None:
            return {"status": "error", "error": f"Payload key not found: '{src}'"}
        if isinstance(raw, (dict, list)):
            payload[dest] = raw
            return {"status": "ok", "key": dest, "type": type(raw).__name__}
        text = str(raw).strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r'(\{[\s\S]*\}|\[[\s\S]*\])', text)
            if not m:
                return {"status": "error", "error": f"'{src}' is not JSON"}
            try:
                parsed = json.loads(m.group(1))
            except json.JSONDecodeError as e:
                return {"status": "error", "error": f"'{src}' is not JSON: {e}"}
        payload[dest] = parsed
        return {"status": "ok", "key": dest, "type": type(parsed).__name__,
                "count": len(parsed) if hasattr(parsed, "__len__") else 1}

    if op == "drop":
        keys = args.get("keys", [])
        removed = []
        for k in keys:
            if k in payload:
                del payload[k]
                removed.append(k)
        return {"status": "ok", "removed": removed}

    if op == "wait":
        ms = args.get("ms", 1000)
        time.sleep(ms / 1000)
        return {"status": "ok"}

    if op == "log":
        msg = args.get("message", "")
        level = args.get("level", "info")
        print(f"    [LOG:{level.upper()}] {msg}")
        dump_key = args.get("dump_key")
        if dump_key and dump_key in payload:
            val = payload[dump_key]
            preview = str(val)[:500]
            print(f"    [DUMP:{dump_key}] {preview}")
        return {"status": "ok", "message": msg}

    if op == "noop":
        return {"status": "ok"}

    return {"error": f"Unregistered operation: '{op}'", "status": "error"}


def execute_from_cli():
    import argparse
    parser = argparse.ArgumentParser(description="IAC Runner — Execute IAC plans")
    parser.add_argument("config", help="Path to IAC plan-config JSON file")
    parser.add_argument("-o", "--output", help="Override output path")
    parser.add_argument("--source", default="cli", help="Source identifier")
    parser.add_argument("--allow-eval", action="store_true", help="Enable eval operations")
    args = parser.parse_args()

    ctx = {
        "source": args.source,
        "allow_eval": args.allow_eval,
        "source_trusted": args.source in ("cli", "local", "human")
    }

    if args.output:
        import json
        with open(args.config, 'r', encoding='utf-8') as f:
            data = json.load(f)
        data.setdefault("config", {})["output"] = args.output
        result = execute(data, context=ctx)
    else:
        result = execute(args.config, context=ctx)

    if result.get("error"):
        print(f"\n[ERROR] {result['error']}")
        return 1
    return 0


if __name__ == "__main__":
    exit(execute_from_cli())
