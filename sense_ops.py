# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
"""
sense.* -- reliable, consistent capture of what is actually there.

WHY THIS IS NOT desktop.snap
----------------------------
`desktop.snap` takes one screenshot of one screen and writes it to a file named
after the clock. That is the right shape for "look at this so you can click
it", which is what it was built for, and the wrong shape for everything to do
with watching:

  * It cannot address a second monitor or a region, so a watch is either the
    whole desktop or nothing.
  * It has no notion of a camera, and half the interesting sensors are cameras.
  * It writes every frame, unconditionally. A watch running overnight at one
    frame a second fills a disk with 28,800 copies of an idle desktop.
  * It produces no comparable quantity, so nothing downstream can tell one
    frame from the next without opening both.

The last two are the same problem: capture that commits to disk before anything
has decided whether the frame matters. So this module separates the three
things `snap` conflates -- ACQUIRE (get pixels), CHARACTERISE (produce
something comparable), and KEEP (write it down) -- and only the first two happen
on every read.

THE SIGNATURE
-------------
A 12x12 greyscale reduction of the frame, as a flat tuple. Small enough that
producing and comparing one is free, structured enough that the comparison is
graded rather than binary: a cursor moving reads as nearly nothing, someone
walking through the shot reads as a lot. A cryptographic hash would be cheaper
still and useless -- every frame differs from every other frame in the last bit,
so a hash can only ever say "different", which is the same as saying nothing.

HOW IT REACHES VIGIL
--------------------
Through plain dicts. IAC does not import DNLAAS and must not: the boundary hook
in the registry is duck-typed for exactly this reason, and the source protocol
is the same idea one layer up. `frame_source()` hands back a callable that
returns a dict; DNLAAS's `Source` accepts one; neither package knows the other
exists.

    from IAC.sense_ops import attach_sources
    attach_sources(vigil.sources, screens_wanted=True, cameras_wanted=[0])

DEGRADATION
-----------
Every backend is optional and independently optional. A machine with a camera
and no screen registers a camera. A headless box with neither registers nothing
and says so, rather than failing to import.
"""

import logging
import os
import threading
import time

logger = logging.getLogger("IAC.sense")

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import mss
    HAS_MSS = True
except (ImportError, Exception):
    HAS_MSS = False


# OpenCV is imported on first camera use, not at module load. It costs two
# seconds to import and every machine in the lab has it; almost none of them
# are watching a camera. A capture module that makes `import IAC` measurably
# slower for the screen case has charged everyone for the camera case.
_cv2_module = None
_cv2_tried = False


def _cv2():
    global _cv2_module, _cv2_tried
    if not _cv2_tried:
        _cv2_tried = True
        try:
            import cv2 as _module
            _cv2_module = _module
        except Exception as e:
            logger.debug("OpenCV is not available: %s", e)
    return _cv2_module


try:
    import pyautogui
    HAS_PYAUTOGUI = True
except (ImportError, Exception):
    HAS_PYAUTOGUI = False


# Side of the square greyscale reduction used as a signature. 144 values: free
# to produce, free to compare, and fine-grained enough to distinguish "the
# clock changed" from "someone walked in".
SIGNATURE_GRID = 12

DEFAULT_FRAME_DIR = os.path.join(os.getcwd(), ".vigil", "frames")

_local = threading.local()
_cameras = {}
_camera_lock = threading.RLock()


# ── acquiring ────────────────────────────────────────────────────────────
def _sct():
    """One mss instance per thread. It is explicitly not thread-safe."""
    handle = getattr(_local, "sct", None)
    if handle is None:
        handle = _local.sct = mss.mss()
    return handle


def screens() -> list:
    """Every screen this machine has, with the geometry to address it."""
    if HAS_MSS:
        try:
            # monitors[0] is the union of all screens; the rest are physical.
            found = _sct().monitors
            return [{"index": i, "left": m["left"], "top": m["top"],
                     "width": m["width"], "height": m["height"],
                     "name": "all-screens" if i == 0 else f"screen-{i}"}
                    for i, m in enumerate(found)]
        except Exception as e:
            logger.debug("mss could not enumerate screens: %s", e)
    if HAS_PYAUTOGUI:
        try:
            width, height = pyautogui.size()
            return [{"index": 0, "left": 0, "top": 0, "width": int(width),
                     "height": int(height), "name": "all-screens"}]
        except Exception as e:
            logger.debug("pyautogui could not report a screen size: %s", e)
    return []


def cameras(probe=4) -> list:
    """Camera indices that actually open. Probing is the only way to know.

    Deliberately opens and closes rather than trusting an enumeration: on every
    platform the lab runs on, the list of devices and the list of devices that
    will hand you a frame are different lists.
    """
    cv2 = _cv2()
    if cv2 is None:
        return []
    found = []
    for index in range(max(0, int(probe))):
        capture = None
        try:
            capture = cv2.VideoCapture(index)
            if capture.isOpened():
                ok, frame = capture.read()
                if ok and frame is not None:
                    found.append({"index": index, "name": f"camera-{index}",
                                  "width": int(frame.shape[1]),
                                  "height": int(frame.shape[0])})
        except Exception as e:
            logger.debug("Camera %d could not be probed: %s", index, e)
        finally:
            if capture is not None:
                capture.release()
    return found


def grab_screen(index=0, region=None):
    """A PIL image of a screen, or of a region of one. None if unavailable.

    `region` is {x, y, w, h} in the coordinates of that screen, so a watch on
    one window does not move when a second monitor is plugged in.
    """
    if not HAS_PIL:
        return None
    if HAS_MSS:
        try:
            monitors = _sct().monitors
            index = max(0, min(int(index), len(monitors) - 1))
            box = dict(monitors[index])
            if region:
                box = {"left": box["left"] + int(region.get("x", 0)),
                       "top": box["top"] + int(region.get("y", 0)),
                       "width": max(1, int(region.get("w", box["width"]))),
                       "height": max(1, int(region.get("h", box["height"])))}
            shot = _sct().grab(box)
            return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        except Exception as e:
            logger.debug("mss capture failed (%s); falling back", e)
    if HAS_PYAUTOGUI:
        try:
            if region:
                return pyautogui.screenshot(region=(
                    int(region.get("x", 0)), int(region.get("y", 0)),
                    int(region.get("w", 0)), int(region.get("h", 0)))).convert("RGB")
            return pyautogui.screenshot().convert("RGB")
        except Exception as e:
            logger.debug("pyautogui capture failed: %s", e)
    return None


def grab_camera(index=0, region=None):
    """A PIL image from a camera. The handle is kept open between reads.

    Opening a capture device costs a few hundred milliseconds, which is fine
    once and ruinous at one frame a second, so handles are cached and released
    together by `close_cameras`.
    """
    cv2 = _cv2()
    if cv2 is None or not HAS_PIL:
        return None
    with _camera_lock:
        capture = _cameras.get(index)
        if capture is None or not capture.isOpened():
            capture = cv2.VideoCapture(int(index))
            if not capture.isOpened():
                capture.release()
                return None
            _cameras[index] = capture
        ok, frame = capture.read()
    if not ok or frame is None:
        return None
    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    if region:
        x, y = int(region.get("x", 0)), int(region.get("y", 0))
        image = image.crop((x, y, x + int(region.get("w", image.width)),
                            y + int(region.get("h", image.height))))
    return image


def close_cameras():
    """Release every cached device. Call it when the watch ends."""
    with _camera_lock:
        for capture in _cameras.values():
            try:
                capture.release()
            except Exception:
                pass
        _cameras.clear()


# ── characterising ───────────────────────────────────────────────────────
def signature(image, grid=SIGNATURE_GRID) -> tuple:
    """A comparable reduction of a frame. Cheap, graded, and not a hash.

    Greyscale because a change in colour with no change in structure is almost
    never what a watch is for, and dropping two channels makes this three times
    cheaper on the path that runs most often.
    """
    if image is None:
        return ()
    try:
        small = image.convert("L").resize((grid, grid), Image.BILINEAR)
        return tuple(small.getdata())
    except Exception as e:
        logger.debug("Could not reduce a frame to a signature: %s", e)
        return ()


# ── keeping ──────────────────────────────────────────────────────────────
def _frame_path(directory, prefix, fmt="png"):
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    millis = int((time.time() % 1) * 1000)
    return os.path.join(directory, f"{prefix}_{stamp}_{millis:03d}.{fmt}")


def _writer(image, directory, prefix, fmt):
    """The thunk handed to Vigil, run only if the frame survives the gate."""
    def keep():
        os.makedirs(directory, exist_ok=True)
        path = _frame_path(directory, prefix, fmt)
        image.save(path)
        return path
    return keep


# ── sources, for Vigil ───────────────────────────────────────────────────
def frame_source(grab, kind="vision", directory=None, prefix="frame",
                 fmt="png", grid=SIGNATURE_GRID):
    """Wrap any frame-producing callable as a Vigil source reading.

    Returns a callable of no arguments producing a plain dict. Nothing in this
    module imports DNLAAS, and DNLAAS's `Source` accepts a dict precisely so
    that it need not import this.
    """
    directory = directory or DEFAULT_FRAME_DIR

    def read():
        image = grab()
        if image is None:
            return None          # DNLAAS turns this into an unreadable reading
        return {
            "kind": kind,
            "signature": signature(image, grid),
            # No artifact yet, and no file yet. Both appear only if the
            # attention gate decides this frame is worth keeping.
            "persist": _writer(image, directory, prefix, fmt),
            # `scale` is the one field the gate cannot work out for itself: an
            # 8-bit greyscale reduction runs 0-255 whatever is in front of the
            # lens, and without saying so a dark frame's own noise reads as a
            # whole new scene.
            "meta": {"width": image.width, "height": image.height,
                     "scale": 255},
        }
    return read


def attach_sources(sources, screens_wanted=True, cameras_wanted=(),
                   regions=None, directory=None, interval=2.0, cost=4.0):
    """Register everything this machine can see with a `Sources` collection.

    Returns the names registered, so a caller can tell the difference between
    "watching three things" and "watching nothing because this is a headless
    box", which is otherwise a silence that looks like success.
    """
    directory = directory or DEFAULT_FRAME_DIR
    registered = []

    if screens_wanted:
        found = screens()
        # Skip the union monitor when there are real ones behind it: capturing
        # both means every change is recorded twice, from two sources, which
        # would let a finding claim corroboration it has not got.
        physical = [s for s in found if s["index"] > 0] or found
        for screen in physical:
            name = screen["name"]
            sources.register(
                name,
                frame_source(lambda i=screen["index"]: grab_screen(i),
                             directory=directory, prefix=name),
                kind="vision", interval=interval, cost=cost,
                note=f"{screen['width']}x{screen['height']} at "
                     f"{screen['left']},{screen['top']}")
            registered.append(name)

    for index in (cameras_wanted or ()):
        if grab_camera(index) is None:
            logger.warning("Camera %s is not available; not registering it.", index)
            continue
        name = f"camera-{index}"
        sources.register(
            name,
            frame_source(lambda i=index: grab_camera(i), directory=directory,
                         prefix=name),
            kind="vision", interval=interval, cost=cost * 1.5,
            note="camera")
        registered.append(name)

    for name, spec in (regions or {}).items():
        target = str(spec.get("target", "screen-1"))
        region = {k: spec[k] for k in ("x", "y", "w", "h") if k in spec}
        if target.startswith("camera-"):
            index = int(target.split("-", 1)[1])
            grab = (lambda i=index, r=region: grab_camera(i, r))
        else:
            index = int(target.split("-", 1)[1]) if "-" in target else 0
            grab = (lambda i=index, r=region: grab_screen(i, r))
        sources.register(name, frame_source(grab, directory=directory,
                                            prefix=name),
                         kind="vision", interval=spec.get("interval", interval),
                         cost=spec.get("cost", cost / 2),
                         note=str(spec.get("note", "region")))
        registered.append(name)

    if not registered:
        logger.warning("Nothing on this machine could be registered as a "
                       "visual source (PIL=%s mss=%s cv2=%s pyautogui=%s).",
                       HAS_PIL, HAS_MSS, _cv2() is not None, HAS_PYAUTOGUI)
    return registered


# ── operations ───────────────────────────────────────────────────────────
def register_sense_operations(registry, frame_dir=None):
    """Registers capture as IAC operations, for plans and pipelines.

    Vigil does not go through these -- it holds sources directly, because an
    operation call per frame would put a dispatch, a policy check and a JSON
    round trip in front of something that runs a hundred thousand times. These
    exist for the other half of the job: a pipeline that wants one frame, and
    an agent that wants to look at something on purpose.
    """
    if not HAS_PIL:
        print("[WARN] 'Pillow' is not installed. Sense operations (sense.*) "
              "are disabled.")
        return 0

    directory = frame_dir or DEFAULT_FRAME_DIR

    def _sources(args, context):
        found_screens = screens()
        found_cameras = cameras(probe=int(args.get("probe", 3) or 3)) \
            if args.get("cameras") else []
        lines = [f"screen: {s['name']} ({s['width']}x{s['height']})"
                 for s in found_screens]
        lines += [f"camera: {c['name']} ({c['width']}x{c['height']})"
                  for c in found_cameras]
        if not lines:
            return {"status": "ok", "data": {"screens": [], "cameras": []},
                    "message": "This machine has nothing that can be seen "
                               "through. Capture backends available: "
                               f"mss={HAS_MSS}, cv2={_cv2() is not None}, "
                               f"pyautogui={HAS_PYAUTOGUI}."}
        return {"status": "ok",
                "data": {"screens": found_screens, "cameras": found_cameras},
                "message": "What can be sensed here:\n" + "\n".join(lines)}

    def _capture(args, context):
        payload = (context or {}).get("payload", {})
        target = str(args.get("target") or "screen-1")
        region = args.get("region") or None
        if region and not all(k in region for k in ("x", "y", "w", "h")):
            return {"status": "error",
                    "error": "A region needs x, y, w and h. Partial regions "
                             "are refused rather than guessed at, because a "
                             "guessed crop looks like a real observation."}

        if target.startswith("camera"):
            index = int(target.split("-", 1)[1]) if "-" in target else 0
            image = grab_camera(index, region)
            what = f"camera {index}"
        else:
            index = int(target.split("-", 1)[1]) if "-" in target else 0
            image = grab_screen(index, region)
            what = f"screen {index}"
        if image is None:
            return {"status": "error",
                    "error": f"Could not capture {what}. It may not exist on "
                             f"this machine -- call sense.sources to see what "
                             f"does."}

        path = args.get("path")
        if not path:
            os.makedirs(directory, exist_ok=True)
            path = _frame_path(directory, target.replace(":", "-"))
        path = os.path.abspath(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        image.save(path)

        key = args.get("payload_key") or f"image_{len(payload)}"
        payload[key] = {"src": path}
        return {"status": "ok", "key": key, "path": path,
                "data": {"path": path, "width": image.width,
                         "height": image.height,
                         "signature": list(signature(image))},
                "message": f"Captured {what} ({image.width}x{image.height}) "
                           f"to {path}"}

    def _compare(args, context):
        """How different two captured images are, on the same scale Vigil uses."""
        first, second = args.get("a"), args.get("b")
        if not first or not second:
            return {"status": "error", "error": "Two image paths are needed."}
        try:
            left = signature(Image.open(first))
            right = signature(Image.open(second))
        except Exception as e:
            return {"status": "error", "error": f"Could not read both: {e}"}
        if not left or not right or len(left) != len(right):
            return {"status": "error", "error": "Images could not be compared."}
        scale = max(max(left), max(right)) or 1
        novelty = min(1.0, sum(abs(x - y) for x, y in zip(left, right))
                      / (len(left) * scale))
        return {"status": "ok", "data": {"novelty": round(novelty, 4)},
                "message": f"{novelty:.1%} of the available range differs "
                           f"between those two frames."}

    schemas = {
        "sources": {"type": "object", "properties": {
            "cameras": {"type": "boolean",
                        "description": "also probe for cameras (slower)"},
            "probe": {"type": "integer",
                      "description": "how many camera indices to try"}}},
        "capture": {"type": "object", "properties": {
            "target": {"type": "string",
                       "description": "screen-1, screen-2, camera-0 ...",
                       "default": "screen-1"},
            "region": {"type": "object",
                       "description": "{x, y, w, h} within that target"},
            "path": {"type": "string", "description": "where to save it"},
            "payload_key": {"type": "string",
                            "description": "payload key to store it under"}}},
        "compare": {"type": "object", "properties": {
            "a": {"type": "string", "description": "path to the first image"},
            "b": {"type": "string", "description": "path to the second"}},
            "required": ["a", "b"]},
    }

    ops = [
        ("sources", "What this machine can see through -- screens, and "
                    "optionally cameras", _sources),
        ("capture", "Capture a screen, a camera, or a region of either, to an "
                    "image file", _capture),
        ("compare", "How much two captured frames differ, on the same 0-1 "
                    "scale a watch uses to decide something happened", _compare),
    ]

    count = 0
    for name, description, handler in ops:
        try:
            registry.register(name=name, domain="sense", description=description,
                              parameters=schemas.get(name, {}), handler=handler)
            count += 1
        except Exception as e:
            logger.warning("Could not register sense.%s: %s", name, e)
    return count
