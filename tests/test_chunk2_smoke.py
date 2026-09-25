import asyncio
from types import SimpleNamespace

import pytest

from aloy.chunk2_smoke import check
from aloy.contracts import AgentConfig, Message, ProviderTurn
from aloy.dispatch import DispatchBudget
from aloy.providers import GeminiProvider


def test_canvas_smoke_uses_canonical_fake_runtime():
    result = asyncio.run(check())
    assert result["result"] == "passed"
    assert result["artifacts"] == 1
    assert result["dispatches"] == 0
    assert result["estimated_usd"] == 0


def test_smoke_rejects_unbounded_provider():
    with pytest.raises(ValueError):
        asyncio.run(check(route="codex"))


def test_incomplete_canvas_generation_fails_with_content_free_diagnostics():
    async def scenario():
        async def create(**kwargs):
            async def events():
                yield SimpleNamespace(
                    event_type="interaction.completed",
                    interaction=SimpleNamespace(
                        id="synthetic",
                        status="incomplete",
                        usage=SimpleNamespace(total_input_tokens=30, total_output_tokens=1200),
                    ),
                )

            return events()

        provider = GeminiProvider.__new__(GeminiProvider)
        provider.client = SimpleNamespace(
            aio=SimpleNamespace(interactions=SimpleNamespace(create=create))
        )
        provider.dispatch = DispatchBudget(limit=1)
        turn = ProviderTurn("synthetic", "Synthetic", [Message("user", "Synthetic")], AgentConfig())
        with pytest.raises(RuntimeError, match="status=incomplete, output_tokens=1200"):
            await provider.step(turn)
        assert provider.dispatch.count == 1

    asyncio.run(scenario())
