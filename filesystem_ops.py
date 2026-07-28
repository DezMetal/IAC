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
                            return {"status": "error", "error": f"File not found: '{path}'"}
                        with open(target_path, 'r', encoding='utf-8') as f:
                            content = f.read()

                    if target_path.endswith('.json'):
                        try:
                            content = json.loads(content)
                        except json.JSONDecodeError:
                            pass
                    
                    payload["_output"] = content
                    return {"status": "success", "data": content}

                elif action == "write":
                    path = args.get("path")
                    content = args.get("content", "")
                    
                    if not isinstance(path, str) or not path:
                        return {"status": "error", "error": "Invalid or missing 'path' parameter"}
                    
                    target_path, err = _resolve_and_validate(path, workspace_dir, resolver, "write")
                    if err:
                        return {"status": "error", "error": err}

                    if isinstance(content, (dict, list)):
                        content = json.dumps(content, indent=2)
                    else:
                        content = str(content)

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

                    os.makedirs(os.path.dirname(target_path), exist_ok=True)

                    with open(target_path, 'w', encoding='utf-8') as f:
                        f.write(content)

                    payload["_output"] = content
                    return {"status": "success", "data": {"path": target_path, "content_length": len(content)}}

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
                "path": {"type": "string", "description": "The path of the file to read, relative to workspace."}
            },
            "required": ["path"]
        },
        "write": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The path of the file to write to, relative to workspace."},
                "content": {"description": "The content to write to the file."}
            },
            "required": ["path", "content"]
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
        ("read", "Reads the entire content of a specified file."),
        ("write", "Writes content to a specified file."),
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
