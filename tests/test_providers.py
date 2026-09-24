import asyncio
from types import SimpleNamespace as NS

import pytest

from aloy import AgentConfig, ChatAgent, ConversationStore
from aloy.dispatch import DispatchBudget, gemini_client
from aloy.providers import GeminiProvider, OpenRouterProvider


def test_gemini_retries_explicitly_disabled(monkeypatch):
    from google import genai

    captured = {}
    monkeypatch.setattr(genai, "Client", lambda **kw: captured.update(kw))
    gemini_client("synthetic-not-a-key")
    assert captured["http_options"]["retry_options"]["attempts"] == 1


@pytest.mark.parametrize("completion", [True, False])
def test_gemini_payload_session_and_dispatch(tmp_path, monkeypatch, completion):
    calls = []

    async def get(**kw):
        return NS(name=kw["model"])

    async def create(**kw):
        calls.append(kw)

        async def events():
            yield NS(event_type="step.delta", delta=NS(type="text", text="Hallo"))
            if completion:
                yield NS(
                    event_type="interaction.completed",
                    interaction=NS(
                        id="remote-id", usage=NS(total_input_tokens=2, total_output_tokens=3)
                    ),
                )

        return events()

    monkeypatch.setattr(
        "aloy.providers.gemini_client",
        lambda _: NS(aio=NS(models=NS(get=get), interactions=NS(create=create))),
    )

    async def scenario():
        store = ConversationStore(tmp_path)
        config = AgentConfig(system_prompt="P")
        cid = store.create_conversation("P")
        dispatch = DispatchBudget(limit=2)
        provider = GeminiProvider(store, api_key="synthetic", dispatch=dispatch)
        agent = ChatAgent(config, store, provider)
        if completion:
            await agent.reply(cid, "Q1")
            await agent.reply(cid, "Q2")
            assert calls[0]["system_instruction"] == "P"
            assert calls[0]["input"][0]["content"][0]["text"] == "Q1"
            assert calls[1]["previous_interaction_id"] == "remote-id"
            assert calls[1]["input"] == "Q2"
            assert dispatch.count == 2
        else:
            with pytest.raises(RuntimeError, match="without completion"):
                await agent.reply(cid, "Q1")
            assert store.messages(cid)[-1]["status"] == "failed"
            assert dispatch.count == 1
        store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("available,complete", [(True, True), (True, False), (False, False)])
def test_openrouter_preflight_and_no_incomplete_success(tmp_path, monkeypatch, available, complete):
    import openai

    calls = []
    options = {}

    async def catalog():
        return NS(data=[NS(id="deepseek/deepseek-v4.1-flash")] if available else [])

    async def create(**kw):
        calls.append(kw)

        async def events():
            yield NS(
                choices=[NS(delta=NS(content="Hallo"), finish_reason="stop" if complete else None)],
                usage=NS(prompt_tokens=2, completion_tokens=3),
            )

        return events()

    def client(**kw):
        options.update(kw)
        return NS(models=NS(list=catalog), chat=NS(completions=NS(create=create)))

    monkeypatch.setattr(openai, "AsyncOpenAI", client)

    async def scenario():
        store = ConversationStore(tmp_path)
        cid = store.create_conversation("P")
        config = AgentConfig(
            system_prompt="P", provider="openrouter", model="deepseek/deepseek-v4.1-flash"
        )
        guard = DispatchBudget(limit=1)
        agent = ChatAgent(config, store, OpenRouterProvider(api_key="synthetic", dispatch=guard))
        if available and complete:
            assert (await agent.reply(cid, "Q")).text == "Hallo"
            assert calls[0]["extra_body"]["provider"]["allow_fallbacks"] is False
        else:
            with pytest.raises(RuntimeError):
                await agent.reply(cid, "Q")
        assert guard.count == int(available)
        assert len(calls) == int(available)
        assert options["max_retries"] == 0
        store.close()

    asyncio.run(scenario())


def test_missing_credentials_fail_before_client_creation(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        OpenRouterProvider()
    store = ConversationStore(tmp_path)
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        GeminiProvider(store)
    store.close()
