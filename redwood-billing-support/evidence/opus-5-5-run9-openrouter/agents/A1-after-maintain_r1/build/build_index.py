"""Build-time only: write the LanceDB index the support agent searches.

    python build/build_index.py          # from /app/workspace

Two tables in workspace/index/:
  billing_kb      billing-scope chunks: text + metadata + MiniLM vectors, FTS index on `text`
  corpus_lexical  every other chunk of R: text + metadata, FTS index only (no vectors)
A manifest (index/manifest.json) records source hashes, counts and the embedding model so an R1
update can tell what changed. Full source records are not stored, only chunks.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

import lancedb
import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent))
import corpus  # noqa: E402

from redwood_kit.embeddings import embed  # noqa: E402

MODEL = "sentence-transformers/all-MiniLM-L6-v2"
WORKSPACE = Path(__file__).resolve().parent.parent
INDEX = WORKSPACE / "index"
CACHE = Path("/tmp/redwood-embed-cache.json")  # build-time reuse of unchanged vectors

META_COLS = ["chunk_id", "source", "platform", "title", "status", "flags", "date", "owner", "labels", "section"]


def display_text(c: dict) -> str:
    status = c["status"] or "n/a"
    if c["flags"]:
        status += f" [{c['flags']}]"
    return (f"{c['title']}\nsource: {c['source']} | status: {status} | updated: {c['date'] or 'n/a'}"
            f" | owner: {c['owner'] or 'n/a'}\n{c['body']}")


def embed_text(c: dict) -> str:
    return f"{c['title']}. {c['section']}.\n{c['body']}"[:1100]


def _vectors_from_index() -> dict[str, list[float]]:
    """embed_key -> vector from the currently shipped index, so unchanged chunks are not re-embedded."""
    try:
        table = lancedb.connect(str(INDEX)).open_table("billing_kb")
        data = table.to_arrow().select(["embed_key", "vector"]).to_pylist()
        return {row["embed_key"]: row["vector"] for row in data}
    except Exception:
        return {}


def main() -> None:
    started = time.time()
    dense, lexical, hashes = [], [], {}
    for rel, rec, raw in corpus.iter_records():
        hashes[rel] = hashlib.sha1(raw.encode()).hexdigest()
        chunks = corpus.chunk_record(rel, rec)
        target = dense if corpus.in_billing_scope(rel, rec, raw) else lexical
        target.extend(chunks)
    print(f"chunks: dense={len(dense)} lexical={len(lexical)}", flush=True)

    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    cache.update(_vectors_from_index())  # reuse vectors already shipped (e.g. R0 -> R1 update)
    keys = [hashlib.sha1(embed_text(c).encode()).hexdigest() for c in dense]
    todo = [i for i, k in enumerate(keys) if k not in cache]
    if todo:
        print(f"embedding {len(todo)} chunks", flush=True)
        vectors = embed(model=MODEL, inputs=[embed_text(dense[i]) for i in todo])
        for i, v in zip(todo, vectors):
            cache[keys[i]] = v
        CACHE.write_text(json.dumps(cache))
    print(f"embedded in {time.time() - started:.0f}s", flush=True)

    if INDEX.exists():
        shutil.rmtree(INDEX)
    db = lancedb.connect(str(INDEX))

    def rows(chunks, with_vec):
        out = []
        for c, k in zip(chunks, keys if with_vec else [None] * len(chunks)):
            row = {col: str(c[col]) for col in META_COLS}
            row["seq"] = int(c["seq"])
            row["text"] = display_text(c)
            if with_vec:
                row["embed_key"] = k
                row["vector"] = cache[k]
            out.append(row)
        return out

    fields = [pa.field(col, pa.string()) for col in META_COLS] + [pa.field("seq", pa.int32()),
                                                                   pa.field("text", pa.string())]
    kb_schema = pa.schema(fields + [pa.field("embed_key", pa.string()),
                                    pa.field("vector", pa.list_(pa.float32(), 384))])
    kb = db.create_table("billing_kb", data=rows(dense, True), schema=kb_schema)
    kb.create_fts_index("text", use_tantivy=False, with_position=True)
    lex = db.create_table("corpus_lexical", data=rows(lexical, False), schema=pa.schema(fields))
    lex.create_fts_index("text", use_tantivy=False, with_position=True)

    manifest = {
        "embedding_model": MODEL,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "release": Path("/etc/redwood-release").read_text().strip() if Path("/etc/redwood-release").exists() else "",
        "chunks": {"billing_kb": len(dense), "corpus_lexical": len(lexical)},
        "dense_sources": sorted({c["source"] for c in dense}),
        "source_sha1": hashes,
    }
    (INDEX / "manifest.json").write_text(json.dumps(manifest, indent=0))
    print(f"done in {time.time() - started:.0f}s", flush=True)


if __name__ == "__main__":
    main()
