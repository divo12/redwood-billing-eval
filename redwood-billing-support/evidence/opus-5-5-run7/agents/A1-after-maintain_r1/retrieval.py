"""Hybrid retrieval over the shipped LanceDB index (read-only at runtime).

`billing` table: billing-relevant chunks with MiniLM vectors and an FTS index -> dense + lexical, fused by RRF.
`archive` table: every other record's chunks, FTS only -> lexical fallback for exact ids and rare terms.
Results carry provenance (source, kind, title, status, date) so the agent can weigh authority.
"""
from __future__ import annotations

import re
from pathlib import Path

import lancedb

HERE = Path(__file__).resolve().parent
INDEX = HERE / "index"
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
RRF_C = 60
COLUMNS = ["chunk_id", "source", "part", "parts", "kind", "title", "status", "date", "text"]

# Records that define how Billing Support works today (team procedures, Finance's approval matrix).
# A small prior so they are not buried under long tickets that repeat the same words.
POLICY_PREFIXES = (
    "sources/confluence/customer-success-and-support/support-process/",
    "sources/confluence/customer-success-and-support/known-issues-and-workarounds/",
    "sources/confluence/product-docs/pricing-and-packaging/",
    "sources/confluence/finance-and-legal/",
    "sources/google_drive/shared_drives/finance/",
    "sources/confluence/oncall-and-incident-response/status-page-and-comms/",
    "sources/confluence/policies-and-process/delegation-of-authority/",
    "sources/confluence/eng-platform/systems-and-services/support-console-record-format-changes",
    "sources/confluence/eng-developer-experience/docs-site/api-error-reference",
)
POLICY_FILTER = " OR ".join(f"source LIKE '{p}%'" for p in POLICY_PREFIXES)
ID_PATTERN = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b|\b\w+_\w+\b")


class Retriever:
    def __init__(self, gateway=None):
        self.gateway = gateway
        self.db = lancedb.connect(str(INDEX), read_consistency_interval=None)
        self.billing = self.db.open_table("billing")
        self.archive = self.db.open_table("archive")

    def _vector(self, query: str):
        if self.gateway is None:
            return None
        try:
            return self.gateway.embed(model=EMBED_MODEL, inputs=[query])[0]
        except Exception as error:  # embedding unavailable: fall back to lexical only
            print(f"embed failed: {error}")
            return None

    @staticmethod
    def _fts_query(query: str) -> str:
        # Strip characters the FTS parser treats as syntax; keep words, ids and numbers.
        cleaned = re.sub(r"[^\w\s$.\-/]", " ", query)
        return " ".join(cleaned.split())[:400]

    def _fts(self, table, query: str, limit: int) -> list[dict]:
        q = self._fts_query(query)
        if not q:
            return []
        try:
            return table.search(q, query_type="fts").select(COLUMNS).limit(limit).to_list()
        except Exception as error:
            print(f"fts failed: {error}")
            return []

    def search(self, query: str, k: int = 6, include_archive: bool = False) -> list[dict]:
        pools: list[list[dict]] = []
        vector = self._vector(query)
        if vector is not None:
            pools.append(self.billing.search(vector).select(COLUMNS).limit(30).to_list())
            # Routed pass: the same query restricted to current-procedure directories, so a short policy
            # page is not crowded out by long threads that repeat its vocabulary.
            try:
                pools.append(self.billing.search(vector).where(POLICY_FILTER, prefilter=True)
                             .select(COLUMNS).limit(12).to_list())
            except Exception as error:
                print(f"policy pass failed: {error}")
        pools.append(self._fts(self.billing, query, 30))
        # Exact identifiers (ticket keys, request ids) may live only in the archive table.
        ids = [i for i in ID_PATTERN.findall(query) if any(ch.isdigit() for ch in i)]
        if include_archive:
            pools.append(self._fts(self.archive, query, 15))
        for ident in ids[:3]:
            pools.append(self._fts(self.billing, ident, 8))
            pools.append(self._fts(self.archive, ident, 8))
        scores: dict[str, float] = {}
        rows: dict[str, dict] = {}
        for pool in pools:
            for rank, row in enumerate(pool, start=1):
                cid = row["chunk_id"]
                scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_C + rank)
                rows[cid] = row
        for cid, row in rows.items():
            if row["source"].startswith(POLICY_PREFIXES):
                scores[cid] += 0.006
            if "SUPERSEDED" in (row["status"] or ""):
                scores[cid] -= 0.006  # still visible for history, but below the page that replaced it
            if any(ident.lower() in row["text"].lower() for ident in ids):
                scores[cid] += 0.05  # the passage names the exact id asked about
        ranked = sorted(rows.values(), key=lambda r: scores[r["chunk_id"]], reverse=True)
        out, per_source = [], {}
        for row in ranked:
            n = per_source.get(row["source"], 0)
            if n >= 2:  # at most two chunks per record so one long thread cannot fill the context
                continue
            per_source[row["source"]] = n + 1
            out.append(row)
            if len(out) >= k:
                break
        return [self._public(row) for row in out]

    @staticmethod
    def _public(row: dict) -> dict:
        return {
            "source": row["source"],
            "chunk": f"{row['part'] + 1}/{row['parts']}",
            "kind": row["kind"],
            "title": row["title"],
            "status": row["status"] or "n/a",
            "date": row["date"] or "n/a",
            "text": row["text"].split("\n", 1)[-1],
        }
