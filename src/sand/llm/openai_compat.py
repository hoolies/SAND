"""OpenAI-compatible chat completions client."""

from __future__ import annotations

import httpx

from sand.core.config import Settings, get_settings


class LLMNotConfiguredError(RuntimeError):
    """Raised when NL chat is requested without an LLM endpoint/key."""


class OpenAICompatClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    @property
    def is_configured(self) -> bool:
        if self.settings.llm_api_key:
            return True
        base = self.settings.llm_base_url
        return "localhost" in base or "127.0.0.1" in base

    def complete(self, system: str, user: str) -> str:
        if not self.is_configured:
            raise LLMNotConfiguredError(
                "No LLM configured. Set SAND_LLM_API_KEY (and optionally SAND_LLM_BASE_URL / "
                "SAND_LLM_MODEL), or use Offline asks (Profile / Top-N / Group-by / Time series) "
                "which do not need an LLM."
            )

        headers = {"Content-Type": "application/json"}
        if self.settings.llm_api_key:
            headers["Authorization"] = f"Bearer {self.settings.llm_api_key}"

        payload = {
            "model": self.settings.llm_model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        url = self.settings.llm_base_url.rstrip("/") + "/chat/completions"
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]
