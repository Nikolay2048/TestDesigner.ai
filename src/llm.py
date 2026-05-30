from langchain_ollama import ChatOllama

MODEL = "qwen2.5:14b-instruct"


def create_llm() -> ChatOllama:
    """Единая точка создания LLM. Поменяй MODEL — переключишь всю систему."""
    return ChatOllama(model=MODEL, temperature=0)
