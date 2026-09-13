import os
import logging
import httpx
from typing import Optional
from dotenv import load_dotenv

from src.llm.base_client import BaseLLMClient, LLMResponse
from src.config import LLM_FALLBACK_MODELS

load_dotenv()
logger = logging.getLogger(__name__)


class CerebrasClient(BaseLLMClient):
    """Cerebras Cloud API client for Llama 3.3 70B."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 120.0,
    ):
        self._api_key = api_key or os.getenv("CEREBRAS_API_KEY")
        self._base_url = (
            base_url or os.getenv("CEREBRAS_BASE_URL", "https://api.cerebras.ai/v1")
        ).rstrip("/")
        self._model = model or os.getenv("CEREBRAS_MODEL", "llama-3.3-70b")
        self._timeout = timeout

        if not self._api_key:
            raise ValueError("CEREBRAS_API_KEY is required")

    def _get_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _build_request_body(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
    ) -> dict:
        return {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

    def _parse_response(self, response_data: dict) -> LLMResponse:
        choice = response_data.get("choices", [{}])[0]
        message = choice.get("message", {})
        content = message.get("content", "")
        usage = response_data.get("usage")

        return LLMResponse(
            content=content,
            model=response_data.get("model", self._model),
            usage=usage,
            raw_response=response_data,
        )

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        import time
        url = f"{self._base_url}/chat/completions"
        body = self._build_request_body(messages, temperature, max_tokens)

        max_retries = 6
        fallback_models = LLM_FALLBACK_MODELS
        for attempt in range(max_retries):
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(url, headers=self._get_headers(), json=body)
                if response.status_code == 429 and attempt < max_retries - 1:
                    remaining = response.headers.get("x-ratelimit-remaining-requests", "1")
                    # If daily/RPD limit hit or multiple attempts failed, switch model
                    if remaining == "0" or attempt >= 1:
                        for fb in fallback_models:
                            if fb != body.get("model"):
                                logger.warning(f"[LLM] Failover model: {body.get('model')} → {fb}")
                                body["model"] = fb
                                break
                    retry_after = response.headers.get("retry-after")
                    wait_time = float(retry_after) if retry_after and float(retry_after) < 15.0 else max(3.0, 2.0 * (attempt + 1))
                    logger.warning(f"[LLM] Rate limit hit (429), waiting {wait_time:.1f}s (attempt {attempt+1}/{max_retries})...")
                    time.sleep(min(wait_time, 15.0))
                    continue
                response.raise_for_status()
                return self._parse_response(response.json())

    async def achat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        import asyncio
        url = f"{self._base_url}/chat/completions"
        body = self._build_request_body(messages, temperature, max_tokens)

        max_retries = 6
        fallback_models = LLM_FALLBACK_MODELS
        for attempt in range(max_retries):
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    url, headers=self._get_headers(), json=body
                )
                if response.status_code == 429 and attempt < max_retries - 1:
                    remaining = response.headers.get("x-ratelimit-remaining-requests", "1")
                    if remaining == "0" or attempt >= 1:
                        for fb in fallback_models:
                            if fb != body.get("model"):
                                logger.warning(f"[LLM] Failover model: {body.get('model')} → {fb}")
                                body["model"] = fb
                                break
                    retry_after = response.headers.get("retry-after")
                    wait_time = float(retry_after) if retry_after and float(retry_after) < 15.0 else max(3.0, 2.0 * (attempt + 1))
                    logger.warning(f"[LLM] Async rate limit hit (429), waiting {wait_time:.1f}s (attempt {attempt+1}/{max_retries})...")
                    await asyncio.sleep(min(wait_time, 15.0))
                    continue
                response.raise_for_status()
                return self._parse_response(response.json())

    def is_available(self) -> bool:
        try:
            url = f"{self._base_url}/models"
            with httpx.Client(timeout=10.0) as client:
                response = client.get(url, headers=self._get_headers())
                return response.status_code == 200
        except Exception:
            return False

    def get_model_name(self) -> str:
        return self._model

    def set_model(self, model_name: str) -> None:
        """Set the model for subsequent requests."""
        self._model = model_name

    @classmethod
    def from_env(cls) -> "CerebrasClient":
        return cls()
