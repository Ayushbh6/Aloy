"""SQLite is the canonical local transcript and run ledger."""

# ruff: noqa: E501

import hashlib
import os
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
            for asset in self.db.execute("SELECT id,path FROM audio_assets"):
                if not Path(asset["path"]).exists():
                    self.db.execute(
                        "UPDATE audio_assets SET playback_status='missing' WHERE id=?",
                        (asset["id"],),
                    )
        self.drain_deletions()
        # Unreferenced audio/tmp files are retained, never silently deleted.

    def reserve(self, amount: float, ceiling: float = 30.0) -> str:
        if amount < 0:
            raise ValueError("Negative reservation")
        reservation_id = str(uuid.uuid4())
        # Acquire the write lock before reading the ledger, across connections.
        with self.transaction():
            self.db.execute("UPDATE settings SET value=value WHERE key='budget_lock'")
            if self.monthly_spend() + amount > ceiling:
                raise RuntimeError("Aloy's monthly estimated spend limit has been reached")
            self.db.execute(
                "INSERT INTO reservations VALUES(?,?,?,?)", (reservation_id, amount, "held", _now())
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
            if not path.resolve().is_relative_to((self.root / "audio").resolve()):
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
        with self.transaction():
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

    def monthly_spend(self) -> float:
        month = datetime.now(UTC).strftime("%Y-%m")
        amount = self.db.execute(
            "SELECT COALESCE(SUM(amount_usd),0) FROM spend_events WHERE created_at LIKE ?",
            (month + "%",),
        ).fetchone()[0]
        reserved = self.db.execute(
            "SELECT COALESCE(SUM(amount_usd),0) FROM reservations WHERE status!='settled' OR created_at LIKE ?",
            (month + "%",),
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
        folder = self.root / "audio" / conversation_id
        if folder.exists():
            paths.extend(path for path in folder.iterdir() if path.is_file())
        with self.transaction():
            self.db.executemany(
                "INSERT OR IGNORE INTO pending_deletions VALUES(?)",
                [(str(path),) for path in paths],
            )
            self.db.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
        self.drain_deletions()
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
