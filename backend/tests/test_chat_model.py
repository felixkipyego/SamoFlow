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
import asyncio
import json
import logging
import threading

import pytest
from pydantic import BaseModel

from app.agent import chat_model
from app.config import SettingsError, get_settings
from tests.conftest import VALID_ENV, set_valid_env

# Every test below that calls get_chat_model() clears the cache itself
# FIRST (matching qdrant.get_qdrant_client.cache_clear()'s own
# established inline-call precedent, test_job_handlers.py's
# _prepare_env()), so no test ever inherits a stale instance built under
# a different test's own env. Construction itself opens no socket
# (confirmed live), so this is purely about env isolation for most tests
# here -- EXCEPT the one real-connection regression test near the bottom
# of this file (Task 3.4.b), which also needs
# close_chat_model_cache_between_tests (tests/conftest.py) specifically
# because IT establishes a real (local, fake-server) connection -- see
# that test's own docstring and chat_model.py's own header comment for
# the full event-loop/keep-alive finding this guards against.

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


def test_get_chat_model_sets_max_retries_zero_and_the_configured_timeout(monkeypatch):
    # Task 3.4.b: explicit, never the installed SDK default of 2 -- see
    # chat_model.py's own header comment for the live 2.597s-vs-0.008s
    # proof of why this matters for .with_fallbacks().
    set_valid_env(
        monkeypatch,
        VALID_ENV,
        ANTHROPIC_API_KEY="sk-ant-real-looking-key",
        CHAT_MODEL_TIMEOUT_SECONDS="7",
    )
    chat_model.get_chat_model.cache_clear()
    model = chat_model.get_chat_model()
    primary = model.runnable
    secondary = model.fallbacks[0]
    assert primary.root_async_client.max_retries == 0
    assert secondary._async_client.max_retries == 0
    assert primary.request_timeout == 7.0
    assert secondary.default_request_timeout == 7.0


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


# --- Event-loop/keep-alive regression proof (Task 3.4.b) --------------------
#
# Confirmed live, during this task's own research, NOT reproducible
# against an unreachable address (the connection never succeeds, so no
# keep-alive connection is ever pooled) -- only against something that
# completes a REAL request/response cycle over a REAL (not mocked)
# transport. A local server is used so this stays a zero-cost, zero-
# external-dependency, committed regression test, matching this
# project's own "never a real paid call in the committed suite"
# convention -- Python's own blocking `http.server` (this project's
# established `local_http_server()`, tests/conftest.py) does NOT
# reproduce it (confirmed live, tried first): its default HTTP/1.0
# behavior closes the connection after every request, so nothing is ever
# pooled for the bug to exploit. This needs a REAL, independently-
# running async server with genuine HTTP/1.1 keep-alive, kept alive in
# its own dedicated background thread/event loop so it outlives each
# short-lived client-side `asyncio.run()` call.


def _start_local_keep_alive_openai_like_server() -> tuple[int, threading.Thread]:
    body = json.dumps(
        {
            "id": "x",
            "object": "chat.completion",
            "created": 1,
            "model": "gpt-4o-mini",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "hi"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    ).encode()

    async def _handle(reader, writer):
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                response = (
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                    + str(len(body)).encode()
                    + b"\r\nConnection: keep-alive\r\n\r\n"
                    + body
                )
                writer.write(response)
                await writer.drain()
        except Exception:  # noqa: S110 -- expected teardown noise, see below
            # The client side closes its connection abruptly once its own
            # event loop ends (that's the whole point of this test) --
            # ordinary, expected teardown noise on the server side, not a
            # real failure to surface.
            pass

    port_holder: dict = {}
    server_loop = asyncio.new_event_loop()

    def _run_server():
        asyncio.set_event_loop(server_loop)

        async def _start():
            server = await asyncio.start_server(_handle, "127.0.0.1", 0)
            port_holder["port"] = server.sockets[0].getsockname()[1]
            async with server:
                await server.serve_forever()

        server_loop.run_until_complete(_start())

    thread = threading.Thread(target=_run_server, daemon=True)
    thread.start()
    while "port" not in port_holder:
        pass
    return port_holder["port"], thread


@pytest.mark.usefixtures("close_chat_model_cache_between_tests")
def test_a_cached_chat_model_can_hit_runtimeerror_across_repeated_asyncio_run_calls():
    """Proves `get_chat_model()`'s own cached instance is NOT universally
    safe to reuse across separately-closed event loops, once a real
    connection has actually been established -- confirmed live, this is
    a real, reproducible hazard (chat_model.py's own header comment has
    the full finding and root cause).

    Tests the underlying MECHANISM directly (a `ChatOpenAI`, built the
    identical way `get_chat_model()` builds its own primary), not
    `get_chat_model()` itself: that function has no injectable `base_url`,
    and adding one purely so a test could point it at a local fake server
    would be speculative production surface this project's own rule 11
    argues against -- the real risk lives in the SHARED LangChain/openai-
    SDK/httpx machinery both paths use identically, not in anything
    `get_chat_model()` itself does differently.

    Not perfectly deterministic on which specific repetition fails --
    confirmed live, by direct experimentation before settling on these
    exact parameters: httpx's own connection pool discards a keep-alive
    connection once it's older than `keepalive_expiry` (default 5.0s,
    confirmed by reading `httpx._config.Limits()`'s own default directly)
    BEFORE even attempting to reuse it, which can mask this bug entirely
    if too much wall-clock time passes between repetitions -- so this
    test passes `httpx.Limits(keepalive_expiry=None)` explicitly (never
    age out a connection) to remove that confound. Even then, 10
    repetitions was NOT reliable (observed both "fails at least once" and
    "never fails" across repeated trial runs); **40 repetitions, tested
    empirically across 10 independent trials during this task's own
    build, failed at least once in all 10** (observed counts of 2-6
    `RuntimeError`s per 40 attempts) -- the number below is chosen from
    that real data, not a guess. Asserts "at least one" within 40 tries,
    never "the Nth one specifically", for exactly this reason.
    """
    import httpx
    from langchain_openai import ChatOpenAI

    port, _thread = _start_local_keep_alive_openai_like_server()
    async_client = httpx.AsyncClient(limits=httpx.Limits(keepalive_expiry=None))
    model = ChatOpenAI(
        model="gpt-4o-mini",
        api_key="sk-fake",
        base_url=f"http://127.0.0.1:{port}/v1",
        http_async_client=async_client,
        max_retries=0,
        timeout=5,
    )

    async def _use_it():
        try:
            await model.ainvoke("hi")
            return "OK"
        except Exception as e:  # noqa: BLE001 -- classifying by name, not catching to suppress
            return type(e).__name__

    results = [asyncio.run(_use_it()) for _ in range(40)]
    assert "RuntimeError" in results, (
        f"expected at least one 'Event loop is closed' RuntimeError across 40 "
        f"repeated asyncio.run() calls reusing one cached model, got: {results}"
    )
