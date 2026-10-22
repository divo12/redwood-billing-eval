from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import lancedb
from redwood_kit.embeddings import embed

MODEL = "sentence-transformers/all-MiniLM-L6-v2"
MAX_EMBED_BYTES = 1000  # the model reads at most 256 tokens
MAX_EMBED_REQUEST_BYTES = 250_000
TERMS = {
    "billing": ("billing", "bill", "billed"),
    "invoice": ("invoice", "invoices", "invoicing"),
    "credit": ("credit", "credits", "credited", "crediting"),
    "dispute": ("dispute", "disputes"),
    "refund": ("refund", "refunds"),
    "charge": ("charge", "charges", "charged"),
    "duplicate": ("duplicate", "duplicates", "double counting", "double-counting"),
    "usage": ("usage", "consumption"),
    "metering": ("meter", "metered", "metering", "token accounting"),
    "token": ("token", "tokens", "tokenization"),
    "quota": ("quota", "quotas", "rate limit", "rate limits", "429"),
    "overage": ("overage", "overages"),
    "pricing": ("pricing", "price", "prices"),
    "plan": ("plan", "plans", "packaged tier", "package tier"),
    "marketplace": ("marketplace",),
    "sla": ("sla", "service level", "service credit", "error budget"),
    "slo": ("slo",),
    "payment": ("payment", "payments", "invoicing"),
    "accounting": ("accounting", "reconciliation"),
    "escalation": ("escalation", "escalate", "handoff", "triage"),
    "operations": (
        "streaming",
        "timeout",
        "timeouts",
        "retry",
        "retries",
        "dashboard",
        "request id",
        "request_id",
        "throughput",
        "status page",
    ),
}
METADATA_FIELDS = {
    "author",
    "authors",
    "owner",
    "owner_team",
    "space",
    "channel",
    "status",
    "approval_status",
    "decision_status",
    "lifecycle",
    "authority",
    "authority_level",
    "record_type",
    "confidentiality",
    "created_at",
    "updated_at",
    "last_updated",
    "last_modified",
    "published_at",
    "date",
    "first_message_ts",
    "last_message_ts",
    "thread_ts",
    "thread_id",
    "message_count",
    "participants",
    "participants_internal",
    "participants_external",
    "reviewers",
    "labels",
    "related_pages",
    "related_links",
    "attachments",
    "has_attachments",
    "related_account",
    "deal_id",
    "region",
}
AUTHORITATIVE_STATUSES = {"published", "final"}
BILLING_TOPICS = {
    "accounting",
    "billing",
    "sla",
    "escalation",
    "invoice",
    "credit",
    "dispute",
    "refund",
    "charge",
    "duplicate",
    "marketplace",
    "overage",
    "payment",
}
MESSAGE_START = re.compile(r"^\s*(?:From:\s|[^:\n]{1,80}:\s)")


def _normalized(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _keyword_hits(text: str) -> set[str]:
    normalized = f" {_normalized(text)} "
    hits = set()
    for name, forms in TERMS.items():
        if any(f" {_normalized(form)} " in normalized for form in forms):
            hits.add(name)
    return hits


def _flatten(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_flatten(item) for item in value)
    if isinstance(value, dict):
        return "\n".join(f"{key}: {_flatten(item)}" for key, item in value.items())
    return ""


def _title(record: dict[str, Any]) -> str:
    for field in ("title", "subject", "summary", "name"):
        value = record.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "Untitled"


def _content(record: dict[str, Any]) -> str:
    return "\n".join(
        value
        for field, raw in record.items()
        if field not in METADATA_FIELDS | {"title", "subject"}
        if (value := _flatten(raw)).strip()
    )


def is_relevant(source: str | Path, record: dict[str, Any]) -> bool:
    source_text = str(source)
    path_hits = _keyword_hits(source_text)
    title_hits = _keyword_hits(_title(record))
    body_hits = _keyword_hits(_content(record))
    score = 4 * len(path_hits) + 3 * len(title_hits) + len(body_hits)
    support_path = any(part in source_text.lower() for part in ("support", "customer-success"))
    mentions_billing = score >= 8 or len(body_hits) >= 5 or (support_path and len(body_hits) >= 3)
    if not mentions_billing:
        return False
    if _is_billing_topic(source_text, record):
        return True
    billing_body = body_hits & BILLING_TOPICS
    return _is_authoritative(record) and (len(billing_body) >= 4 or "support-process" in source_text)


def _is_authoritative(record: dict[str, Any]) -> bool:
    return str(record.get("status", "")).lower() in AUTHORITATIVE_STATUSES


def _is_billing_topic(source: str, record: dict[str, Any]) -> bool:
    name = Path(source).stem.replace("-", " ")
    return bool(_keyword_hits(f"{name} {_title(record)}") & BILLING_TOPICS)


def _source_path(root: Path, path: Path) -> str:
    relative = path.relative_to(root).as_posix()
    return f"{root.name}/{relative}" if root.name in {"R0", "R1"} else relative


def selected_records(roots: list[Path]) -> list[tuple[str, dict[str, Any]]]:
    selected: dict[str, dict[str, Any]] = {}
    for root in roots:
        for source_dir in sorted(root.glob("**/sources")):
            for path in sorted(source_dir.rglob("*.json")):
                source = _source_path(root, path)
                record = json.loads(path.read_text())
                if is_relevant(source, record):
                    selected[source] = record
    return sorted(selected.items())


def _metadata(source: str, record: dict[str, Any]) -> tuple[str, str, str]:
    status = next(
        (
            str(record[field])
            for field in ("status", "approval_status", "decision_status", "lifecycle")
            if record.get(field) not in (None, "")
        ),
        "unspecified",
    )
    authority = next(
        (
            str(record[field])
            for field in ("authority", "authority_level", "record_type")
            if record.get(field) not in (None, "")
        ),
        "unspecified",
    )
    date = next(
        (
            str(record[field])
            for field in (
                "last_updated",
                "updated_at",
                "last_modified",
                "published_at",
                "date",
                "created_at",
                "last_message_ts",
            )
            if record.get(field) not in (None, "")
        ),
        "undated",
    )
    title = _title(record)[:400]
    header = (
        f"source: {source}\ntitle: {title}\n"
        f"status/authority: {status}; {authority}\ndate: {date}\n"
    )
    return status, date, header


def _sections(record: dict[str, Any]) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    for field, value in record.items():
        if field in METADATA_FIELDS | {"title", "subject"}:
            continue
        text = _flatten(value).strip()
        if not text:
            continue
        if isinstance(value, list):
            parts = [_flatten(item).strip() for item in value]
        elif field == "body":
            parts = re.split(r"(?m)(?=^#{1,6}\s)", text)
        elif field in {"messages", "text"}:
            lines = text.splitlines()
            starts = [i for i, line in enumerate(lines) if MESSAGE_START.match(line)]
            if len(starts) > 1:
                parts = []
                for index, start in enumerate(starts):
                    end = starts[index + 1] if index + 1 < len(starts) else len(lines)
                    parts.append("\n".join(lines[start:end]).strip())
            else:
                parts = [text]
        else:
            parts = [text]
        sections.extend(
            (f"{field} {index + 1}", part.strip())
            for index, part in enumerate(parts)
            if part.strip()
        )
    return sections


def _bounded_chunks(text: str, limit: int) -> list[str]:
    chunks = []
    while text:
        byte_count = 0
        cut = 0
        last_space = 0
        for index, char in enumerate(text):
            size = len(char.encode("utf-8"))
            if byte_count + size > limit:
                break
            byte_count += size
            cut = index + 1
            if char.isspace():
                last_space = cut
        if cut == 0:
            cut = 1
        if cut < len(text) and last_space > cut // 2:
            cut = last_space
        chunks.append(text[:cut])
        text = text[cut:]
    return chunks


def _record_chunks(prefix: str, sections: list[tuple[str, str]], limit: int) -> list[str]:
    chunks = []
    parts = []
    part_bytes = 0
    for section, text in sections:
        marker = f"[section: {section}]\n"
        marker_bytes = len(marker.encode("utf-8"))
        for piece in _bounded_chunks(text, limit - marker_bytes):
            part = f"{marker}{piece}"
            size = len(part.encode("utf-8"))
            if parts and part_bytes + 2 + size > limit:
                chunks.append(prefix + "\n\n".join(parts))
                parts = []
                part_bytes = 0
            if parts:
                part_bytes += 2
            parts.append(part)
            part_bytes += size
    if parts:
        chunks.append(prefix + "\n\n".join(parts))
    return chunks


def _existing_table(workspace: Path) -> Any:
    """The index already in the workspace, or None. R only grows, so its chunks stay valid."""
    if not (workspace / "evidence.lance").exists():
        return None
    return lancedb.connect(str(workspace / "evidence.lance")).open_table("evidence")


def build_index(roots: list[Path], workspace: Path, only_new: bool = False) -> tuple[int, int, int]:
    """Index billing-relevant records. With only_new, embed just the records the index lacks and append them."""
    start = time.perf_counter()
    records = selected_records(roots)
    existing = _existing_table(workspace) if only_new else None
    if existing is not None:
        known = set(existing.to_arrow().column("source").to_pylist())
        records = [(source, record) for source, record in records if source not in known]
    chunks = []
    for source, record in records:
        status, date, prefix = _metadata(source, record)
        budget = MAX_EMBED_BYTES - len(prefix.encode("utf-8"))
        for number, text in enumerate(_record_chunks(prefix, _sections(record), budget)):
            chunks.append(
                {
                    "id": f"{source}#{number}",
                    "source": source,
                    "status": status,
                    "date": date,
                    "text": text,
                }
            )
    if not chunks and existing is None:
        raise ValueError("no billing-relevant source chunks were selected")

    vectors = []
    batch = []
    batch_bytes = 0
    for chunk in chunks:
        text = chunk["text"]
        size = len(text.encode("utf-8"))
        if batch and batch_bytes + size > MAX_EMBED_REQUEST_BYTES:
            vectors.extend(embed(model=MODEL, inputs=batch))
            batch = []
            batch_bytes = 0
        batch.append(text)
        batch_bytes += size
    if batch:
        vectors.extend(embed(model=MODEL, inputs=batch))
    for chunk, vector in zip(chunks, vectors, strict=True):
        chunk["vector"] = vector
    if existing is not None:
        table = existing
        if chunks:
            table.add(chunks)
    else:
        table = lancedb.connect(str(workspace / "evidence.lance")).create_table("evidence", chunks, mode="overwrite")
    table.create_fts_index("text", replace=True, with_position=True)
    elapsed = time.perf_counter() - start
    byte_count = sum(len(chunk["text"].encode("utf-8")) for chunk in chunks)
    print(
        f"records={len(records)} chunks={len(chunks)} bytes={byte_count} "
        f"seconds={elapsed:.2f}"
    )
    return len(records), len(chunks), byte_count


def main() -> None:
    args = sys.argv[1:]
    only_new = "--new" in args
    roots = [Path(path) for path in args if path != "--new"] or [Path("/app/R")]
    workspace = Path(__file__).resolve().parent
    build_index(roots, workspace, only_new=only_new)


if __name__ == "__main__":
    main()
