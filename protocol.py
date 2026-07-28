# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
"""
IAC Protocol — D-Net Node Protocol Envelope

Defines the canonical IAC payload structure used for all
structured data exchange between D-Net ecosystem nodes.
"""

IAC_VERSION = "2.0"

VALID_SOURCES = {
    "local", "cli", "aether", "dnet_live", "opas",
    "pipeline", "webagent", "human", "api", "sitegen", "envoy"
}


def create_envelope(plan: list, config: dict = None, data_payload=None,
                    source: str = "local", target: str = None) -> dict:
    envelope = {
        "iac_version": IAC_VERSION,
        "source": source,
        "plan": plan,
        "config": config or {},
        "data_payload": data_payload or {}
    }
    if target:
        envelope["target"] = target
    return envelope


def normalize_envelope(raw: dict) -> dict:
    if "iac_version" not in raw:
        raw["iac_version"] = IAC_VERSION
    if "source" not in raw:
        raw["source"] = "local"
    if "config" not in raw:
        raw["config"] = {}
    if "data_payload" not in raw:
        raw["data_payload"] = {}

    if "plan" not in raw:
        if "workflow" in raw:
            raw["plan"] = raw.pop("workflow")
        else:
            raw["plan"] = []

    if "web_agent" in raw:
        web_cfg = raw["web_agent"]
        if isinstance(web_cfg, dict):
            for k, v in web_cfg.items():
                # Allow root config to override web_agent, but ensure web_agent is present
                if k not in raw["config"]:
                    raw["config"][k] = v

    if "ai_core" in raw:
        ai_cfg = raw["ai_core"]
        if isinstance(ai_cfg, dict) and "ai_config" not in raw["config"]:
            raw["config"]["ai_config"] = ai_cfg

    return raw


def get_security_context(envelope: dict) -> dict:
    import os, json as _json
    config = envelope.get("config", {})
    security = config.get("security", {})

    policy = {}
    policy_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "security_policy.json")
    if os.path.exists(policy_path):
        try:
            with open(policy_path, "r", encoding="utf-8") as _f:
                policy = _json.load(_f)
        except Exception:
            pass

    allowed_domains = security.get("allowed_domains")
    if not allowed_domains:
        allowed_domains = policy.get("allowed_domains")

    return {
        "allow_eval": security.get("allow_eval", policy.get("allow_eval", False)),
        "allow_shell": security.get("allow_shell", policy.get("allow_shell", False)),
        "allowed_domains": allowed_domains,
        "allowed_paths": security.get("allowed_paths"),
        "source": envelope.get("source", "local"),
        "source_trusted": envelope.get("source", "local") in {"local", "human", "cli", "iac_web_gui", "aether"}
    }
