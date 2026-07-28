# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
"""
IAC Sanitizer — Drop protocol and payload size management.

Prevents buffer bloat by removing heavy keys (encoded images, etc.)
after processing, and enforces payload size limits.
"""

import sys
from typing import Any, Dict, List

DEFAULT_DROP_KEYS = ["encoded"]
DEFAULT_MAX_PAYLOAD_BYTES = 50 * 1024 * 1024  # 50MB


def drop_keys(info: dict, keys: list = None) -> dict:
    for k in (keys or DEFAULT_DROP_KEYS):
        info.pop(k, None)
    return info


def sanitize_payload(payload: dict, drop: list = None,
                     max_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES) -> dict:
    drop_list = drop or DEFAULT_DROP_KEYS
    if isinstance(drop_list, str):
        drop_list = [d.strip() for d in drop_list.split(",")]

    for key, value in payload.items():
        if isinstance(value, dict):
            for dk in drop_list:
                value.pop(dk, None)

    _enforce_size_limit(payload, max_bytes)
    return payload


def _enforce_size_limit(payload: dict, max_bytes: int):
    try:
        import json
        size = len(json.dumps(payload).encode('utf-8'))
        if size > max_bytes:
            _trim_largest_values(payload, size, max_bytes)
    except (TypeError, ValueError):
        pass


def _trim_largest_values(payload: dict, current_size: int, max_bytes: int):
    import json
    sized_keys = []
    for key, value in payload.items():
        if isinstance(value, dict):
            try:
                val_size = len(json.dumps(value).encode('utf-8'))
                sized_keys.append((key, val_size))
            except (TypeError, ValueError):
                pass

    sized_keys.sort(key=lambda x: x[1], reverse=True)

    for key, val_size in sized_keys:
        if current_size <= max_bytes:
            break
        if isinstance(payload[key], dict):
            for heavy_key in ["encoded", "raw_output", "input_data"]:
                if heavy_key in payload[key]:
                    removed = payload[key].pop(heavy_key)
                    try:
                        current_size -= len(json.dumps(removed).encode('utf-8'))
                    except (TypeError, ValueError):
                        current_size -= sys.getsizeof(removed)


def sanitize_step_result(result: dict, drop: list = None) -> dict:
    if not isinstance(result, dict):
        return result
    for dk in (drop or DEFAULT_DROP_KEYS):
        result.pop(dk, None)
    return result


def get_payload_stats(payload: dict) -> dict:
    import json
    total_keys = len(payload)
    nested_keys = sum(1 for v in payload.values() if isinstance(v, dict))
    try:
        size_bytes = len(json.dumps(payload).encode('utf-8'))
    except (TypeError, ValueError):
        size_bytes = -1
    return {
        "total_keys": total_keys,
        "nested_objects": nested_keys,
        "size_bytes": size_bytes,
        "size_mb": round(size_bytes / (1024 * 1024), 2) if size_bytes > 0 else 0
    }
