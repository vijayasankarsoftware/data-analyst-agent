"""LLM + embedding clients. One place to swap providers or models.

Both talk the OpenAI wire protocol, so the same code works with api.openai.com, a
self-hosted vLLM/SGLang server, or a small FastAPI embedding service (e.g. BGE-M3).
"""

import re
from functools import lru_cache
from typing import Any

from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from pydantic import SecretStr

from analyst.config import get_settings

_THINK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


@lru_cache
def get_chat_model() -> ChatOpenAI:
    s = get_settings()
    extra: dict[str, Any] = {}
    if s.llm_disable_thinking:
        extra["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
    return ChatOpenAI(
        model=s.require("openai_model"),
        base_url=s.openai_base_url,
        api_key=s.openai_api_key or SecretStr("not-needed"),
        temperature=0,  # analysis should be deterministic-ish and reproducible in evals
        timeout=180,
        max_retries=2,
        **extra,
    )


def structured_model(schema: type[Any]) -> Runnable[Any, Any]:
    """Chat model that returns an instance of `schema` (a Pydantic model)."""
    return get_chat_model().with_structured_output(schema, method=get_settings().llm_structured_method)


def strip_thinking(text: str) -> str:
    """Remove <think>…</think> blocks some reasoning models put in their content."""
    return _THINK.sub("", text).strip()


@lru_cache
def get_embeddings() -> OpenAIEmbeddings:
    s = get_settings()
    hosted_openai = s.embedding_base_url is None
    key = s.embedding_api_key or (s.openai_api_key if hosted_openai else None) or SecretStr("not-needed")
    return OpenAIEmbeddings(
        model=s.embedding_model,
        base_url=s.embedding_base_url,
        api_key=key,
        # Only OpenAI's text-embedding-3 models accept `dimensions`; other servers return their native size.
        dimensions=s.embedding_dim if hosted_openai else None,
        # Send raw text. (True = tokenize with OpenAI's tiktoken and send token ids, which other models can't read.)
        check_embedding_ctx_length=hosted_openai,
        chunk_size=s.embedding_batch_size,
        max_retries=3,
    )


def _check(vectors: list[list[float]]) -> list[list[float]]:
    expected = get_settings().embedding_dim
    if vectors and len(vectors[0]) != expected:
        raise RuntimeError(
            f"embedding model returned {len(vectors[0])}-dim vectors but EMBEDDING_DIM={expected}; "
            "fix the setting (and the vector(...) columns) to match the model"
        )
    return vectors


async def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    return _check(await get_embeddings().aembed_documents(texts))


async def embed_query(text: str) -> list[float]:
    return _check([await get_embeddings().aembed_query(text)])[0]
