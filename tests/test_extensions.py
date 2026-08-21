"""
Tests for the IAC extension API.

What is being protected here is a boundary, not a feature. IAC is published;
the things built on it are not. If an extension can silently shadow a core
operation, or register capabilities a host said no to, then "IAC is safe to
deploy anywhere" stops being true -- and the failure is quiet.

Run:  python tests/test_extensions.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from IAC.extensions import (  # noqa: E402
    Domain, ExtensionError, list_extensions, forget, CORE_DOMAINS,
)
from IAC.registry import OperationRegistry  # noqa: E402


class Reg(OperationRegistry):
    """A registry isolated from the process-wide singleton."""

    def __new__(cls):
        obj = object.__new__(cls)
        obj._ops = {}
        obj._aliases = {}
        return obj


def _fresh(name="demo", **kw):
    forget(name)
    return Domain(name, **kw), Reg()


# ── declaring ─────────────────────────────────────────────────────────────

def test_a_domain_registers_its_operations():
    d, reg = _fresh()

    @d.op("greet", "Say hello", {"who": {"type": "string"}}, ["who"])
    def greet(args, context):
        return {"status": "ok", "message": f"hello {args['who']}"}

    assert d.register(reg) == 1
    assert reg.has("demo.greet")
    assert reg.execute("demo.greet", {"who": "world"}, {})["message"] == "hello world"
    forget("demo")


def test_the_imperative_form_works_too():
    """Not everyone has a function to decorate at the point of declaration."""
    d, reg = _fresh()
    d.add("ping", lambda args, context: {"status": "ok"}, "Ping")
    assert d.register(reg) == 1
    assert reg.execute("demo.ping", {}, {})["status"] == "ok"
    forget("demo")


def test_the_schema_reaches_the_registry_intact():
    """The schema is what a model sees. If it is dropped, the operation exists
    but nothing knows how to call it."""
    d, reg = _fresh()
    d.add("find", lambda a, c: None, "Find a thing",
          {"q": {"type": "string", "description": "query"}}, ["q"])
    d.register(reg)
    schema = reg.list_operations("demo")[0]
    assert schema["op"] == "demo.find"
    assert schema["description"] == "Find a thing"
    assert schema["parameters"]["properties"]["q"]["description"] == "query"
    assert schema["parameters"]["required"] == ["q"]
    forget("demo")


# ── the boundary ──────────────────────────────────────────────────────────

def test_a_core_domain_cannot_be_shadowed_by_accident():
    """An extension quietly redefining filesystem.write is an attack, not a
    feature. It must be possible, but never silent."""
    reg = Reg()
    for core in ("filesystem", "web", "core"):
        forget(core)
        d = Domain(core)
        d.add("write", lambda a, c: {"status": "ok", "data": "hijacked"})
        try:
            d.register(reg)
            assert False, f"silently shadowed core domain '{core}'"
        except ExtensionError as e:
            assert "core IAC domain" in str(e)

    # Deliberate override remains possible for a host that means it.
    forget("filesystem")
    d = Domain("filesystem")
    d.add("write", lambda a, c: {"status": "ok"})
    assert d.register(reg, replace=True) == 1
    forget("filesystem")


def test_registering_the_same_domain_twice_is_refused():
    """Two extensions claiming one name is a conflict the host must see."""
    d, reg = _fresh()
    d.add("x", lambda a, c: None)
    d.register(reg)
    try:
        d.register(reg)
        assert False, "second registration was silent"
    except ExtensionError as e:
        assert "already registered" in str(e)
    assert d.register(reg, replace=True) == 1
    forget("demo")


def test_safe_mode_refuses_capabilities_the_host_did_not_allow():
    """A host can load an extension it has not audited: the domain declares
    what it touches, and the host says yes or no."""
    reg = Reg()
    forget("risky")
    risky = Domain("risky", requires=["subprocess", "network"])
    risky.add("run", lambda a, c: None)
    assert risky.register(reg, safe_mode=True, allow=[]) == 0
    assert not reg.has("risky.run")

    forget("risky")
    assert risky.register(reg, safe_mode=True,
                          allow=["subprocess", "network"]) == 1
    assert reg.has("risky.run")
    forget("risky")

    # A domain that declares nothing is allowed through safe mode.
    forget("plain")
    plain = Domain("plain")
    plain.add("noop", lambda a, c: None)
    assert plain.register(Reg(), safe_mode=True, allow=[]) == 1
    forget("plain")


def test_malformed_extensions_fail_at_declaration_not_at_call_time():
    """A bad extension should be impossible to build, not merely broken once
    something invokes it."""
    for kwargs, expect in (
        ({"name": "bad.name"}, "no dots or spaces"),
        ({"name": "two words"}, "no dots or spaces"),
        ({"name": ""}, "needs a name"),
        ({"name": "x", "requires": ["telepathy"]}, "unknown capabilities"),
    ):
        try:
            Domain(**kwargs)
            assert False, f"accepted {kwargs}"
        except ExtensionError as e:
            assert expect in str(e), (kwargs, str(e))

    d = Domain("ok")
    for bad, expect in ((("a.b", lambda x, y: None), "may not contain a dot"),
                        (("nope", "not callable"), "not callable")):
        try:
            d.add(*bad)
            assert False, f"accepted {bad}"
        except ExtensionError as e:
            assert expect in str(e)

    d.add("dupe", lambda a, c: None)
    try:
        d.add("dupe", lambda a, c: None)
        assert False, "accepted a duplicate operation"
    except ExtensionError as e:
        assert "already defined" in str(e)


def test_every_core_domain_name_is_actually_reserved():
    """If a domain IAC ships is missing from CORE_DOMAINS, an extension can
    take it over without anyone being warned."""
    for name in ("filesystem", "web", "vision", "desktop", "datastore",
                 "core", "sys", "ai", "prism"):
        assert name in CORE_DOMAINS, name
    assert "dnet" not in CORE_DOMAINS, \
        "dnet is a D-Net Lab extension, not part of published IAC"
    assert "aether" not in CORE_DOMAINS


# ── provenance ────────────────────────────────────────────────────────────

def test_a_host_can_see_what_is_loaded_and_where_it_came_from():
    """"What can this instance actually do" needs an answer."""
    forget("crm")
    reg = Reg()
    crm = Domain("crm", description="Customer database", author="Example Co",
                 version="2.1", requires=["network"])
    crm.add("lookup", lambda a, c: None, "Find a customer")
    crm.register(reg)

    entry = next(e for e in list_extensions() if e["domain"] == "crm")
    assert entry["author"] == "Example Co"
    assert entry["version"] == "2.1"
    assert entry["requires"] == ["network"]
    assert entry["operations"] == ["lookup"]
    assert entry["registered"] == 1
    forget("crm")


def test_an_empty_domain_registers_nothing_rather_than_half_succeeding():
    d, reg = _fresh()
    assert d.register(reg) == 0
    assert len(reg) == 0
    forget("demo")


# ── what IAC itself ships ─────────────────────────────────────────────────

def test_published_iac_does_not_ship_private_domains():
    """The whole point of the extension model. dnet.* is D-Net Lab's platform
    API and aether.* belongs to a commercial product; neither may be present in
    this repository."""
    iac_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for private in ("dnet_ops.py", "aether_ops.py"):
        assert not os.path.exists(os.path.join(iac_dir, private)), \
            f"{private} is still in the IAC repository"


def test_bootstrapping_iac_exposes_no_private_domain():
    """Belt and braces: even if a stray module reappeared, a clean bootstrap
    must not surface dnet.* or aether.* operations."""
    from IAC.iac import bootstrap
    from IAC.registry import get_registry
    bootstrap(safe_mode=False)
    domains = set(get_registry().list_domains())
    assert "dnet" not in domains, "dnet leaked into published IAC"
    assert "aether" not in domains, "aether leaked into published IAC"


def _run():
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:
            failed.append(name)
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - len(failed)}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run())


def test_discovery_suggests_real_operations_and_never_invents_them():
    """An agent that cannot find an operation invents one.

    Observed: execute_shell called and refused while sys.exec sat available,
    and web.search emitted before it existed. Discovery answers that WITHOUT a
    model, by matching words against the registry actually loaded -- so it can
    only ever name operations that exist on this host.
    """
    from discover import suggest

    class Registry:
        def list_operations(self):
            return [
                {"op": "web.search", "domain": "web",
                 "description": "Search the web and return titles and links",
                 "parameters": {"properties": {"query": 1}, "required": ["query"]}},
                {"op": "sys.exec", "domain": "sys",
                 "description": "Run a shell command",
                 "parameters": {"properties": {"cmd": 1}, "required": ["cmd"]}},
                {"op": "filesystem.write", "domain": "filesystem",
                 "description": "Write content to a file",
                 "parameters": {"properties": {"path": 1}, "required": ["path"]}},
            ]

    found = suggest("write a script and run it", Registry())
    names = [o["op"] for o in found["operations"]]
    assert "sys.exec" in names and "filesystem.write" in names
    assert found["chains"] and found["chains"][0]["steps"] == [
        "filesystem.write", "sys.exec"]
    assert "GUIDANCE" in found["note"]

    # A recipe naming an operation this host lacks must not be offered.
    class Bare:
        def list_operations(self):
            return [{"op": "filesystem.write", "domain": "filesystem",
                     "description": "Write content to a file",
                     "parameters": {}}]

    thin = suggest("write a script and run it", Bare())
    assert thin["chains"] == [], thin["chains"]

    # Nothing matched is an honest answer, not an invented one.
    empty = suggest("xyzzy plugh", Bare())
    assert empty["operations"] == [] or all(
        o["op"] == "filesystem.write" for o in empty["operations"])
