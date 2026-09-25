"""One durable run lifecycle shared by text and audio entry points."""

import asyncio

from aloy.contracts import Usage
from aloy.storage import ConversationStore


class RunLifecycle:
    def __init__(
        self,
        store: ConversationStore,
        conversation_id: str,
        provider: str,
        model: str,
        text: str,
        reservation: float,
        *,
        externally_accounted: bool = False,
        input_origin: str = "user",
    ):
        self.store = store
        self.conversation_id = conversation_id
        self.provider, self.model, self.text = provider, model, text
        self.reservation = reservation
        self.partial = ""
        self.usage = Usage()
        self.finished = False
        self.externally_accounted = externally_accounted
        self.input_origin = input_origin

    def __enter__(self):
        with self.store.transaction():
            self.reservation_id = self.store.reserve(self.reservation, provider=self.provider)
            self.run_id = self.store.begin_run(
                self.conversation_id,
                self.provider,
                self.model,
                self.text,
                input_origin=self.input_origin,
            )
        return self

    def dispatched(self):
        self.store.mark_dispatched(self.reservation_id)

    def finish(self, status: str, error: str = ""):
        if self.finished:
            return
        with self.store.transaction():
            self.store.finish_run(
                self.run_id, status, self.partial, self.usage, error, accounted=True
            )
            self.store.attach_run_audio(self.run_id)
            # Only a completed response with known usage releases unused allowance.
            self.store.settle(
                self.reservation_id,
                0.0
                if self.externally_accounted
                else self.usage.estimated_usd
                if status == "completed"
                else None,
            )
        self.finished = True

    def complete(self, text: str, usage: Usage):
        self.partial, self.usage = text, usage
        self.finish("completed")

    def __exit__(self, kind, error, traceback):
        if not self.finished:
            cancelled = kind is not None and issubclass(
                kind, (asyncio.CancelledError, GeneratorExit)
            )
            self.finish(
                "cancelled" if cancelled else "failed",
                type(error).__name__ if error else "Incomplete run",
            )
