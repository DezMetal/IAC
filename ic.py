# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
"""
IAC Intelligence Core Interface — Standard interface for any
intelligence operating within the D-Net ecosystem.

Every IC receives IAC payloads, processes them, and returns
structured results. This is what makes the AI "battery" swappable.
"""

from typing import Any, Dict, List, Optional

from .inference import OllamaProvider, InferenceProvider, ai_process


class IntelligenceCore:
    def process(self, iac_payload: dict) -> dict:
        raise NotImplementedError

    def can_handle(self, operation: str) -> bool:
        caps = self.capabilities
        return "*" in caps or operation in caps

    @property
    def capabilities(self) -> List[str]:
        return []

    @property
    def name(self) -> str:
        return "base"

    def is_available(self) -> bool:
        return False


class OllamaIC(IntelligenceCore):
    def __init__(self, host: str = None, model: str = None):
        self._provider = OllamaProvider(host, model)
        self._host = host or "http://127.0.0.1:11434"
        self._model = model or "qwen3.5:4b"

    def process(self, iac_payload: dict) -> dict:
        config = iac_payload.get("config", {})
        ai_cfg = config.get("ai_config", {})
        merged = {
            "host": ai_cfg.get("host", self._host),
            "model": ai_cfg.get("model", self._model),
            **ai_cfg
        }

        payload = iac_payload.get("data_payload", {})
        results = {}
        for key, info in payload.items():
            if isinstance(info, dict) and (info.get("src") or info.get("prompt")):
                success = ai_process(key, info, merged)
                results[key] = {"processed": success}

        return {"status": "ok", "results": results}

    def can_handle(self, operation: str) -> bool:
        return operation.startswith("ai.")

    @property
    def capabilities(self) -> List[str]:
        return ["ai.analyze", "ai.brain", "ai.encode"]

    @property
    def name(self) -> str:
        return f"ollama ({self._host}, {self._model})"

    def is_available(self) -> bool:
        return self._provider.is_available()


class GeminiIC(IntelligenceCore):
    def __init__(self, api_key: str = None, model: str = None):
        self._api_key = api_key
        self._model = model or "gemma-4-31b-it"
        self._client = None
        self._init_client()

    def _init_client(self):
        if not self._api_key:
            import os
            self._api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not self._api_key:
            return
        try:
            from google import genai
            self._client = genai.Client(api_key=self._api_key)
        except Exception:
            self._client = None

    def process(self, iac_payload: dict) -> dict:
        if not self._client:
            return {"status": "error", "error": "Gemini client not initialized"}

        payload = iac_payload.get("data_payload", {})
        results = {}

        for key, info in payload.items():
            if isinstance(info, dict) and info.get("prompt"):
                try:
                    from google.genai import types
                    response = self._client.models.generate_content(
                        model=self._model,
                        contents=[types.Content(
                            role="user",
                            parts=[types.Part.from_text(text=info["prompt"])]
                        )],
                        config=types.GenerateContentConfig(
                            system_instruction="Respond concisely and accurately."
                        )
                    )
                    info["raw_output"] = response.text.strip() if response.text else ""
                    results[key] = {"processed": True}
                except Exception as e:
                    info["raw_output"] = f"[Gemini Error: {e}]"
                    results[key] = {"processed": False, "error": str(e)}

        return {"status": "ok", "results": results}

    @property
    def capabilities(self) -> List[str]:
        return ["ai.analyze", "ai.brain"]

    @property
    def name(self) -> str:
        return f"gemini ({self._model})"

    def is_available(self) -> bool:
        return self._client is not None


class HumanIC(IntelligenceCore):
    def process(self, iac_payload: dict) -> dict:
        return {
            "status": "pending_approval",
            "message": "Plan requires human review before execution",
            "plan": iac_payload.get("plan", [])
        }

    @property
    def capabilities(self) -> List[str]:
        return ["*"]

    @property
    def name(self) -> str:
        return "human"

    def is_available(self) -> bool:
        return True


class ICRouter:
    def __init__(self):
        self._cores: Dict[str, IntelligenceCore] = {}
        self._default: str = ""

    def register(self, name: str, core: IntelligenceCore):
        self._cores[name] = core
        if not self._default:
            self._default = name

    def set_default(self, name: str):
        if name in self._cores:
            self._default = name

    def get(self, name: str = None) -> Optional[IntelligenceCore]:
        target = name or self._default
        return self._cores.get(target)

    def find_for_operation(self, operation: str) -> Optional[IntelligenceCore]:
        for core in self._cores.values():
            if core.can_handle(operation) and core.is_available():
                return core
        return self._cores.get(self._default)

    def list_cores(self) -> List[dict]:
        return [
            {
                "name": name,
                "label": core.name,
                "available": core.is_available(),
                "capabilities": core.capabilities,
                "default": name == self._default
            }
            for name, core in self._cores.items()
        ]

    def status(self) -> dict:
        return {
            "default": self._default,
            "cores": self.list_cores()
        }


_router: Optional[ICRouter] = None

def get_ic_router() -> ICRouter:
    global _router
    if _router is None:
        _router = ICRouter()
    return _router
