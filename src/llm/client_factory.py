import os
from enum import Enum
from typing import Optional
from dotenv import load_dotenv

from src.llm.base_client import BaseLLMClient
from src.llm.cerebras_client import CerebrasClient

load_dotenv()


class LLMProvider(str, Enum):
    CEREBRAS = "cerebras"


class LLMClientFactory:
    @staticmethod
    def create(provider: Optional[str] = None) -> BaseLLMClient:
        provider = provider or os.getenv("ACTIVE_LLM_PROVIDER", "cerebras")
        provider = provider.lower().strip()

        if provider == LLMProvider.CEREBRAS.value:
            return CerebrasClient()
        else:
            raise ValueError(
                f"Unknown LLM provider: {provider}. Available: cerebras"
            )

    @staticmethod
    def get_available_providers() -> list[str]:
        available = []
        try:
            client = CerebrasClient()
            if client.is_available():
                available.append(LLMProvider.CEREBRAS.value)
        except Exception:
            pass
        return available


def get_llm_client(provider: Optional[str] = None) -> BaseLLMClient:
    """Convenience function to get an LLM client."""
    return LLMClientFactory.create(provider)
