# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
"""
IAC Encoder — Base64 encoding for vision/binary data.

Handles local files, remote URLs, and raw byte streams.
Used by the AI inference pipeline for image analysis.
"""

import base64
import os
import time
from typing import Any, Dict, Optional


def encode_source(key: str, info: Dict[str, Any], config: Dict[str, Any] = None) -> bool:
    src = info.get("src")
    if not src:
        return False

    try:
        if src.startswith("http"):
            image_bytes = _fetch_remote(src)
        elif os.path.exists(src):
            image_bytes = _read_local(src)
        else:
            print(f"    [!] Encode Error: Source not found {src}")
            return False

        s_time = time.time()
        info["encoded"] = base64.b64encode(image_bytes).decode('utf-8')
        print(f"    [ENCODE] {key} ({len(info['encoded'])} chars) in {time.time()-s_time:.2f}s")
        return True
    except Exception as e:
        print(f"    [!] Encode Error [{key}]: {e}")
        return False


def _fetch_remote(url: str, timeout: int = 30) -> bytes:
    import requests
    print(f"    [FETCH] Downloading: {url[:60]}...")
    res = requests.get(url, timeout=timeout)
    res.raise_for_status()
    print(f"    [FETCH] Success: {len(res.content)} bytes")
    return res.content


def _read_local(path: str) -> bytes:
    from pathlib import Path
    with open(Path(path), "rb") as f:
        return f.read()


def encode_if_needed(key: str, info: Dict[str, Any], config: Dict[str, Any] = None) -> bool:
    if info.get("src") and not info.get("encoded"):
        return encode_source(key, info, config)
    return bool(info.get("encoded"))


def decode_to_bytes(encoded_str: str) -> bytes:
    return base64.b64decode(encoded_str)


def encode_bytes(data: bytes) -> str:
    return base64.b64encode(data).decode('utf-8')


def encode_file(file_path: str) -> Optional[str]:
    if not os.path.exists(file_path):
        return None
    with open(file_path, "rb") as f:
        return base64.b64encode(f.read()).decode('utf-8')
