# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
import os
import json

def register_filesystem_operations(registry):
    """Registers filesystem operations as IAC native operations."""

    def _resolve_and_validate(raw_path, workspace_dir, resolver=None, mode="read"):
        """Resolve a path expression and enforce whatever boundary applies.

        Returns (resolved_path, error_string_or_None).

        A host with a policy richer than "one directory" -- several named roots,
        per-root read/write permissions, a deny list -- supplies a `resolver`
        in the execution context. IAC stays generic: it needs an object with
        `resolve(raw_path, mode) -> (absolute_path, error)` and knows nothing
        about how that decision is reached. `workspace_dir` remains the simple
        single-root fallback for hosts that want nothing more.
        """
        if resolver is not None:
            try:
                return resolver.resolve(raw_path, mode)
            except Exception as e:
                return None, f"Workspace resolver failed on '{raw_path}': {e}"

        if not workspace_dir:
            return os.path.abspath(raw_path), None

        workspace_dir = os.path.abspath(workspace_dir)
        os.makedirs(workspace_dir, exist_ok=True)

        if os.path.isabs(raw_path):
            resolved = os.path.abspath(raw_path)
        else:
            resolved = os.path.abspath(os.path.join(workspace_dir, raw_path))

        if not resolved.startswith(workspace_dir + os.sep) and resolved != workspace_dir:
            return None, f"Access denied: '{raw_path}' resolves outside the workspace boundary."

        return resolved, None

    def _stage(context):
        """Optional write-staging hook supplied by the host.

        A host that wants human review before files change can put a staging
        object in the execution context. IAC stays generic: it only needs an
        object exposing stage_write / stage_delete / read_effective, and knows
        nothing about how review or approval works.

        Aether uses this so an agent's writes land in a shadow copy until the
        user approves them.
        """
        return (context or {}).get("stage")

    def _payload_ref(payload, key_path):
        """Resolve a dotted payload path. Returns None if any hop is missing."""
        current = payload
        for part in str(key_path).split("."):
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, (list, tuple)):
                try:
                    current = current[int(part)]
                except (ValueError, IndexError):
                    return None
            else:
                return None
            if current is None:
                return None
        return current

    def make_filesystem_handler(action):
        def handler(args, context):
            payload = context.get("payload", {}) if context else {}
            workspace_dir = (context or {}).get("workspace_dir", None)
            resolver = (context or {}).get("workspace_resolver", None)
            stage = _stage(context)
            # Who is acting. A host running several participants through one
            # registry passes this so staged work carries its author.
            author = (context or {}).get("author") or "aether"

            try:
                if action == "list":
                    path = args.get("path", ".")
                    if not isinstance(path, str):
                        return {"status": "error", "error": f"Invalid path: {path}"}
                    
                    target_path, err = _resolve_and_validate(path, workspace_dir, resolver)
                    if err:
                        return {"status": "error", "error": err}
                    if not os.path.isdir(target_path):
                        return {"status": "error", "error": f"Path '{path}' is not a valid directory."}
                    
                    items = os.listdir(target_path)
                    if resolver is not None:
                        # A listing must not leak what a read would refuse.
                        # Re-asking the resolver per entry needs no new
                        # interface -- it is the same (path, mode) question,
                        # so IAC still knows nothing about the host's policy.
                        items = [
                            name for name in items
                            if resolver.resolve(os.path.join(target_path, name),
                                                "read")[1] is None
                        ]
                    return {"status": "success", "data": items, "message": f"Found {len(items)} items in {target_path}"}

                elif action == "read":
                    path = args.get("path")
                    if not isinstance(path, str) or not path:
                        return {"status": "error", "error": "Invalid or missing 'path' parameter"}
                    
                    target_path, err = _resolve_and_validate(path, workspace_dir, resolver)
                    if err:
                        return {"status": "error", "error": err}
                    # Read through staging so an agent sees its own pending
                    # edits rather than the stale on-disk version, which would
                    # make it undo itself on the next pass.
                    content = None
                    if stage is not None:
                        try:
                            content = stage.read_effective(path)
                        except Exception:
                            content = None

                    if content is None:
                        if not os.path.isfile(target_path):
                            # Say WHERE it looked. "File not found: notes.md"
                            # is indistinguishable from "the file is gone"
                            # when the real problem is that a relative path
                            # was resolved against a root the caller did not
                            # have in mind. Naming the resolved path turns an
                            # apparent dead end into an obvious correction.
                            resolved = os.path.abspath(target_path).replace(chr(92), "/")
                            hint = ""
                            if not os.path.isabs(path):
                                hint = (" -- that is a RELATIVE path, resolved "
                                        "against the default root. Give the "
                                        "full path instead.")
                            return {"status": "error",
                                    "error": "File not found: '%s' (looked in %s)%s"
                                             % (path, resolved, hint)}
                        with open(target_path, 'r', encoding='utf-8') as f:
                            content = f.read()

                    # A plan reads a file in order to feed it to a later step,
                    # so the content has to land somewhere addressable. Without
                    # this the value is reachable only as {{_output}}, which the
                    # very next operation overwrites.
                    dest_key = args.get("payload_key") or args.get("to_key")

                    if target_path.endswith('.json'):
                        try:
                            content = json.loads(content)
                        except json.JSONDecodeError:
                            pass

                    if dest_key:
                        payload[dest_key] = content

                    # WINDOWED READS.
                    #
                    # A whole file was the only thing on offer, so reading
                    # anything large meant handing the caller more than it
                    # could hold. Downstream that arrives as a truncated blob
                    # with no way to ask for the rest, and the caller either
                    # gives up or reads the same file again expecting a
                    # different answer. A person in that position would just
                    # read it in pieces; the operation has to make that
                    # possible before anyone can be blamed for not doing it.
                    #
                    # Line-based, because lines are what a caller can reason
                    # about and cite. Only applied to text -- parsed JSON is
                    # an object and slicing it by line would be nonsense.
                    total_lines = None
                    truncated = False
                    next_offset = None
                    try:
                        offset = max(0, int(args.get("offset") or 0))
                    except (TypeError, ValueError):
                        offset = 0
                    limit_raw = args.get("limit")
                    try:
                        limit = int(limit_raw) if limit_raw is not None else None
                    except (TypeError, ValueError):
                        limit = None
                    if limit is not None:
                        limit = max(1, limit)

                    # AUTOMATIC CHUNKING, WITH A HARD CAP.
                    #
                    # Asking for a whole file is the natural thing to ask for,
                    # and for most files it is the right answer. For a large
                    # one it hands back more than any caller can hold, and the
                    # damage happens downstream where it is hard to attribute.
                    # So a read with no limit still gets a limit -- enough to
                    # work with, and the result says plainly that there is
                    # more and how to reach it. Nobody has to opt in, and
                    # nobody is asked a question.
                    #
                    # `limit: 0` (or full: true) means literally all of it,
                    # for a caller that has decided it wants that.
                    AUTO_CHUNK_LINES = 400
                    wants_everything = (limit_raw == 0
                                        or bool(args.get("full")))
                    if (limit is None and not wants_everything
                            and isinstance(content, str)
                            and content.count(chr(10)) >= AUTO_CHUNK_LINES):
                        limit = AUTO_CHUNK_LINES

                    if isinstance(content, str) and (offset or limit is not None):
                        lines = content.splitlines()
                        total_lines = len(lines)
                        window = lines[offset:] if limit is None                             else lines[offset:offset + limit]
                        end = offset + len(window)
                        truncated = end < total_lines or offset > 0
                        if end < total_lines:
                            next_offset = end
                        content = chr(10).join(window)

                    payload["_output"] = content
                    result = {"status": "success", "data": content,
                              "payload_key": dest_key}
                    if total_lines is not None:
                        result.update({"path": path, "offset": offset,
                                       "lines_returned": len(content.splitlines()) if content else 0,
                                       "total_lines": total_lines,
                                       "truncated": truncated})
                        if next_offset is not None:
                            # Name the exact next call. "There is more" that
                            # does not say how to get it is not help.
                            result["next_offset"] = next_offset
                            result["more"] = (
                                "%d of %d lines. For the next part call "
                                "filesystem.read with path '%s', offset %d, "
                                "limit %s."
                                % (end, total_lines, path, next_offset,
                                   limit if limit is not None else 200))
                    return result

                elif action == "write":
                    path = args.get("path")

                    # Two content sources. `content` is an inline literal;
                    # `from_key` names a payload key, which is how anything
                    # generated earlier in the plan gets to disk -- an AI step
                    # stores 6KB of HTML under a key, and interpolating that
                    # through `content` would mean pasting the whole document
                    # into the plan file.
                    #
                    # A missing from_key is an ERROR, never an empty file.
                    # Silently writing "" here is what turned a failed
                    # generation step into a 0-byte project.json and let the
                    # rest of the pipeline run on top of the wreckage.
                    if "content" in args:
                        content = args["content"]
                    elif args.get("from_key"):
                        content = _payload_ref(payload, args["from_key"])
                        if content is None:
                            return {"status": "error",
                                    "error": f"Payload key not found: '{args['from_key']}'. "
                                             f"Refusing to write an empty file to '{path}'."}
                    else:
                        return {"status": "error",
                                "error": "No content source: pass either 'content' or 'from_key'."}

                    if not isinstance(path, str) or not path:
                        return {"status": "error", "error": "Invalid or missing 'path' parameter"}

                    target_path, err = _resolve_and_validate(path, workspace_dir, resolver, "write")
                    if err:
                        return {"status": "error", "error": err}

                    if isinstance(content, (dict, list)):
                        content = json.dumps(content, indent=2, ensure_ascii=False)
                    else:
                        content = str(content)

                    # Optional shape check before the file is touched. A model
                    # that hit its token cap returns a document truncated
                    # mid-string; writing that over a valid file destroys the
                    # good copy and the failure only surfaces several steps
                    # later, as a parse error in whatever reads it next.
                    expect = str(args.get("expect", "")).lower()
                    if expect in ("json", "object", "array"):
                        try:
                            parsed = json.loads(content)
                        except json.JSONDecodeError as e:
                            return {"status": "error",
                                    "error": f"Refusing to write '{path}': content is "
                                             f"not valid JSON ({e}). {len(content)} bytes "
                                             f"— likely a truncated generation."}
                        if expect == "object" and not isinstance(parsed, dict):
                            return {"status": "error",
                                    "error": f"Refusing to write '{path}': expected a JSON "
                                             f"object, got {type(parsed).__name__}."}
                        if expect == "array" and not isinstance(parsed, list):
                            return {"status": "error",
                                    "error": f"Refusing to write '{path}': expected a JSON "
                                             f"array, got {type(parsed).__name__}."}

                    # When the host supplies a staging hook, the real file is
                    # never touched here -- the change waits for approval.
                    if stage is not None:
                        try:
                            info = stage.stage_write(path, content, author=author)
                            payload["_output"] = content
                            rel = info.get("path", path)
                            # The host decides whether a write is held for
                            # review or applied directly; report what happened
                            # so the agent knows if the file is on disk yet.
                            applied = bool(info.get("applied"))
                            return {"status": "success",
                                    "staged": not applied,
                                    "data": {"path": rel,
                                             "content_length": len(content),
                                             "staged": not applied},
                                    "message": (f"Wrote '{rel}' ({len(content)} bytes)."
                                                if applied else
                                                f"Staged change to '{rel}' awaiting your approval. "
                                                f"It is NOT on disk yet.")}
                        except Exception as e:
                            return {"status": "error", "error": f"Could not stage write: {e}"}

                    parent = os.path.dirname(target_path)
                    if parent:
                        os.makedirs(parent, exist_ok=True)

                    with open(target_path, 'w', encoding='utf-8') as f:
                        f.write(content)

                    payload["_output"] = content
                    return {"status": "success", "data": {"path": target_path, "content_length": len(content)}}

                elif action == "copy":
                    src = args.get("src") or args.get("from") or args.get("source")
                    dst = args.get("dst") or args.get("to") or args.get("dest")
                    if not isinstance(src, str) or not src:
                        return {"status": "error", "error": "Invalid or missing 'src' parameter"}
                    if not isinstance(dst, str) or not dst:
                        return {"status": "error", "error": "Invalid or missing 'dst' parameter"}

                    src_path, err = _resolve_and_validate(src, workspace_dir, resolver, "read")
                    if err:
                        return {"status": "error", "error": err}
                    dst_path, err = _resolve_and_validate(dst, workspace_dir, resolver, "write")
                    if err:
                        return {"status": "error", "error": err}
                    if not os.path.isfile(src_path):
                        return {"status": "error", "error": f"Source file not found: '{src}'"}

                    with open(src_path, 'r', encoding='utf-8', errors='replace') as f:
                        data = f.read()

                    # Routed through the same staging hook as write, so a host
                    # holding writes for review does not get bypassed by a copy.
                    if stage is not None:
                        try:
                            info = stage.stage_write(dst, data, author=author)
                            applied = bool(info.get("applied"))
                            return {"status": "success", "staged": not applied,
                                    "data": {"src": src_path, "dst": info.get("path", dst),
                                             "content_length": len(data)}}
                        except Exception as e:
                            return {"status": "error", "error": f"Could not stage copy: {e}"}

                    parent = os.path.dirname(dst_path)
                    if parent:
                        os.makedirs(parent, exist_ok=True)
                    with open(dst_path, 'w', encoding='utf-8') as f:
                        f.write(data)

                    return {"status": "success",
                            "data": {"src": src_path, "dst": dst_path, "content_length": len(data)},
                            "message": f"Copied {src} -> {dst} ({len(data)} bytes)"}

                elif action == "grep":
                    path = args.get("path")
                    pattern = args.get("pattern")
                    
                    if not isinstance(path, str) or not path:
                        return {"status": "error", "error": "Invalid 'path' parameter"}
                    if not isinstance(pattern, str) or not pattern:
                        return {"status": "error", "error": "Invalid 'pattern' parameter"}
                        
                    target_path, err = _resolve_and_validate(path, workspace_dir, resolver)
                    if err:
                        return {"status": "error", "error": err}
                    if not os.path.isfile(target_path):
                        return {"status": "error", "error": f"File not found: '{path}'"}

                    matching_lines = []
                    with open(target_path, 'r', encoding='utf-8') as f:
                        for i, line in enumerate(f, 1):
                            if pattern in line:
                                matching_lines.append(f"L{i}: {line.strip()}")
                                
                    return {"status": "success", "data": matching_lines, "message": f"Found {len(matching_lines)} matches"}

                return {"status": "error", "error": f"Unknown action: {action}"}
                
            except Exception as e:
                return {"status": "error", "error": str(e)}
                
        return handler

    filesystem_schemas = {
        "list": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The directory path to list. Relative to workspace. Defaults to current workspace root."}
            }
        },
        "read": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The path of the file to read, relative to workspace."},
                "offset": {"type": "integer", "description": "First line to return (0-based). Use with limit to read a large file in parts."},
                "limit": {"type": "integer", "description": "How many lines to return. Omit and a large file is chunked automatically (400 lines) with next_offset telling you how to continue; pass 0 to force the entire file."},
                "full": {"type": "boolean", "description": "Return the whole file even if it is large. Only when you genuinely need all of it at once."},
                "payload_key": {"type": "string", "description": "Store the file content under this payload key so later steps can reference it as {{key}}."}
            },
            "required": ["path"]
        },
        "write": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The path of the file to write to, relative to workspace."},
                "content": {"description": "Literal content to write. Use 'from_key' instead for anything a previous step generated."},
                "from_key": {"type": "string", "description": "Payload key (dotted paths allowed) holding the content to write. Errors rather than writing an empty file if the key is missing."},
                "expect": {"type": "string", "description": "Validate before writing: 'json', 'object' or 'array'. Refuses the write if the content does not parse — a truncated generation must not overwrite a good file."}
            },
            "required": ["path"]
        },
        "copy": {
            "type": "object",
            "properties": {
                "src": {"type": "string", "description": "Path of the file to copy from."},
                "dst": {"type": "string", "description": "Path to copy to. Parent directories are created."}
            },
            "required": ["src", "dst"]
        },
        "grep": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The path of the file to search in, relative to workspace."},
                "pattern": {"type": "string", "description": "The text pattern to search for."}
            },
            "required": ["path", "pattern"]
        }
    }

    filesystem_ops = [
        ("list", "Lists files and directories in a given path."),
        ("read", "Reads a file. Returns the whole thing by default; pass offset and limit to read a large one in line-sized parts, and the result tells you the next offset."),
        ("write", "Writes content to a specified file, either inline or from a payload key."),
        ("copy", "Copies a file from one path to another, creating parent directories."),
        ("grep", "Searches for a pattern within a file and returns matching lines.")
    ]

    for op_name, desc in filesystem_ops:
        registry.register(
            name=op_name,
            domain="filesystem",
            description=desc,
            parameters=filesystem_schemas.get(op_name, {}),
            handler=make_filesystem_handler(op_name)
        )
