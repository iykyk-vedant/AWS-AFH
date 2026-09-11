"""
Base agent class for Amaze on Work.

Adapted from tata-lcr project with Amaze on Work-specific pipeline state.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from src.agents.state import PipelineState, AgentType
from src.llm.base_client import BaseLLMClient


@dataclass
class AgentResponse:
    """Standard response from any agent."""
    success: bool
    message: str
    data: Any = None
    next_agent: str | None = None
    needs_retry: bool = False
    error: str | None = None


class BaseAgent(ABC):
    """Base class for all Amaze on Work agents."""
    agent_type: AgentType

    def __init__(self, llm_client: BaseLLMClient):
        self.llm = llm_client

    @abstractmethod
    def execute(self, state: PipelineState) -> AgentResponse:
        """Synchronous execution of the agent's task."""
        pass

    @abstractmethod
    async def aexecute(self, state: PipelineState) -> AgentResponse:
        """Async execution of the agent's task."""
        pass

    def get_system_prompt(self) -> str:
        """Get the system prompt for this agent's LLM calls."""
        return (
            f"You are the {self.agent_type.value} agent in the Amaze on Work "
            f"incident resolution system. You help resolve software incidents "
            f"by analyzing code, diagnosing root causes, and applying fixes."
        )

    def _llm_call(
        self,
        user_prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> str:
        """Make an LLM call with system + user prompts."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        else:
            messages.append({"role": "system", "content": self.get_system_prompt()})
        messages.append({"role": "user", "content": user_prompt})

        response = self.llm.chat(messages, temperature=temperature, max_tokens=max_tokens)
        return response.content

    async def _allm_call(
        self,
        user_prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> str:
        """Async LLM call."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        else:
            messages.append({"role": "system", "content": self.get_system_prompt()})
        messages.append({"role": "user", "content": user_prompt})

        response = await self.llm.achat(messages, temperature=temperature, max_tokens=max_tokens)
        return response.content
