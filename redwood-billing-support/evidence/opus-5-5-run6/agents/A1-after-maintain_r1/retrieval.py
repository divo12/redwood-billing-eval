"""Runtime search over the shipped LanceDB index (read-only).

Two tables built by indexing/build_index.py:
  billing - billing-relevant chunks with all-MiniLM-L6-v2 vectors and a full-text index
  corpus  - every other chunk, full-text index only
A query runs dense + lexical search on `billing` and lexical search on `corpus`, then merges the
lists with reciprocal rank fusion. Results keep source id, title, status and date so the agent can
judge authority and recency itself.
"""
from __future__ import annotations

import re
from pathlib import Path

import lancedb

INDEX = Path(__file__).resolve().parent / "index"
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
RRF_C = 60
POOL = 30
MAX_PER_SOURCE = 2
STOPWORDS = set("""a an and are as at be by can do does for from has have how i if in is it its me my of on or our
so that the their them there they this to was we what when where which who why will with you your""".split())


POLICY_PLATFORMS = ("confluence", "google_drive", "company")
POLICY_FILTER = "platform IN ('confluence', 'google_drive', 'company')"
CORE_FILTER = "billing_score >= 100000"
ID_PATTERN = re.compile(r"\b([A-Z][A-Z0-9]{1,7}-\d{2,8})\b")


def _fts_query(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9_\-\.$&]*", text)
    kept = [w.strip(".") for w in words if w.lower() not in STOPWORDS]
    return " ".join(kept)[:500] or text[:200]


class Retriever:
    def __init__(self, gateway=None) -> None:
        self.gateway = gateway
        db = lancedb.connect(str(INDEX))
        self.billing = db.open_table("billing")
        self.corpus = db.open_table("corpus")

    def _vector(self, text: str) -> list[float] | None:
        if self.gateway is None:
            from redwood_kit.embeddings import embed  # build-time testing only
            return embed(model=EMBED_MODEL, inputs=[text])[0]
        return self.gateway.embed(model=EMBED_MODEL, inputs=[text])[0]

    def _run(self, table, query: str, vector, where: str | None) -> list[tuple[str, list[dict]]]:
        out = []
        if vector is not None and table is self.billing:
            try:
                search = table.search(vector)
                if where:
                    search = search.where(where, prefilter=True)
                out.append(("dense", search.limit(POOL).to_list()))
            except Exception as error:
                print(f"dense search failed: {error}")
        try:
            search = table.search(_fts_query(query), query_type="fts")
            if where:
                search = search.where(where, prefilter=True)
            out.append(("fts", search.limit(POOL).to_list()))
        except Exception as error:
            print(f"fts search failed: {error}")
        return out

    def _fuse(self, lists: list[tuple[float, list[dict]]], rows: dict[str, dict]) -> list[str]:
        scores: dict[str, float] = {}
        for weight, results in lists:
            for rank, row in enumerate(results, start=1):
                scores[row["id"]] = scores.get(row["id"], 0.0) + weight / (RRF_C + rank)
                rows.setdefault(row["id"], row)
        return sorted(scores, key=scores.get, reverse=True)

    def _id_hits(self, query: str) -> list[dict]:
        """Chunks of records whose id or title carries an exact identifier from the query (e.g. SUP-1913)."""
        hits: list[dict] = []
        for ident in dict.fromkeys(ID_PATTERN.findall(query)):
            pattern = f"%{ident.lower()}%"
            for table in (self.billing, self.corpus):
                try:
                    hits.extend(table.search().where(
                        f"lower(source) LIKE '{pattern}' OR lower(title) LIKE '{pattern}'").limit(6).to_list())
                except Exception as error:
                    print(f"id lookup failed: {error}")
        hits.sort(key=lambda row: row.get("part", 0))
        return hits

    def search(self, query: str, k: int = 6) -> list[dict]:
        """Routed retrieval: policy/procedure pages and case history are ranked separately, then merged
        with policy pages first, so a busy thread cannot crowd out the page that sets the rule."""
        try:
            vector = self._vector(query[:1000])
        except Exception as error:  # dense search unavailable: lexical still answers
            print(f"embedding failed: {error}")
            vector = None
        rows: dict[str, dict] = {}
        everything: list[tuple[float, list[dict]]] = []
        for table, weight in ((self.billing, 1.0), (self.corpus, 0.8)):
            everything += [(weight, results) for _, results in self._run(table, query, vector, None)]
        policy: list[tuple[float, list[dict]]] = []
        for table, weight in ((self.billing, 1.0), (self.corpus, 0.6)):
            policy += [(weight, results) for _, results in self._run(table, query, vector, POLICY_FILTER)]
        policy += [(0.7, results) for _, results in self._run(self.billing, query, vector, CORE_FILTER)]
        ids = self._id_hits(query)
        if ids:
            everything.append((2.0, ids))
            policy.append((2.0, [r for r in ids if r["platform"] in POLICY_PLATFORMS]))
        ranked_policy = self._fuse(policy, rows)
        ranked_all = self._fuse(everything, rows)
        quota = max(1, (k + 1) // 2 + (1 if k >= 6 else 0))
        per_source: dict[str, int] = {}
        chosen: list[str] = []
        named = [row["id"] for row in ids]  # a record the query names exactly comes first
        for row in ids:
            rows.setdefault(row["id"], row)
        for ranked, extra in ((named, 3), (ranked_policy, quota), (ranked_all, k)):
            limit = min(k, len(chosen) + extra)
            for chunk_id in ranked:
                if len(chosen) >= limit:
                    break
                source = rows[chunk_id]["source"]
                if chunk_id in chosen or per_source.get(source, 0) >= MAX_PER_SOURCE:
                    continue
                per_source[source] = per_source.get(source, 0) + 1
                chosen.append(chunk_id)
        out = []
        for chunk_id in chosen:
            row = rows[chunk_id]
            out.append({
                "source": row["source"],
                "kind": "policy/procedure page" if row["platform"] in POLICY_PLATFORMS else "case record or discussion",
                "title": row["title"],
                "status": row["status"] or "n/a",
                "updated": row["date"] or "n/a",
                "text": row["text"].split("\n", 1)[-1],
            })
        return out
