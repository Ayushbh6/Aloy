import socket

import pytest


@pytest.fixture(autouse=True)
def offline_network_guard(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Ordinary tests must not access the network")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)

    async def no_ollama(*args, **kwargs):
        raise RuntimeError("Ollama unavailable in offline tests; inject a fake embedder")

    monkeypatch.setattr("aloy.memory_index.OllamaEmbeddings.fingerprint", no_ollama)
    monkeypatch.setattr("aloy.memory_index.OllamaEmbeddings.embed", no_ollama)
