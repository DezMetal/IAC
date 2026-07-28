# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
"""
IAC Resolver — Recursive resource resolution.

Resolves file pointers, data_payload references, and nested
resource chains into flat, usable data structures.
"""

import json
import os
from typing import Any, Dict, List, Union


def resolve_resource(source, is_list=True) -> Union[list, dict]:
    if not source:
        return [] if is_list else {}
    final = [] if is_list else {}
    items = source if isinstance(source, list) else [source]
    _seen = set()

    for item in items:
        resolved = _resolve_single(item, _seen)
        if is_list:
            if isinstance(resolved, list):
                final.extend(resolved)
            else:
                final.append(resolved)
        else:
            if isinstance(resolved, dict):
                final.update(resolved)
    return final


def _resolve_single(item, seen: set) -> Any:
    if isinstance(item, str) and os.path.isfile(item):
        abs_path = os.path.abspath(item)
        if abs_path in seen:
            raise ValueError(f"Circular reference detected: {abs_path}")
        seen.add(abs_path)
        with open(abs_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return item


def resolve_data_payload(payload_ref) -> dict:
    if isinstance(payload_ref, dict):
        return payload_ref
    if isinstance(payload_ref, str):
        if os.path.isfile(payload_ref):
            with open(payload_ref, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict) and "data_payload" in data:
                return resolve_data_payload(data["data_payload"])
            if isinstance(data, dict) and "final_payload" in data:
                return resolve_data_payload(data["final_payload"])
            return data if isinstance(data, dict) else {}
    return {}


def resolve_plan(plan_ref) -> list:
    if isinstance(plan_ref, list):
        return plan_ref
    if isinstance(plan_ref, str) and os.path.isfile(plan_ref):
        with open(plan_ref, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("plan", data.get("workflow", []))
    return []


def resolve_config(config_path: str) -> dict:
    if not config_path or not os.path.isfile(config_path):
        return {}
    with open(config_path, 'r', encoding='utf-8') as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        return {}
    return raw


def resolve_full_envelope(source) -> dict:
    from .protocol import normalize_envelope

    if isinstance(source, str) and os.path.isfile(source):
        raw = resolve_config(source)
    elif isinstance(source, dict):
        raw = source
    else:
        return normalize_envelope({"plan": []})

    envelope = normalize_envelope(raw)

    dp = envelope.get("data_payload", envelope.get("config", {}).get("data_payload", {}))
    envelope["data_payload"] = resolve_data_payload(dp)

    if isinstance(envelope.get("plan"), str):
        envelope["plan"] = resolve_plan(envelope["plan"])

    return envelope
