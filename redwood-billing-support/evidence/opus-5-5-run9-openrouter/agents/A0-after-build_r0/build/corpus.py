"""Build-time only: turn R source records into attributed chunks.

Not imported by the running agent. `build_index.py` uses it to write `index/`.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path

R_SOURCES = Path("/app/R/sources")

BILLING_TERMS = re.compile(
    r"\b(invoice[sd]?|invoicing|refund(s|ed)?|credit[- ]?notes?|credits?|billing|billed|billable|overage|"
    r"prorat\w*|pricing|price[sd]?|plan fee|chargeback|disputes?|payments?|tax|w-?9|ach|wire|marketplace|"
    r"reseller|discount|prepaid|promotional|service credit|goodwill|waiver|dunning|collections|"
    r"cached tokens?|charge[sd]?|per[- ]token|free tier|quota_exceeded|autopay|receipt)\b",
    re.I,
)

# Fields that carry identity or metadata rather than body text.
META_FIELDS = {
    "title", "subject", "summary", "status", "space", "author", "owner", "owner_team", "team",
    "created_at", "last_updated", "last_modified", "updated_at", "first_email_at", "last_email_at",
    "recorded_at", "thread_ts", "first_message_ts", "last_message_ts", "labels", "tags", "reviewers",
    "collaborators", "participants", "participants_internal", "participants_external", "related_pages",
    "linked_artifacts", "confidentiality", "doc_type", "drive_area", "path", "key", "project",
    "issue_type", "priority", "severity", "channel", "mailbox_owner", "repo", "pr_number", "company_name",
    "company_id", "meeting_id", "message_count", "thread_type", "related_account", "region",
}
SHORT_LIST_FIELDS = {"labels", "tags", "related_pages", "linked_issues", "linked_artifacts"}


def _ts_date(value: str) -> str:
    try:
        return dt.datetime.fromtimestamp(float(value), dt.UTC).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def record_meta(rel: str, rec: dict) -> dict:
    platform = rel.split("/", 1)[0]
    title = (rec.get("title") or rec.get("subject") or rec.get("company_name") or "")
    if platform in ("jira", "linear") and rec.get("key"):
        title = f"{rec['key']}: {rec.get('summary') or rec.get('title') or ''}"
    if platform == "github":
        title = f"{rec.get('repo', '')} PR {rec.get('pr_number', '')}: {rec.get('title', '')}"
    if platform == "slack":
        title = f"#{rec.get('channel', '')} thread"
    date = (rec.get("last_updated") or rec.get("last_modified") or rec.get("updated_at")
            or rec.get("last_email_at") or rec.get("recorded_at") or "")
    if platform == "slack":
        date = _ts_date(rec.get("last_message_ts") or rec.get("thread_ts"))
    status = str(rec.get("status") or rec.get("state") or "").strip()
    labels = [str(x).lower() for x in (rec.get("labels") or rec.get("tags") or [])]
    flags = []
    low_title = title.lower()
    if "archived" in labels or "(archived)" in low_title:
        flags.append("archived")
    if "draft" in labels or "draft" in low_title:
        flags.append("draft")
    if "proposal" in labels or low_title.startswith("proposal"):
        flags.append("proposal")
    owner = rec.get("owner_team") or rec.get("team") or rec.get("author") or rec.get("owner") or ""
    if platform == "gmail":
        owner = rec.get("mailbox_owner", "")
    if platform == "slack":
        owner = ", ".join(rec.get("participants", [])[:4])
    return {
        "source": rel.removesuffix(".json"),
        "platform": platform,
        "title": " ".join(str(title).split())[:160],
        "status": status[:40],
        "flags": ",".join(flags),
        "date": str(date)[:25],
        "owner": str(owner)[:80],
        "labels": ",".join(labels)[:200],
    }


def record_sections(rec: dict) -> list[tuple[str, str]]:
    """(field, text) pairs of body text, in record order."""
    sections = []
    for key, value in rec.items():
        if key in META_FIELDS and key not in ("summary",):
            continue
        if isinstance(value, str):
            text = value.replace("\\n", "\n").strip()
            if len(text) >= 3 and not (len(text) < 40 and key not in ("resolution", "root_cause", "impact")):
                sections.append((key, text))
            elif text and key in ("state", "merge_outcome", "stage", "account_tier"):
                sections.append((key, text))
        elif isinstance(value, list) and value and all(isinstance(x, str) for x in value):
            if key in SHORT_LIST_FIELDS:
                continue
            joined = "\n".join(x.replace("\\n", "\n").strip() for x in value)
            if len(joined) >= 40:
                sections.append((key, joined))
    return sections


HEADING = re.compile(r"^(#{1,6}\s|h[1-6]\.\s)")


def _blocks(text: str) -> list[tuple[str, str]]:
    """Split text into (heading, block) where blocks are paragraphs / tables under a heading."""
    lines = text.split("\n")
    out, heading, buf = [], "", []

    def flush():
        if buf and "".join(buf).strip():
            out.append((heading, "\n".join(buf).strip()))
        buf.clear()

    for line in lines:
        if HEADING.match(line.strip()):
            flush()
            heading = re.sub(r"^(#{1,6}\s|h[1-6]\.\s)", "", line.strip()).strip()
            continue
        if not line.strip():
            flush()
            continue
        if buf and buf[-1].lstrip().startswith("|") != line.lstrip().startswith("|"):
            flush()
        buf.append(line)
    flush()
    return out


def _split_long(block: str, limit: int) -> list[str]:
    if len(block) <= limit:
        return [block]
    lines = block.split("\n")
    if lines[0].lstrip().startswith("|") and len(lines) > 2:
        head = lines[:2]  # table header + separator kept with every piece
        pieces, cur = [], list(head)
        for line in lines[2:]:
            if len("\n".join(cur + [line])) > limit and len(cur) > 2:
                pieces.append("\n".join(cur))
                cur = list(head)
            cur.append(line)
        pieces.append("\n".join(cur))
        return pieces
    if len(lines) > 1:
        pieces, cur = [], ""
        for line in lines:
            for part in _split_long(line, limit) if len(line) > limit else [line]:
                if cur and len(cur) + len(part) + 1 > limit:
                    pieces.append(cur)
                    cur = ""
                cur = f"{cur}\n{part}" if cur else part
        if cur:
            pieces.append(cur)
        return pieces
    sentences = re.split(r"(?<=[.!?])\s+", block)
    pieces, cur = [], ""
    for s in sentences:
        while len(s) > limit:
            pieces.append(s[:limit])
            s = s[limit:]
        if cur and len(cur) + len(s) + 1 > limit:
            pieces.append(cur)
            cur = ""
        cur = f"{cur} {s}" if cur else s
    if cur:
        pieces.append(cur)
    return pieces


def chunk_record(rel: str, rec: dict, limit: int = 1000) -> list[dict]:
    """Pack a record's sections into chunks of about `limit` characters.

    Paragraphs stay under their heading, tables keep their header row, and short fields
    (components, links) are packed with their neighbours instead of becoming chunks alone.
    """
    meta = record_meta(rel, rec)
    units = []
    for field, text in record_sections(rec):
        for heading, block in _blocks(text):
            label = field if not heading else f"{field} > {heading}"
            for piece in _split_long(block, limit - 60):
                units.append((label, piece))
    packed, cur_labels, cur, last = [], [], "", None
    for label, piece in units:
        text = piece if label == last else f"[{label}]\n{piece}"
        if cur and len(cur) + len(text) + 2 > limit:
            packed.append((cur_labels, cur))
            cur, cur_labels = "", []
            text = f"[{label}]\n{piece}"
        cur = f"{cur}\n\n{text}" if cur else text
        if label not in cur_labels:
            cur_labels.append(label)
        last = label
    if cur:
        packed.append((cur_labels, cur))
    chunks = []
    for i, (labels, body) in enumerate(packed):
        chunks.append({**meta, "section": " | ".join(labels)[:160], "body": body, "seq": i,
                       "chunk_id": hashlib.sha1(f"{meta['source']}#{i}#{body}".encode()).hexdigest()[:16]})
    return chunks


def iter_records():
    for dirpath, _, files in os.walk(R_SOURCES):
        for name in sorted(files):
            if not name.endswith(".json"):
                continue
            path = Path(dirpath, name)
            rel = path.relative_to(R_SOURCES).as_posix()
            try:
                rec = json.loads(path.read_text())
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(rec, dict):
                yield rel, rec, path.read_text()


def billing_score(raw: str) -> int:
    return len(BILLING_TERMS.findall(raw))


def in_billing_scope(rel: str, rec: dict, raw: str) -> bool:
    """Records that get dense vectors: billing policy, process, pricing, and billing cases."""
    score = billing_score(raw)
    density = score * 1000 / max(1, len(raw))
    parts = rel.split("/")
    platform = parts[0]
    if platform == "confluence":
        if parts[1] in ("customer-success-and-support", "finance-and-legal") or rel.startswith(
            ("confluence/product-docs/pricing-and-packaging", "confluence/sales-enablement/pricing-guidance",
             "confluence/oncall-and-incident-response/status-page-and-comms")):
            return True
        return score >= 6
    if platform == "google_drive":
        if "/finance/" in rel:
            return True
        return score >= 6
    if platform == "slack":
        if parts[1] in ("support", "finance", "help"):
            return score >= 3
        return score >= 6
    if platform == "jira":
        return score >= 6 or rel.startswith("jira/misc-requests")
    if platform == "gmail":
        return score >= 8
    if platform == "hubspot":
        return score >= 20
    return score >= 10 and density >= 1.5
