"""WebSocket relay with real servers: frames both ways, client close, abrupt upstream drop."""
import asyncio
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

GATEWAY_DIR = Path(__file__).resolve().parents[1]
for p in (str(GATEWAY_DIR), str(GATEWAY_DIR.parent / "libs" / "service_common"), str(GATEWAY_DIR.parent / "kit")):
    if p not in sys.path:
        sys.path.insert(0, p)

uvicorn = pytest.importorskip("uvicorn")
websockets = pytest.importorskip("websockets")


def _port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(app, port):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    return server


async def upstream(scope, receive, send):
    """Echo; 'drop' makes the server vanish without a close frame."""
    if scope["type"] != "websocket":
        return
    await receive()
    await send({"type": "websocket.accept"})
    await send({"type": "websocket.send", "text": "0{\"hello\":1}"})
    while True:
        msg = await receive()
        if msg["type"] == "websocket.disconnect":
            return
        text = msg.get("text")
        if text == "drop":
            raise RuntimeError("upstream crashed")  # uvicorn aborts the connection
        await send({"type": "websocket.send", "text": "echo:" + (text or "")})


@pytest.fixture(scope="module")
def stack():
    from gateway_app.config import SERVICE_NAMES, GatewaySettings
    from gateway_app.main import create_app
    from mskit.http_client import CallPolicy

    up_port, gw_port = _port(), _port()
    up = _serve(upstream, up_port)
    settings = GatewaySettings(service_urls={n: f"http://127.0.0.1:{up_port}" for n in SERVICE_NAMES}, policy=CallPolicy())
    gw = _serve(create_app(settings), gw_port)
    yield gw_port
    gw.should_exit = up.should_exit = True


def test_frames_relay_both_ways_and_close(stack):
    from websockets.asyncio.client import connect

    async def run():
        async with connect(f"ws://127.0.0.1:{stack}/socket.io/?EIO=4&transport=websocket", open_timeout=5) as ws:
            assert await asyncio.wait_for(ws.recv(), 5) == '0{"hello":1}'
            await ws.send("ping")
            assert await asyncio.wait_for(ws.recv(), 5) == "echo:ping"
    asyncio.run(run())


def test_abrupt_upstream_drop_closes_the_client_promptly(stack):
    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed

    async def run():
        async with connect(f"ws://127.0.0.1:{stack}/socket.io/?EIO=4&transport=websocket", open_timeout=5) as ws:
            await asyncio.wait_for(ws.recv(), 5)
            await ws.send("drop")
            with pytest.raises(ConnectionClosed):
                await asyncio.wait_for(ws.recv(), 5)  # must not hang
    asyncio.run(run())
