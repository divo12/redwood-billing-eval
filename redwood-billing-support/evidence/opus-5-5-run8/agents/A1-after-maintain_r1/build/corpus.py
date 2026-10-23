"""Build-time only: turn /app/R source records into attributed search chunks.

Not imported at runtime. Each chunk keeps the source path, title, status, date and an
authority note so the support agent can judge current policy vs drafts and history.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
from datetime import datetime, timezone

R = os.environ.get("REDWOOD_R", "/app/R")
SOURCES = os.path.join(R, "sources")

BILLING_TERMS = re.compile(
    r"\b(invoice[sd]?|invoicing|billing|billed|bill|refund\w*|credit(?:s|ed| note| notes)?|dispute\w*|charge[sd]?|"
    r"overage|prepaid|goodwill|waiver|chargeback|payment\w*|autopay|card|dunning|overdue|past due|"
    r"marketplace|reseller|price|pricing|plan|subscription|quota|free tier|usage export|billed tokens|"
    r"tokeni[sz]er|metering|meter|SLA credit|service credit|tax|VAT|W-9|ACH|wire|PO number|Billing Ops|"
    r"FP&A|Finance|Controller|line item|proration|prorated|discount|commit|true-up)\b",
    re.I,
)

# Directories whose pages are written for Support / Finance / pricing work.
POLICY_DIRS = (
    "confluence/customer-success-and-support/support-process",
    "confluence/customer-success-and-support/known-issues-and-workarounds",
    "confluence/customer-success-and-support/escalation-playbook",
    "confluence/product-docs/pricing-and-packaging",
    "confluence/finance-and-legal",
    "confluence/sales-enablement/pricing-guidance",
    "confluence/oncall-and-incident-response/status-page-and-comms",
    "google_drive/shared_drives/finance",
    "confluence/policies-and-process/delegation-of-authority",
    "confluence/security-and-compliance/data-residency-and-retention/payment-card",
    "confluence/eng-platform/systems-and-services/support-console",
    "confluence/eng-developer-experience/docs-site/api-error-reference",
    "confluence/finance-and-legal",
    "linear/engineering/ENG-9120",
)

TEXT_KEYS = ("body", "content", "content_continued", "messages", "text", "thread", "description",
             "transcript", "summary", "notes", "comments", "investigation_notes", "resolution",
             "acceptance_criteria", "review_comments", "next_steps", "action_items", "appendix",
             "change_log", "release_notes", "raw", "thread_body", "raw_emails", "body_summary")


def _date(value) -> str:
    if value is None or value == "":
        return ""
    s = str(value)
    if re.fullmatch(r"\d{9,10}(\.\d+)?", s):
        try:
            return datetime.fromtimestamp(float(s), tz=timezone.utc).strftime("%Y-%m-%d")
        except (OverflowError, ValueError, OSError):
            return ""
    m = re.match(r"\d{4}-\d{2}-\d{2}", s)
    return m.group(0) if m else s[:10]


def _render(value, depth=0) -> str:
    """Readable text for nested JSON: dict keys become labels, lists become lines."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = [_render(v, depth + 1) for v in value]
        return "\n".join(p for p in parts if p)
    if isinstance(value, dict):
        # message-like objects: "sender (date): text"
        who = value.get("from") or value.get("sender") or value.get("author") or value.get("user") or value.get("speaker")
        when = value.get("date") or value.get("sent_at") or value.get("ts") or value.get("timestamp") or value.get("created_at")
        body = value.get("body") or value.get("text") or value.get("content") or value.get("message")
        if body and isinstance(body, str) and (who or when):
            head = " ".join(x for x in (str(who or ""), f"({_date(when) or when})" if when else "") if x)
            rest = {k: v for k, v in value.items() if k not in ("from", "sender", "author", "user", "speaker", "date",
                                                                  "sent_at", "ts", "timestamp", "created_at", "body",
                                                                  "text", "content", "message", "to", "cc")}
            extra = _render(rest, depth + 1) if rest and depth < 3 else ""
            return f"{head}: {body.strip()}" + (f"\n{extra}" if extra else "")
        lines = []
        for k, v in value.items():
            r = _render(v, depth + 1)
            if not r:
                continue
            if "\n" in r or len(r) > 120:
                lines.append(f"{k}:\n{r}")
            else:
                lines.append(f"{k}: {r}")
        return "\n".join(lines)
    return str(value)


def _first(d: dict, *keys) -> str:
    for k in keys:
        v = d.get(k)
        if v not in (None, "", []):
            return str(v)
    return ""


def describe(path: str, d: dict) -> dict:
    """Metadata used for display, filtering and authority notes."""
    src = path.split("/", 1)[0]
    title = _first(d, "title", "subject", "summary", "company_name", "thread_title", "meeting_id")
    key = _first(d, "key", "thread_id", "company_id", "meeting_id")
    if src == "github":
        key = f"{d.get('repo', '')}#{d.get('pr_number', '')}"
    if src == "slack":
        title = title or f"#{d.get('channel', '')} thread"
    status = _first(d, "status", "state", "stage")
    date = _date(_first(d, "last_updated", "last_modified", "updated_at", "last_email_at", "last_message_ts",
                        "recorded_at", "merged_at", "created_at", "first_message_ts", "thread_ts"))
    created = _date(_first(d, "created_at", "first_email_at", "first_message_ts", "recorded_at", "thread_ts"))
    author = _first(d, "author", "owner", "reporter", "creator", "mailbox_owner", "redwood_owner")
    team = _first(d, "owner_team", "team", "space", "channel", "project")
    body_text = json.dumps(d, ensure_ascii=False)[:4000].lower()
    kind = {
        "confluence": "Confluence page", "google_drive": "Drive doc", "slack": "Slack thread",
        "gmail": "Email thread", "jira": "Jira ticket", "linear": "Linear issue", "github": "GitHub PR",
        "fireflies": "Call transcript", "hubspot": "CRM record",
    }.get(src, src)
    s = status.lower()
    if "archived" in body_text[:600] or "archived" in title.lower() or "no longer current" in body_text[:800]:
        authority = "ARCHIVED / superseded - not current policy"
    elif s in ("draft", "in_review", "proposed", "proposal") or "draft" in title.lower() or "not approved" in body_text[:800] \
            or "proposal" in title.lower():
        authority = f"{s or 'draft'} - proposal/draft, NOT approved policy"
    elif src in ("confluence", "google_drive") and s in ("published", "final", "approved"):
        authority = f"{s} {kind} - official documentation"
    elif src in ("slack", "gmail", "fireflies"):
        authority = f"{kind} - discussion/correspondence; check against published pages"
    elif src in ("jira", "linear", "github"):
        authority = f"{kind} ({status or 'n/a'}) - historical case/work item, not policy"
    elif src == "hubspot":
        authority = "CRM record - sales notes, not policy"
    else:
        authority = f"{kind} ({status or 'n/a'})"
    return {"source": path, "kind": kind, "title": title.strip()[:200], "key": key, "status": status,
            "date": date, "created": created, "author": author, "team": team, "authority": authority}


def record_text(d: dict) -> str:
    skip = {"title", "subject", "status", "reviewers", "labels", "space", "confidentiality", "related_pages",
            "participants", "participants_internal", "participants_external", "collaborators", "tags",
            "linked_artifacts", "first_message_ts", "last_message_ts", "thread_ts"}
    body_keys = [k for k in d if k in TEXT_KEYS]
    meta_keys = [k for k in d if k not in skip and k not in body_keys]
    meta = _render({k: d[k] for k in meta_keys})
    body = "\n\n".join(_render(d[k]) for k in body_keys)
    rel = d.get("related_pages") or d.get("linked_artifacts")
    rel_text = ("related: " + ", ".join(map(str, rel))) if rel else ""
    meta_block = "## Record metadata\n" + meta if meta else ""
    return "\n\n".join(x for x in (body, meta_block, rel_text) if x)


def billing_score(meta: dict, text: str) -> float:
    hits = len(BILLING_TERMS.findall(text)) + 3 * len(BILLING_TERMS.findall(meta["title"]))
    density = hits / max(1.0, len(text) / 1000)
    return density


def in_scope(meta: dict, text: str) -> bool:
    p = meta["source"]
    if p.startswith(POLICY_DIRS):
        return True
    if p.startswith("hubspot"):
        return False
    hits = len(BILLING_TERMS.findall(text))
    return hits >= 6 and billing_score(meta, text) >= 1.2 or len(BILLING_TERMS.findall(meta["title"])) >= 1


def _split_long(p: str, target: int) -> list[str]:
    """Split one long paragraph by lines (tables keep their header rows) or sentences."""
    lines = p.split("\n")
    table_head = ""
    for i in range(len(lines) - 1):
        if lines[i].lstrip().startswith("|") and lines[i + 1].lstrip().startswith("|") \
                and set(lines[i + 1].replace("|", "").strip()) <= set("-: "):
            table_head = lines[i] + "\n" + lines[i + 1]
            break
    out: list[str] = []
    buf = ""
    for line in lines:
        if len(line) > target:
            for sent in re.split(r"(?<=[.!?])\s+", line):
                while len(sent) > target:
                    if buf:
                        out.append(buf.strip()); buf = ""
                    out.append(sent[:target]); sent = sent[target:]
                if len(buf) + len(sent) + 1 > target and buf:
                    out.append(buf.strip()); buf = ""
                buf += (" " if buf else "") + sent
            continue
        if len(buf) + len(line) + 1 > target and buf:
            out.append(buf.strip())
            buf = table_head if (table_head and line.lstrip().startswith("|")) else ""
        buf += ("\n" if buf else "") + line
    if buf.strip():
        out.append(buf.strip())
    return out


HEADING = re.compile(r"^(#{1,4} .+|h\d\. .+)$")


def split_units(text: str, target: int = 900) -> list[str]:
    """Pack paragraphs/messages into ~target-char chunks. Long paragraphs split on lines/sentences,
    tables repeat their header, and a chunk that starts mid-section is prefixed with its heading."""
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n(?=#{1,4} )|\n(?=h\d\. )", text) if p.strip()]
    pieces: list[tuple[str, str]] = []  # (heading in force, text)
    heading = ""
    for p in paras:
        first = p.split("\n", 1)[0].strip()
        if HEADING.match(first):
            heading = first
        for piece in ([p] if len(p) <= target else _split_long(p, target)):
            pieces.append((heading, piece))
    chunks: list[str] = []
    buf, buf_heading = "", ""
    for heading, piece in pieces:
        bare_heading = HEADING.match(piece) and "\n" not in piece
        if buf and len(buf) + len(piece) + 2 > target and not bare_heading:
            chunks.append(buf); buf = ""
        if not buf:
            buf_heading = heading
            if heading and not piece.startswith(heading):
                buf = f"[section: {heading.lstrip('#h0123456789. ').strip()}]"
        buf = (buf + "\n\n" + piece).strip()
    if buf:
        chunks.append(buf)
    return chunks


def iter_records():
    for f in sorted(glob.glob(os.path.join(SOURCES, "**", "*"), recursive=True)):
        if not os.path.isfile(f):
            continue
        rel = os.path.relpath(f, SOURCES)
        raw = open(f, "rb").read()
        try:
            d = json.loads(raw)
            if not isinstance(d, dict):
                d = {"content": d}
        except Exception:
            d = {"raw": raw.decode("utf-8", "replace")}
        yield rel, d, hashlib.sha256(raw).hexdigest()[:16]


def build_chunks():
    rows = []
    for rel, d, digest in iter_records():
        meta = describe(rel, d)
        text = record_text(d)
        scope = in_scope(meta, text)
        parts = split_units(text)
        n = len(parts)
        for i, part in enumerate(parts):
            prev_tail = parts[i - 1][-250:] if i > 0 else ""
            rows.append({
                "chunk_id": f"{rel}#{i}",
                **meta,
                "part": i, "parts": n,
                "text": part,
                "context_before": prev_tail,
                "in_scope": scope,
                "record_hash": digest,
            })
    return rows
