"""Build-time only: turn R source records into searchable chunks with provenance.

Not imported by the running agent. See build.py.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

TITLE_KEYS = ("title", "subject", "company_name", "summary")
STATUS_KEYS = ("status", "state", "stage")
DATE_KEYS = ("last_updated", "last_modified", "updated_at", "last_email_at", "recorded_at",
             "last_message_ts", "created_at", "first_email_at")
ID_KEYS = ("key", "pr_number", "thread_id", "meeting_id", "company_id", "thread_ts")
META_KEYS = ("space", "channel", "team", "owner_team", "project", "repo", "drive_area", "path",
             "author", "owner", "creator", "reporter", "assignee", "mailbox_owner", "customer_company",
             "related_account", "labels", "tags", "issue_type", "priority", "thread_type", "call_type",
             "customer_tier", "doc_type", "participants", "reviewers", "collaborators")
SKIP_KEYS = {"related_links", "linked_artifacts", "related_github_prs", "related_confluence_pages",
             "linked_fireflies", "linked_gmail_threads", "linked_drive_docs", "attachments"}

POLICY_SPACES = (
    "confluence/customer-success-and-support/",
    "confluence/product-docs/pricing-and-packaging/",
    "confluence/finance-and-legal/",
    "confluence/sales-enablement/pricing-guidance/",
    "confluence/oncall-and-incident-response/status-page-and-comms/",
    "google_drive/shared_drives/finance/",
)


def _date(d: dict) -> str:
    for k in DATE_KEYS:
        v = d.get(k)
        if isinstance(v, str) and v:
            if k.endswith("_ts"):
                continue
            return v[:10]
    ts = d.get("thread_ts") or d.get("first_message_ts")
    if isinstance(ts, str) and ts.split(".")[0].isdigit():
        import datetime
        try:
            return datetime.datetime.fromtimestamp(int(ts.split(".")[0]), datetime.UTC).strftime("%Y-%m-%d")
        except (OverflowError, ValueError, OSError):
            return ""
    return ""


def _clean(text: str) -> str:
    text = text.replace("\\n", "\n").replace("\r", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def kind_of(rel: str) -> str:
    return rel.split("/", 1)[0]


def authority_of(rel: str, d: dict, body: str) -> str:
    """A coarse label for how much weight a record carries; the agent still reads the text."""
    kind = kind_of(rel)
    status = str(d.get("status") or d.get("state") or "").lower()
    head = (str(d.get("title", "")) + " " + body[:400]).lower()
    tags = " ".join(map(str, (d.get("labels") or []) + (d.get("tags") or []))).lower()
    if "archived" in head or "archived" in tags or "no longer current" in head:
        return "archived"
    if kind in ("confluence", "google_drive"):
        if "draft" in status or "in_review" in status or "review" in status or "draft" in head[:200] \
                or "not approved" in head or "proposal" in head[:120]:
            return "draft"
        if status in ("published", "final", "approved", ""):
            return "published"
        return "draft"
    if kind in ("jira", "linear"):
        return "ticket"
    if kind in ("slack", "gmail", "fireflies"):
        return "discussion"
    if kind == "hubspot":
        return "crm"
    return "other"


def render(rel: str, d: dict) -> tuple[dict, list[tuple[str, str]]]:
    """Return (metadata, [(field_name, long_text), ...])."""
    title = ""
    for k in TITLE_KEYS:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            title = v.strip()
            break
    if not title:
        msgs = d.get("messages") or d.get("text") or ""
        first = msgs[0] if isinstance(msgs, list) and msgs else msgs
        title = str(first)[:100].replace("\n", " ")
    ident = next((str(d[k]) for k in ID_KEYS if d.get(k)), "")
    status = next((str(d[k]) for k in STATUS_KEYS if d.get(k)), "")
    meta_bits = []
    long_fields: list[tuple[str, str]] = []
    used = set(TITLE_KEYS[:1]) | {"title", "subject", "company_name"}
    for k, v in d.items():
        if k in SKIP_KEYS or k in ("title", "subject", "company_name"):
            continue
        if isinstance(v, str):
            if len(v) > 160:
                long_fields.append((k, _clean(v)))
            elif k in META_KEYS or k in ID_KEYS or k in STATUS_KEYS or k in DATE_KEYS:
                meta_bits.append(f"{k}: {v}")
            else:
                meta_bits.append(f"{k}: {v}")
        elif isinstance(v, list):
            if all(isinstance(x, str) for x in v):
                joined = "\n\n".join(_clean(x) for x in v)
                if len(joined) > 200:
                    long_fields.append((k, joined))
                elif v:
                    meta_bits.append(f"{k}: {', '.join(v)}")
            else:
                parts = []
                for x in v:
                    if isinstance(x, dict):
                        parts.append("; ".join(f"{a}: {_clean(str(b))}" for a, b in x.items()))
                    else:
                        parts.append(_clean(str(x)))
                long_fields.append((k, "\n\n".join(parts)))
        elif isinstance(v, dict):
            s = "; ".join(f"{a}: {b}" for a, b in v.items())
            (long_fields.append((k, _clean(s))) if len(s) > 200 else meta_bits.append(f"{k}: {s}"))
        elif v is not None:
            meta_bits.append(f"{k}: {v}")
    body_all = "\n".join(t for _, t in long_fields)
    meta = {
        "source": rel,
        "kind": kind_of(rel),
        "title": title[:200],
        "ident": ident,
        "status": status,
        "date": _date(d),
        "authority": authority_of(rel, d, body_all),
        "meta": "; ".join(meta_bits)[:700],
    }
    return meta, long_fields


HEADING = re.compile(r"^(#{1,6}\s+.+|h[1-6]\.\s+.+)$")


def _blocks(text: str) -> list[tuple[str, str]]:
    """Split into (heading, block) keeping tables and paragraphs whole where possible."""
    out: list[tuple[str, str]] = []
    heading = ""
    buf: list[str] = []

    def flush():
        if buf and "\n".join(buf).strip():
            out.append((heading, "\n".join(buf).strip()))
        buf.clear()

    for line in text.split("\n"):
        if HEADING.match(line.strip()):
            flush()
            heading = re.sub(r"^(#+|h[1-6]\.)\s*", "", line.strip())
            buf.append(line)
            continue
        if not line.strip():
            # paragraph boundary; tables never contain blank lines
            if buf and sum(len(x) for x in buf) > 700:
                flush()
            else:
                buf.append("")
            continue
        buf.append(line)
    flush()
    return out


def _split_long(block: str, limit: int) -> list[str]:
    if len(block) <= limit:
        return [block]
    lines = block.split("\n")
    table_header = [l for l in lines[:3] if l.strip().startswith("|")][:2]
    pieces, cur = [], ""
    for line in lines:
        if len(line) > limit:  # very long line: split on sentences
            for sent in re.split(r"(?<=[.!?])\s+", line):
                if len(cur) + len(sent) > limit and cur:
                    pieces.append(cur)
                    cur = ""
                cur += sent + " "
            continue
        if len(cur) + len(line) + 1 > limit and cur:
            pieces.append(cur)
            cur = "\n".join(table_header) + "\n" if (table_header and line.strip().startswith("|")) else ""
        cur += line + "\n"
    if cur.strip():
        pieces.append(cur)
    return [p.strip() for p in pieces if p.strip()]


def chunk_record(rel: str, d: dict, child_chars: int = 900, context_chars: int = 2600,
                 whole_doc_chars: int = 6500) -> list[dict]:
    meta, fields = render(rel, d)
    units: list[tuple[str, str, str]] = []  # (field, heading, text)
    for field, text in fields:
        cur_head, cur = "", ""
        for heading, block in _blocks(text):
            for piece in _split_long(block, child_chars):
                if cur and (len(cur) + len(piece) > child_chars or heading != cur_head):
                    units.append((field, cur_head, cur.strip()))
                    cur = ""
                cur_head = heading
                cur += piece + "\n\n"
        if cur.strip():
            units.append((field, cur_head, cur.strip()))
    if not units:
        units = [("meta", "", meta["meta"])]
    header = f"{meta['title']}"
    total = sum(len(u[2]) for u in units)
    if meta["kind"] in ("confluence", "google_drive") and total <= whole_doc_chars:
        context_chars = whole_doc_chars  # a short page is read whole: rules, exceptions and scope stay together
    chunks = []
    for i, (field, heading, text) in enumerate(units):
        # parent window: this unit plus neighbours until the context budget is used
        lo, hi, size = i, i, len(text)
        while True:
            grew = False
            if hi + 1 < len(units) and size + len(units[hi + 1][2]) <= context_chars:
                hi += 1; size += len(units[hi][2]); grew = True
            if lo - 1 >= 0 and size + len(units[lo - 1][2]) <= context_chars:
                lo -= 1; size += len(units[lo][2]); grew = True
            if not grew:
                break
        context = "\n\n".join(u[2] for u in units[lo:hi + 1])
        cid = hashlib.sha1(f"{rel}#{i}".encode()).hexdigest()[:16]
        section = f"{field}" + (f" > {heading}" if heading else "")
        embed_text = f"{header} | {heading}\n{text}" if heading else f"{header}\n{text}"
        chunks.append({
            "id": cid,
            **meta,
            "section": section[:160],
            "position": i,
            "n_chunks": len(units),
            "text": text,
            "context": context,
            "embed_text": embed_text[:1100],
            "search_text": f"{meta['title']} {meta['ident']} {meta['meta']} {section}\n{text}",
        })
    return chunks


BILLING_RE = re.compile(r"\b(invoic\w*|refund\w*|credit[- ]?notes?|billing|billed|charg\w+|pric\w+|overage|prepaid|promotional|service credits?|dispute\w*|chargeback|dunning|collections|w-9|w-8\w*|ach|vat|tax\w*|payment\w*|marketplace|free tier|plan fee|per million|cached tokens?|billable|metering|credits?|discount\w*|true-?up|prorat\w*|subscription|goodwill|finance|reseller|sla)\b", re.I)


def billing_score(text: str) -> int:
    return len(BILLING_RE.findall(text))


def in_dense_scope(rel: str, raw: str) -> bool:
    kind = kind_of(rel)
    if kind in ("github", "hubspot"):
        return False
    if rel.startswith(POLICY_SPACES):
        return True
    # short Slack threads and call records carry announcements/decisions that pages point to
    if (kind == "slack" and len(raw) < 2500) or (kind == "fireflies" and len(raw) < 4000):
        return True
    return billing_score(raw) >= 5
