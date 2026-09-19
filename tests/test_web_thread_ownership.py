# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
"""Who owns the browser, and who is allowed to create it.

Playwright's sync API may only be driven from the thread that started it. One
worker therefore owns the browser for the life of the process -- and the whole
arrangement fails if anything can start the browser somewhere else, because
the object is then bound to a thread that is about to retire.

That is not hypothetical. `sys.*` is implemented as methods on WebAgent, so a
shell command constructs one, and `sys.exec` called the factory directly from
whatever pool thread the turn was on. A single `echo` bound the browser to a
worker that then retired, and every web operation for the rest of the session
failed in a hundredth of a second with "cannot switch to a different thread
(which happens to have exited)" -- a message pointing nowhere near the shell.
"""

import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web import ops


@pytest.fixture
def fake_browser(monkeypatch):
    """A WebAgent that records the thread it was born on, and starts nothing."""
    born = {}

    class FakePage:
        # A real agent whose page is None or closed is correctly treated as
        # dead and replaced; the fake has to look alive or every reuse check
        # discards it.
        def is_closed(self):
            return False

    class FakeWebAgent:
        closed = False

        def __init__(self, config=None):
            self.page = FakePage()
            born["thread"] = threading.current_thread().name
            born["config"] = config

        def close(self):
            self.closed = True

    monkeypatch.setattr(ops, "WebAgent", FakeWebAgent)
    monkeypatch.setattr(ops, "_agent_instance", None)
    yield born
    ops._agent_instance = None


def _on_a_retiring_worker(fn):
    """Call `fn` on a pool thread and let that thread die, as a turn does."""
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="turn_worker")
    try:
        return pool.submit(fn).result()
    finally:
        pool.shutdown(wait=True)


def test_a_shell_command_cannot_bind_the_browser_to_a_dying_thread(fake_browser):
    """The exact sequence from the log: sys.exec, then every web op broken."""
    _on_a_retiring_worker(lambda: ops.get_web_agent({}))
    assert fake_browser["thread"].startswith("iac_web"), (
        "born on %s -- a worker that is now gone, so the next web operation "
        "raises 'cannot switch to a different thread'" % fake_browser["thread"])


def test_the_owning_thread_is_still_alive_afterwards(fake_browser):
    _on_a_retiring_worker(lambda: ops.get_web_agent({}))
    alive = {t.name for t in threading.enumerate()}
    assert fake_browser["thread"] in alive


def test_a_later_operation_lands_on_the_same_thread(fake_browser):
    _on_a_retiring_worker(lambda: ops.get_web_agent({}))
    where = ops._on_browser_thread(lambda: threading.current_thread().name)
    assert where == fake_browser["thread"]


def test_asking_from_the_browser_thread_does_not_deadlock(fake_browser):
    agent = ops._on_browser_thread(lambda: ops.get_web_agent({}))
    assert agent is not None
    assert fake_browser["thread"].startswith("iac_web")


def test_the_browser_is_made_once_however_many_ask(fake_browser):
    """Two turns can call at the same moment; two browsers would be one too
    many, and the second would be the one bound to the wrong thread."""
    made = []

    class Counting(ops.WebAgent):
        def __init__(self, config=None):
            super().__init__(config)
            made.append(1)

    ops.WebAgent = Counting
    start = threading.Barrier(4)

    def ask():
        start.wait()
        return ops.get_web_agent({})

    with ThreadPoolExecutor(max_workers=4) as pool:
        agents = [f.result() for f in [pool.submit(ask) for _ in range(4)]]

    assert len(made) == 1
    assert len({id(a) for a in agents}) == 1


def test_config_still_reaches_the_agent(fake_browser):
    """The hop must not drop what the caller passed."""
    ops.get_web_agent({"config": {"web": {"headless": True}}})
    assert fake_browser["config"] == {"web": {"headless": True}}


def test_a_closed_browser_is_replaced_on_the_right_thread(fake_browser):
    first = ops.get_web_agent({})
    first.closed = True
    second = _on_a_retiring_worker(lambda: ops.get_web_agent({}))
    assert second is not first
    assert fake_browser["thread"].startswith("iac_web")
