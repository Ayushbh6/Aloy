"""SQLite is the canonical local transcript and run ledger."""

# ruff: noqa: E501

import hashlib
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from aloy.contracts import Message, Usage


def data_root() -> Path:
    return Path(os.environ.get("ALOY_DATA_DIR", Path.home() / "Library/Application Support/Aloy"))


def _now() -> str:
    return datetime.now(UTC).isoformat()


MIGRATIONS = [
    """
    CREATE TABLE conversations (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, system_prompt TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE runs (
        id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        provider TEXT NOT NULL, model TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('running','completed','failed','cancelled','interrupted')),
        started_at TEXT NOT NULL, ended_at TEXT, error TEXT,
        input_tokens INTEGER, output_tokens INTEGER, estimated_usd REAL
    );
    CREATE TABLE messages (
        id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
        sequence INTEGER NOT NULL, role TEXT NOT NULL CHECK(role IN ('user','assistant')),
        text TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('complete','failed','cancelled')),
        created_at TEXT NOT NULL, UNIQUE(conversation_id, sequence)
    );
    CREATE INDEX messages_order ON messages(conversation_id, sequence);
    CREATE TABLE audio_assets (
        id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
        run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
        path TEXT NOT NULL UNIQUE, format TEXT NOT NULL, duration_seconds REAL,
        sha256 TEXT NOT NULL, direction TEXT NOT NULL CHECK(direction IN ('input','output')),
        playback_status TEXT NOT NULL DEFAULT 'ready', created_at TEXT NOT NULL
    );
    CREATE TABLE provider_sessions (
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        provider TEXT NOT NULL, session_id TEXT NOT NULL, synced_sequence INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(conversation_id, provider)
    );
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """,
    """
    ALTER TABLE audio_assets ADD COLUMN provider TEXT;
    ALTER TABLE audio_assets ADD COLUMN estimated_usd REAL;
    """,
    """
    CREATE TABLE spend_events (
        source_type TEXT NOT NULL CHECK(source_type IN ('run','audio')),
        source_id TEXT NOT NULL, amount_usd REAL NOT NULL CHECK(amount_usd >= 0),
        created_at TEXT NOT NULL, PRIMARY KEY(source_type, source_id)
    );
    INSERT INTO spend_events
        SELECT 'run', id, estimated_usd, started_at FROM runs
        WHERE estimated_usd IS NOT NULL AND estimated_usd > 0;
    INSERT INTO spend_events
        SELECT 'audio', id, estimated_usd, created_at FROM audio_assets
        WHERE estimated_usd IS NOT NULL AND estimated_usd > 0;
    """,
    """
    ALTER TABLE provider_sessions ADD COLUMN updated_at TEXT;
    UPDATE provider_sessions SET updated_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now')
        WHERE updated_at IS NULL;
    """,
]


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
        for version, sql in enumerate(MIGRATIONS, 1):
            if not self.db.execute(
                "SELECT 1 FROM schema_migrations WHERE version=?", (version,)
            ).fetchone():
                with self.db:
                    for statement in sql.split(";"):
                        if statement.strip():
                            self.db.execute(statement)
                    self.db.execute("INSERT INTO schema_migrations VALUES(?)", (version,))
        self.db.execute(
            "UPDATE runs SET status='interrupted', ended_at=? WHERE status='running'", (_now(),)
        )

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
            Message(row["role"], row["text"])
            for row in self.messages(conversation_id)
            if row["status"] == "complete"
        ]

    def begin_run(self, conversation_id: str, provider: str, model: str, user_text: str) -> str:
        run_id = str(uuid.uuid4())
        with self.db:
            self.db.execute(
                "INSERT INTO runs(id,conversation_id,provider,model,status,started_at) VALUES(?,?,?,?,?,?)",
                (run_id, conversation_id, provider, model, "running", _now()),
            )
            self._add_message(conversation_id, run_id, "user", user_text, "complete")
            self.db.execute(
                "UPDATE conversations SET updated_at=?, title=CASE WHEN title='New conversation' "
                "THEN ? ELSE title END WHERE id=?",
                (_now(), user_text[:64], conversation_id),
            )
        return run_id

    def update_user_text(self, run_id: str, text: str) -> None:
        with self.db:
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
        self, conversation_id: str, run_id: str, role: str, text: str, status: str
    ) -> str:
        message_id = str(uuid.uuid4())
        self.db.execute(
            "INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)",
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
            ),
        )
        return message_id

    def finish_run(
        self, run_id: str, status: str, text: str = "", usage: Usage | None = None, error: str = ""
    ) -> None:
        row = self.db.execute("SELECT conversation_id FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        usage = usage or Usage()
        with self.db:
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
            if usage.estimated_usd is not None and usage.estimated_usd > 0:
                self.db.execute(
                    "INSERT INTO spend_events VALUES('run',?,?,?) "
                    "ON CONFLICT(source_type,source_id) DO UPDATE SET amount_usd=excluded.amount_usd",
                    (run_id, usage.estimated_usd, _now()),
                )
            if text:
                self._add_message(
                    row["conversation_id"],
                    run_id,
                    "assistant",
                    text,
                    "complete" if status == "completed" else "failed",
                )
            self.db.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?", (_now(), row["conversation_id"])
            )

    def run(self, run_id: str) -> dict:
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return dict(row)

    def monthly_spend(self) -> float:
        month = datetime.now(UTC).strftime("%Y-%m")
        amount = self.db.execute(
            "SELECT COALESCE(SUM(amount_usd),0) FROM spend_events WHERE created_at LIKE ?",
            (month + "%",),
        ).fetchone()[0]
        return float(amount)

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
    ) -> dict:
        self.conversation(conversation_id)
        if direction not in ("input", "output") or extension not in ("wav", "m4a"):
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
            with self.db:
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
                if estimated_usd is not None and estimated_usd > 0:
                    self.db.execute(
                        "INSERT INTO spend_events VALUES('audio',?,?,?)",
                        (asset_id, estimated_usd, _now()),
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

    def storage_bytes(self) -> int:
        return sum(
            path.lstat().st_size
            for path in self.root.rglob("*")
            if path.is_file() or path.is_symlink()
        )

    def delete_conversation(self, conversation_id: str) -> None:
        self.conversation(conversation_id)
        paths = [Path(row["path"]) for row in self.audio_assets(conversation_id)]
        self.db.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
        for path in paths:
            path.unlink(missing_ok=True)
        folder = self.root / "audio" / conversation_id
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
        self, conversation_id: str, provider: str, session_id: str, synced_sequence: int
    ) -> None:
        self.db.execute(
            "INSERT INTO provider_sessions VALUES(?,?,?,?,?) ON CONFLICT(conversation_id,provider) "
            "DO UPDATE SET session_id=excluded.session_id,synced_sequence=excluded.synced_sequence,"
            "updated_at=excluded.updated_at",
            (conversation_id, provider, session_id, synced_sequence, _now()),
        )
