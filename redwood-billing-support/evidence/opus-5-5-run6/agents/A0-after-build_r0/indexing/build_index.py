"""Build the shipped LanceDB index from /app/R (build time only).

    python indexing/build_index.py [/app/R]

Two tables in workspace/index/:
  billing  - chunks of billing-relevant records, with all-MiniLM-L6-v2 vectors + full-text index
  corpus   - every other chunk, full-text index only (no dense vectors)
Vectors for unchanged chunk text are reused from the previous build (keyed by text hash).
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import lancedb
import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent))
from corpus import chunk_records  # noqa: E402

from redwood_kit.embeddings import embed

MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DENSE_THRESHOLD = 2.5
WORKSPACE = Path(__file__).resolve().parent.parent
INDEX = WORKSPACE / "index"
CACHE = Path("/tmp/redwood-vector-cache.json")


def main(root: Path) -> None:
    started = time.time()
    chunks = chunk_records(root)
    dense = [c for c in chunks if c["billing_score"] >= DENSE_THRESHOLD]
    lexical = [c for c in chunks if c["billing_score"] < DENSE_THRESHOLD]
    print(f"{len(chunks)} chunks; dense {len(dense)} ({len({c['source'] for c in dense})} records); "
          f"lexical-only {len(lexical)}")

    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    keys = [hashlib.sha256(c["text"].encode()).hexdigest() for c in dense]
    todo = sorted({k: c["text"] for k, c in zip(keys, dense) if k not in cache}.items())
    if todo:
        print(f"embedding {len(todo)} new chunk texts")
        vectors = embed(model=MODEL, inputs=[t for _, t in todo])
        cache.update({k: v for (k, _), v in zip(todo, vectors)})
        CACHE.write_text(json.dumps(cache))
    for key, chunk in zip(keys, dense):
        chunk["vector"] = cache[key]

    import shutil
    shutil.rmtree(INDEX, ignore_errors=True)  # no stale dataset versions in the shipped artifact
    db = lancedb.connect(str(INDEX))
    for name, rows, with_vectors in (("billing", dense, True), ("corpus", lexical, False)):
        fields = [pa.field("id", pa.string()), pa.field("text", pa.string()), pa.field("source", pa.string()),
                  pa.field("platform", pa.string()), pa.field("title", pa.string()),
                  pa.field("status", pa.string()), pa.field("date", pa.string()), pa.field("labels", pa.string()),
                  pa.field("part", pa.int32()), pa.field("record_hash", pa.string()),
                  pa.field("billing_score", pa.float64())]
        if with_vectors:
            fields.append(pa.field("vector", pa.list_(pa.float32(), 384)))
        schema = pa.schema(fields)
        data = [{f.name: r[f.name] for f in fields} for r in rows]
        table = db.create_table(name, data=pa.Table.from_pylist(data, schema=schema), mode="overwrite")
        table.create_fts_index("text", replace=True, with_position=False)
        print(f"table {name}: {table.count_rows()} rows")
    manifest = {
        "embedding_model": MODEL,
        "dense_threshold": DENSE_THRESHOLD,
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "records": {c["source"]: c["record_hash"] for c in chunks},
        "dense_records": sorted({c["source"] for c in dense}),
    }
    (INDEX / "manifest.json").write_text(json.dumps(manifest, indent=0))
    print(f"done in {time.time() - started:.0f}s")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "/app/R"))
