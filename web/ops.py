# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
import os, sys, json, time, threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, Optional

try:
    from .webagent import WebAgent
except ImportError:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    from web.webagent import WebAgent

_agent_instance: Optional[WebAgent] = None

# ONE thread owns the browser, for the life of the process.
#
# Playwright's SYNC api may only be driven from the thread that started it.
# Aether runs turns on a pool, so op 1 of a turn could start the browser on
# worker A and op 2 could touch it from worker B -- and once worker A retired,
# the failure read "cannot switch to a different thread (which happens to have
# exited)". Nothing was wrong with the browser or the plan; the calls simply
# arrived from the wrong place.
#
# Every web operation is therefore marshalled onto this single worker. It is
# created once, never replaced, and outlives any individual turn, so the
# browser it owns stays usable across a whole session. The caller still blocks
# on the result, so ordering and error handling are exactly as before.
_web_thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="iac_web")


def _on_browser_thread(fn, *args, **kwargs):
    """Run `fn` on the thread that owns the browser and return its result."""
    if threading.current_thread().name.startswith("iac_web"):
        return fn(*args, **kwargs)      # already home; do not deadlock
    return _web_thread.submit(fn, *args, **kwargs).result()

#: None = whatever config says (headless, normally). True/False = the person
#: asked to watch, or asked to stop watching.
#:
#: A browser that pops open on every lookup is intolerable, so headless stays
#: the default and nothing changes it implicitly. But "pull up the pricing
#: page" is a request to SEE something, and answering it with a text summary
#: of a page nobody can look at is the wrong shape of help. So the window is
#: switchable, deliberately, on request.
_visible_override = None


def browser_is_visible(config: dict = None) -> bool:
    """Whether the next launch will show a window."""
    if _visible_override is not None:
        return bool(_visible_override)
    web_cfg = ((config or {}).get("web") or {})
    return not web_cfg.get("headless", True)


def set_browser_visible(visible: bool, context: dict = None) -> dict:
    """Show or hide the browser, carrying the session and page across.

    Playwright fixes headless at launch, so this relaunches. The two things
    worth keeping are kept: cookies (saved on close, reloaded on start, which
    is why a signed-in session survives) and wherever the page had got to.

    Relaunching an unchanged state would be a visible flicker for nothing, so
    a no-op returns early.
    """
    global _visible_override, _agent_instance

    config = (context or {}).get("config", {}) or {}
    already = browser_is_visible(config)
    if bool(visible) == already and _agent_instance is not None:
        return {"status": "ok", "visible": already, "changed": False,
                "message": "The browser is already %s."
                           % ("visible" if already else "running out of sight")}

    was_at = None
    if _agent_instance is not None and not getattr(_agent_instance, "closed", True):
        try:
            url = _agent_instance.page.url
            if url and not url.startswith("about:"):
                was_at = url
        except Exception:
            was_at = None
        try:
            # close() saves the storage state, so the next launch is still
            # signed in to whatever this one was.
            _agent_instance.close()
        except Exception:
            pass
        _agent_instance = None

    _visible_override = bool(visible)

    agent = get_web_agent(context)
    if was_at:
        try:
            agent.execute("goto", {"url": was_at})
        except Exception:
            pass

    return {"status": "ok", "visible": bool(visible), "changed": True,
            "url": was_at,
            "message": ("The browser window is open and on screen%s. You are "
                        "both looking at the same page."
                        % (" at %s" % was_at if was_at else "")) if visible else
                       ("The browser is out of sight again%s; it keeps working "
                        "exactly the same."
                        % (" (still at %s)" % was_at if was_at else ""))}


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
        if _visible_override is not None:
            # Copied rather than mutated: the caller's config is not ours to
            # edit, and a stale override written into it would outlive the
            # request that asked for it.
            config = dict(config or {})
            web_cfg = dict(config.get("web") or {})
            web_cfg["headless"] = not _visible_override
            config["web"] = web_cfg
        _agent_instance = WebAgent(config)
    return _agent_instance

def close_web_agent():
    # Teardown touches the context too, so it goes home as well. Closing from
    # a cleanup thread is what produced an alarming traceback after runs that
    # had otherwise succeeded.
    return _on_browser_thread(_close_web_agent)


def _close_web_agent():
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
            # Hop to the browser's own thread before touching anything
            # Playwright owns -- including creating the agent in the first
            # place, so the browser is born on the thread that will drive it.
            return _on_browser_thread(_handle, args, context)

        def _handle(args, context):
            agent = get_web_agent(context)
            
            # Auto-routing for 'analyze' if no payload_key is provided
            if op_name == "analyze" and not args.get("payload_key"):
                # ...but not onto a page that was never loaded. A fresh browser
                # sits on about:blank, so this snapped pure white and sent it
                # to a vision model, which spent twelve seconds describing a
                # white rectangle. Twice. The agent learned nothing and tried
                # again. An empty page is a precondition failure, not a
                # picture, and saying so is instant.
                current = ""
                try:
                    current = agent.page.url or ""
                except Exception:
                    current = ""
                if (not current) or current.startswith("about:"):
                    return {"status": "error",
                            "error": "No page is loaded, so there is nothing "
                                     "to analyse. Use web.search for a lookup, "
                                     "or web.goto a URL first."}
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
        "search": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search the web for"},
                "limit": {"type": "integer", "description": "Max results", "default": 8}
            },
            "required": ["query"]
        },
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
        ("goto", "Navigate to a URL and return its title and opening text"),
        ("click", "Click an element by selector"),
        ("type", "Type value into an element"),
        ("wait", "Wait for specified milliseconds"),
        ("scroll", "Scroll the page"),
        ("tour", "Execute an automated guided tour"),
        ("snap", "Take a screenshot"),
        ("analyze", "Analyze the CURRENTLY LOADED page via AI -- goto or search first"),
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
        ("brain", "Run the AI sweep over every matching payload item"),
        # Named the way an agent asks for it. Without this, "look up X on the
        # web" had no operation to land on: it analysed the blank browser
        # twice, then emitted `web.search` anyway and had the call dropped as
        # unregistered.
        ("search", "Search the web and return result titles, links and snippets")
    ]

    for op_name, desc in web_ops:
        registry.register(
            name=op_name,
            domain="web",
            description=desc,
            parameters=web_schemas.get(op_name, {"type": "object", "properties": {}}),
            handler=make_web_handler(op_name)
        )

    # SHOWING THE WORK.
    #
    # Registered separately because these do not go through the WebAgent
    # command table -- they replace the agent underneath it.
    def _show(args, context=None):
        return _on_browser_thread(set_browser_visible, True, context)

    def _hide(args, context=None):
        return _on_browser_thread(set_browser_visible, False, context)

    def _where(args, context=None):
        config = (context or {}).get("config", {}) or {}
        visible = browser_is_visible(config)
        url = None
        agent = _agent_instance
        if agent is not None and not getattr(agent, "closed", True):
            try:
                url = agent.page.url
            except Exception:
                url = None
        return {"status": "ok", "visible": visible, "url": url,
                "message": ("The browser is on screen%s -- they can see what "
                            "you are doing." % (" at %s" % url if url else ""))
                           if visible else
                           ("The browser is working out of sight%s. Use "
                            "web.show if they want to watch or take over."
                            % (" (at %s)" % url if url else ""))}

    for name, desc, fn in (
            ("show",
             "Bring the browser window on screen, keeping the page and the "
             "signed-in session you already have. Use it when they ask to SEE "
             "something, want to watch, or need to take over -- logging in, "
             "clicking something you should not decide alone. Browsing is "
             "invisible by default; this is how it stops being.",
             _show),
            ("hide",
             "Put the browser back out of sight. Work continues exactly the "
             "same, without a window in their way.",
             _hide),
            ("visible",
             "Whether the browser is on screen right now, and what page it is "
             "on.",
             _where)):
        registry.register(name=name, domain="web", description=desc,
                          parameters={"type": "object", "properties": {},
                                      "required": []},
                          handler=fn)

    registry.alias("web_agent", "web.goto") # Backward compatibility if needed
    for _spoken, _target in (("web.open_window", "web.show"),
                             ("browser.show", "web.show"),
                             ("web.headful", "web.show"),
                             ("browser.hide", "web.hide"),
                             ("web.headless", "web.hide")):
        registry.alias(_spoken, _target)
