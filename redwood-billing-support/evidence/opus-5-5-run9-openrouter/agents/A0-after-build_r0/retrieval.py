"""Hybrid retrieval over the shipped LanceDB index (read-only at runtime).

`billing_kb` holds billing-scope chunks with MiniLM vectors and a full-text index; `corpus_lexical`
holds the rest of Redwood's records with a full-text index only. A search runs BM25 and dense
retrieval on `billing_kb`, fuses them with reciprocal rank fusion, and (for scope="all") adds
lexical hits from `corpus_lexical`. Results are chunks with their source id, status and date;
no full source record is stored or returned.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import lancedb

HERE = Path(__file__).resolve().parent
INDEX = HERE / "index"
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
RRF_C = 60
POOL = 30
PER_SOURCE = 3

# Mild prior for curated process / policy spaces over chatter. Status is shown, never hidden.
POLICY_PREFIXES = (
    "confluence/customer-success-and-support/support-process",
    "confluence/customer-success-and-support/known-issues-and-workarounds",
    "confluence/product-docs/pricing-and-packaging",
    "confluence/finance-and-legal",
    "google_drive/shared_drives/finance",
    "confluence/oncall-and-incident-response/status-page-and-comms",
)

_FTS_SAFE = re.compile(r"[^\w\s$.%-]")
# Identifiers (SUP-1913, INT-4928, PRACTICE-DUP-180) and quoted or code-like terms get an exact pass
# over both tables, so a named record is found even outside the billing scope.
_EXACT = re.compile(r'"([^"]{3,80})"|\b([A-Za-z]+-[A-Za-z0-9-]*\d[\w-]*|[a-z]+[A-Z]\w+|\w+(?:-\w+){2,})\b')


@lru_cache(maxsize=1)
def _tables():
    db = lancedb.connect(str(INDEX))
    return db.open_table("billing_kb"), db.open_table("corpus_lexical")


def _fts_query(text: str) -> str:
    text = _FTS_SAFE.sub(" ", text)
    words = [w.strip(".-") for w in text.split()]
    return " ".join(w for w in words if w)[:500]


def _prior(row: dict) -> float:
    weight = 1.0
    if row["source"].startswith(POLICY_PREFIXES):
        weight *= 1.5
    if row["platform"] in ("hubspot", "github", "fireflies", "gmail"):
        weight *= 0.8
    return weight


def _fts(table, query: str, limit: int) -> list[dict]:
    q = _fts_query(query)
    if not q:
        return []
    try:
        return table.search(q, query_type="fts", fts_columns="text").limit(limit).to_list()
    except Exception:  # an unparsable query degrades to dense-only, never to an error
        return []


def _exact_terms(query: str) -> list[str]:
    terms = []
    for quoted, token in _EXACT.findall(query):
        term = (quoted or token).strip()
        if term and term not in terms:
            terms.append(term)
    return terms[:4]


def _raw_fts(table, query: str, limit: int) -> list[dict]:
    try:
        return table.search(query, query_type="fts", fts_columns="text").limit(limit).to_list()
    except Exception:
        return []


def search(query: str, vector: list[float] | None, k: int = 6, scope: str = "billing",
           exclude: set[str] | None = None) -> list[dict]:
    kb, lexical = _tables()
    exclude = exclude or set()
    scores: dict[str, float] = {}
    rows: dict[str, dict] = {}

    def add(results: list[dict], weight: float = 1.0) -> None:
        for rank, row in enumerate(results, start=1):
            cid = row["chunk_id"]
            rows.setdefault(cid, row)
            scores[cid] = scores.get(cid, 0.0) + weight / (RRF_C + rank)

    for term in _exact_terms(query):
        phrase = '"' + _fts_query(term) + '"'
        add(_raw_fts(kb, phrase, 10), weight=3.0)
        add(_raw_fts(lexical, phrase, 10), weight=3.0)
    add(_fts(kb, query, POOL))
    if vector is not None:
        add(kb.search(vector, vector_column_name="vector").metric("cosine").limit(POOL).to_list())
    if scope == "all":
        add(_fts(lexical, query, POOL))
    ranked = sorted(scores, key=lambda cid: scores[cid] * _prior(rows[cid]), reverse=True)
    out, per_source = [], {}
    for cid in ranked:
        if cid in exclude:
            continue
        row = rows[cid]
        if per_source.get(row["source"], 0) >= PER_SOURCE:
            continue
        per_source[row["source"]] = per_source.get(row["source"], 0) + 1
        out.append(row)
        if len(out) >= k:
            break
    return [_public(row) for row in out]


def _public(row: dict) -> dict:
    status = row["status"] or "n/a"
    if row["flags"]:
        status += f" [{row['flags']}]"
    return {
        "chunk_id": row["chunk_id"],
        "source": row["source"],
        "title": row["title"],
        "status": status,
        "updated": row["date"] or "n/a",
        "section": row["section"],
        "text": row["text"].split("\n", 2)[-1],
    }
