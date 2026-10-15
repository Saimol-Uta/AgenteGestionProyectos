"""Abstract port for LLM provider adapters."""

from abc import ABC, abstractmethod

from agente_gestion.domain.llm import (
    GenerationRequest,
    GenerationResult,
    ModelCapabilities,
)


class LLMProviderPort(ABC):
    """Port for Gemini, Groq, and deterministic sandbox model adapters."""

    @abstractmethod
    def generate(self, request: GenerationRequest) -> GenerationResult:
        """Execute model inference and return structured generation result."""
        raise NotImplementedError

    @abstractmethod
    def get_capabilities(self) -> ModelCapabilities:
        """Return advertised capabilities and token limits of the underlying model."""
        raise NotImplementedError
