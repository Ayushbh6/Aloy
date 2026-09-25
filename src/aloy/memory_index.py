"""Rebuildable, chunked Lance projection. SQLite always decides visibility."""

import asyncio
import fcntl
import hashlib
import json
import os
import shutil
import uuid
from contextlib import asynccontextmanager

import httpx

from aloy.storage import ConversationStore

EMBEDDING_MODEL = "embeddinggemma:latest"
EMBEDDING_DIMENSION = 768
INDEX_VERSION = 2


class OllamaEmbeddings:
    def __init__(self, url="http://127.0.0.1:11434"):
        self.url = url

    async def fingerprint(self):
        async with httpx.AsyncClient(timeout=2) as client:
            response = await client.get(f"{self.url}/api/tags")
            response.raise_for_status()
        for item in response.json().get("models", []):
            if item.get("name") == EMBEDDING_MODEL and item.get("digest"):
                return f"{EMBEDDING_MODEL}:{item['digest']}:{EMBEDDING_DIMENSION}"
        raise RuntimeError(f"Ollama model {EMBEDDING_MODEL} is unavailable")

    async def embed(self, text):
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.post(
                f"{self.url}/api/embed",
                json={"model": EMBEDDING_MODEL, "input": text, "truncate": False},
            )
            response.raise_for_status()
        vectors = response.json().get("embeddings") or []
        if len(vectors) != 1 or len(vectors[0]) != EMBEDDING_DIMENSION:
            raise RuntimeError("Ollama embedding dimension changed; rebuild the index")
        return vectors[0]


def chunks(text):
    for start in range(0, len(text), 1440):
        yield start, text[start : start + 1600]


def reciprocal_rank_fusion(streams, limit):
    ranks, records = {}, {}
    for stream in streams:
        for rank, row in enumerate(stream, 1):
            key = row["id"]
            ranks[key] = ranks.get(key, 0) + 1 / (60 + rank)
            records[key] = row
    return [
        {**records[key], "score": ranks[key]}
        for key in sorted(records, key=lambda key: ranks[key], reverse=True)[:limit]
    ]


class MemoryIndex:
    def __init__(self, store, embedder=None):
        self.store, self.embedder = store, embedder or OllamaEmbeddings()
        self.root = store.root / "index"
        self.path = self.root / "lancedb"
        self.manifest = self.root / "manifest.json"

    @asynccontextmanager
    async def _lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        with (self.root / "index.lock").open("a") as lock:
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    await asyncio.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    @staticmethod
    def _table(path):
        import lancedb
        import pyarrow as pa
        from lancedb.index import FTS

        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o700)
        db = lancedb.connect(str(path))
        if "entries" in db.list_tables().tables:
            return db.open_table("entries")
        schema = pa.schema(
            [
                pa.field(key, pa.string())
                for key in ("id", "entity_id", "kind", "conversation_id", "text", "digest")
            ]
            + [pa.field("vector", pa.list_(pa.float32(), EMBEDDING_DIMENSION))]
        )
        table = db.create_table("entries", schema=schema)
        table.create_index("text", config=FTS())
        return table

    def _manifest_fingerprint(self):
        if not self.manifest.exists():
            return None
        value = json.loads(self.manifest.read_text())
        return (
            value.get("fingerprint")
            if value.get("version") == INDEX_VERSION
            else "obsolete-index-format"
        )

    def _write_manifest(self, fingerprint):
        temporary = self.root / f".manifest-{uuid.uuid4().hex}.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as file:
            json.dump(
                {
                    "model": EMBEDDING_MODEL,
                    "dimension": EMBEDDING_DIMENSION,
                    "fingerprint": fingerprint,
                    "version": INDEX_VERSION,
                },
                file,
            )
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(self.manifest)

    async def _rows(self, kind, entity):
        digest = hashlib.sha256(entity["text"].encode()).hexdigest()
        rows = []
        for start, text in chunks(entity["text"]):
            rows.append(
                {
                    "id": f"{kind}:{entity['id']}:{start}",
                    "entity_id": entity["id"],
                    "kind": kind,
                    "conversation_id": entity.get("conversation_id") or "",
                    "text": text,
                    "digest": digest,
                    "vector": await self.embedder.embed(text),
                }
            )
        return rows

    async def drain(self, limit=100):
        async with self._lock():
            pending = self.store.pending_index(limit)
            if not pending:
                return 0
            fingerprint = await self.embedder.fingerprint()
            if self._manifest_fingerprint() not in (None, fingerprint):
                raise RuntimeError("Embedding model or index format changed; run index rebuild")
            table = await asyncio.to_thread(self._table, self.path)
            self._write_manifest(fingerprint)
            for item in pending:
                kind, entity_id = item["entity_type"], item["entity_id"]
                entity = self.store.index_entity(kind, entity_id)
                rows = await self._rows(kind, entity) if entity else []
                escaped = entity_id.replace("'", "''")
                await asyncio.to_thread(
                    table.delete, f"entity_id = '{escaped}' AND kind = '{kind}'"
                )
                if rows:
                    await asyncio.to_thread(table.add, rows)
                self.store.indexed(item["id"])
            return len(pending)

    async def search(self, query, limit=8):
        if not self.path.exists():
            return []
        async with self._lock():
            if await self.embedder.fingerprint() != self._manifest_fingerprint():
                raise RuntimeError("Embedding model changed; rebuild the index")
            vector = await self.embedder.embed(query[:1600])
            table = await asyncio.to_thread(self._table, self.path)
            limit = max(1, min(limit, 30))

            def candidates():
                semantic = table.search(vector).limit(limit * 3).to_list()
                try:
                    lexical = table.search(query, query_type="fts").limit(limit * 3).to_list()
                except (ValueError, RuntimeError):
                    lexical = []
                return semantic, lexical

            streams = await asyncio.to_thread(candidates)
            results = []
            for row in reciprocal_rank_fusion(streams, limit * 6):
                entity = self.store.index_entity(row["kind"], row["entity_id"])
                if (
                    not entity
                    or hashlib.sha256(entity["text"].encode()).hexdigest() != row["digest"]
                ):
                    continue
                results.append(
                    {
                        "id": f"{row['kind']}:{row['entity_id']}",
                        "chunk_id": row["id"],
                        "kind": row["kind"],
                        "text": row["text"],
                        "conversation_id": entity.get("conversation_id"),
                        "scope": entity.get("scope"),
                        "score": row["score"],
                    }
                )
                if len(results) >= limit:
                    break
            return results

    async def rebuild(self):
        async with self._lock():
            fingerprint = await self.embedder.fingerprint()
            with self.store.transaction():
                watermark = self.store.db.execute(
                    "SELECT COALESCE(MAX(id),0) FROM index_outbox"
                ).fetchone()[0]
                entities = []
                for kind, table in (
                    ("message", "messages"),
                    ("memory", "memories"),
                    ("summary", "conversation_summaries"),
                ):
                    for row in self.store.db.execute(f"SELECT id FROM {table}").fetchall():
                        entity = self.store.index_entity(kind, row[0])
                        if entity:
                            entities.append((kind, entity))
            build = self.root / f"lancedb-build-{uuid.uuid4().hex}"
            backup = None
            try:
                table = await asyncio.to_thread(self._table, build)
                for kind, entity in entities:
                    rows = await self._rows(kind, entity)
                    if rows:
                        await asyncio.to_thread(table.add, rows)
                del table
                if self.path.exists():
                    backup = self.root / f"lancedb-backup-{uuid.uuid4().hex}"
                    self.path.replace(backup)
                build.replace(self.path)
                self._write_manifest(fingerprint)
                with self.store.transaction():
                    self.store.db.execute(
                        "UPDATE index_outbox SET indexed_at=datetime('now') "
                        "WHERE indexed_at IS NULL AND id<=?",
                        (watermark,),
                    )
                if backup:
                    await asyncio.to_thread(shutil.rmtree, backup)
                return len(entities)
            finally:
                if build.exists():
                    await asyncio.to_thread(shutil.rmtree, build)


async def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("rebuild", "drain"))
    args = parser.parse_args()
    store = ConversationStore()
    try:
        index = MemoryIndex(store)
        count = await (index.rebuild() if args.command == "rebuild" else index.drain())
        print(f"{args.command}: {count} records")
    finally:
        store.close()


if __name__ == "__main__":
    asyncio.run(main())
