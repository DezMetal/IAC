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

__all__ = ["suggest", "register_discovery_operations"]

_WORD = re.compile(r"[a-z0-9_.]+")

# Words that match everything and therefore distinguish nothing.
_STOP = frozenset("""
a an the and or of to for in on at by with from is are be do does can could
i you it this that these those my your please help me want need would like
some make get set
""".split())

_SYNONYMS = {
    "run": {"exec", "execute", "start", "launch"},
    "exec": {"run", "execute", "start", "launch"},
    "execute": {"run", "exec", "start", "launch"},
    "look": {"search", "find", "check", "inspect"},
    "search": {"find", "look", "query", "discover"},
    "find": {"search", "look", "locate", "discover"},
    "read": {"open", "view", "show", "display"},
    "write": {"save", "create", "store"},
    "delete": {"remove", "erase", "clear"},
    "remove": {"delete", "erase", "clear"},
    "navigate": {"go", "goto", "visit", "open", "browse"},
    "browse": {"navigate", "visit", "open", "web"},
    "click": {"press", "tap", "select"},
    "capture": {"screenshot", "snap", "grab"},
    "screenshot": {"capture", "snap", "grab"},
}


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
        syns = _SYNONYMS.get(word)
        if syns:
            words.update(syns)
    return words


def _spoken_words(text):
    """The words a person actually said, with punctuation taken off.

    `_WORD` keeps `.` inside a token on purpose, so `filesystem.read` and
    `dnet.live` survive tokenising in one piece. The cost is that a word at
    the end of a sentence keeps its full stop: "make a beat with strudel."
    tokenises to `strudel.`, which is not equal to `strudel`.

    Both bonuses in `suggest` compared against the raw list, so naming a
    domain earned its +4 only when nothing followed the word. Whisper ends
    every transcription with a full stop, and people naturally put the domain
    last -- "with strudel.", "on the web." -- so on VOICE INPUT the strongest
    signal a person can give was being thrown away almost every time.
    Measured: "strudel" scored 5 and "make a little beat or music with
    strudel." scored 2, under the threshold, so nothing was offered at all
    and she went off and invented a drum grid with ai.plan instead.
    """
    out = set()
    for word in _WORD.findall(str(text or "").lower()):
        out.add(word)
        out.update(part for part in word.replace(".", " ").split() if part)
    return out


def _available(registry):
    """Every operation this host has RIGHT NOW, keyed by qualified name."""
    try:
        return {op["op"]: op for op in registry.list_operations()}
    except Exception:
        return {}


def suggest(goal, registry, limit=6, min_score=0):
    """Operations that look like they fit the goal. Guidance, never a plan.

    Scored on overlap between the words in the goal and the words in the name,
    domain and description of each operation. Description-only matches are
    weighted at half to prevent common verbs from dragging in every domain.
    """
    ops = _available(registry)
    wanted = _tokens(goal)
    if not ops:
        return {"goal": goal, "operations": [], "chains": [],
                "note": "No operations are registered on this host."}

    scored = []
    lowered = str(goal or "").lower()
    spoken_words = _spoken_words(goal)
    for name, spec in ops.items():
        domain, _, bare = name.partition(".")
        name_tokens = _tokens("%s %s" % (name, domain))
        desc_tokens = _tokens(spec.get("description", ""))
        haystack = name_tokens | desc_tokens
        hits = wanted & haystack
        if not hits:
            continue
        # Require at least one word the user actually said to match
        if not (hits & spoken_words):
            continue
        # Name/domain hits count full, description-only hits count half
        name_hits = hits & name_tokens
        desc_only_hits = hits - name_tokens
        score = len(name_hits) + len(desc_only_hits) * 0.5
        # Naming the operation outright is not a coincidence.
        if name.lower() in lowered or bare in wanted:
            score += 5
        # Nor is naming its domain.
        if domain and domain in spoken_words:
            score += 4
        scored.append((score, name, spec))

    scored.sort(key=lambda row: (-row[0], row[1]))
    operations = []
    for _score, name, spec in scored[:limit]:
        if min_score and _score < min_score:
            continue
        schema = spec.get("parameters") or {}
        operations.append({
            "op": name,
            "score": _score,
            "description": spec.get("description", ""),
            "args": list(schema.get("properties") or {}),
            "required": list(schema.get("required") or []),
        })


    return {
        "goal": goal,
        "operations": operations,
        "note": ("GUIDANCE, not instruction. These operations exist on this "
                 "host and their descriptions resemble the goal. The MATCHES "
                 "carry no order -- they are ranked by wording, and reading "
                 "them as a sequence would have you run a script before "
                 "writing it. Check your available skills. "
                 "You decide what the task actually needs."),
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
