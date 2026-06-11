"""Provider integration and structured LLM calls."""

from testdesigner_ai.llm.client import StructuredLLMClient
from testdesigner_ai.llm.factory import create_chat_model, create_llm_client
from testdesigner_ai.llm.models import PromptMessage, StructuredLLMAttempt
from testdesigner_ai.llm.ollama import create_ollama_model
from testdesigner_ai.llm.openrouter import create_openrouter_model
from testdesigner_ai.llm.settings import LLMProvider, LLMSettings

__all__ = [
    "LLMProvider",
    "LLMSettings",
    "PromptMessage",
    "StructuredLLMAttempt",
    "StructuredLLMClient",
    "create_chat_model",
    "create_llm_client",
    "create_ollama_model",
    "create_openrouter_model",
]
