"""Real host subprocesses with durable logs, PTYs and explicit lifecycle control."""

import asyncio
import errno
import os
import pty
import shutil
import signal
import time
import uuid
from pathlib import Path


class HostTerminal:
    def __init__(self):
        self.sessions = {}

    async def start(self, c, a):
        sid = uuid.uuid4().hex
        cwd = Path(a.get("cwd", str(Path.home()))).expanduser().resolve(strict=True)
        if not cwd.is_dir():
            raise ValueError("cwd must be a directory")
        folder = c.store.root / "terminal-logs"
        folder.mkdir(mode=0o700, exist_ok=True)
        path = folder / (sid + ".log")
        log = path.open("xb")
        path.chmod(0o600)
        master = slave = None
        if a.get("tty", False):
            master, slave = pty.openpty()
            os.set_blocking(master, False)
        env = {**os.environ, **a.get("env", {})}
        # Commands execute on the host. The cwd is a starting point, never a fence.
        try:
            process = await asyncio.create_subprocess_exec(
                a.get("shell", shutil.which("zsh") or "/bin/bash"),
                "-l",
                "-c",
                a["command"],
                cwd=cwd,
                env=env,
                stdin=slave if slave is not None else asyncio.subprocess.PIPE,
                stdout=slave if slave is not None else asyncio.subprocess.PIPE,
                stderr=slave if slave is not None else asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        except BaseException:
            log.close()
            if master is not None:
                os.close(master)
            raise
        finally:
            if slave is not None:
                os.close(slave)
        record = dict(
            process=process,
            master=master,
            path=path,
            store=c.store,
            conversation_id=c.conversation_id,
            run_id=c.run_id,
            offset=0,
        )
        self.sessions[sid] = record
        with c.store.transaction():
            c.store.db.execute(
                "INSERT INTO terminal_sessions VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    sid,
                    c.conversation_id,
                    c.run_id,
                    a["command"],
                    str(cwd),
                    process.pid,
                    "running",
                    None,
                    str(path),
                    time.time(),
                ),
            )
        record["pump"] = asyncio.create_task(self._pump(sid, record, log))
        record["deadline"] = asyncio.create_task(
            self._deadline(record, a.get("timeout_seconds", 900))
        )
        try:
            return await self.control(
                c, {"session_id": sid, "action": "poll", "wait_ms": a.get("yield_ms", 1000)}
            )
        except asyncio.CancelledError:
            await self._stop(record)
            raise

    async def _deadline(self, r, seconds):
        try:
            await asyncio.wait_for(asyncio.shield(r["pump"]), seconds)
        except TimeoutError:
            await self._stop(r)

    async def _pump(self, sid, r, log):
        size = 0
        try:
            while True:
                if r["master"] is None:
                    data = await r["process"].stdout.read(16384)
                else:
                    try:
                        data = os.read(r["master"], 16384)
                    except BlockingIOError:
                        await asyncio.sleep(0.02)
                        continue
                    except OSError as exc:
                        if exc.errno == errno.EIO:
                            break
                        raise
                if not data:
                    break
                size += len(data)
                if size > 64_000_000:
                    log.write(b"\n[Aloy: 64 MB output limit reached; process stopped]\n")
                    self._signal(r, signal.SIGTERM)
                    try:
                        await asyncio.wait_for(r["process"].wait(), 3)
                    except TimeoutError:
                        self._signal(r, signal.SIGKILL)
                    break
                log.write(data)
                log.flush()
            await r["process"].wait()
        finally:
            log.close()
            if r["master"] is not None:
                os.close(r["master"])
                r["master"] = None
            with r["store"].transaction():
                r["store"].db.execute(
                    "UPDATE terminal_sessions SET status=?,exit_code=? WHERE id=?",
                    ("exited", r["process"].returncode, sid),
                )

    @staticmethod
    def _signal(r, sig):
        # A shell can exit while a child still owns its output pipe. The group
        # remains ours until the pump reaches EOF; don't orphan that child.
        if r["process"].returncode is None or not r["pump"].done():
            try:
                os.killpg(r["process"].pid, sig)
            except ProcessLookupError:
                pass

    async def _stop(self, r):
        self._signal(r, signal.SIGTERM)
        try:
            await asyncio.wait_for(asyncio.shield(r["pump"]), 3)
        except TimeoutError:
            # Only the process group created by this tool, never the desktop app.
            self._signal(r, signal.SIGKILL)
            await r["pump"]

    async def control(self, c, a):
        sid = a.get("session_id")
        if a["action"] == "list":
            return {
                "sessions": [
                    dict(row)
                    for row in c.store.db.execute(
                        "SELECT id,command,cwd,status,exit_code,output_path FROM "
                        "terminal_sessions WHERE conversation_id=? ORDER BY created_at "
                        "DESC LIMIT 30",
                        (c.conversation_id,),
                    )
                ]
            }
        row = c.store.db.execute(
            "SELECT * FROM terminal_sessions WHERE id=? AND conversation_id=?",
            (sid, c.conversation_id),
        ).fetchone()
        if row is None:
            raise ValueError("Unknown terminal session")
        r = self.sessions.get(sid)
        action = a["action"]
        if action not in {"poll", "read", "write", "interrupt", "stop"}:
            raise ValueError("Unknown terminal action")
        if action in {"write", "interrupt", "stop"}:
            if not r or r["pump"].done():
                raise ValueError("Process is no longer running")
            if action == "stop":
                await self._stop(r)
            elif action == "interrupt":
                self._signal(r, signal.SIGINT)
            else:
                data = a.get("text", "").encode()
                if r["master"] is not None:
                    while data:
                        try:
                            sent = os.write(r["master"], data)
                        except BlockingIOError:
                            await asyncio.sleep(0.01)
                            continue
                        data = data[sent:]
                else:
                    r["process"].stdin.write(data)
                    await r["process"].stdin.drain()
        wait = min(a.get("wait_ms", 0), 10000) / 1000
        if r and wait and not r["pump"].done():
            try:
                await asyncio.wait_for(asyncio.shield(r["pump"]), wait)
            except TimeoutError:
                pass
        offset = a.get("offset", r["offset"] if r else 0)
        with Path(row["output_path"]).open("rb") as file:
            file.seek(offset)
            data = file.read(12000)
            next_offset = file.tell()
            more = bool(file.read(1))
        if r:
            r["offset"] = next_offset
        status = c.store.db.execute(
            "SELECT status,exit_code FROM terminal_sessions WHERE id=?", (sid,)
        ).fetchone()
        return {
            "session_id": sid,
            "status": status["status"],
            "exit_code": status["exit_code"],
            "output": data.decode(errors="replace"),
            "offset": offset,
            "next_offset": next_offset,
            "more": more,
            "output_path": row["output_path"],
        }

    async def cancel_run(self, run_id):
        for r in list(self.sessions.values()):
            if r["run_id"] == run_id and not r["pump"].done():
                await self._stop(r)

    async def close(self):
        for r in list(self.sessions.values()):
            if not r["pump"].done():
                await self._stop(r)
            if r.get("deadline"):
                await r["deadline"]
