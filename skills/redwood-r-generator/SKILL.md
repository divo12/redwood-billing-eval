---
name: redwood-r-generator
description: Generates new Redwood Inference source records (Slack threads, Gmail threads, Jira and Linear issues, Confluence pages, Google Drive docs, HubSpot companies, Fireflies meetings, GitHub PRs) in each platform's native format, for planting spec-derived knowledge into a Redwood release. Use when writing R, ΔR, or knowledge-condition variants (complete, fragmented, incomplete, conflicting, noisy) into environments/redwood-*, or when the user asks to generate or plant Redwood records.
disable-model-invocation: true
---

# Redwood R Generator

## Quick start

```
Task progress:
- [ ] 1. Pick the target path
- [ ] 2. Read the platform reference
- [ ] 3. Read 2-3 existing records in the same folder
- [ ] 4. Write the record
- [ ] 5. Run scripts/check_record.py until it prints OK
- [ ] 6. Record provenance outside the corpus
```

**1. Target.** `environments/<tree>/R<n>/sources/<platform>/<subfolder>/<file>.json`. Use the tree the task mounts (redwood-nano for `tasks/redwood-devsupport`). `R<n>` is the release where the knowledge first appears: `R0` for base knowledge, `R1` for ΔR.

**2. Reference.** Read the platform's generator instructions: file-name format, content rules, and required metadata. Ignore "Target number of files"; it is the upstream corpus size.

**3. Examples.** Copy the field set and tone of real records in the same subfolder, not their content.

**4. Write.**

- Native fields only. Never add `dataset_doc_uuid`, `title_field_name`, `content_field_names`, `original_location`, or `dataset_noise_document`.
- Dates fall inside the release: after anything the record supersedes, and no later than the release's today (`R0` 2026-09-15, `R1` 2027-04-08). Per-platform cutoffs: `environments/redwood-mini/split_manifest.json` `cutoffs`.
- People come from the tree's `R0/employee_directory.yaml`.
- Links (ticket keys, PR URLs, wiki paths, thread ids) point only to records in the same or an earlier release.
- Each record carries only what its artifact would naturally carry. Never paraphrase the spec: a Slack thread states a decision the way people talk; a Confluence page states it as documentation.
- Write with `json.dumps(record, indent=2, ensure_ascii=...)` and no trailing newline, like the corpus.

**5. Validate.**

```bash
python skills/redwood-r-generator/scripts/check_record.py environments/redwood-nano <new records...>
```

It reports generator fields, formatting that differs from the corpus, a path already used in another release (`redwood-advance` refuses an `R1` file on an `R0` path), and any `agents.md` inside a release folder. Fix and rerun until it prints `OK`.

**6. Provenance.** For every rule a new or edited record touches, record one line: rule ID, release, record path, and the record's role for that rule:

| Role | Meaning |
|---|---|
| `full` | States the whole rule on its own |
| `partial` | States part of the rule (name the parts, e.g. `when`, `action`, `limit`) |
| `contradicting` | In the same release, would make the agent behave differently on this rule, whatever its authority (a draft or Slack thread counts) |
| `superseding` / `superseded` | A later-release record replaces an earlier one |
| `distractor` | Looks relevant but states no competing version: off-topic, internal-only, or an exception scoped to one customer |

Roles are per (rule, record); one record can be `contradicting` for one rule and a `distractor` for another.

Add `form` (`stated`, `examples-only`, `image`) and `authority` (`published`, `draft`, `thread`, `ticket`) when they apply. The knowledge condition of each (rule, release) is computed from these roles, never assigned by hand:
- `complete`: a `full` carrier exists;
- `fragmented`: `partial` carriers cover every part;
- `incomplete`: some part has no carrier;
- `conflicting`: a `contradicting` carrier, or a `superseding` one;
- `noisy`: any `distractor`.

This goes in the task's hidden `tests/`, never in the corpus or this skill. After planting into redwood-nano, update `slice_manifest.json` counts.

## Platform references

- [confluence.md](references/confluence.md)
- [fireflies.md](references/fireflies.md)
- [github.md](references/github.md)
- [gmail.md](references/gmail.md)
- [google_drive.md](references/google_drive.md)
- [hubspot.md](references/hubspot.md)
- [jira.md](references/jira.md)
- [linear.md](references/linear.md)
- [slack.md](references/slack.md)

These are EnterpriseRAG-Bench's per-platform `agents.md`, unchanged except for the name, removed from `environments/` so a builder never reads them. Never copy them, or any file named `agents.md`, into a release folder.
