"""Runtime retrieval over the shipped LanceDB index (read-only).

Routed hybrid search:
  * `billing` table: billing-relevant chunks with MiniLM vectors and a full-text index.
    Searched twice — once restricted to documents (Confluence / Drive pages) and once over
    records (tickets, Slack, email, call notes) — each with dense + lexical candidates fused
    by reciprocal rank. Documents and records come back as separate groups so a policy page is
    never buried under look-alike tickets.
  * `archive` table: the rest of the corpus, lexical only, consulted when asked (`wider`) or
    when the billing index has little to say.
Each hit carries provenance (source path, kind, status, date, authority) and a bounded passage
(the matching chunk with its neighbouring text), never a whole stored record.
"""
from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

os.environ.setdefault("LANCEDB_LOG", "error")  # quiet scanner deprecation warnings in the agent log
import lancedb  # noqa: E402

HERE = Path(__file__).resolve().parent
INDEX = HERE / "knowledge"
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
RRF_C = 40
DOC_KINDS = ("confluence", "google_drive")

_lock = threading.Lock()
_db = None


def _open():
    global _db
    with _lock:
        if _db is None:
            _db = lancedb.connect(str(INDEX), read_consistency_interval=None)
        return _db


_FTS_SAFE = re.compile(r"[^\w$%.\-/ ]+")
_STOP = set("a an the of to in on for and or is are was were be been do does did what which who whom how "
            "why when where can could should would will i we our us you your my me it its this that these "
            "those there their with as at by from about any some not no yes if than then so".split())


def fts_query(text: str) -> str:
    words = [w.strip(".-/") for w in _FTS_SAFE.sub(" ", text).split()]
    words = [w for w in words if w and w.lower() not in _STOP]
    return " ".join(words[:40])


@dataclass
class Hit:
    row: dict
    score: float


def _rrf(lists: list[list[dict]], weights: list[float]) -> list[Hit]:
    scores: dict[str, float] = {}
    rows: dict[str, dict] = {}
    for results, w in zip(lists, weights):
        for rank, row in enumerate(results, start=1):
            scores[row["id"]] = scores.get(row["id"], 0.0) + w / (RRF_C + rank)
            rows.setdefault(row["id"], row)
    return [Hit(rows[i], s) for i, s in sorted(scores.items(), key=lambda kv: -kv[1])]


def _where_kinds(kinds: tuple[str, ...], include: bool) -> str:
    inner = ", ".join(f"'{k}'" for k in kinds)
    return f"kind {'IN' if include else 'NOT IN'} ({inner})"


def _search_table(table, vector, text: str, where: str | None, pool: int,
                  dense_weight: float = 1.0) -> list[Hit]:
    lists, weights = [], []
    cols = ["id", "source", "kind", "title", "ident", "status", "date", "authority", "section",
            "position", "n_chunks", "text", "context"]
    if vector is not None:
        q = table.search(vector, vector_column_name="vector").metric("cosine").limit(pool)
        if where:
            q = q.where(where, prefilter=True)
        lists.append(q.select(cols).to_list())
        weights.append(dense_weight)
    ft = fts_query(text)
    if ft:
        try:
            q = table.search(ft, query_type="fts", fts_columns="search_text").limit(pool)
            if where:
                q = q.where(where, prefilter=True)
            lists.append(q.select(cols).to_list())
            weights.append(1.0)
        except Exception:  # noqa: BLE001 - a bad lexical query must not sink the dense one
            pass
    return _rrf(lists, weights)


def _dedupe(hits: list[Hit], limit: int, per_source: int = 2) -> list[Hit]:
    """Keep the best chunk per source; a second only if its passage does not overlap the first."""
    out: list[Hit] = []
    seen: dict[str, list[dict]] = {}
    for h in hits:
        prev = seen.get(h.row["source"], [])
        if len(prev) >= per_source:
            continue
        if any(h.row["text"][:200] in p["context"] or p["text"][:200] in h.row["context"] for p in prev):
            continue
        prev.append(h.row)
        seen[h.row["source"]] = prev
        out.append(h)
        if len(out) >= limit:
            break
    return out


def _clip(text: str, n: int) -> str:
    text = text.strip()
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + " …[passage truncated]"


def _render(h: Hit, chars: int) -> dict:
    r = h.row
    return {
        "source": r["source"].removesuffix(".json"),
        "title": r["title"],
        "kind": r["kind"],
        "status": r["status"] or "n/a",
        "date": r["date"] or "unknown",
        "authority": r["authority"],
        "section": r["section"],
        "passage": _clip(r["context"], chars),
    }


class Knowledge:
    def __init__(self, gateway=None):
        self.gateway = gateway

    def _vector(self, text: str):
        if self.gateway is None:
            from redwood_kit.embeddings import embed  # build-time testing only
            return embed(model=EMBED_MODEL, inputs=[text])[0]
        return self.gateway.embed(model=EMBED_MODEL, inputs=[text])[0]

    def search(self, query: str, docs: int = 5, records: int = 5, wider: bool = False) -> dict:
        db = _open()
        billing = db.open_table("billing")
        vec = self._vector(query[:1000])
        doc_hits = _dedupe(_search_table(billing, vec, query, _where_kinds(DOC_KINDS, True), 40), docs)
        rec_hits = _dedupe(_search_table(billing, vec, query, _where_kinds(DOC_KINDS, False), 40, 0.7), records, 1)
        result = {
            "documents": [_render(h, 6500) for h in doc_hits],
            "records": [_render(h, 1300) for h in rec_hits],
        }
        if wider or len(doc_hits) + len(rec_hits) < 3:
            archive = db.open_table("archive")
            arc = _dedupe(_search_table(archive, None, query, None, 20), 3, 1)
            result["outside_billing_index"] = [_render(h, 1300) for h in arc]
        return result


_LIMIT_RE = re.compile(r"duplicate[- ]charge credit[^|\n]*\|[^|\n]*support[^|\n]*\|\s*up to \$([\d,]+(?:\.\d\d)?) per duplicated line",
                       re.I)


def duplicate_credit_limit_cents() -> tuple[int | None, str]:
    """Support's per-line duplicate-credit limit as stated in the newest published approval matrix chunk.

    Read from the shipped index at conversation time (not hard-coded) so a re-indexed policy change
    is picked up. Returns (None, reason) when no published statement is found.
    """
    try:
        table = _open().open_table("billing")
        rows = (table.search("approval matrix duplicate-charge credit support per duplicated line",
                             query_type="fts", fts_columns="search_text")
                .where("authority = 'published'", prefilter=True).limit(20)
                .select(["source", "date", "text"]).to_list())
    except Exception as error:  # noqa: BLE001
        return None, f"index unavailable: {error}"
    found = []
    for r in rows:
        m = _LIMIT_RE.search(r["text"])
        if m:
            found.append((r["date"] or "", int(round(float(m.group(1).replace(",", "")) * 100)), r["source"]))
    if not found:
        return None, "no published limit found"
    date, cents, source = max(found)
    return cents, f"{source} ({date})"
