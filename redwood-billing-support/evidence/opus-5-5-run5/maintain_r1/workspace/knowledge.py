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


_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                          "september", "october", "november", "december"], start=1)}
_ROW_RE = re.compile(r"\|\s*duplicate[- ]charge credit[^|\n]*\|[^|\n]*support[^|\n]*\|([^|\n]+)\|", re.I)
_BOOK_LIMIT_RE = re.compile(r"(\d{4}) price book:?\s*up to \$([\d,]+(?:\.\d\d)?)", re.I)
_EARLIER_RE = re.compile(r"earlier price book:?\s*up to \$([\d,]+(?:\.\d\d)?)", re.I)
_FLAT_RE = re.compile(r"up to \$([\d,]+(?:\.\d\d)?) per duplicated line", re.I)
_EFFECTIVE_RE = re.compile(r"(\d{4}) price book takes effect for usage from (\d{1,2}) (\w+) (\d{4})", re.I)


def _published(query: str, n: int = 20) -> list[dict]:
    table = _open().open_table("billing")
    return (table.search(query, query_type="fts", fts_columns="search_text")
            .where("authority = 'published'", prefilter=True).limit(n)
            .select(["source", "date", "text", "context"]).to_list())


def _cents(text: str) -> int:
    return int(round(float(text.replace(",", "")) * 100))


def duplicate_credit_limit_cents(period_start: str | None = None) -> tuple[int | None, str]:
    """Support's per-line duplicate-credit limit for an invoice, read from the indexed policy at
    conversation time (not hard-coded): the newest published approval-matrix row, and — when the row
    differs by price book — the price-book effective dates from Finance's close calendar.
    Returns (None, reason) when no published statement is found.
    """
    try:
        rows = _published("approval matrix duplicate-charge credit support per duplicated line price book")
    except Exception as error:  # noqa: BLE001
        return None, f"index unavailable: {error}"
    cells = []
    for r in rows:
        for m in _ROW_RE.finditer(r["text"] + "\n" + r["context"]):
            cells.append((r["date"] or "", m.group(1), r["source"]))
    if not cells:
        return None, "no published limit found"
    date, cell, source = max(cells)
    books = {int(y): _cents(v) for y, v in _BOOK_LIMIT_RE.findall(cell)}
    earlier = _EARLIER_RE.search(cell)
    if not books:
        flat = _FLAT_RE.search(cell)
        return (_cents(flat.group(1)), f"{source} ({date})") if flat else (None, "limit not parsed")
    # which price book rated this invoice: the newest book whose effective date <= the period start
    effective: dict[int, str] = {}
    try:
        for r in _published("price book takes effect for usage close calendar invoice periods"):
            for y, d, mon, yr in _EFFECTIVE_RE.findall(r["text"] + "\n" + r["context"]):
                if mon.lower() in _MONTHS:
                    effective[int(y)] = f"{int(yr):04d}-{_MONTHS[mon.lower()]:02d}-{int(d):02d}"
    except Exception:  # noqa: BLE001
        pass
    start = (period_start or "")[:10]
    applicable = [y for y in books if y in effective and start and effective[y] <= start]
    if applicable:
        y = max(applicable)
        return books[y], f"{source} ({date}): {y} price book, effective {effective[y]}"
    if earlier and start and all(y in effective for y in books):
        return _cents(earlier.group(1)), f"{source} ({date}): earlier price book"
    # unknown period or effective date: use the strictest stated limit
    candidates = list(books.values()) + ([_cents(earlier.group(1))] if earlier else [])
    return min(candidates), f"{source} ({date}): strictest limit (price book not determined)"
