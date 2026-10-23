"""Runtime retrieval over the shipped LanceDB index (workspace/index).

Hybrid search: LanceDB full-text search over every chunk plus vector search over the
billing-scoped chunks, merged with reciprocal rank fusion. Small, explainable boosts favour
official published pages and exact identifier matches; drafts and archived pages stay
visible (the agent must see conflicts) but are labelled. Results are chunks, never whole records.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import lancedb

HERE = Path(__file__).resolve().parent
INDEX = HERE / "index"
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
RRF_C = 50
POOL = 40
COLUMNS = ["chunk_id", "source", "kind", "title", "key", "status", "date", "authority", "part", "parts",
           "text", "context_before", "in_scope"]

POLICY_PREFIXES = (
    "confluence/customer-success-and-support/",
    "confluence/product-docs/pricing-and-packaging/",
    "confluence/finance-and-legal/",
    "confluence/sales-enablement/pricing-guidance/",
    "confluence/oncall-and-incident-response/status-page-and-comms/",
    "google_drive/shared_drives/finance/",
)
CASE_PREFIXES = ("jira/", "slack/", "gmail/", "linear/", "fireflies/", "github/", "hubspot/")
ID_PATTERN = re.compile(r"\b[A-Z]{2,6}-\d{2,7}\b")

_table = None


def table():
    global _table
    if _table is None:
        _table = lancedb.connect(str(INDEX)).open_table("chunks")
    return _table


def _fts_query(text: str) -> str:
    # Keep words, numbers and identifiers; drop syntax the FTS parser could misread.
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9_\-\.$/]*", text)
    words = [w.strip(".-/") for w in words]
    return " ".join(w for w in words if w)[:400]


def _official(row: dict) -> bool:
    return row["authority"].endswith("official documentation")


def _demoted(row: dict) -> bool:
    a = row["authority"]
    return a.startswith("ARCHIVED") or "NOT approved" in a


def search(query: str, embed: Callable[[list[str]], list[list[float]]] | None = None, k: int = 8,
           scope: str = "all", per_source: int = 2) -> list[dict]:
    """Hybrid search; returns up to k chunk dicts ranked by fused score."""
    tbl = table()
    ranked: dict[str, float] = {}
    rows: dict[str, dict] = {}
    where = None
    if scope == "policy":
        where = " OR ".join(f"source LIKE '{p}%'" for p in POLICY_PREFIXES) + " OR authority LIKE '%official%'"
    elif scope == "cases":
        where = " OR ".join(f"source LIKE '{p}%'" for p in CASE_PREFIXES)

    q = _fts_query(query)
    if q:
        try:
            builder = tbl.search(q, query_type="fts", fts_columns="search_text").select(COLUMNS).limit(POOL)
            if where:
                builder = builder.where(f"({where})", prefilter=True)
            for rank, row in enumerate(builder.to_list(), start=1):
                rows[row["chunk_id"]] = row
                ranked[row["chunk_id"]] = ranked.get(row["chunk_id"], 0.0) + 1.0 / (RRF_C + rank)
        except Exception:  # noqa: BLE001 - lexical failure falls back to dense only
            pass
    if embed is not None:
        try:
            vector = embed([query[:1000]])[0]
            clause = "in_scope = true" + (f" AND ({where})" if where else "")
            builder = (tbl.search(vector, query_type="vector", vector_column_name="vector").metric("cosine")
                       .where(clause, prefilter=True).select(COLUMNS).limit(POOL))
            for rank, row in enumerate(builder.to_list(), start=1):
                rows.setdefault(row["chunk_id"], row)
                ranked[row["chunk_id"]] = ranked.get(row["chunk_id"], 0.0) + 1.0 / (RRF_C + rank)
        except Exception:  # noqa: BLE001
            pass

    ids_in_query = {m.upper() for m in ID_PATTERN.findall(query)}
    scored = []
    for cid, score in ranked.items():
        row = rows[cid]
        boost = 1.0
        if row["in_scope"]:
            boost += 0.15
        if _official(row):
            boost += 0.5
        if row["source"].startswith(POLICY_PREFIXES):
            boost += 0.2
        if _official(row) and (row["date"] or "") >= "2026":
            boost += 0.15  # recently reviewed procedure pages
        if _demoted(row):
            boost -= 0.1
        if ids_in_query and any(i in (row["key"] or "").upper() or i in row["title"].upper()
                                or i in row["source"].upper() for i in ids_in_query):
            boost += 1.0
        scored.append((score * boost, cid))
    scored.sort(reverse=True)

    out, per = [], {}
    for score, cid in scored:
        row = rows[cid]
        if per.get(row["source"], 0) >= per_source:
            continue
        per[row["source"]] = per.get(row["source"], 0) + 1
        out.append({**row, "score": round(score, 4)})
        if len(out) >= k:
            break
    return out


def format_hit(row: dict, max_chars: int = 1300) -> dict:
    text = row["text"]
    if row.get("part", 0) and row.get("context_before"):
        text = "…" + row["context_before"][-200:] + "\n" + text
    return {
        "chunk_id": row["chunk_id"],
        "title": row["title"],
        "source_type": row["kind"],
        "status": row["status"] or "n/a",
        "date": row["date"],
        "authority": row["authority"],
        "section": f"{row['part'] + 1}/{row['parts']}",
        "text": text[:max_chars],
    }


def chunk_texts(source_prefix: str) -> list[dict]:
    """Chunks whose source matches exactly (used by guardrails, not exposed as a tool)."""
    safe = source_prefix.replace("'", "")
    return table().search().where(f"source = '{safe}'").select(COLUMNS).limit(200).to_list()


LIMIT_PATTERN = re.compile(r"[Uu]p to \$([\d,]+)(?:\.\d\d)? per duplicated line")


def support_duplicate_credit_limit_cents() -> int | None:
    """The per-line duplicate-credit limit stated in current official (non-draft) documentation.

    Read from the index at call time so a change in the source pages changes the guardrail.
    Returns None when no official chunk states it.
    """
    try:
        hits = table().search("duplicate-charge credit Support per duplicated line limit approval matrix",
                              query_type="fts", fts_columns="search_text").select(COLUMNS).limit(30).to_list()
    except Exception:  # noqa: BLE001
        return None
    found = []
    for row in hits:
        if not _official(row) or _demoted(row):
            continue
        for m in LIMIT_PATTERN.finditer(row["text"]):
            found.append((row["date"] or "", int(m.group(1).replace(",", "")) * 100))
    if not found:
        return None
    found.sort(reverse=True)
    return found[0][1]
