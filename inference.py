# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
"""
IAC Inference — AI inference pipeline.

Provider-agnostic AI processing. Handles prompt construction,
schema enforcement, retry logic, and response parsing.
Extracted from agent_tools.task_ai_process().
"""

import json
import re
import time
import requests
from typing import Any, Dict, Optional

try:
    from .encoder import encode_if_needed
    from .sanitizer import drop_keys
except ImportError:
    from encoder import encode_if_needed
    from sanitizer import drop_keys


DEFAULT_AI_CONFIG = {
    "host": "http://127.0.0.1:11434",
    "model": "Gemma4:E4B",        # General fallback
    "vision_model": "Gemma4:E4B", 
    "text_model": "Gemma4:E4B",
    "prompt": "Analyze.",
    "ai_timeout": 300,
    "retries": 3,
    "temp": 0.2,
    "drop": ["encoded"],
    "output_key": "raw_output"
}

# Cached endpoint capability per host
_ollama_capabilities = {}

def ollama_request(host: str, model: str, prompt: str, images: list = None,
                   options: dict = None, extras: dict = None, timeout: int = 300) -> str:
    """Unified Ollama request handler. Auto-detects /api/chat vs /api/generate."""
    global _ollama_capabilities
    host = host.rstrip("/")

    use_chat = _ollama_capabilities.get(host, True)

    messages_payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt, "images": images or []}],
        "stream": False,
        "options": options or {"temperature": 0.2}
    }
    generate_payload = {
        "model": model,
        "prompt": prompt,
        "images": images or [],
        "stream": False,
        "options": options or {"temperature": 0.2}
    }

    if extras:
        messages_payload.update(extras)
        generate_payload.update(extras)

    def _try_endpoint(endpoint, payload, parse_fn):
        base_host = host
        if base_host.endswith("/api/chat"):
            base_host = base_host[:-9]
        elif base_host.endswith("/api/generate"):
            base_host = base_host[:-13]
        elif base_host.endswith("/v1"):
            base_host = base_host[:-3]
            
        url = f"{base_host}{endpoint}"
        res = requests.post(url, json=payload, timeout=timeout)
        try:
            res.raise_for_status()
        except requests.exceptions.HTTPError as e:
            err_msg = res.text
            if "model" in err_msg.lower() and "not found" in err_msg.lower():
                raise RuntimeError(f"Ollama Model Not Found: {err_msg}")
            # If it's a 404 and not a model error, we can raise it
            if res.status_code == 404 and "model" not in err_msg.lower():
                raise
            raise RuntimeError(f"Ollama Error {res.status_code}: {err_msg}")
        return parse_fn(res.json())

    try:
        if use_chat:
            try:
                result = _try_endpoint("/api/chat", messages_payload,
                                       lambda d: d.get("message", {}).get("content", "").strip())
                _ollama_capabilities[host] = True
                return result
            except requests.exceptions.HTTPError as e:
                # Fallback to /api/generate only for plain 404 endpoint not found
                if e.response is not None and e.response.status_code == 404:
                    print(f"    [AI] Host {host} /api/chat 404, falling back to /api/generate")
                    _ollama_capabilities[host] = False
                    try:
                        result = _try_endpoint("/api/generate", generate_payload,
                                               lambda d: d.get("response", "").strip())
                        return result
                    except requests.exceptions.HTTPError as e2:
                        raise RuntimeError(f"Ollama Fallback Error {e2.response.status_code}: {e2.response.text}")
                raise RuntimeError(f"Ollama Request Error: {str(e)}")
        else:
            try:
                result = _try_endpoint("/api/generate", generate_payload,
                                       lambda d: d.get("response", "").strip())
                return result
            except requests.exceptions.HTTPError as e:
                raise RuntimeError(f"Ollama Endpoint Error: {e.response.text}")
    except Exception:
        raise


def ai_process(key: str, info: Dict[str, Any], config: Dict[str, Any]) -> bool:
    merged = {**DEFAULT_AI_CONFIG, **config}

    # Intelligent Model Routing
    if not merged.get("model"):
        if info.get("src") or info.get("encoded"):
            merged["model"] = merged.get("vision_model", merged["model"])
        else:
            merged["model"] = merged.get("text_model", merged["model"])

    if info.get("src") and not info.get("encoded"):
        if not encode_if_needed(key, info, merged):
            print(f"    [!] AI Aborted: Encoding failed for {key}")
            return False

    prompt = info.get("prompt") or merged.get("prompt", "Analyze.")

    ai_options = {"temperature": merged.get("temp", 0.2)}
    if merged.get("options"):
        ai_options.update(merged["options"])

    extras = {}
    for extra in ["think", "stream"]:
        if extra in merged:
            extras[extra] = merged[extra]

    try:
        final_prompt = prompt
        if info.get("input_data"):
            final_prompt += f"\n\nContext Data:\n{info['input_data']}"
        if merged.get("schema"):
            final_prompt += f"\n\nResponse must strictly adhere to this JSON schema: {json.dumps(merged['schema'])}"

        images = [info["encoded"]] if info.get("encoded") else []
        print(f"    [AI-REQ] Task: {key} | Vision: {bool(images)}")

        max_retries = merged.get("retries", 3)
        out = ""

        for attempt in range(max_retries):
            s_time = time.time()
            out = ollama_request(
                host=merged.get("host", DEFAULT_AI_CONFIG["host"]),
                model=merged.get("model"),
                prompt=final_prompt,
                images=images,
                options=ai_options,
                extras=extras if extras else None,
                timeout=merged.get("ai_timeout", 300)
            )
            print(f"    [AI-RES] Received in {time.time()-s_time:.2f}s")

            if out:
                break
            if attempt + 1 < max_retries:
                print(f"    [!] AI returned blank. Retrying ({attempt+1}/{max_retries})...")
                time.sleep(1)

        o_key = merged.get("output_key", "raw_output")
        match = re.search(r'\{.*\}', out, re.DOTALL)
        if match:
            try:
                info.update(json.loads(match.group()))
            except (json.JSONDecodeError, ValueError):
                info[o_key] = out
        else:
            info[o_key] = out

        drop_keys(info, merged.get("drop", ["encoded"]))
        return True

    except Exception as e:
        print(f"    [!] AI Error: {e}")
        return False


def ai_batch(payload: dict, config: dict, prefix: str = "image_") -> int:
    count = 0
    merged = {**DEFAULT_AI_CONFIG, **config}
    for key, info in payload.items():
        if key.startswith(prefix) and isinstance(info, dict):
            ai_process(key, info, merged)
            count += 1
    return count


class InferenceProvider:
    def infer(self, prompt: str, images: list = None,
              schema: dict = None, config: dict = None) -> Dict[str, Any]:
        raise NotImplementedError

    @property
    def name(self) -> str:
        return "base"

    def is_available(self) -> bool:
        return False


class OllamaProvider(InferenceProvider):
    def __init__(self, host: str = None, model: str = None):
        self._host = host or DEFAULT_AI_CONFIG["host"]
        self._model = model or DEFAULT_AI_CONFIG["model"]

    def infer(self, prompt: str, images: list = None,
              schema: dict = None, config: dict = None) -> Dict[str, Any]:
        cfg = config or {}

        final_prompt = prompt
        if schema:
            final_prompt += f"\n\nResponse must strictly adhere to this JSON schema: {json.dumps(schema)}"

        try:
            content = ollama_request(
                host=self._host,
                model=self._model,
                prompt=final_prompt,
                images=images,
                options={"temperature": cfg.get("temp", 0.2)},
                timeout=cfg.get("ai_timeout", 300)
            )
            return {"success": True, "content": content}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @property
    def name(self) -> str:
        return f"ollama ({self._host})"

    def is_available(self) -> bool:
        try:
            r = requests.get(self._host, timeout=3)
            return r.status_code == 200
        except Exception:
            return False
