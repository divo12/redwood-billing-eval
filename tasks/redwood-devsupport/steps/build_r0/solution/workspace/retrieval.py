from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import lancedb

MODEL = "text-embedding-3-small"
RRF_K = 60
MAX_RESULTS = 8
MAX_CHARS = 12_000
SOURCE_CAP = 2
_table: Any = None


def _open_table() -> Any:
    global _table
    if _table is None:
        database = lancedb.connect(Path(__file__).resolve().parent / "evidence.lance")
        _table = database.open_table("evidence")
    return _table


def search_policy(query: str, gateway: Any) -> list[dict[str, str]]:
    if not query.strip():
        return []
    table = _open_table()
    vector = gateway.embed(model=MODEL, inputs=[query])[0]
    columns = ["id", "source", "status", "date", "text"]
    lexical = table.search(query, query_type="fts").select([*columns, "_score"]).limit(32).to_list()
    dense = table.search(vector, query_type="vector").select([*columns, "_distance"]).limit(32).to_list()

    scores: dict[str, float] = defaultdict(float)
    rows: dict[str, dict[str, Any]] = {}
    for ranked in (lexical, dense):
        for rank, row in enumerate(ranked, start=1):
            key = row["id"]
            scores[key] += 1 / (RRF_K + rank)
            rows[key] = row

    results = []
    source_counts: dict[str, int] = defaultdict(int)
    chars = 0
    for key in sorted(scores, key=lambda item: (-scores[item], item)):
        row = rows[key]
        if source_counts[row["source"]] >= SOURCE_CAP:
            continue
        text = row["text"]
        remaining = MAX_CHARS - chars
        if remaining <= 0:
            break
        if len(text) > remaining:
            text = text[:remaining]
        results.append(
            {
                "source": row["source"],
                "status": row["status"],
                "date": row["date"],
                "text": text,
            }
        )
        chars += len(text)
        source_counts[row["source"]] += 1
        if len(results) == MAX_RESULTS or chars >= MAX_CHARS:
            break
    return results
