import socket

import pytest


@pytest.fixture(autouse=True)
def offline_network_guard(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Ordinary tests must not access the network")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
