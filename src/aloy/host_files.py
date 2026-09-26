"""Host file tools. Paths are unrestricted; results and changes are recoverable."""

import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def resolve(path):
    return Path(path).expanduser().resolve()


def atomic(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".aloy-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read(c, a):
    path = resolve(a["path"])
    if not path.is_file():
        raise ValueError("read requires a regular file; use glob for discovery")
    size = path.stat().st_size
    if size > 20_000_000:
        raise ValueError("File exceeds 20 MB per read; use terminal to extract bounded sections")
    mime = mimetypes.guess_type(path.name)[0] or ""
    if mime.startswith("audio/"):
        asset = c.store.save_media(
            c.conversation_id, path.read_bytes(), kind="audio", mime_type=mime, run_id=c.run_id
        )
        return {
            "path": str(path),
            "media_id": asset["id"],
            "kind": "audio",
            "instruction": "Use media.inspect on this media_id to hear it before feedback.",
        }
    if mime.startswith(("image/", "video/")):
        kind = mime.split("/")[0]
        if kind not in c.media_types:
            raise ValueError(
                f"Active model does not support native {kind}; "
                "use a supported route or explicitly extract frames with terminal"
            )
        if size > 20_000_000:
            raise ValueError("Media exceeds 20 MB; use terminal to resize or split it explicitly")
        asset = c.store.save_media(
            c.conversation_id, path.read_bytes(), kind=kind, mime_type=mime, run_id=c.run_id
        )
        c.media_out.append(asset)
        return {
            "path": str(path),
            "media_id": asset["id"],
            "kind": kind,
            "delivery": "native",
            "bytes": size,
        }
    if mime == "application/pdf":
        return {
            "path": str(path),
            "kind": "pdf",
            "instruction": (
                "Use terminal: pdfinfo, pdftotext, then pdftoppm -png. Read every "
                "rendered page as an image. Text extraction alone is not visual "
                "inspection."
            ),
        }
    start = a.get("offset", 1)
    limit = a.get("limit", 500)
    lines, total, used = [], 0, 0
    h = hashlib.sha256()
    # Stream exact lines, including large files, without loading the whole file.
    with path.open("rb") as file:
        for total, raw in enumerate(file, 1):
            h.update(raw)
            if start <= total < start + limit and used < 14000:
                text = raw.decode("utf-8")
                if "\x00" in text:
                    raise ValueError("Binary file; use a suitable terminal extractor")
                excerpt = text.rstrip("\n")[: min(4000, 14000 - used)]
                lines.append(
                    {
                        "line": total,
                        "text": excerpt,
                        "line_truncated": len(text.rstrip("\n")) > len(excerpt),
                    }
                )
                used += len(excerpt)
    end = lines[-1]["line"] if lines else start - 1
    return {
        "path": str(path),
        "sha256": h.hexdigest(),
        "lines": lines,
        "total_lines": total,
        "truncated": end < total or any(x["line_truncated"] for x in lines),
        "next_offset": end + 1 if end < total else None,
    }


async def search(a, grep=False):
    root = resolve(a.get("path", str(Path.home())))
    if not root.exists():
        raise ValueError("Search path does not exist")
    command = [
        shutil.which("rg") or "rg",
        "--hidden",
        "--no-ignore",
        "--sort",
        "path",
        "--glob",
        "!.git/**",
    ]
    if grep:
        command += ["--json", "--max-columns", "2000"]
        if a.get("literal"):
            command += ["--fixed-strings"]
        if not a.get("case_sensitive", True):
            command += ["--ignore-case"]
        if a.get("glob"):
            command += ["--glob", a["glob"]]
        command += ["--", a["pattern"], str(root)]
    else:
        command += ["--files", "--glob", a["pattern"], str(root)]
    signature = digest(json.dumps([command], sort_keys=True).encode())
    offset = 0
    if a.get("cursor"):
        saved = json.loads(base64.urlsafe_b64decode(a["cursor"]))
        if saved["query"] != signature:
            raise ValueError("Cursor belongs to another query")
        offset = saved["offset"]
    limit = a.get("limit", 100)
    matches = []
    count = 0
    complete = False
    p = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, limit=2_000_000
    )
    try:
        async with asyncio.timeout(15):
            while line := await p.stdout.readline():
                item = line.decode(errors="replace").rstrip("\n")
                if grep:
                    entry = json.loads(item)
                    if entry["type"] != "match":
                        continue
                    entry = entry["data"]
                    item = {
                        "path": entry["path"].get("text"),
                        "line": entry["line_number"],
                        "text": entry["lines"].get("text", "")[:2000],
                    }
                count += 1
                if count <= offset:
                    continue
                matches.append(item)
                if len(matches) > limit:
                    break
            else:
                complete = True
                await p.wait()
    finally:
        if p.returncode is None:
            p.terminate()
        await p.wait()
    if complete and p.returncode not in (0, 1):
        raise ValueError("Search failed: invalid pattern or an unreadable path; narrow the path")
    more = len(matches) > limit
    cursor = (
        base64.urlsafe_b64encode(
            json.dumps({"query": signature, "offset": offset + limit}).encode()
        ).decode()
        if more
        else None
    )
    return {
        "root": str(root),
        "matches": matches[:limit],
        "next_cursor": cursor,
        "truncated": more,
        "note": (
            "Cursor continues the query over current filesystem state; edits may change ordering."
        ),
    }


def write_changes(c, changes):
    planned = []
    for path, data, expected in changes:
        path = resolve(path)
        if any(path == p[0] for p in planned):
            raise ValueError("Duplicate patch path")
        old = path.read_bytes() if path.exists() else None
        if old is not None and len(old) > 20_000_000:
            raise ValueError("Edit exceeds 20 MB; use terminal")
        if expected is not None and digest(old or b"") != expected:
            raise ValueError("File changed since read")
        planned.append((path, old, data, path.stat().st_mode & 0o777 if path.exists() else 0o600))
    folder = c.store.root / "file-backups"
    folder.mkdir(mode=0o700, exist_ok=True)
    result = []
    for path, old, data, mode in planned:
        ident = uuid.uuid4().hex
        backup = folder / ident
        if old is not None:
            atomic(backup, old)
        # Revalidate directly before each replacement; completed changes remain
        # journaled on failure.
        current = path.read_bytes() if path.exists() else None
        if current != old:
            raise ValueError(
                "File changed while patch was being prepared; inspect file_changes "
                "before continuing"
            )
        with c.store.transaction():
            c.store.db.execute(
                "INSERT INTO file_changes VALUES(?,?,?,?,?,?,?)",
                (
                    ident,
                    c.run_id,
                    str(path),
                    str(backup) if old is not None else None,
                    digest(old) if old is not None else None,
                    digest(data) if data is not None else None,
                    time.time(),
                ),
            )
        if data is None:
            path.unlink()
        else:
            atomic(path, data, mode)
        result.append(
            {
                "path": str(path),
                "change_id": ident,
                "sha256": digest(data) if data is not None else None,
                "backup": str(backup) if old is not None else None,
            }
        )
    return {"changes": result}


def edit(c, a):
    path = resolve(a["path"])
    old = path.read_bytes() if path.exists() else b""
    if "content" in a:
        if path.exists() and not a.get("expected_sha256"):
            raise ValueError("Replacing an existing file requires expected_sha256 from read")
        new = a["content"].encode()
    else:
        text = old.decode()
        if not a.get("old") or text.count(a["old"]) != 1:
            raise ValueError("old text must match exactly once; read and include more context")
        new = text.replace(a["old"], a.get("new", ""), 1).encode()
    return write_changes(c, [(str(path), new, a.get("expected_sha256") or digest(old))])


def apply_patch(c, a):
    lines = a["patch"].splitlines(keepends=True)
    if not lines or lines[0].strip() != "*** Begin Patch" or lines[-1].strip() != "*** End Patch":
        raise ValueError(
            "Use *** Begin Patch / *** End Patch with Add File, Update File or Delete File sections"
        )
    changes = []
    i = 1
    while i < len(lines) - 1:
        header = lines[i].rstrip("\n")
        i += 1
        kind, path = header.removeprefix("*** ").split(": ", 1)
        target = resolve(path)
        body = []
        while i < len(lines) - 1 and not lines[i].startswith("*** "):
            body.append(lines[i])
            i += 1
        if kind == "Add File":
            if target.exists() or any(not line.startswith("+") for line in body):
                raise ValueError("Add requires absent path and + lines")
            changes.append((path, "".join(line[1:] for line in body).encode(), digest(b"")))
        elif kind == "Delete File":
            expected = a.get("expected_hashes", {}).get(path)
            if not expected:
                raise ValueError("Deletion requires expected_hashes[path] from read")
            changes.append((path, None, expected))
        elif kind == "Update File":
            old = target.read_bytes()
            text = old.decode()
            hunks = []
            hunk = []
            for line in body:
                if line.startswith("@@"):
                    if hunk:
                        hunks.append(hunk)
                    hunk = []
                else:
                    hunk.append(line)
            if hunk:
                hunks.append(hunk)
            for hunk in hunks:
                if any(line[:1] not in {" ", "+", "-"} for line in hunk):
                    raise ValueError("Patch lines need a space, + or - prefix")
                before = "".join(line[1:] for line in hunk if line[0] in " -")
                after = "".join(line[1:] for line in hunk if line[0] in " +")
                if not before or text.count(before) != 1:
                    raise ValueError("Patch context must match exactly once")
                text = text.replace(before, after, 1)
            changes.append(
                (path, text.encode(), a.get("expected_hashes", {}).get(path, digest(old)))
            )
        else:
            raise ValueError("Unsupported patch section")
    if not changes:
        raise ValueError("Empty patch")
    return write_changes(c, changes)
