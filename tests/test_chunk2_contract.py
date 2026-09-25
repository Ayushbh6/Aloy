"""Shared backend/native wire example: fixtures are synthetic and deliberately complete."""

import json
from pathlib import Path

from aloy.canvas import validate_canvas_input
from aloy.storage import ConversationStore


def test_shared_native_fixture_matches_canonical_envelope(tmp_path):
    fixture = json.loads((Path(__file__).parent / "fixtures/chunk2_canvas.json").read_text())
    payload = validate_canvas_input({"title": fixture["title"], "blocks": fixture["blocks"]})
    store = ConversationStore(tmp_path)
    try:
        cid = store.create_conversation("Synthetic test")
        run = store.begin_run(cid, "fake", "fake-v1", "Explain word order")
        actual = store.save_canvas_artifact(run, payload, operation_id="synthetic-operation")
        assert set(actual) == set(fixture)
        assert actual["blocks"] == fixture["blocks"]
        assert actual["schema_version"] == fixture["schema_version"]
        store.finish_run(run, "completed", "The verb stays second.")
        assert store.canvas_artifacts(cid) == [actual]
    finally:
        store.close()
