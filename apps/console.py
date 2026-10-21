# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
"""
`say()` -- printing that cannot fail the thing it is describing.

The Windows console is cp1252 and `print` RAISES on anything it cannot encode.
Aether learned this when a single degree sign in a page summary crashed an IAC
execution into its fallback path, and the rule it produced is the one applied
here: **a log line must never be able to fail the thing it is describing.**

It matters for an app launcher because the text it prints is app titles,
summaries and log tails -- none of which it wrote, any of which may contain a
character this console cannot render.
"""

import sys

__all__ = ["say", "safe", "rule"]


def safe(text, stream=None):
    """`text` reduced to what this stream can actually encode."""
    stream = stream or sys.stdout
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        return str(text).encode(encoding, "replace").decode(encoding,
                                                            "replace")
    except Exception:
        return str(text).encode("ascii", "replace").decode("ascii")


def say(text="", stream=None):
    """Print, without ever raising on an encoding the console lacks."""
    stream = stream or sys.stdout
    try:
        print(text, file=stream)
        return
    except (UnicodeEncodeError, Exception):
        pass
    try:
        print(safe(text, stream), file=stream)
    except Exception:
        try:
            stream.write(str(text).encode("ascii", "replace").decode("ascii")
                         + "\n")
        except Exception:
            pass


def rule(width=66, char="-"):
    """A horizontal rule, ASCII because a separator is the least important
    thing on the line and the most likely to be unprintable."""
    say(char * width)
