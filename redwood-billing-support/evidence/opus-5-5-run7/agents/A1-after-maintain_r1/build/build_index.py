"""Build the shipped knowledge index from /app/R (build time only).

Two LanceDB tables under workspace/index/:
  billing  - chunks of billing-relevant records: text + MiniLM vectors + FTS index (hybrid search)
  archive  - chunks of every other record: text + FTS index only (lexical fallback, no vectors)

Each chunk carries provenance (source path, kind, title, status, dates) in columns and in a short
header line, so the agent can judge authority (published vs draft/archived, date) from what search returns.
Run:  python build/build_index.py [--no-embed]
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from flatten import R, load, walk  # noqa: E402

WS = Path(__file__).resolve().parent.parent
OUT = WS / "index"
MODEL = "sentence-transformers/all-MiniLM-L6-v2"
CHUNK = 1100  # body characters per chunk (the embedder reads ~1000 chars)

# Metadata keys that never help answer a billing question; dropped from chunk bodies.
NOISE = {
    "participants", "participants_internal", "participants_external", "reviewers", "collaborators",
    "related_links", "attachments", "linked_fireflies", "linked_gmail_threads", "linked_drive_docs",
    "related_github_prs", "commits", "ci_checks", "ci_status", "changed_files", "files_changed",
    "files_changed_count", "additions", "deletions", "head_branch", "base_branch", "merge_method",
    "thread_ts", "first_message_ts", "last_message_ts", "has_attachments", "message_count",
    "redwood_attendees", "customer_attendees", "transcription_quality", "confidentiality", "estimate",
    "cycle", "labels", "tags", "linked_artifacts", "related_pages", "linked_issues", "mailbox_owner",
    "thread_id", "company_domain", "employee_count_range", "logs", "path", "drive_area",
}
HEAD_KEYS = ("title", "subject", "summary", "company_name", "key")

# Authority judgments made while reviewing releases: an older page that a newer published page replaces.
# The flag goes into the chunk's status so the agent sees it next to the text (the old text stays searchable
# for historical questions).
SUPERSEDED = {
    "sources/confluence/customer-success-and-support/support-process/support-console-billing-credits-and-disputes.json":
        "SUPERSEDED by 'Support Console: Duplicate-Charge Credits and Billing Disputes (v2)' effective 2027-02-26",
    "sources/confluence/product-docs/pricing-and-packaging/free-tier-limits-2025.json":
        "SUPERSEDED on the status code by 'API Error Reference (2027)': Free monthly limit returns 402 quota_exceeded",
    "sources/confluence/finance-and-legal/billing-controls/collections-and-dunning-schedule-2026.json":
        "SUPERSEDED by 'Collections: Dunning Schedule for Overdue Invoices (2027)' from 2027-01-01",
}

BILLING_TERMS = re.compile(
    r"invoice|billing|billed|refund|credit note|credit|dispute|duplicate charge|charge|payment|"
    r"pricing|price|plan|overage|quota|free tier|token usage|billed tokens|usage export|marketplace|"
    r"reseller|SLA|goodwill|waiver|tax|W-9|VAT|receipt|dunning|collections|autopay|card|discount|"
    r"contract|order form|prepaid|promotional|finance|FP&A|controller|write-off",
    re.I,
)
# Directories whose records are about billing support by construction; always dense-indexed.
CORE_DIRS = (
    "sources/confluence/customer-success-and-support/",
    "sources/confluence/product-docs/pricing-and-packaging/",
    "sources/confluence/finance-and-legal/",
    "sources/confluence/sales-enablement/pricing-guidance/",
    "sources/confluence/oncall-and-incident-response/status-page-and-comms/",
    "sources/google_drive/shared_drives/finance/",
    "sources/slack/finance/",
    "sources/confluence/policies-and-process/delegation-of-authority/",
)
CORE_FILES = (
    "sources/confluence/oncall-and-incident-response/incident-closure-and-customer-reconciliation-procedure-2025.json",
    "sources/google_drive/shared_drives/customer-success/billing-escalations-raci-v0-3-draft.json",
    "company_overview.md",
    # R1: pages the current billing procedures point to from outside the core directories
    "sources/confluence/eng-platform/systems-and-services/support-console-record-format-changes-2027.json",
    "sources/confluence/eng-developer-experience/docs-site/api-error-reference-2027.json",
    "sources/linear/engineering/ENG-9120-usage-pipeline-hourly-batch-cutover.json",
    "sources/fireflies/customer-success/2027-03-10-finance-x-support-sync-march.json",
    "sources/fireflies/customer-success/2027-03-29-finance-x-support-sync-credit-ledger-cutover.json",
    "sources/google_drive/shared_drives/customer-success/contract-account-credit-pilot.json",
    "sources/google_drive/shared_drives/customer-success/missing-usage-threshold-proposal.json",
    "sources/google_drive/shared_drives/customer-success/support-refund-authority-proposal.json",
    "sources/gmail/connor_obrien/20270327-support-agent-one-subagent-per-product.json",
    "sources/slack/help/1805384400-cache-hits-half-price.json",
)


def kind_of(rel: str) -> str:
    parts = rel.split("/")
    if parts[0] != "sources":
        return "company file"
    return {
        "confluence": "Confluence page", "google_drive": "Drive doc", "slack": "Slack thread",
        "gmail": "email thread", "jira": "Jira ticket", "linear": "Linear issue", "github": "GitHub PR",
        "hubspot": "CRM record", "fireflies": "call transcript",
    }.get(parts[1], parts[1])


def meta_of(rel: str, data) -> dict:
    d = data if isinstance(data, dict) else {}
    title = next((str(d[k]) for k in HEAD_KEYS if d.get(k)), Path(rel).stem)
    if d.get("key") and d.get("summary"):
        title = f"{d['key']}: {d['summary']}"
    if rel.startswith("sources/slack/"):
        title = f"#{d.get('channel', rel.split('/')[2])} thread {Path(rel).stem}"
    status = str(d.get("status") or d.get("state") or "")
    labels = " ".join(map(str, (d.get("labels") or []) + (d.get("tags") or []))).lower()
    body_start = str(d.get("body") or d.get("content") or "")[:200].lower()
    if "archived" in labels or body_start.lstrip("> ").startswith("archived"):
        status = (status + " ARCHIVED").strip()
    if re.search(r"\bdraft\b|\bproposal\b|not approved", title, re.I) and "draft" not in status.lower():
        status = (status + " DRAFT/PROPOSAL").strip()
    date = ""
    for k in ("last_updated", "last_modified", "updated_at", "last_email_at", "recorded_at", "created_at"):
        if d.get(k):
            date = str(d[k])[:10]
            break
    if not date and rel.startswith("sources/slack/"):
        try:
            ts = float(d.get("last_message_ts") or d.get("thread_ts"))
            date = time.strftime("%Y-%m-%d", time.gmtime(ts))
        except (TypeError, ValueError):
            pass
    if rel in SUPERSEDED:
        status = f"{status} {SUPERSEDED[rel]}".strip()
    owner = str(d.get("owner_team") or d.get("team") or d.get("owner") or d.get("project") or "")
    return {"title": title[:200], "status": status, "date": date, "owner": owner[:60], "kind": kind_of(rel)}


def body_lines(data) -> list[str]:
    if not isinstance(data, dict):
        return list(walk(data))
    kept = {k: v for k, v in data.items() if k not in NOISE}
    lines = []
    for line in walk(kept):
        # messages arrive as one string with literal \n escapes in some exports
        lines.extend(line.replace("\\n", "\n").split("\n"))
    return [ln.rstrip() for ln in lines if ln.strip()]


def chunks_of(lines: list[str]) -> list[str]:
    """Greedy packing of lines into ~CHUNK-char chunks.

    A new chunk starts at a heading when the open chunk is reasonably full. A continuation chunk
    repeats the current section heading and, inside a markdown table, the table's header rows, so a
    table row is never separated from what its columns mean.
    """
    out, cur = [], ""
    heading, table_head, prev = "", [], ""
    for ln in lines:
        stripped = ln.strip()
        is_heading = stripped.startswith(("#", "h1.", "h2.", "h3."))
        if is_heading:
            heading, table_head = stripped[:150], []
        elif stripped.startswith("|"):
            if re.match(r"^\|[\s:|-]+\|$", stripped) and prev.strip().startswith("|"):
                table_head = [prev.strip(), stripped]
        else:
            table_head = []
        if len(ln) > CHUNK:  # very long line: hard-wrap
            if cur:
                out.append(cur)
                cur = ""
            out.extend(ln[i:i + CHUNK] for i in range(0, len(ln), CHUNK))
            prev = ln
            continue
        if cur and (len(cur) + len(ln) + 1 > CHUNK or (is_heading and len(cur) > CHUNK * 0.45)):
            out.append(cur)
            carry = []
            if heading and not is_heading:
                carry.append(f"(continued) {heading}")
            if table_head and stripped.startswith("|") and ln.strip() not in table_head:
                carry.extend(table_head)
            cur = "\n".join(carry)
        cur = f"{cur}\n{ln}" if cur else ln
        prev = ln
    if cur:
        out.append(cur)
    if len(out) > 1 and len(out[-1]) < 200:
        tail = out.pop()
        out[-1] += "\n" + tail
    return out


def billing_score(rel: str, text: str) -> float:
    hits = len(BILLING_TERMS.findall(text))
    return hits / max(1.0, len(text) / 1000)


def is_dense(rel: str, data, text: str) -> bool:
    if rel.startswith(CORE_DIRS) or rel in CORE_FILES:
        return True
    if rel.startswith(("sources/hubspot/", "sources/github/", "sources/fireflies/")):
        return False
    d = data if isinstance(data, dict) else {}
    if rel.startswith("sources/gmail/") and d.get("thread_type") == "finance_billing":
        return True
    if rel.startswith("sources/jira/"):
        tags = " ".join(map(str, (d.get("components") or []) + (d.get("labels") or []))).lower()
        if "billing" in tags or "token-accounting" in tags or "invoice" in tags:
            return True
    if rel.startswith(("sources/slack/support/", "sources/slack/help/")):
        return billing_score(rel, text) >= 2.0
    return billing_score(rel, text) >= 3.0


def main(embed: bool = True) -> None:
    t0 = time.time()
    rows_dense, rows_lex = [], []
    for path in sorted(R.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(R))
        data = load(path)
        meta = meta_of(rel, data)
        lines = body_lines(data)
        text = "\n".join(lines)
        dense = is_dense(rel, data, text)
        pieces = chunks_of(lines)
        for i, piece in enumerate(pieces):
            header = f"[{meta['kind']} | {meta['title']} | status: {meta['status'] or 'n/a'} | date: {meta['date'] or 'n/a'}]"
            row = {
                "chunk_id": f"{rel}#{i}",
                "source": rel,
                "part": i,
                "parts": len(pieces),
                "kind": meta["kind"],
                "title": meta["title"],
                "status": meta["status"],
                "date": meta["date"],
                "owner": meta["owner"],
                "text": f"{header}\n{piece}",
            }
            (rows_dense if dense else rows_lex).append(row)
    print(f"dense chunks {len(rows_dense)} from {len({r['source'] for r in rows_dense})} records; "
          f"lexical-only chunks {len(rows_lex)} from {len({r['source'] for r in rows_lex})} records")
    if not embed:
        return

    import lancedb
    from redwood_kit.embeddings import embed as embed_fn

    cache_path = Path(__file__).parent / "embed_cache.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    keys = [hashlib.sha1(r["text"].encode()).hexdigest() for r in rows_dense]
    todo = [i for i, k in enumerate(keys) if k not in cache]
    print(f"embedding {len(todo)} new chunks ({len(rows_dense) - len(todo)} cached)")
    if todo:
        vecs = embed_fn(model=MODEL, inputs=[rows_dense[i]["text"] for i in todo])
        for i, v in zip(todo, vecs):
            cache[keys[i]] = [round(x, 6) for x in v]
        cache_path.write_text(json.dumps(cache))
    for r, k in zip(rows_dense, keys):
        r["vector"] = cache[k]

    if OUT.exists():
        shutil.rmtree(OUT)
    db = lancedb.connect(str(OUT))
    billing = db.create_table("billing", data=rows_dense)
    billing.create_fts_index("text", with_position=True, replace=True)
    archive = db.create_table("archive", data=rows_lex)
    archive.create_fts_index("text", with_position=True, replace=True)
    manifest = {
        "model": MODEL, "chunk_chars": CHUNK, "built": time.strftime("%Y-%m-%d %H:%M:%S"),
        "release": Path("/etc/redwood-release").read_text().strip(),
        "billing_chunks": len(rows_dense), "archive_chunks": len(rows_lex),
        "dense_sources": sorted({r["source"] for r in rows_dense}),
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1))
    inventory = {str(p.relative_to(R)): hashlib.sha1(p.read_bytes()).hexdigest()
                 for p in sorted(R.rglob("*")) if p.is_file()}
    (Path(__file__).parent / f"inventory_{manifest['release']}.json").write_text(json.dumps(inventory, indent=0))
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main(embed="--no-embed" not in sys.argv)
