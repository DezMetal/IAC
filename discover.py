# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
"""Capability discovery: say what you are trying to do, get operations back.

WHY THIS EXISTS
---------------
Watching agents work, the same failure kept appearing in different costumes.
One asked to run a script, called it execute_shell, was refused, and concluded
out loud that it had no permission to run anything -- sys.exec had been
available the whole time. Another analysed a blank browser twice at twelve
seconds each, then invented web.search. Neither is a reasoning failure. It is
what happens when the only way to find an operation is to already know its
name.

Nothing here uses a model. It is string matching over the registry that is
actually loaded, so it can only ever suggest operations that exist right now,
on this host, with these extensions. That is the point: an agent guessing at
its capabilities is guessing; an agent reading this is looking.

GUIDANCE, NOT INSTRUCTION
-------------------------
Every result says so, in the payload the agent reads. A suggestion is a place
to start, not a plan to carry out -- the caller knows what it is trying to do,
and this only knows which words look similar. Chains especially: they are the
shape such tasks usually take, not the shape THIS task must take.
"""

import re

__all__ = ["suggest", "register_discovery_operations", "RECIPES"]

_WORD = re.compile(r"[a-z0-9_.]+")

# Words that match everything and therefore distinguish nothing.
_STOP = frozenset("""
a an the and or of to for in on at by with from is are be do does can could
i you it this that these those my your please help me want need would like
""".split())

# Vocabulary the registry does not contain but people use constantly. Maps a
# spoken word onto the words operations are actually described with.
_SYNONYMS = {
    "run": "exec shell command",
    "execute": "exec shell command",
    "shell": "exec command",
    "terminal": "exec command",
    "script": "exec write file",
    "search": "search web query",
    "google": "search web",
    "look": "read snap capture see",
    "see": "capture snap analyze screen",
    "screen": "capture snap screenshot desktop",
    "screenshot": "capture snap screen",
    "browse": "goto web page",
    "website": "goto web page url",
    "url": "goto web page",
    "page": "goto extract web",
    "download": "goto write file",
    "save": "write file",
    "remember": "datastore set",
    "recall": "datastore get",
    "note": "write file",
    "picture": "capture snap image",
    "image": "capture snap analyze",
    "summarise": "process analyze brain",
    "summarize": "process analyze brain",
    "analyse": "analyze process",
    "folder": "list directory",
    "directory": "list",
}

# The shapes these tasks usually take. Every operation named here is checked
# against the live registry before it is offered, so a recipe naming something
# this host does not have simply does not appear.
RECIPES = (
    (("search", "look up", "find out", "google", "web search"),
     ["web.search"],
     "One call. Returns titles, links and snippets."),
    (("read a page", "website", "what does the site say", "browse", "url"),
     ["web.goto", "web.extract"],
     "goto returns the title and opening text; extract pulls specific elements when you need more than the opening."),
    (("scrape", "pull data from a site", "collect from the web"),
     ["web.goto", "web.extract", "web.brain"],
     "brain runs the AI sweep over whatever extract left in the payload."),
    (("what is on my screen", "look at the screen", "screenshot"),
     ["sense.capture", "ai.process"],
     "capture writes an image to the payload; ai.process is what actually looks at it. A capture on its own is material, not an answer."),
    (("write a script", "run a script", "make a program", "create a script",
      "write a program", "build a script", "script to", "script that",
      "write some code", "run it", "run the code"),
     ["filesystem.write", "sys.exec"],
     "exec runs in the workspace, so a file written there is runnable by name."),
    (("read a file", "what is in", "open the file"),
     ["filesystem.read"],
     "Paths are ordinary paths, relative to a root."),
    (("process a file", "parse the csv", "analyse a file", "read these csv",
      "summarise a file", "go through the data", "read the csv"),
     ["filesystem.read", "ai.process"],
     "read puts the content in the payload; ai.process is what reasons over "
     "it. For anything repetitive, write a script instead -- cheaper, and "
     "repeatable."),
    (("list files", "what files", "what is in the folder"),
     ["filesystem.list"],
     "Use it before guessing at a filename."),
    (("remember this", "save for later", "keep a note of"),
     ["datastore.set"],
     "Survives the conversation; datastore.get reads it."),
    (("what do i remember", "recall", "look up what i saved"),
     ["datastore.list", "datastore.get"],
     "list first if unsure of the key."),
    (("fill in a form", "log in to a site", "click through"),
     ["web.goto", "web.type", "web.click"],
     "web.sandbox hands the browser to a person when a step needs a human."),
)


def _tokens(text):
    """Words worth matching on, with common synonyms folded in."""
    words = set()
    for word in _WORD.findall(str(text or "").lower()):
        if word in _STOP or len(word) < 2:
            continue
        words.add(word)
        for part in word.replace(".", " ").split():
            if part not in _STOP and len(part) > 1:
                words.add(part)
        extra = _SYNONYMS.get(word)
        if extra:
            words.update(extra.split())
    return words


def _available(registry):
    """Every operation this host has RIGHT NOW, keyed by qualified name."""
    try:
        return {op["op"]: op for op in registry.list_operations()}
    except Exception:
        return {}


def suggest(goal, registry, limit=6):
    """Operations that look like they fit the goal. Guidance, never a plan.

    Scored on overlap between the words in the goal and the words in the name,
    domain and description of each operation. Nothing cleverer, because
    anything cleverer would need a model, and answering without one is the
    entire point.
    """
    ops = _available(registry)
    wanted = _tokens(goal)
    if not ops:
        return {"goal": goal, "operations": [], "chains": [],
                "note": "No operations are registered on this host."}

    scored = []
    lowered = str(goal or "").lower()
    for name, spec in ops.items():
        haystack = _tokens("%s %s %s" % (name, spec.get("domain", ""),
                                         spec.get("description", "")))
        hits = wanted & haystack
        if not hits:
            continue
        score = len(hits)
        domain, _, bare = name.partition(".")
        # Naming the operation outright is not a coincidence.
        if name.lower() in lowered or bare in wanted:
            score += 5
        # Nor is naming its domain. "look up X on the WEB" should not rank a
        # screenshot above a search just because "look" resembles "capture";
        # the domain is the strongest signal a person gives about where they
        # think the answer lives.
        if domain and domain in _WORD.findall(lowered):
            score += 4
        # A word that only reached the operation through a synonym is a weaker
        # signal than one the person actually said.
        spoken = set(_WORD.findall(lowered))
        if not (hits & spoken):
            score -= 2
        scored.append((score, name, spec))

    scored.sort(key=lambda row: (-row[0], row[1]))
    operations = []
    for _score, name, spec in scored[:limit]:
        schema = spec.get("parameters") or {}
        operations.append({
            "op": name,
            # How well this matched, so a caller can tell a real hit from the
            # best of a bad set. Without it every query returns its top few
            # rows and "hey how are you" looks as answerable as "read the log".
            "score": _score,
            "description": spec.get("description", ""),
            "args": list(schema.get("properties") or {}),
            "required": list(schema.get("required") or []),
        })

    chains = []
    for keywords, steps, why in RECIPES:
        if not any(k in lowered for k in keywords):
            continue
        if not all(step in ops for step in steps):
            continue          # never suggest what this host cannot run
        chains.append({"steps": steps, "why": why})

    return {
        "goal": goal,
        "operations": operations,
        "chains": chains,
        "note": ("GUIDANCE, not instruction. These operations exist on this "
                 "host and their descriptions resemble the goal. The MATCHES "
                 "carry no order -- they are ranked by wording, and reading "
                 "them as a sequence would have you run a script before "
                 "writing it. Only a suggested chain is ordered, and even that "
                 "is the shape such tasks usually take, not the shape this one "
                 "must take. You decide what the task actually needs."),
    }


def register_discovery_operations(registry):
    """Expose discovery as an ordinary operation. Returns how many."""
    if registry is None:
        return 0

    def _search(args, context=None):
        goal = (args or {}).get("goal") or (args or {}).get("query") or ""
        if not str(goal).strip():
            return {"status": "error",
                    "error": "iac.search needs a goal to look for."}
        try:
            limit = int((args or {}).get("limit", 6) or 6)
        except (TypeError, ValueError):
            limit = 6
        found = suggest(goal, registry, limit=limit)
        lines = ["Operations that may fit %r." % goal,
                 "MATCHES (no order -- ranked by how closely the words fit, "
                 "NOT the sequence to run them in):"]
        for item in found["operations"]:
            hint = ", ".join(item["args"][:6]) or "no arguments"
            lines.append("  %s(%s) -- %s" % (item["op"], hint,
                                             item["description"]))
        if not found["operations"]:
            lines.append("  (nothing matched -- the operation index is the "
                         "full list; this only matches words)")
        for chain in found["chains"]:
            lines.append("SUGGESTED ORDER (this one IS a sequence): %s"
                         % " -> ".join(chain["steps"]))
            lines.append("         %s" % chain["why"])
        lines.append(found["note"])
        return {"status": "ok", "data": found,
                "message": chr(10).join(lines)}

    registry.register(
        "search", "iac",
        "Find operations that fit a goal you describe. Guidance, not "
        "instruction -- returns real operations available on this host.",
        {"type": "object",
         "properties": {
             "goal": {"type": "string",
                      "description": "What you are trying to do, in words"},
             "limit": {"type": "integer", "description": "Max operations",
                       "default": 6}},
         "required": ["goal"]},
        _search)
    return 1
