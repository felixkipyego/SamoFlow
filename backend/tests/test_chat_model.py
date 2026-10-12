# backend/tests/test_chat_model.py
# Task 3.4.a [SECURITY]: Settings field tests, get_chat_model() behavior,
# and two mechanism proofs the real answer node (Step 3.4.b) will depend
# on -- both run against hand-built LangChain FAKE chat models, no real
# API calls anywhere in this file (matching this project's own established
# "verify live once with fakes/test doubles, never a real paid call in the
# committed suite" convention, e.g. patch_embed_dense()).
#
# Construction-only proofs throughout: init_chat_model()/ChatOpenAI()/
# ChatAnthropic() never validate a key against a live endpoint at
# CONSTRUCTION time (confirmed live, before writing these tests, by
# constructing ChatOpenAI() with an obviously-fake key and an unset env
# var -- it raised only when no key was found anywhere, never "the key is
# wrong"; a real auth failure only surfaces on an actual call). This lets
# get_chat_model() itself be tested fully offline, with VALID_ENV's own
# fake OPENAI_API_KEY -- no live test-service dependency, unlike most
# other [SECURITY] tasks in this project.
import logging

import pytest
from pydantic import BaseModel

from app.agent import chat_model
from app.config import SettingsError, get_settings
from tests.conftest import VALID_ENV, set_valid_env

# No shared teardown fixture needed: get_chat_model()'s own @lru_cache
# holds a plain Python object (construction opens no socket/connection,
# confirmed live -- see chat_model.py's own header comment), unlike
# get_qdrant_client()'s own cached client. Every test below that calls
# get_chat_model() clears the cache itself FIRST (matching qdrant.
# get_qdrant_client.cache_clear()'s own established inline-call
# precedent, test_job_handlers.py's _prepare_env()), so no test ever
# inherits a stale instance built under a different test's own env.

# --- Settings fields ---------------------------------------------------------


def test_anthropic_api_key_defaults_to_none(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    assert get_settings().anthropic_api_key is None
    assert get_settings().anthropic_api_key_str() is None


def test_anthropic_api_key_empty_string_means_not_configured(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV, ANTHROPIC_API_KEY="")
    assert get_settings().anthropic_api_key is None


def test_anthropic_api_key_set_round_trips(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV, ANTHROPIC_API_KEY="sk-ant-real-looking-key")
    assert get_settings().anthropic_api_key_str() == "sk-ant-real-looking-key"


def test_anthropic_api_key_rejects_whitespace(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV, ANTHROPIC_API_KEY="bad\nkey")
    with pytest.raises(SettingsError):
        get_settings()


def test_chat_model_primary_and_secondary_have_defaults(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    settings = get_settings()
    assert settings.chat_model_primary
    assert settings.chat_model_secondary


# --- get_chat_model() ---------------------------------------------------------


def test_get_chat_model_is_primary_only_when_no_secondary_key(monkeypatch, caplog):
    set_valid_env(monkeypatch, VALID_ENV)
    chat_model.get_chat_model.cache_clear()
    with caplog.at_level(logging.WARNING):
        model = chat_model.get_chat_model()
    assert type(model).__name__ == "ChatOpenAI"
    assert "ANTHROPIC_API_KEY is not set" in caplog.text


def test_get_chat_model_builds_a_fallback_chain_when_secondary_key_is_set(monkeypatch, caplog):
    set_valid_env(monkeypatch, VALID_ENV, ANTHROPIC_API_KEY="sk-ant-real-looking-key")
    chat_model.get_chat_model.cache_clear()
    with caplog.at_level(logging.WARNING):
        model = chat_model.get_chat_model()
    assert type(model).__name__ == "RunnableWithFallbacks"
    assert type(model.runnable).__name__ == "ChatOpenAI"
    assert len(model.fallbacks) == 1
    assert type(model.fallbacks[0]).__name__ == "ChatAnthropic"
    assert "ANTHROPIC_API_KEY is not set" not in caplog.text


def test_get_chat_model_is_a_cached_singleton(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    chat_model.get_chat_model.cache_clear()
    first = chat_model.get_chat_model()
    second = chat_model.get_chat_model()
    assert first is second


# --- Mechanism proof 1: with_structured_output through .with_fallbacks() ----
#
# get_chat_model() always returns a plain Runnable (BaseChatModel or
# RunnableWithFallbacks) -- 3.4.b's own answer node will call
# `.with_structured_output(Answer)` on WHATEVER get_chat_model() returns,
# then `.invoke()`/`.ainvoke()` it. Neither of langchain_core's own
# built-in fake chat models (GenericFakeChatModel, FakeListChatModel)
# implements with_structured_output() -- confirmed live, both raise
# NotImplementedError (it needs bind_tools(), which neither fake
# implements) -- so this file defines its own minimal fake specifically
# to prove the FALLBACK MECHANICS (does invoking a with_structured_output-
# wrapped chain correctly fall through to the SECONDARY provider's own
# with_structured_output-wrapped chain when the primary raises, with the
# final result still the right pydantic type), not to re-test LangChain's
# own already-tested with_structured_output() implementation itself.


def _build_fake_structured_model(content: str, *, should_fail: bool = False):
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.runnables import RunnableLambda

    class _Fake(BaseChatModel):
        fake_content: str
        fake_should_fail: bool = False

        @property
        def _llm_type(self) -> str:
            return "fake-structured"

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if self.fake_should_fail:
                raise RuntimeError("primary provider down")
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content=self.fake_content))]
            )

        def with_structured_output(self, schema, **kwargs):
            def _parse(ai_message):
                return schema.model_validate_json(ai_message.content)

            return self | RunnableLambda(_parse)

    return _Fake(fake_content=content, fake_should_fail=should_fail)


class _FakeAnswer(BaseModel):
    can_answer: bool
    answer: str
    used_chunk_ids: list[str]


def test_structured_output_falls_through_to_the_secondary_provider_on_primary_failure():
    primary = _build_fake_structured_model("irrelevant -- never reached", should_fail=True)
    secondary = _build_fake_structured_model(
        '{"can_answer": true, "answer": "hi", "used_chunk_ids": ["doc-1"]}'
    )

    chain = primary.with_structured_output(_FakeAnswer).with_fallbacks(
        [secondary.with_structured_output(_FakeAnswer)]
    )
    result = chain.invoke("irrelevant input")

    assert isinstance(result, _FakeAnswer)
    assert result.can_answer is True
    assert result.used_chunk_ids == ["doc-1"]


def test_structured_output_works_directly_with_no_fallback_needed():
    # Control: proves the primary path itself (no failure, no fallback
    # engaged) produces the correct type too -- the fallback test above
    # alone wouldn't distinguish "fallback mechanics work" from "the
    # fallback's own result happens to be right regardless of how it was
    # reached."
    primary = _build_fake_structured_model(
        '{"can_answer": false, "answer": "", "used_chunk_ids": []}'
    )
    result = primary.with_structured_output(_FakeAnswer).invoke("irrelevant input")
    assert isinstance(result, _FakeAnswer)
    assert result.can_answer is False


# --- Mechanism proof 2: stream_mode="messages" emits real token chunks -----
#
# docs/SPEC.md §10: `stream_mode=["messages","custom","updates"]`:
# `messages` becomes `token`. Proves this live, through a REAL LangGraph
# node (not a bare model.astream() call outside any graph), using
# GenericFakeChatModel (langchain_core's own fake -- DOES implement real
# streaming, confirmed live by reading its source: it splits content on
# whitespace and calls run_manager.on_llm_new_token() per piece, the exact
# callback LangGraph's own stream_mode="messages" listens for).
#
# Confirmed live, worth recording: the node below calls plain
# model.ainvoke() (NOT model.astream()) -- real token-by-token chunks
# still arrive under stream_mode="messages" regardless, because LangGraph
# instruments the model's own callback wiring at the GRAPH level, not by
# requiring the node to explicitly stream. 3.4.b's own answer node does
# NOT need to call .astream() on the chat model itself for this to work.


async def test_stream_mode_messages_emits_token_by_token_chunks_from_a_graph_node():
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage
    from langgraph.graph import END, START, MessagesState, StateGraph

    async def _node(state):
        model = GenericFakeChatModel(messages=iter([AIMessage(content="hello world answer")]))
        response = await model.ainvoke(state["messages"])
        return {"messages": [response]}

    builder = StateGraph(MessagesState)
    builder.add_node("answer", _node)
    builder.add_edge(START, "answer")
    builder.add_edge("answer", END)
    graph = builder.compile()

    tokens = []
    async for message_chunk, _metadata in graph.astream(
        {"messages": ["hi"]}, stream_mode="messages"
    ):
        if message_chunk.content:
            tokens.append(message_chunk.content)

    # More than one chunk proves real token-by-token delivery, not the
    # whole answer arriving as a single chunk under this stream mode.
    assert len(tokens) > 1
    assert "".join(tokens) == "hello world answer"
