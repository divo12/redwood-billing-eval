"""Build the shipped LanceDB index from /app/R (build time only).

    python build_index/build.py [--R /app/R] [--out knowledge]

- `billing` table: in-scope chunks (billing-relevant records) with MiniLM vectors + FTS index.
- `archive` table: every other chunk, lexical (FTS) only; no vectors.
Vectors are cached by content hash in build_index/.cache so an R1 rebuild only embeds new text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

import lancedb
import numpy as np
import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent))
from chunking import chunk_record, in_dense_scope  # noqa: E402

MODEL = "sentence-transformers/all-MiniLM-L6-v2"
HERE = Path(__file__).parent
CACHE = HERE / ".cache" / "vectors.npz"
FIELDS = ["id", "source", "kind", "title", "ident", "status", "date", "authority", "meta",
          "section", "position", "n_chunks", "text", "context", "search_text"]


def load_cache() -> dict[str, np.ndarray]:
    if CACHE.exists():
        z = np.load(CACHE)
        return dict(zip(z["keys"].tolist(), z["vecs"]))
    return {}


def save_cache(cache: dict[str, np.ndarray]) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    keys = list(cache)
    np.savez(CACHE, keys=np.array(keys), vecs=np.stack([cache[k] for k in keys]).astype(np.float32))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--R", default="/app/R")
    ap.add_argument("--out", default=str(HERE.parent / "knowledge"))
    args = ap.parse_args()
    src = Path(args.R) / "sources"
    dense, archive = [], []
    inventory = {}
    for p in sorted(src.rglob("*.json")):
        rel = str(p.relative_to(src))
        raw = p.read_text(errors="ignore")
        inventory[rel] = hashlib.sha1(raw.encode()).hexdigest()
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            print("skip malformed", rel)
            continue
        chunks = chunk_record(rel, record)
        (dense if in_dense_scope(rel, raw) else archive).extend(chunks)
    # company files at the root of R are short context documents
    for p in sorted(Path(args.R).glob("*")):
        if p.is_file() and p.suffix in (".md", ".txt", ".yaml") and "directory" not in p.name:
            raw = p.read_text(errors="ignore")
            inventory[p.name] = hashlib.sha1(raw.encode()).hexdigest()
            archive.extend(chunk_record("company/" + p.name, {"title": p.stem.replace("_", " "), "content": raw,
                                                               "status": "published"}))
    print(f"dense chunks {len(dense)}, archive chunks {len(archive)}")

    cache = load_cache()
    keyed = [hashlib.sha1(c["embed_text"].encode()).hexdigest() for c in dense]
    todo = sorted({k for k in keyed if k not in cache})
    if todo:
        from redwood_kit.embeddings import embed
        texts = {k: c["embed_text"] for k, c in zip(keyed, dense)}
        t0 = time.time()
        vecs = embed(model=MODEL, inputs=[texts[k] for k in todo])
        print(f"embedded {len(todo)} in {time.time() - t0:.0f}s")
        for k, v in zip(todo, vecs):
            cache[k] = np.asarray(v, dtype=np.float32)
        save_cache(cache)
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    db = lancedb.connect(str(out))
    dim = len(next(iter(cache.values())))
    rows = []
    for k, c in zip(keyed, dense):
        row = {f: c[f] for f in FIELDS}
        row["vector"] = cache[k].tolist()
        rows.append(row)
    schema = pa.schema([pa.field(f, pa.int32() if f in ("position", "n_chunks") else pa.string()) for f in FIELDS]
                       + [pa.field("vector", pa.list_(pa.float32(), dim))])
    t = db.create_table("billing", data=rows, schema=schema)
    t.create_fts_index("search_text", use_tantivy=False, replace=True)
    a_rows = [{f: c[f] for f in FIELDS} for c in archive]
    a_schema = pa.schema([pa.field(f, pa.int32() if f in ("position", "n_chunks") else pa.string()) for f in FIELDS])
    a = db.create_table("archive", data=a_rows, schema=a_schema)
    a.create_fts_index("search_text", use_tantivy=False, replace=True)
    for tbl in (t, a):
        try:
            tbl.optimize()
        except Exception as e:  # noqa: BLE001
            print("optimize skipped:", e)
    (out / "manifest.json").write_text(json.dumps({
        "embedding_model": MODEL, "dim": dim, "dense_chunks": len(dense), "archive_chunks": len(archive),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "sources": inventory,
    }, indent=0))
    print("done", out)


if __name__ == "__main__":
    main()
