# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
import os
import time

try:
    import pyautogui
    HAS_PYAUTOGUI = True
except (ImportError, Exception):
    HAS_PYAUTOGUI = False

def register_desktop_operations(registry):
    """Registers desktop automation commands as IAC operations."""
    if not HAS_PYAUTOGUI:
        print("[WARN] 'pyautogui' is not installed. Desktop operations (desktop.*) are disabled.")
        return
        
    # We use 'desktop.' prefix for all desktop operations
    
    def make_desktop_handler(op_name):
        def handler(args, context):
            payload = context.get("payload", {}) if context else {}
            
            try:
                if op_name == "snap":
                    path = args.get("path") or f"desktop_snap_{int(time.time())}.png"
                    path = os.path.abspath(path)

                    # DEPRECATED, and deliberately not deleted. `sense.capture`
                    # supersedes this: it can address a second monitor, a
                    # region or a camera, where this can only ever return the
                    # whole primary desktop.
                    #
                    # The NAME survives because plans, pipelines and skills in
                    # the wild call it, and breaking those to make a point
                    # about naming is not an upgrade. The IMPLEMENTATION does
                    # not: it delegates, so there is one screen-capture path in
                    # the ecosystem rather than two that drift apart. Two
                    # capture implementations is exactly the redundancy that
                    # produced an unpoliced fallback in Aether's media layer.
                    screenshot = None
                    try:
                        from .sense_ops import grab_screen
                    except ImportError:
                        try:
                            from sense_ops import grab_screen
                        except ImportError:
                            grab_screen = None
                    if grab_screen is not None:
                        screenshot = grab_screen(0)
                    if screenshot is None:
                        screenshot = pyautogui.screenshot()
                    screenshot.save(path)

                    key = args.get("payload_key") or f"image_{len(payload)}"
                    payload[key] = {"src": path}
                    return {"status": "ok", "key": key, "path": path}
                    
                elif op_name == "click":
                    x = args.get("x")
                    y = args.get("y")
                    button = args.get("button", "left")
                    clicks = args.get("clicks", 1)
                    
                    if x is not None and y is not None:
                        pyautogui.click(x=int(x), y=int(y), clicks=clicks, button=button)
                    else:
                        pyautogui.click(clicks=clicks, button=button)
                    return {"status": "ok", "action": "click"}
                    
                elif op_name == "type":
                    text = args.get("text", "")
                    interval = args.get("interval", 0.0)
                    pyautogui.write(text, interval=interval)
                    return {"status": "ok", "action": "type", "length": len(text)}
                    
                elif op_name == "hotkey":
                    keys = args.get("keys", [])
                    if isinstance(keys, str):
                        keys = [k.strip() for k in keys.split(",") if k.strip()]
                    if keys:
                        pyautogui.hotkey(*keys)
                    return {"status": "ok", "action": "hotkey"}
                    
                elif op_name == "move":
                    x = args.get("x", 0)
                    y = args.get("y", 0)
                    duration = args.get("duration", 0.0)
                    relative = args.get("relative", True)
                    
                    if relative:
                        pyautogui.move(int(x), int(y), duration=duration)
                    else:
                        pyautogui.moveTo(int(x), int(y), duration=duration)
                    return {"status": "ok", "action": "move"}
                
                return {"status": "error", "error": f"Unknown desktop operation: {op_name}"}
                
            except Exception as e:
                return {"status": "error", "error": f"Desktop op '{op_name}' failed: {str(e)}"}
                
        return handler

    desktop_schemas = {
        "snap": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to save screenshot"},
                "payload_key": {"type": "string", "description": "Payload key to save under"}
            }
        },
        "click": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X coordinate"},
                "y": {"type": "integer", "description": "Y coordinate"},
                "button": {"type": "string", "description": "Mouse button (left/right/middle)", "default": "left"},
                "clicks": {"type": "integer", "description": "Number of clicks", "default": 1}
            }
        },
        "type": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to type"},
                "interval": {"type": "number", "description": "Seconds between key presses", "default": 0.0}
            },
            "required": ["text"]
        },
        "hotkey": {
            "type": "object",
            "properties": {
                "keys": {"type": "array", "items": {"type": "string"}, "description": "List of keys (or comma separated string)"}
            },
            "required": ["keys"]
        },
        "move": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X coordinate"},
                "y": {"type": "integer", "description": "Y coordinate"},
                "duration": {"type": "number", "description": "Movement duration in seconds", "default": 0.0},
                "relative": {"type": "boolean", "description": "Move relative to current position", "default": True}
            },
            "required": ["x", "y"]
        }
    }

    desktop_ops = [
        ("snap", "Screenshot of the whole primary desktop (delegates to "
                 "sense.capture). For detail, use sense.capture with a "
                 "square region crop instead of reading a full-screen image; "
                 "sense.capture can also address any screen or a camera"),
        ("click", "Click the mouse at specific coordinates or current location"),
        ("type", "Type text simulating keyboard input"),
        ("hotkey", "Press a combination of keys"),
        ("move", "Move the mouse cursor to coordinates")
    ]

    for op_name, desc in desktop_ops:
        registry.register(
            name=op_name,
            domain="desktop",
            description=desc,
            parameters=desktop_schemas.get(op_name, {}),
            handler=make_desktop_handler(op_name)
        )
