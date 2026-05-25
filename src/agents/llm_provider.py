import os
from langchain_ollama import ChatOllama

from src.agents.utils import check_llm_connection

MODEL_NAME = 'qwen2.5:14b-instruct'
os.environ['OPENAI_API_KEY'] = 'api key'


def create_llm() -> ChatOllama:
    return ChatOllama(
        model=MODEL_NAME,
        temperature=0,
    )


llm = create_llm()

check_llm_connection(llm)