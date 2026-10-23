"""Build-time only: turn /app/R source records into searchable chunks with provenance.

Not imported at runtime. The agent ships only the LanceDB index this produces.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path

CHUNK_CHARS = 1000      # the embedding model reads ~1,000 characters
OVERLAP_CHARS = 150

STRONG = re.compile(
    r"\b(billing|billed|bill|invoic\w*|refund\w*|credit[- ]notes?|credits?|charge[sd]?|charging|pricing|priced?|"
    r"overage|prepaid|dispute\w*|marketplace|reseller\w*|dunning|collections|payment\w*|tax|vat|w-?9|ach|"
    r"discount\w*|commit(ment)?s?|promotional|goodwill|sla|service[- ]credit|rate[- ]card|free[- ]tier|"
    r"plan|plans|subscription|usage|tokens?|meter\w*|quota\w*|finance|fp&a|controller|write[- ]off)\b",
    re.I,
)

# Directories whose records are billing policy/process material: always embedded.
BILLING_DIRS = (
    "confluence/customer-success-and-support/support-process",
    "confluence/customer-success-and-support/known-issues-and-workarounds",
    "confluence/customer-success-and-support/escalation-playbook",
    "confluence/product-docs/pricing-and-packaging",
    "confluence/finance-and-legal",
    "confluence/sales-enablement/pricing-guidance",
    "confluence/oncall-and-incident-response/status-page-and-comms",
    "google_drive/shared_drives/finance",
    "jira/misc-requests",
    # R1: pages that set or change Support's billing authority and console use
    "confluence/finance-and-legal/revrec-and-billing",
    "confluence/policies-and-process/delegation-of-authority",
    "confluence/security-and-compliance/data-residency-and-retention/payment-card-data-handling",
    "confluence/eng-platform/systems-and-services/support-console",
    "confluence/eng-developer-experience/docs-site/api-error-reference",
    "google_drive/shared_drives/customer-success",
    "fireflies/customer-success/2027-03-10-finance-x-support",
    "fireflies/customer-success/2027-03-29-finance-x-support",
    "linear/engineering/ENG-9120",
)

TITLE_KEYS = ("title", "subject", "summary", "company_name", "thread_title")
SKIP_KEYS = {"confidentiality", "space", "thread_ts", "first_message_ts", "last_message_ts", "base_branch",
             "head_branch", "merge_method", "ci_status", "transcription_quality", "file_format_version","related_pages", "reviewers", "collaborators", "participants", "participants_internal",
             "participants_external", "linked_fireflies", "linked_gmail_threads", "linked_drive_docs",
             "linked_support_tickets", "ci_checks", "commits", "files_changed_count", "additions", "deletions"}


def _ts_date(value: object) -> str:
    try:
        return dt.datetime.fromtimestamp(float(str(value)), dt.UTC).date().isoformat()
    except (ValueError, OverflowError, OSError):
        return ""


def metadata(rel: str, record: dict) -> dict:
    platform = rel.split("/", 1)[0]
    title = next((record[k] for k in TITLE_KEYS if isinstance(record.get(k), str) and record[k].strip()), "")
    if platform in ("jira", "linear") and record.get("key"):
        title = f"{record['key']}: {record.get('summary') or record.get('title') or ''}"
    if platform == "github":
        title = f"{record.get('repo', '')} PR #{record.get('pr_number', '')}: {record.get('title', '')}"
    if platform == "slack":
        title = f"#{record.get('channel', '')} thread {record.get('thread_ts', '')}"
    if platform == "hubspot":
        title = f"HubSpot company {record.get('company_name', '')}"
    if platform == "gmail":
        title = f"Email: {record.get('subject', '')}"
    if platform == "fireflies":
        title = f"{record.get('title', '')} ({record.get('customer_company', '')})"
    status = record.get("status") or record.get("state") or record.get("stage") or ""
    labels = record.get("labels") or record.get("tags") or []
    if isinstance(labels, list) and "archived" in [str(x).lower() for x in labels]:
        status = f"{status} (archived)" if status else "archived"
    date = ""
    for key in ("last_updated", "last_modified", "updated_at", "last_email_at", "recorded_at", "merged_at",
                "created_at"):
        if isinstance(record.get(key), str) and record[key]:
            date = record[key][:10]
            break
    if platform == "slack":
        date = _ts_date(record.get("last_message_ts") or record.get("thread_ts"))
        date = date if "2015" <= date <= "2030" else ""
    return {
        "source": rel.removesuffix(".json"),
        "platform": platform,
        "title": str(title)[:200],
        "status": str(status)[:40],
        "date": date,
        "labels": ", ".join(map(str, labels))[:200] if isinstance(labels, list) else "",
    }


def _flatten(value: object, key: str = "") -> list[str]:
    """Readable lines from nested JSON, keeping field names and message speakers."""
    if isinstance(value, str):
        text = value.strip()
        return [f"{key}: {text}" if key and "\n" not in text and len(text) < 300 else
                (f"[{key}]\n{text}" if key else text)] if text else []
    if isinstance(value, (int, float, bool)) or value is None:
        return [f"{key}: {value}"] if key and value not in (None, "") else []
    if isinstance(value, list):
        if all(isinstance(v, (str, int, float)) for v in value):
            joined = ", ".join(map(str, value))
            if len(joined) < 300:
                return [f"{key}: {joined}"] if key and joined else []
        lines: list[str] = [f"[{key}]"] if key else []
        for item in value:
            if isinstance(item, dict):
                who = item.get("from") or item.get("author") or item.get("user") or item.get("speaker") or ""
                when = item.get("ts") or item.get("date") or item.get("created_at") or item.get("sent_at") or ""
                body = item.get("text") or item.get("body") or item.get("content") or item.get("message")
                if isinstance(body, str) and (who or when):
                    lines.append(f"{who} ({when}): {body.strip()}" if when else f"{who}: {body.strip()}")
                    rest = {k: v for k, v in item.items()
                            if k not in ("from", "author", "user", "speaker", "ts", "date", "created_at",
                                         "sent_at", "text", "body", "content", "message", "to", "cc")}
                    lines.extend(_flatten(rest))
                else:
                    lines.extend(_flatten(item))
            else:
                lines.extend(_flatten(item))
        return lines
    if isinstance(value, dict):
        lines = []
        for k, v in value.items():
            if k in SKIP_KEYS:
                continue
            lines.extend(_flatten(v, k if not key else f"{key}.{k}"))
        return lines
    return []


HEADER_KEYS = {"status", "state", "last_updated", "last_modified", "updated_at", "path", "drive_area", "doc_type",
               "file_name", "file_path", "filetype", "filename"}


def record_text(record: dict) -> str:
    """Short scalar fields become one compact `meta:` line; long fields follow as text."""
    meta, body = [], {}
    for key, value in record.items():
        if key in TITLE_KEYS[:1] or key in HEADER_KEYS or key in SKIP_KEYS:
            continue
        if isinstance(value, (str, int, float, bool)) and len(str(value)) <= 120 and "\n" not in str(value):
            if str(value).strip():
                meta.append(f"{key}={value}")
        elif isinstance(value, list) and all(isinstance(v, (str, int, float)) for v in value) \
                and len(", ".join(map(str, value))) <= 160:
            if value:
                meta.append(f"{key}=" + ", ".join(map(str, value)))
        else:
            body[key] = value
    lines = (["meta: " + "; ".join(meta)] if meta else []) + _flatten(body)
    return "\n".join(lines)


def _windows(text: str) -> list[str]:
    """Split on paragraph/line boundaries into windows of about CHUNK_CHARS with a small overlap."""
    pieces: list[str] = []
    for para in re.split(r"\n(?=#|\[|\|---|\n)|\n\n", text):
        para = para.strip()
        while len(para) > CHUNK_CHARS:
            cut = para.rfind("\n", 0, CHUNK_CHARS)
            if cut < CHUNK_CHARS // 2:
                cut = para.rfind(". ", 0, CHUNK_CHARS)
            if cut < CHUNK_CHARS // 2:
                cut = CHUNK_CHARS
            pieces.append(para[:cut + 1].strip())
            para = para[cut + 1:].strip()
        if para:
            pieces.append(para)
    windows: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 1 > CHUNK_CHARS:
            windows.append(current)
            tail = current[-OVERLAP_CHARS:]
            tail = tail[tail.find("\n") + 1:] if "\n" in tail else ""
            current = (tail + "\n" + piece).strip() if tail else piece
        else:
            current = f"{current}\n{piece}".strip()
    if current:
        windows.append(current)
    return windows


def header(meta: dict) -> str:
    parts = [f"[{meta['source']}]", meta["title"]]
    if meta["status"]:
        parts.append(f"status: {meta['status']}")
    if meta["date"]:
        parts.append(f"updated: {meta['date']}")
    return " | ".join(p for p in parts if p)


def billing_score(rel: str, text: str) -> float:
    if rel.startswith(BILLING_DIRS):
        return 1e9
    if rel.startswith("hubspot/"):  # prospect CRM notes: lexical search only unless strongly billing
        return len(STRONG.findall(text)) / max(1.0, len(text) / 1000) / 2
    hits = len(STRONG.findall(text))
    return hits / max(1.0, len(text) / 1000)


def load_records(root: Path) -> list[tuple[str, dict, str]]:
    out = []
    for path in sorted((root / "sources").rglob("*.json")):
        raw = path.read_text(errors="replace")
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            record = {"content": raw}
        if not isinstance(record, dict):
            record = {"content": record}
        out.append((str(path.relative_to(root / "sources")), record, raw))
    for name in ("company_overview.md", "initiatives.md", "project_list.txt"):
        path = root / name
        if path.exists():
            out.append((f"company/{name}", {"title": name, "content": path.read_text()}, path.read_text()))
    return out


def chunk_records(root: Path) -> list[dict]:
    chunks = []
    for rel, record, raw in load_records(root):
        meta = metadata(rel, record)
        text = record_text(record)
        digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
        score = billing_score(rel, text)
        head = header(meta)
        for i, window in enumerate(_windows(text)):
            chunks.append({
                "id": f"{meta['source']}#{i}",
                "text": f"{head}\n{window}",
                "part": i,
                "record_hash": digest,
                "billing_score": float(min(score, 1e6)),
                **meta,
            })
    return chunks
