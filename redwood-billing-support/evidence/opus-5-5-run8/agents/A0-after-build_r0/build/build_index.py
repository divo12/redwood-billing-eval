"""Build-time only: chunk /app/R and write the LanceDB index into workspace/index/.

Usage: python build/build_index.py            (from the workspace directory)
Billing-scoped chunks get dense vectors (all-MiniLM-L6-v2 via redwood_kit.embeddings.embed);
every chunk gets full-text indexing. Out-of-scope chunks carry a zero vector and in_scope=False,
and the runtime filters them out of vector search. Unchanged chunks reuse cached vectors.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import lancedb
import pyarrow as pa

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parent
sys.path.insert(0, str(HERE))
import corpus  # noqa: E402

MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DIM = 384
INDEX = WORKSPACE / "index"
CACHE = Path(os.environ.get("EMBED_CACHE", "/tmp/redwood_embed_cache.npz"))


def embed_text(row: dict) -> str:
    head = row["title"]
    if row.get("key") and row["key"] not in head:
        head = f"{row['key']} {head}"
    return f"{head}\n{row['text']}"[:1000]


def search_text(row: dict) -> str:
    return f"{row['title']}\n{row['key']}\n{row['source']}\n{row['text']}"


def main() -> None:
    t0 = time.time()
    rows = corpus.build_chunks()
    print(f"{len(rows)} chunks, {sum(r['in_scope'] for r in rows)} in scope", flush=True)
    cache: dict[str, np.ndarray] = {}
    if CACHE.exists():
        data = np.load(CACHE)
        cache = dict(zip(data["keys"].tolist(), data["vecs"]))
    todo, keys = [], []
    for r in rows:
        if not r["in_scope"]:
            continue
        t = embed_text(r)
        k = hashlib.sha256(t.encode()).hexdigest()
        r["_k"] = k
        if k not in cache:
            todo.append(t); keys.append(k)
    print(f"embedding {len(todo)} new texts", flush=True)
    if todo:
        from redwood_kit.embeddings import embed
        vecs = embed(model=MODEL, inputs=todo)
        for k, v in zip(keys, vecs):
            cache[k] = np.asarray(v, dtype=np.float32)
        np.savez(CACHE, keys=np.array(list(cache.keys())), vecs=np.stack(list(cache.values())))
    zero = np.zeros(DIM, dtype=np.float32)
    table_rows = []
    for r in rows:
        v = cache[r["_k"]] if r["in_scope"] else zero
        table_rows.append({
            "chunk_id": r["chunk_id"], "source": r["source"], "kind": r["kind"], "title": r["title"],
            "key": r["key"], "status": r["status"], "date": r["date"], "author": r["author"],
            "team": r["team"], "authority": r["authority"], "part": r["part"], "parts": r["parts"],
            "text": r["text"], "context_before": r["context_before"], "in_scope": bool(r["in_scope"]),
            "search_text": search_text(r), "vector": v.tolist(),
        })
    if INDEX.exists():
        shutil.rmtree(INDEX)
    db = lancedb.connect(str(INDEX))
    schema = pa.schema([
        ("chunk_id", pa.string()), ("source", pa.string()), ("kind", pa.string()), ("title", pa.string()),
        ("key", pa.string()), ("status", pa.string()), ("date", pa.string()), ("author", pa.string()),
        ("team", pa.string()), ("authority", pa.string()), ("part", pa.int32()), ("parts", pa.int32()),
        ("text", pa.string()), ("context_before", pa.string()), ("in_scope", pa.bool_()),
        ("search_text", pa.string()), ("vector", pa.list_(pa.float32(), DIM)),
    ])
    batch = 6000  # several data files, each well under the runtime's 128 MiB file limit
    table = db.create_table("chunks", data=table_rows[:batch], schema=schema)
    for start in range(batch, len(table_rows), batch):
        table.add(table_rows[start:start + batch])
    table.create_fts_index("search_text", replace=True)
    manifest = {
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "release": Path("/etc/redwood-release").read_text().strip() if Path("/etc/redwood-release").exists() else "",
        "embedding_model": MODEL, "chunks": len(rows), "dense_chunks": sum(r["in_scope"] for r in rows),
        "records": len({r["source"] for r in rows}),
        "record_hashes": {r["source"]: r["record_hash"] for r in rows},
    }
    (INDEX / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"done in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
