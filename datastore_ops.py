# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
import json

def register_datastore_operations(registry):
    """Registers datastore operations as IAC native operations."""

    def make_datastore_handler(action):
        def handler(args, context):
            datastore = context.get("datastore") if context else None
            token = context.get("aether_token") if context else None
            
            if not datastore:
                return {"status": "error", "error": "No datastore provided in context."}
            if not token:
                return {"status": "error", "error": "No aether_token provided in context."}
            
            try:
                if action == "set":
                    key = args.get("key")
                    value = args.get("value")
                    
                    if not isinstance(key, str) or not key:
                        return {"status": "error", "error": "Invalid 'key' parameter"}
                    if value is None:
                        return {"status": "error", "error": "'value' parameter is required"}

                    value_to_store = value
                    if isinstance(value, str):
                        try:
                            value_to_store = json.loads(value)
                        except json.JSONDecodeError:
                            pass

                    datastore.set(token, key, value_to_store)
                    if context and "payload" in context:
                        context["payload"][key] = value_to_store
                    return {"status": "success", "message": f"Set '{key}'.", "data": {key: value_to_store}}

                elif action == "get":
                    key = args.get("key")
                    if not isinstance(key, str) or not key:
                        return {"status": "error", "error": "Invalid 'key' parameter"}

                    value = datastore.get(token, key)
                    if context and "payload" in context:
                        context["payload"][key] = value
                    return {"status": "success", "message": f"Retrieved '{key}'.", "data": value}

                elif action == "delete":
                    key = args.get("key")
                    if not isinstance(key, str) or not key:
                        return {"status": "error", "error": "Invalid 'key' parameter"}

                    success = datastore.delete(token, key)
                    if success:
                        return {"status": "success", "message": f"Deleted '{key}'.", "data": {"key": key, "deleted": True}}
                    else:
                        return {"status": "error", "error": f"Key '{key}' not found."}

                elif action == "list":
                    all_data = datastore.get_all_for_project(token)
                    return {"status": "success", "message": "Retrieved all entries.", "data": all_data}

                return {"status": "error", "error": f"Unknown action: {action}"}
                
            except Exception as e:
                return {"status": "error", "error": str(e)}
                
        return handler

    datastore_schemas = {
        "get": {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"]
        },
        "set": {
            "type": "object",
            "properties": {"key": {"type": "string"}, "value": {}},
            "required": ["key", "value"]
        },
        "delete": {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"]
        },
        "list": {
            "type": "object",
            "properties": {}
        }
    }

    datastore_ops = [
        ("get", "Retrieves a value for a given key."),
        ("set", "Sets a value for a given key."),
        ("delete", "Deletes a key-value pair."),
        ("list", "Lists all key-value pairs in the datastore.")
    ]

    for op_name, desc in datastore_ops:
        registry.register(
            name=op_name,
            domain="datastore",
            description=desc,
            parameters=datastore_schemas.get(op_name, {}),
            handler=make_datastore_handler(op_name)
        )
