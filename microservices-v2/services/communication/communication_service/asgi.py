"""Socket.IO around the communication API, exactly like the monolith's ``socket_app`` (app/main.py).

This is the only Socket.IO server in v2. Other services publish their real-time events through Redis
(``SOCKETIO_REDIS_URL``, mskit.realtime) and this server delivers them to the connected clients.
"""
import importlib


def wrap(app):
    import socketio

    importlib.import_module("communication_service.realtime.events")  # registers the Socket.IO handlers
    from communication_service.realtime.server import SOCKETIO_PATH, sio

    return socketio.ASGIApp(sio, other_asgi_app=app, socketio_path=SOCKETIO_PATH)
