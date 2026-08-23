# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
"""Headless by default, visible on request.

No browser is launched here. What is worth pinning is the DECISION -- which
mode the next launch will use -- because the failure everyone would notice is
a window opening when nobody asked for one.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from IAC.web import ops  # noqa: E402


def setup_function():
    ops._visible_override = None


def teardown_function():
    ops._visible_override = None


def test_the_browser_is_out_of_sight_unless_asked():
    """A window that opens on every lookup is intolerable, so nothing may
    change this implicitly."""
    assert ops.browser_is_visible({}) is False
    assert ops.browser_is_visible({"web": {}}) is False


def test_config_can_still_ask_for_a_window():
    assert ops.browser_is_visible({"web": {"headless": False}}) is True


def test_an_explicit_request_wins_over_config():
    ops._visible_override = True
    assert ops.browser_is_visible({"web": {"headless": True}}) is True
    ops._visible_override = False
    assert ops.browser_is_visible({"web": {"headless": False}}) is False


def test_asking_for_what_is_already_true_changes_nothing():
    """Relaunching an unchanged state would be a visible flicker for nothing."""
    ops._visible_override = False
    ops._agent_instance = object()
    try:
        result = ops.set_browser_visible(False, {"config": {}})
        assert result["changed"] is False
        assert result["visible"] is False
    finally:
        ops._agent_instance = None


def test_the_override_never_edits_the_callers_config():
    """A stale override written into a shared dict would outlive the request
    that asked for it."""
    ops._visible_override = True
    config = {"web": {"headless": True}}
    given = {"config": config}
    try:
        ops.get_web_agent(given)
    except Exception:
        pass          # launching a real browser is not the point here
    assert config["web"]["headless"] is True
