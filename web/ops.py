# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
import os, sys, json, time
from typing import Dict, Any, Optional

try:
    from .webagent import WebAgent
except ImportError:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    from web.webagent import WebAgent

_agent_instance: Optional[WebAgent] = None

def get_web_agent(context: dict = None) -> WebAgent:
    global _agent_instance
    if _agent_instance is not None:
        if getattr(_agent_instance, "closed", False):
            _agent_instance = None
        elif hasattr(_agent_instance, "page") and (_agent_instance.page is None or getattr(_agent_instance.page, "is_closed", lambda: False)()):
            try:
                _agent_instance.close()
            except Exception:
                pass
            _agent_instance = None

    if _agent_instance is None:
        # Pull configuration from context if available
        config = (context or {}).get("config", {})
        _agent_instance = WebAgent(config)
    return _agent_instance

def close_web_agent():
    global _agent_instance
    if _agent_instance is not None:
        try:
            _agent_instance.close()
        except:
            pass
        _agent_instance = None

def register_web_operations(registry):
    """Registers all WebAgent commands as IAC operations."""
    
    # Mapping of WebAgent command names to IAC operation names
    # Note: We use 'web.' prefix for all browser operations
    
    def make_web_handler(op_name):
        def handler(args, context):
            agent = get_web_agent(context)
            
            # Auto-routing for 'analyze' if no payload_key is provided
            if op_name == "analyze" and not args.get("payload_key"):
                # Take a quick snapshot to the payload
                snap_res = agent._cmd_snap({"payload_key": "live_view"})
                args["payload_key"] = "live_view"

            # CRITICAL: Sync runner payload to agent before execution
            # Since dicts are passed by reference, updates to agent.payload 
            # will reflect back in the runner's master payload.
            if context and "payload" in context:
                agent.payload = context["payload"]

            if op_name in agent.registry:
                # Forward host execution policy the agent commands need.
                # command_aliases lets a host declare that this machine runs
                # `python3`, not `python` -- without it the model's perfectly
                # reasonable `python script.py` fails with an opaque exit code.
                if context:
                    for key in ("command_aliases", "workspace_dir"):
                        if key in context and key not in args:
                            args = {**args, key: context[key]}
                res = agent.registry[op_name](args)
                if op_name == "stop":
                    close_web_agent()
                return res
            return {"status": "error", "error": f"Unknown web operation: {op_name}"}
        return handler

    web_schemas = {
        "goto": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to navigate to (http/https/file/local path)"},
                "wait_until": {"type": "string", "description": "Wait until event (load/domcontentloaded/networkidle)", "default": "domcontentloaded"},
                "timeout": {"type": "integer", "description": "Timeout in milliseconds", "default": 60000}
            },
            "required": ["url"]
        },
        "click": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "Playwright selector or window.IAC_PAYLOAD key"}
            },
            "required": ["selector"]
        },
        "type": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "Playwright selector or window.IAC_PAYLOAD key"},
                "value": {"type": "string", "description": "Value to type"}
            },
            "required": ["selector", "value"]
        },
        "wait": {
            "type": "object",
            "properties": {
                "ms": {"type": "integer", "description": "Milliseconds to wait", "default": 1000}
            },
            "required": ["ms"]
        },
        "scroll": {
            "type": "object",
            "properties": {
                "dir": {"type": "string", "description": "Direction to scroll (up/down/bottom)", "default": "down"},
                "amount": {"type": "string", "description": "Scroll amount expression", "default": "window.innerHeight"},
                "duration": {"type": "integer", "description": "Scroll duration in milliseconds", "default": 0}
            }
        },
        "tour": {
            "type": "object",
            "properties": {
                "attr": {"type": "string", "description": "Tourist waypoint attribute", "default": "data-tour"},
                "duration": {"type": "integer", "description": "Total duration in milliseconds (0 for adaptive)", "default": 0},
                "wait": {"type": "integer", "description": "Wait at each waypoint in milliseconds", "default": 1000}
            }
        },
        "snap": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Destination file path"},
                "full_page": {"type": "boolean", "description": "Capture full page", "default": True},
                "payload_key": {"type": "string", "description": "Key to store image under"}
            }
        },
        "analyze": {
            "type": "object",
            "properties": {
                "payload_key": {"type": "string", "description": "Payload key to analyze (live_view if blank)"},
                "prompt": {"type": "string", "description": "Prompt for the AI model"},
                "model": {"type": "string", "description": "DO NOT fill unless user explicitly requests a specific model override"},
                "provider": {"type": "string", "description": "DO NOT fill unless user explicitly requests a specific provider override"},
                "api_key": {"type": "string", "description": "Override Cloud API Key"},
                "host": {"type": "string", "description": "Override Ollama host"}
            },
            "required": ["prompt"]
        },
        "extract": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "Selector to extract text from", "default": "body"},
                "payload_key": {"type": "string", "description": "Payload key to save under"},
                "attr": {"type": "string", "description": "Optional DOM attribute name to extract instead of innerText"}
            }
        },
        "modify": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "Target element selector"},
                "text": {"type": "string", "description": "Set innerText"},
                "value": {"type": "string", "description": "Set input value"},
                "attr": {"type": "string", "description": "Set attribute name"},
                "val": {"type": "string", "description": "Set attribute value"}
            },
            "required": ["selector"]
        },
        "eval": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "JavaScript code to execute"},
                "payload_key": {"type": "string", "description": "Optional payload key to store result"},
                "file": {"type": "string", "description": "Optional JS file path to read code from"},
                "merge": {"type": "boolean", "description": "Merge object result into payload", "default": False}
            },
            "required": ["code"]
        },

        "save_state": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Destination storage state JSON path. Defaults to the plan's save_storage_state, then storage_state, then auth/session.json."}
            }
        },
        "brain": {
            "type": "object",
            "properties": {
                "prefix": {"type": "string", "description": "Payload key prefix to sweep", "default": "image_"},
                "prompt": {"type": "string", "description": "Prompt applied to each matching item"},
                "output_key": {"type": "string", "description": "Key to store each result under"},
                "provider": {"type": "string", "description": "DO NOT fill unless user explicitly requests a specific provider override"}
            }
        },
        "probe": {
            "type": "object",
            "properties": {
                "payload_key": {"type": "string", "description": "Optional payload key to store result"}
            }
        }
    }

    web_ops = [
        ("goto", "Navigate to a URL"),
        ("click", "Click an element by selector"),
        ("type", "Type value into an element"),
        ("wait", "Wait for specified milliseconds"),
        ("scroll", "Scroll the page"),
        ("tour", "Execute an automated guided tour"),
        ("snap", "Take a screenshot"),
        ("analyze", "Analyze the page or element via AI"),
        ("extract", "Extract data from the DOM"),
        ("heartbeat", "Enable high-FPS canvas heartbeat"),
        ("probe", "Discover interactive elements"),
        ("eval", "Execute custom JavaScript"),
        ("stop", "Finalize the session"),
        ("save_state", "Save browser storage state"),
        ("sandbox", "Enter interactive sandbox mode"),
        # These were reachable from the sandbox HUD but never registered, so a
        # chain exported with either of them failed on replay with
        # "Unregistered operation". Anything the sandbox can do, a plan can do.
        ("modify", "Set text, value or an attribute on a DOM element"),
        ("brain", "Run the AI sweep over every matching payload item")
    ]

    for op_name, desc in web_ops:
        registry.register(
            name=op_name,
            domain="web",
            description=desc,
            parameters=web_schemas.get(op_name, {"type": "object", "properties": {}}),
            handler=make_web_handler(op_name)
        )

    registry.alias("web_agent", "web.goto") # Backward compatibility if needed
