"""SQLite is the canonical local transcript and run ledger."""

# ruff: noqa: E501

import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from aloy.contracts import Message, Usage


def data_root() -> Path:
    return Path(os.environ.get("ALOY_DATA_DIR", Path.home() / "Library/Application Support/Aloy"))


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ConversationStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or data_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        self.db = sqlite3.connect(self.root / "aloy.sqlite3", isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY)")
        for migration in sorted(
            files("aloy").joinpath("migrations").iterdir(), key=lambda p: p.name
        ):
            version = int(migration.name.split(".")[0])
            if not self.db.execute(
                "SELECT 1 FROM schema_migrations WHERE version=?", (version,)
            ).fetchone():
                with self.transaction():
                    statement = ""
                    for line in migration.read_text().splitlines(keepends=True):
                        statement += line
                        if sqlite3.complete_statement(statement):
                            self.db.execute(statement)
                            statement = ""
                    self.db.execute("INSERT INTO schema_migrations VALUES(?)", (version,))

    @contextmanager
    def transaction(self):
        # Savepoints also work inside another transaction and roll back DDL.
        name = "tx_" + uuid.uuid4().hex
        self.db.execute(f"SAVEPOINT {name}")
        try:
            yield
        except BaseException:
            self.db.execute(f"ROLLBACK TO {name}")
            self.db.execute(f"RELEASE {name}")
            raise
        else:
            self.db.execute(f"RELEASE {name}")

    def recover(self) -> None:
        """Only the exclusive desktop owner invokes crash recovery."""
        with self.transaction():
            self.db.execute(
                "UPDATE runs SET status='interrupted', ended_at=? WHERE status='running'", (_now(),)
            )
            self.db.execute(
                "UPDATE reservations SET status='settled',amount_usd=0 WHERE status='held'"
            )
            # Dispatched reservations remain charged: remote billing is uncertain.
            self.db.execute("UPDATE reservations SET status='settled' WHERE status='dispatched'")
            self.db.execute(
                "UPDATE run_steps SET status='interrupted',ended_at=? WHERE status='running'",
                (_now(),),
            )
            self.db.execute(
                "UPDATE provider_calls SET status='interrupted',ended_at=? WHERE status='running'",
                (_now(),),
            )
            for asset in self.db.execute("SELECT id,path FROM audio_assets"):
                if not Path(asset["path"]).exists():
                    self.db.execute(
                        "UPDATE audio_assets SET playback_status='missing' WHERE id=?",
                        (asset["id"],),
                    )
        self.drain_deletions()
        # Unreferenced audio/tmp files are retained, never silently deleted.

    def reserve(self, amount: float, ceiling: float = 30.0, provider: str = "unattributed") -> str:
        provider = {
            "gemini-quality": "gemini",
            "gemini-live": "gemini",
            "gemini-lite": "gemini",
            "router-grok": "openrouter",
        }.get(provider, provider)
        if amount < 0:
            raise ValueError("Negative reservation")
        reservation_id = str(uuid.uuid4())
        # Acquire the write lock before reading the ledger, across connections.
        with self.transaction():
            self.db.execute("UPDATE settings SET value=value WHERE key='budget_lock'")
            if self.monthly_spend() + amount > ceiling:
                raise RuntimeError("Aloy's monthly estimated spend limit has been reached")
            self.db.execute(
                "INSERT INTO reservations(id,amount_usd,status,created_at,provider) "
                "VALUES(?,?,?,?,?)",
                (reservation_id, amount, "held", _now(), provider),
            )
        return reservation_id

    def mark_dispatched(self, reservation_id: str) -> None:
        self.db.execute(
            "UPDATE reservations SET status='dispatched' WHERE id=? AND status='held'",
            (reservation_id,),
        )

    def settle(self, reservation_id: str, actual: float | None = None) -> None:
        row = self.db.execute("SELECT * FROM reservations WHERE id=?", (reservation_id,)).fetchone()
        if not row or row["status"] == "settled":
            return
        amount = (
            actual
            if actual is not None
            else (row["amount_usd"] if row["status"] == "dispatched" else 0.0)
        )
        self.db.execute(
            "UPDATE reservations SET status='settled',amount_usd=? WHERE id=?",
            (amount, reservation_id),
        )

    def record_client_metric(self, kind: str, value: float) -> None:
        if kind != "playback_stop_ms" or not 0 <= value <= 60000:
            raise ValueError("Unsupported client metric")
        self.db.execute(
            "INSERT INTO client_metrics(kind,value,created_at) VALUES(?,?,?)", (kind, value, _now())
        )

    def set_audio_duration(self, asset_id: str, seconds: float) -> None:
        self.db.execute(
            "UPDATE audio_assets SET duration_seconds=? WHERE id=?", (seconds, asset_id)
        )

    def mark_playback(self, asset_id: str, status: str) -> None:
        if status not in {"playing", "played", "cancelled", "failed"}:
            raise ValueError("Invalid playback state")
        self.db.execute("UPDATE audio_assets SET playback_status=? WHERE id=?", (status, asset_id))

    def message_id(self, run_id: str, role: str) -> str | None:
        row = self.db.execute(
            "SELECT id FROM messages WHERE run_id=? AND role=? ORDER BY sequence DESC LIMIT 1",
            (run_id, role),
        ).fetchone()
        return row[0] if row else None

    def audio_asset(self, asset_id: str) -> dict:
        row = self.db.execute("SELECT * FROM audio_assets WHERE id=?", (asset_id,)).fetchone()
        if row is None:
            raise KeyError(asset_id)
        return dict(row)

    def link_audio(self, asset_id: str, run_id: str, role: str) -> None:
        self.db.execute(
            "UPDATE audio_assets SET run_id=?,message_id=? WHERE id=? AND conversation_id=(SELECT conversation_id FROM runs WHERE id=?)",
            (run_id, self.message_id(run_id, role), asset_id, run_id),
        )

    def attach_run_audio(self, run_id: str) -> None:
        for role, direction in (("user", "input"), ("assistant", "output")):
            self.db.execute(
                "UPDATE audio_assets SET message_id=? WHERE run_id=? AND direction=?",
                (self.message_id(run_id, role), run_id, direction),
            )

    def drain_deletions(self) -> None:
        for row in self.db.execute("SELECT path FROM pending_deletions").fetchall():
            path = Path(row[0])
            if not any(
                path.resolve().is_relative_to((self.root / folder).resolve())
                for folder in ("audio", "media")
            ):
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
            self.db.execute("DELETE FROM pending_deletions WHERE path=?", (str(path),))

    def close(self) -> None:
        self.db.close()

    def create_conversation(self, system_prompt: str, title: str = "New conversation") -> str:
        conversation_id = str(uuid.uuid4())
        now = _now()
        self.db.execute(
            "INSERT INTO conversations VALUES(?,?,?,?,?)",
            (conversation_id, title, system_prompt, now, now),
        )
        return conversation_id

    def rename_conversation(self, conversation_id: str, title: str) -> dict:
        self.conversation(conversation_id)
        title = " ".join(title.split()).strip()
        if not title or len(title) > 100 or any(ord(char) < 32 for char in title):
            raise ValueError("Conversation title must be 1 to 100 characters")
        self.db.execute(
            "UPDATE conversations SET title=?,updated_at=? WHERE id=?",
            (title, _now(), conversation_id),
        )
        return dict(self.conversation(conversation_id))

    def search_conversations(self, query: str, limit: int = 50) -> list[dict]:
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise ValueError("Conversation search must be 1 to 200 characters")
        terms = re.findall(r"\w+", query, re.UNICODE)[:12]
        if not terms:
            return []
        match = " OR ".join('"' + term.replace('"', "") + '"' for term in terms)
        rows = self.db.execute(
            "SELECT c.id,c.title,c.updated_at,MIN(message_fts.rank) AS rank "
            "FROM message_fts JOIN conversations c ON c.id=message_fts.conversation_id "
            "WHERE message_fts MATCH ? GROUP BY c.id ORDER BY rank LIMIT ?",
            (match, max(1, min(limit, 50))),
        ).fetchall()
        return [
            {"id": row["id"], "title": row["title"], "updated_at": row["updated_at"]}
            for row in rows
        ]

    def conversation(self, conversation_id: str) -> sqlite3.Row:
        row = self.db.execute(
            "SELECT * FROM conversations WHERE id=?", (conversation_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown conversation: {conversation_id}")
        return row

    def list_conversations(self) -> list[dict]:
        return [
            dict(row)
            for row in self.db.execute("SELECT * FROM conversations ORDER BY updated_at DESC")
        ]

    def messages(self, conversation_id: str) -> list[dict]:
        self.conversation(conversation_id)
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY sequence",
                (conversation_id,),
            )
        ]

    def completed_history(self, conversation_id: str) -> list[Message]:
        return [
            Message(row["role"], row["text"], origin=row.get("origin", ""))
            for row in self.completed_messages(conversation_id)
        ]

    def completed_messages(self, conversation_id: str) -> list[dict]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT m.* FROM messages m JOIN runs r ON r.id=m.run_id "
                "WHERE m.conversation_id=? AND m.status='complete' AND r.status='completed' "
                "ORDER BY m.sequence",
                (conversation_id,),
            )
        ]

    def begin_run(
        self,
        conversation_id: str,
        provider: str,
        model: str,
        user_text: str,
        *,
        input_origin: str = "user",
    ) -> str:
        if input_origin not in {"user", "canvas_interaction"}:
            raise ValueError("Unsupported user input origin")
        run_id = str(uuid.uuid4())
        with self.transaction():
            self.db.execute(
                "INSERT INTO runs(id,conversation_id,provider,model,status,started_at) VALUES(?,?,?,?,?,?)",
                (run_id, conversation_id, provider, model, "running", _now()),
            )
            self._add_message(conversation_id, run_id, "user", user_text, "complete", input_origin)
            self.db.execute(
                "UPDATE conversations SET updated_at=?, title=CASE WHEN title='New conversation' "
                "THEN ? ELSE title END WHERE id=?",
                (_now(), user_text[:64], conversation_id),
            )
        return run_id

    def exclude_run_input(self, run_id: str, status: str = "cancelled") -> None:
        self.db.execute(
            "UPDATE messages SET status=? WHERE run_id=? AND role='user'", (status, run_id)
        )

    def update_user_text(self, run_id: str, text: str) -> None:
        with self.transaction():
            self.db.execute(
                "UPDATE messages SET text=? WHERE run_id=? AND role='user'",
                (text, run_id),
            )
            self.db.execute(
                "UPDATE conversations SET title=CASE WHEN title='[voice input]' THEN ? "
                "ELSE title END WHERE id=(SELECT conversation_id FROM runs WHERE id=?)",
                (text[:64], run_id),
            )

    def _add_message(
        self,
        conversation_id: str,
        run_id: str,
        role: str,
        text: str,
        status: str,
        origin: str | None = None,
    ) -> str:
        origin = origin or ("assistant" if role == "assistant" else "user")
        message_id = str(uuid.uuid4())
        self.db.execute(
            "INSERT INTO messages(id,conversation_id,run_id,sequence,role,text,status,created_at,origin) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (
                message_id,
                conversation_id,
                run_id,
                self.db.execute(
                    "SELECT COALESCE(MAX(sequence),0)+1 FROM messages WHERE conversation_id=?",
                    (conversation_id,),
                ).fetchone()[0],
                role,
                text,
                status,
                _now(),
                origin,
            ),
        )
        return message_id

    def finish_run(
        self,
        run_id: str,
        status: str,
        text: str = "",
        usage: Usage | None = None,
        error: str = "",
        *,
        accounted: bool = False,
    ) -> None:
        row = self.db.execute("SELECT conversation_id FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        usage = usage or Usage()
        with self.transaction():
            self.db.execute(
                "UPDATE runs SET status=?, ended_at=?, error=?, input_tokens=?, output_tokens=?, "
                "estimated_usd=? WHERE id=?",
                (
                    status,
                    _now(),
                    error[:500] if error else None,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.estimated_usd,
                    run_id,
                ),
            )
            if not accounted and usage.estimated_usd is not None and usage.estimated_usd > 0:
                self.db.execute(
                    "INSERT INTO spend_events(source_type,source_id,amount_usd,created_at,provider) "
                    "VALUES('run',?,?,?,?) "
                    "ON CONFLICT(source_type,source_id) DO UPDATE SET amount_usd=excluded.amount_usd",
                    (run_id, usage.estimated_usd, _now(), self.run(run_id)["provider"]),
                )
            if text:
                self._add_message(
                    row["conversation_id"],
                    run_id,
                    "assistant",
                    text,
                    "complete"
                    if status == "completed"
                    else "cancelled"
                    if status == "cancelled"
                    else "failed",
                )
            self.db.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?", (_now(), row["conversation_id"])
            )

    def run(self, run_id: str) -> dict:
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return dict(row)

    def monthly_spend(self, provider: str | None = None) -> float:
        month = datetime.now(UTC).strftime("%Y-%m")
        provider_clause = " AND provider=?" if provider else ""
        args = (month + "%", provider) if provider else (month + "%",)
        amount = self.db.execute(
            "SELECT COALESCE(SUM(amount_usd),0) FROM spend_events WHERE created_at LIKE ?"
            + provider_clause,
            args,
        ).fetchone()[0]
        reserved = self.db.execute(
            "SELECT COALESCE(SUM(amount_usd),0) FROM reservations WHERE "
            "(status!='settled' OR created_at LIKE ?)" + provider_clause,
            args,
        ).fetchone()[0]
        return float(amount + reserved)

    def save_audio(
        self,
        conversation_id: str,
        data: bytes,
        *,
        direction: str,
        extension: str,
        message_id: str | None = None,
        run_id: str | None = None,
        duration_seconds: float | None = None,
        provider: str | None = None,
        estimated_usd: float | None = None,
        accounted: bool = False,
    ) -> dict:
        self.conversation(conversation_id)
        if direction not in ("input", "output") or extension not in ("wav", "m4a", "mp3"):
            raise ValueError("Unsupported audio metadata")
        asset_id = str(uuid.uuid4())
        folder = self.root / "audio" / conversation_id
        folder.mkdir(parents=True, exist_ok=True)
        folder.chmod(0o700)
        path = folder / f"{asset_id}.{extension}"
        temporary = folder / f".{asset_id}.tmp"
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            temporary.replace(path)
            with self.transaction():
                self.db.execute(
                    "INSERT INTO audio_assets(id,conversation_id,message_id,run_id,path,format,"
                    "duration_seconds,sha256,direction,playback_status,created_at,provider,estimated_usd) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        asset_id,
                        conversation_id,
                        message_id,
                        run_id,
                        str(path),
                        extension,
                        duration_seconds,
                        hashlib.sha256(data).hexdigest(),
                        direction,
                        "ready",
                        _now(),
                        provider,
                        estimated_usd,
                    ),
                )
                if not accounted and estimated_usd is not None and estimated_usd > 0:
                    self.db.execute(
                        "INSERT INTO spend_events(source_type,source_id,amount_usd,created_at,provider) "
                        "VALUES('audio',?,?,?,?)",
                        (asset_id, estimated_usd, _now(), provider or "unattributed"),
                    )
        except BaseException:
            temporary.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            raise
        return {"id": asset_id, "path": str(path), "direction": direction}

    def audio_assets(self, conversation_id: str) -> list[dict]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM audio_assets WHERE conversation_id=? ORDER BY created_at",
                (conversation_id,),
            )
        ]

    def orphan_audio(self) -> list[Path]:
        known = {row[0] for row in self.db.execute("SELECT path FROM audio_assets")}
        return [
            path
            for folder in (self.root / "audio", self.root / "tmp")
            if folder.exists()
            for path in folder.rglob("*")
            if path.is_file() and str(path) not in known
        ]

    def storage_bytes(self) -> int:
        return sum(
            path.lstat().st_size
            for path in self.root.rglob("*")
            if path.is_file() or path.is_symlink()
        )

    def delete_conversation(self, conversation_id: str) -> None:
        self.conversation(conversation_id)
        paths = [Path(row["path"]) for row in self.audio_assets(conversation_id)]
        paths.extend(Path(row["path"]) for row in self.media_assets(conversation_id))
        for category in ("audio", "media"):
            folder = self.root / category / conversation_id
            if folder.exists():
                paths.extend(path for path in folder.iterdir() if path.is_file())
        with self.transaction():
            self.db.executemany(
                "INSERT OR IGNORE INTO pending_deletions VALUES(?)",
                [(str(path),) for path in paths],
            )
            self.db.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
        self.drain_deletions()
        for category in ("audio", "media"):
            folder = self.root / category / conversation_id
            if folder.exists() and not any(folder.iterdir()):
                folder.rmdir()

    def get_session(self, conversation_id: str, provider: str) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM provider_sessions WHERE conversation_id=? AND provider=?",
            (conversation_id, provider),
        ).fetchone()
        return dict(row) if row else None

    def settings(self) -> dict[str, str]:
        return {row["key"]: row["value"] for row in self.db.execute("SELECT * FROM settings")}

    def set_setting(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def set_session(
        self,
        conversation_id: str,
        provider: str,
        session_id: str,
        synced_sequence: int,
        context_revision: int = 0,
        tool_hash: str = "",
    ) -> None:
        self.db.execute(
            "INSERT INTO provider_sessions(conversation_id,provider,session_id,synced_sequence,"
            "updated_at,context_revision,tool_hash) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(conversation_id,provider) "
            "DO UPDATE SET session_id=excluded.session_id,synced_sequence=excluded.synced_sequence,"
            "updated_at=excluded.updated_at,context_revision=excluded.context_revision,"
            "tool_hash=excluded.tool_hash",
            (
                conversation_id,
                provider,
                session_id,
                synced_sequence,
                _now(),
                context_revision,
                tool_hash,
            ),
        )

    def begin_step(
        self, run_id: str, kind: str, name: str = "", arguments: dict | None = None
    ) -> str:
        step_id = str(uuid.uuid4())
        with self.transaction():
            sequence = self.db.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM run_steps WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            self.db.execute(
                "INSERT INTO run_steps(id,run_id,sequence,kind,status,name,arguments_json,started_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    step_id,
                    run_id,
                    sequence,
                    kind,
                    "running",
                    name,
                    json.dumps(arguments or {}, ensure_ascii=False),
                    _now(),
                ),
            )
        return step_id

    def finish_step(
        self, step_id: str, status: str, result: dict | None = None, error: str = ""
    ) -> None:
        if status not in {"completed", "failed", "cancelled", "interrupted"}:
            raise ValueError("Invalid step outcome")
        with self.transaction():
            self.db.execute(
                "UPDATE run_steps SET status=?,result_json=?,error=?,ended_at=? WHERE id=?",
                (
                    status,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    error[:500] if error else None,
                    _now(),
                    step_id,
                ),
            )

    def steps(self, run_id: str) -> list[dict]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM run_steps WHERE run_id=? ORDER BY sequence", (run_id,)
            )
        ]

    def begin_provider_call(
        self,
        run_id: str | None,
        purpose: str,
        provider: str,
        model: str,
        reservation_id: str | None = None,
    ) -> str:
        call_id = str(uuid.uuid4())
        with self.transaction():
            self.db.execute(
                "INSERT INTO provider_calls(id,run_id,purpose,provider,model,status,"
                "reservation_id,started_at) VALUES(?,?,?,?,?,?,?,?)",
                (call_id, run_id, purpose, provider, model, "running", reservation_id, _now()),
            )
        return call_id

    def finish_provider_call(self, call_id: str, status: str, usage: Usage | None = None) -> None:
        usage = usage or Usage()
        with self.transaction():
            self.db.execute(
                "UPDATE provider_calls SET status=?,input_tokens=?,output_tokens=?,"
                "estimated_usd=?,ended_at=? WHERE id=?",
                (
                    status,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.estimated_usd,
                    _now(),
                    call_id,
                ),
            )

    def save_source(self, run_id: str, title: str, url: str, snippet: str) -> dict:
        source_id = str(uuid.uuid4())
        with self.transaction():
            self.db.execute(
                "INSERT INTO web_sources VALUES(?,?,?,?,?,?)",
                (source_id, run_id, title[:300], url[:2048], snippet[:2000], _now()),
            )
        return {"id": source_id, "title": title, "url": url, "snippet": snippet}

    def sources(self, run_id: str) -> list[dict]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM web_sources WHERE run_id=? ORDER BY created_at", (run_id,)
            )
        ]

    def save_artifact(self, run_id: str, kind: str, payload: dict, version: int = 1) -> dict:
        run = self.run(run_id)
        artifact_id = str(uuid.uuid4())
        created_at = _now()
        with self.transaction():
            sequence = self.db.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM artifacts WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            self.db.execute(
                "INSERT INTO artifacts(id,run_id,version,kind,payload_json,created_at,"
                "conversation_id,sequence,operation_id) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    artifact_id,
                    run_id,
                    version,
                    kind,
                    json.dumps(payload, ensure_ascii=False),
                    created_at,
                    run["conversation_id"],
                    sequence,
                    "",
                ),
            )
        return {
            "id": artifact_id,
            "run_id": run_id,
            "version": version,
            "kind": kind,
            "payload": payload,
        }

    def save_canvas_artifact(self, run_id: str, payload: dict, *, operation_id: str = "") -> dict:
        run = self.run(run_id)
        artifact_id = str(uuid.uuid4())
        created_at = _now()
        with self.transaction():
            sequence = self.db.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM artifacts WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            envelope = {
                "schema_version": 1,
                "artifact_id": artifact_id,
                "conversation_id": run["conversation_id"],
                "run_id": run_id,
                "sequence": sequence,
                "created_at": created_at,
                "title": payload["title"],
                "blocks": payload["blocks"],
            }
            if len(json.dumps(envelope, ensure_ascii=False).encode()) > 24_000:
                raise ValueError("Canvas artifact exceeds the persisted size limit")
            self.db.execute(
                "INSERT INTO artifacts(id,run_id,version,kind,payload_json,created_at,"
                "conversation_id,sequence,operation_id) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    artifact_id,
                    run_id,
                    1,
                    "canvas",
                    json.dumps(envelope, ensure_ascii=False),
                    created_at,
                    run["conversation_id"],
                    sequence,
                    operation_id,
                ),
            )
        return envelope

    def canvas_artifacts(self, conversation_id: str) -> list[dict]:
        self.conversation(conversation_id)
        rows = self.db.execute(
            "SELECT a.payload_json FROM artifacts a JOIN runs r ON r.id=a.run_id "
            "WHERE a.conversation_id=? AND a.kind='canvas' AND r.status='completed' "
            "ORDER BY a.created_at,a.sequence,a.id",
            (conversation_id,),
        ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def resolve_canvas_interaction(
        self,
        conversation_id: str,
        artifact_id: str,
        run_id: str,
        block_id: str,
        interaction: str,
        value: str,
    ) -> dict:
        row = self.db.execute(
            "SELECT a.payload_json,r.status FROM artifacts a JOIN runs r ON r.id=a.run_id "
            "WHERE a.id=? AND a.run_id=? AND a.conversation_id=? AND a.kind='canvas'",
            (artifact_id, run_id, conversation_id),
        ).fetchone()
        if row is None or row["status"] != "completed":
            raise ValueError("Canvas artifact is not available for interaction")
        artifact = json.loads(row["payload_json"])
        if artifact.get("artifact_id") != artifact_id or artifact.get("run_id") != run_id:
            raise ValueError("Canvas artifact metadata does not match its record")
        block = next((item for item in artifact["blocks"] if item["id"] == block_id), None)
        if block is None or not isinstance(value, str) or len(value) > 1_200:
            raise ValueError("Canvas interaction does not match a block")
        if interaction == "choose" and block["kind"] == "choice":
            selected = next((item for item in block["options"] if item["id"] == value), None)
            if selected is None:
                raise ValueError("Selected option is not part of this choice")
            label = selected["label"]
        elif (
            interaction == "transform" and block["kind"] == "sentence" and value == block["target"]
        ):
            label = value
        else:
            raise ValueError("Canvas interaction type or value is invalid")
        return {
            "artifact_id": artifact_id,
            "conversation_id": conversation_id,
            "run_id": run_id,
            "block_id": block_id,
            "interaction": interaction,
            "value": value,
            "label": label,
            "artifact_title": artifact["title"],
            "block_kind": block["kind"],
            "block": block,
        }

    def save_media(
        self,
        conversation_id: str,
        data: bytes,
        *,
        kind: str,
        mime_type: str,
        run_id: str | None = None,
        duration_seconds: float | None = None,
    ) -> dict:
        self.conversation(conversation_id)
        formats = {
            ("image", "image/png"): "png",
            ("image", "image/jpeg"): "jpg",
            ("video", "video/mp4"): "mp4",
            ("video", "video/quicktime"): "mov",
            ("audio", "audio/wav"): "wav",
            ("audio", "audio/mpeg"): "mp3",
            ("audio", "audio/mp4"): "m4a",
        }
        extension = formats.get((kind, mime_type))
        if extension is None or not data or len(data) > 120_000_000:
            raise ValueError("Unsupported or oversized media")
        asset_id = str(uuid.uuid4())
        folder = self.root / "media" / conversation_id
        folder.mkdir(parents=True, exist_ok=True)
        folder.chmod(0o700)
        path = folder / f"{asset_id}.{extension}"
        temporary = folder / f".{asset_id}.tmp"
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            temporary.replace(path)
            with self.transaction():
                self.db.execute(
                    "INSERT INTO media_assets VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        asset_id,
                        conversation_id,
                        run_id,
                        kind,
                        str(path),
                        mime_type,
                        len(data),
                        hashlib.sha256(data).hexdigest(),
                        duration_seconds,
                        _now(),
                    ),
                )
        except BaseException:
            temporary.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            raise
        return {"id": asset_id, "path": str(path), "kind": kind, "mime_type": mime_type}

    def media_asset(self, asset_id: str) -> dict:
        row = self.db.execute("SELECT * FROM media_assets WHERE id=?", (asset_id,)).fetchone()
        if row is None:
            raise KeyError(asset_id)
        return dict(row)

    def media_assets(self, conversation_id: str) -> list[dict]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM media_assets WHERE conversation_id=? ORDER BY created_at",
                (conversation_id,),
            )
        ]

    def remember(
        self,
        text: str,
        category: str,
        *,
        scope: str = "global",
        conversation_id: str | None = None,
        source_message_id: str | None = None,
        evidence_type: str = "user_statement",
        confidence: float = 1.0,
    ) -> dict:
        normalized = " ".join(text.split()).strip()
        if not normalized or len(normalized) > 1500 or scope not in {"global", "conversation"}:
            raise ValueError("Invalid memory")
        from aloy.privacy import PRIVATE_PATTERN

        if scope == "conversation" and not conversation_id:
            raise ValueError("Conversation-scoped memory requires a conversation")
        if PRIVATE_PATTERN.search(normalized):
            raise ValueError("Sensitive content cannot be saved as memory")
        existing = self.db.execute(
            "SELECT id FROM memories WHERE lower(text)=lower(?) AND scope=? AND "
            "COALESCE(conversation_id,'')=COALESCE(?,'') AND status='active' LIMIT 1",
            (normalized, scope, conversation_id),
        ).fetchone()
        if existing:
            return self.memory(existing[0])

        def words(value: str) -> set[str]:
            return set(re.findall(r"\w+", value.casefold())) - {
                "i",
                "my",
                "a",
                "the",
                "that",
                "to",
                "and",
                "am",
                "is",
                "want",
            }

        candidate = words(normalized)
        superseded = []
        for row in self.memories(200):
            if row["scope"] != scope or row["conversation_id"] != conversation_id:
                continue
            other = words(row["text"])
            if candidate and other and len(candidate & other) / len(candidate | other) >= 0.78:
                if row["text"].casefold() == normalized.casefold():
                    return row
                # Keep the prior claim as a tombstone, with its original source intact.
                superseded.append(row["id"])
        memory_id = str(uuid.uuid4())
        with self.transaction():
            for old_id in superseded:
                self.forget(old_id)
            self.db.execute(
                "INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    memory_id,
                    normalized,
                    category[:50],
                    scope,
                    conversation_id,
                    source_message_id,
                    evidence_type,
                    confidence,
                    "active",
                    _now(),
                    _now(),
                ),
            )
        return self.memory(memory_id)

    def memory(self, memory_id: str) -> dict:
        row = self.db.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        if row is None:
            raise KeyError(memory_id)
        return dict(row)

    def memories(self, limit: int = 100) -> list[dict]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM memories WHERE status='active' ORDER BY updated_at DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            )
        ]

    def forget(self, memory_id: str) -> None:
        with self.transaction():
            changed = self.db.execute(
                "UPDATE memories SET status='forgotten',updated_at=? WHERE id=?",
                (_now(), memory_id),
            ).rowcount
            if changed == 0:
                raise KeyError(memory_id)

    def save_summary(self, conversation_id: str, through_sequence: int, text: str) -> dict:
        with self.transaction():
            revision = self.db.execute(
                "SELECT COALESCE(MAX(revision),0)+1 FROM conversation_summaries WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()[0]
            summary_id = str(uuid.uuid4())
            self.db.execute(
                "INSERT INTO conversation_summaries VALUES(?,?,?,?,?,?)",
                (summary_id, conversation_id, revision, through_sequence, text, _now()),
            )
        return self.summary(conversation_id)

    def summary(self, conversation_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM conversation_summaries WHERE conversation_id=? "
            "ORDER BY revision DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
        return dict(row) if row else None

    def search_text(self, query: str, *, mode: str = "fts", limit: int = 8) -> list[dict]:
        limit = max(1, min(limit, 50))
        if mode == "fts":
            # Quote the user's words so FTS syntax cannot change query meaning.
            terms = re.findall(r"\w+", query, re.UNICODE)[:12]
            if not terms:
                return []
            match = " OR ".join('"' + term.replace('"', "") + '"' for term in terms)
            rows = self.db.execute(
                "SELECT message_id AS id,'message' AS kind,text FROM message_fts "
                "WHERE message_fts MATCH ? ORDER BY rank LIMIT ?",
                (match, limit),
            ).fetchall()
            rows += self.db.execute(
                "SELECT memory_id AS id,'memory' AS kind,text FROM memory_fts "
                "WHERE memory_fts MATCH ? ORDER BY rank LIMIT ?",
                (match, limit),
            ).fetchall()
            rows += self.db.execute(
                "SELECT summary_id AS id,'summary' AS kind,text FROM summary_fts "
                "WHERE summary_fts MATCH ? ORDER BY rank LIMIT ?",
                (match, limit),
            ).fetchall()
        elif mode in {"exact", "substring"}:
            rows = self.db.execute(
                "SELECT id,'message' AS kind,text FROM messages WHERE status='complete' AND "
                "instr(lower(text),lower(?))>0 ORDER BY created_at DESC LIMIT ?",
                (query, limit),
            ).fetchall()
            rows += self.db.execute(
                "SELECT id,'memory' AS kind,text FROM memories WHERE status='active' AND "
                "instr(lower(text),lower(?))>0 ORDER BY updated_at DESC LIMIT ?",
                (query, limit),
            ).fetchall()
            rows += self.db.execute(
                "SELECT id,'summary' AS kind,text FROM conversation_summaries "
                "WHERE instr(lower(text),lower(?))>0 ORDER BY created_at DESC LIMIT ?",
                (query, limit),
            ).fetchall()
            if mode == "exact":
                rows = [row for row in rows if row["text"].casefold() == query.casefold()]
        elif mode == "regex":
            if len(query) > 200:
                raise ValueError("Regex is too long")
            import regex

            pattern = regex.compile(query, regex.IGNORECASE)
            candidates = self.db.execute(
                "SELECT id,'message' AS kind,text FROM messages WHERE status='complete' "
                "ORDER BY created_at DESC LIMIT 2000"
            ).fetchall()
            candidates += self.db.execute(
                "SELECT id,'memory' AS kind,text FROM memories WHERE status='active' "
                "ORDER BY updated_at DESC LIMIT 500"
            ).fetchall()
            candidates += self.db.execute(
                "SELECT id,'summary' AS kind,text FROM conversation_summaries "
                "ORDER BY created_at DESC LIMIT 500"
            ).fetchall()
            rows = []
            import time

            deadline = time.monotonic() + 0.15
            for row in candidates:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("Regex search exceeded its time limit")
                try:
                    if pattern.search(row["text"], timeout=remaining):
                        rows.append(row)
                except TimeoutError as exc:
                    raise ValueError("Regex search exceeded its time limit") from exc
                if len(rows) >= limit:
                    break
        else:
            raise ValueError("Unknown search mode")
        results = []
        for row in rows:
            entity = self.index_entity(row["kind"], row["id"])
            if entity is not None:
                results.append(
                    {
                        **dict(row),
                        "conversation_id": entity.get("conversation_id"),
                        "scope": entity.get("scope"),
                    }
                )
        from itertools import zip_longest

        groups = [
            [row for row in results if row["kind"] == kind]
            for kind in ("memory", "message", "summary")
        ]
        return [row for group in zip_longest(*groups) for row in group if row][:limit]

    def pending_index(self, limit: int = 100) -> list[dict]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM index_outbox WHERE indexed_at IS NULL ORDER BY id LIMIT ?",
                (limit,),
            )
        ]

    def indexed(self, outbox_id: int) -> None:
        with self.transaction():
            self.db.execute("UPDATE index_outbox SET indexed_at=? WHERE id=?", (_now(), outbox_id))

    def index_entity(self, entity_type: str, entity_id: str) -> dict | None:
        tables = {"message": "messages", "memory": "memories", "summary": "conversation_summaries"}
        if entity_type not in tables:
            raise ValueError("Unknown index entity")
        row = self.db.execute(
            f"SELECT * FROM {tables[entity_type]} WHERE id=?", (entity_id,)
        ).fetchone()
        if row is None or (entity_type == "memory" and row["status"] != "active"):
            return None
        if entity_type == "message" and row["status"] != "complete":
            return None
        if entity_type == "message" and self.run(row["run_id"])["status"] != "completed":
            return None
        return dict(row)
