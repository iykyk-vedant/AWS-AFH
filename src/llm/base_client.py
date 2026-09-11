from abc import ABC, abstractmethod
from typing import Optional
from dataclasses import dataclass


@dataclass
class LLMResponse:
    content: str
    model: str
    usage: Optional[dict] = None
    raw_response: Optional[dict] = None


class BaseLLMClient(ABC):
    @abstractmethod
    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        pass

    @abstractmethod
    async def achat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        pass

    @abstractmethod
    def is_available(self) -> bool:
        pass

    @abstractmethod
    def get_model_name(self) -> str:
        pass

    def generate(self, prompt: str, **kwargs) -> str:
        """Simple single-prompt generation."""
        messages = [{"role": "user", "content": prompt}]
        response = self.chat(messages, **kwargs)
        return response.content

    def complete(self, messages: list, **kwargs) -> LLMResponse:
        """Complete with flexible message format support."""
        dict_messages = []
        for m in messages:
            if hasattr(m, "role") and hasattr(m, "content"):
                dict_messages.append({"role": m.role, "content": m.content})
            elif isinstance(m, dict):
                dict_messages.append(m)
        return self.chat(dict_messages, **kwargs)
