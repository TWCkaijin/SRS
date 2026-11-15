import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from srs.ping import ping_host


@pytest.mark.asyncio
async def test_ping_parses_icmp_time_and_never_uses_shell(monkeypatch):
    process = Mock(returncode=0, communicate=AsyncMock(return_value=(b'64 bytes: time=1.25 ms', b'')))
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr('srs.ping.shutil.which', lambda _: '/sbin/ping')
    monkeypatch.setattr('srs.ping.asyncio.create_subprocess_exec', spawn)
    assert await ping_host('127.0.0.1') == {'status': 'ok', 'ms': 1.25}
    assert spawn.call_args.args == ('/sbin/ping', '-n', '-c', '1', '127.0.0.1')
    assert (await ping_host('-invalid'))['status'] == 'unavailable'
    assert spawn.await_count == 1


@pytest.mark.asyncio
async def test_timeout_reaps_ping_process(monkeypatch):
    process = Mock(returncode=None, communicate=AsyncMock(side_effect=TimeoutError), wait=AsyncMock())
    monkeypatch.setattr('srs.ping.shutil.which', lambda _: '/sbin/ping')
    monkeypatch.setattr('srs.ping.asyncio.create_subprocess_exec', AsyncMock(return_value=process))
    assert (await ping_host('127.0.0.1'))['status'] == 'timeout'
    process.kill.assert_called_once()
    process.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancellation_reaps_ping_process(monkeypatch):
    process = Mock(returncode=None, communicate=AsyncMock(side_effect=asyncio.CancelledError), wait=AsyncMock())
    monkeypatch.setattr('srs.ping.shutil.which', lambda _: '/sbin/ping')
    monkeypatch.setattr('srs.ping.asyncio.create_subprocess_exec', AsyncMock(return_value=process))
    with pytest.raises(asyncio.CancelledError):
        await ping_host('127.0.0.1')
    process.kill.assert_called_once()
