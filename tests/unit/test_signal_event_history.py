"""Reading presentation history must never steal simulator commands."""

import pytest

from services.gateway import signal_control as signals


@pytest.mark.asyncio
async def test_history_is_bounded_and_non_destructive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(signals, "_command_history", [])
    monkeypatch.setattr(signals, "_pending_commands", [])
    for index in range(15):
        signals._queue_command({"signal_id": str(index), "action": "EXTEND_GREEN_5"})
    signals._queue_command({"signal_id": "hold", "action": "HOLD"})
    result = await signals.command_history()
    assert len(result["commands"]) == 10
    assert result["commands"][0]["signal_id"] == "5"
    assert signals.pending_command_count() == 16
    await signals.drain_pending_commands()
    assert await signals.command_history() == result
