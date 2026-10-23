---
name: redwood-world
description: Reusable Redwood Inference world knowledge for this task. Use with eval-engineering when designing or building Harbor tasks over the Redwood developer-support corpus, its R0–R2 releases, the curated slice, or the redwood-devsupport agent contract.
---

# Redwood World Knowledge

Read `$eval-engineering` first. Use its broad references as guidance. Use this skill for how that guidance applies to Redwood Inference in this repository.

Written from this checkout only: `environments/redwood`, `environments/redwood-mini`, `environments/redwood-nano`, and `redwood-billing-support`. Not from another worktree. Drafted on 2026-09-25. It has not been reconciled with a scored task audit.

## Start here

Redwood Inference is a fictional ~150-person company that sells LLM inference: a hosted API, dedicated capacity, and private (VPC/on-prem) deployments. The corpus is EnterpriseRAG-Bench `generated_data` at commit `d36685e273713975ee20299bbf1ab64165575b3c`, MIT, converted by moving or copying files and removing the generator fields described below. Every other field is unchanged.

Three committed trees, largest first. Do not treat them as the same knowledge:

| Tree | Path | What a task may assume |
|---|---|---|
| Full Redwood | `environments/redwood/R0`, `R1`, `R2` | 511,968 of the 511,978 upstream files (all but `uuid_index.json` and the nine `sources/<platform>/agents.md`), each in the release the split assigns it: redwood-mini's records plus every record the split left out. Byte-compared against the pinned upstream tarball on 2026-09-25, before the generator fields were removed. R0 also holds the upstream catalogs described below. |
| Developer-support split (mini) | `environments/redwood-mini/R0`, `R1`, `R2` | What `redwood-billing-support` builds into the image. Kept records for each release. R0 also has the five upstream company files. Counts and rules are in `environments/redwood-mini/split_manifest.json`. |
| Curated slice (nano) | `environments/redwood-nano/` | Hand-edited after `scripts/slice_redwood.py`, and generator fields removed. Rerunning that script does not reproduce it. Not mounted by the current task. |

- Read `scripts/split_redwood.py` (module docstring) and `environments/redwood-mini/split_manifest.json` (`rules`, `kept`, `checks`) before changing membership, versions, or links. Run both scripts with Python 3.12 or later (`uv run --python 3.12 --no-project python ...`); the system `python3` here is 3.9 and fails on import.
- Read `environments/redwood-nano/slice_manifest.json` before mounting or subsetting the slice. `company_files_in_R0` and `curation` say what the hand edit changed.
- Read `redwood-billing-support/environment/app/framework/agent_contract.md` and `framework/README.md` before changing the evaluated agent.
- `docs/redwood-presales-R.md` is a local, gitignored draft of a different pre-sales bundle. It is not built, and it disagrees with the committed corpus on generator-field stripping. Do not use it as the world.

Rebuild commands, when a human asks to regenerate data:

- `scripts/import_enterprise_rag.sh` downloads the pinned `generated_data` into gitignored `environments/redwood-mini/.upstream/`.
- `python scripts/split_redwood.py` moves files into `environments/redwood-mini` and gitignored `redwood-left/`, one version folder each, then removes the generator fields and updates `original_locations.json`. Their per-release union is `environments/redwood`.
- `python scripts/slice_redwood.py` refuses to run if `environments/redwood-nano/` exists, and would not match the committed slice anyway.

## Knowledge routing

| Current need | Read | What it provides |
|---|---|---|
| Who is in which release, and why | `environments/redwood-mini/split_manifest.json` | Pin, seed `redwood-split-v1`, pools, quotas, cutoffs, achieved `kept` counts, closure checks |
| What the image actually contains | `redwood-billing-support/environment/docker-compose.yaml` and `environment/Dockerfile` | Mini R0 initially, with the mini R1 overlay sealed until the transition |
| Link, window, closure, and fairness rules | `scripts/split_redwood.py` | How a record is assigned and which identifiers count as links |
| How R1 appears after R0 is scored | `redwood-billing-support/environment/advance.py` and `environment/Dockerfile` | Overlay copy, permissions, release file, what is sealed |
| Builder contract and runtime sandbox | `redwood-billing-support/environment/app/framework/agent_contract.md` | `create_agent`, tools, model gateway, what the frozen agent can read |
| Offline engineering references | `redwood-billing-support/environment/app/framework/offline-rag/README.md` | Versioned architecture, chunking, retrieval, runtime examples, and testing guidance supplied to every builder; no task answers |
| Retrieval facilities and diagnostics | `redwood-billing-support/environment/app/framework/RAG.md` | Optional methods, evidence budget, metric interpretation, and release-aware practice API; the builder chooses its architecture |
| What the current verifier scores | `redwood-billing-support/tests/verify.py` | Smoke reward, invalid-run exit, session continuity |
| Which records were planted, and for which rule | `redwood-billing-support/tests/provenance.json` | Per-record role (partial, contradicting), authority, and knowledge condition; hidden with the verifier |
## Corpus rules

Records live at `sources/<platform>/<relative path>.json` for `slack`, `gmail`, `jira`, `linear`, `confluence`, `google_drive`, `hubspot`, `fireflies`, and `github`. One file is one source record.

`R0`, `R1`, and `R2` are additive releases. `R1` and `R2` contain records new in that release, plus, in the devsupport task's corpus, dated in-place revisions of earlier records. A kept record's links resolve to kept records in the same release or an earlier one. The split checks record `kept_links_to_left_out_records: 0` and `links_to_later_versions: 0`. The slice repeats both checks at 0. An `R1` file may occupy an `R0` path only as a later-dated revision of that record (its `last_updated` is later); `advance.py` refuses any other clash, and the maintain_r1 instruction tells the builder that records were revised in place. A builder that only diffs file paths, or keeps its R0 index, misses those revisions.

A link counts only when one corpus record is named unambiguously, by ticket or issue key, GitHub PR URL, wiki-style path (including a misfiled page's original location), Confluence or Drive URL title, Fireflies meeting id, Gmail thread id, or a customer name that matches one HubSpot company. Identifiers shared by several records, and names that point outside the corpus, name nothing. Closure can pull records in from outside the developer-support pools; those counts are `kept_from_outside_the_pools`. Use the manifest's `kept` arrays as the achieved sizes. The script's per-version quotas are targets, not the final counts.

Company files exist only at the top of `R0`: `company_overview.md`, `initiatives.md`, `project_list.txt`, `employee_directory.yaml`, and `visual_employee_directory.txt`. In `environments/redwood` and redwood-mini they are the upstream files. In redwood-nano, `company_overview.md` is byte-identical to upstream and the other four were edited. Read `slice_manifest.json` for which edit. Do not describe a person, project, or policy as true for both trees unless both files agree.

`environments/redwood/R0` holds `source_tree.txt`, the upstream generator's directory tree, not in redwood-mini or redwood-nano. Mount full Redwood without it. Two other upstream catalogs were deleted from full Redwood: `uuid_index.json`, which maps every `dataset_doc_uuid` to a path and so ties records to EnterpriseRAG-Bench's answer set, and the per-platform `sources/<platform>/agents.md` generator instructions. A fresh split still places them in gitignored `redwood-left/R0` (`split_manifest.json` `left_R0_metadata`); do not copy them back.

Planted rule records must not be findable by shape alone. The first Opus 5.5 builder run (2026-10-23) found every R1 rule with one filter (Confluence pages under 6,000 characters with "2027" in the first 3,000) and a fixed list of five folders, then shipped those folders as a retrieval boost. Keep planted pages near the corpus's usual length, add look-alike pages for other jobs in the same spaces, put each rule in the space that would own it (Finance, Security, Support), give a superseding page an effective date instead of a "this replaces X" line, and name files like the rest of the corpus. Put parts of rules on other platforms only in binding forms from the rule's owner (a `final` Drive doc, the owner's Slack announcement, a recorded Fireflies decision on an internal call, a ticket the owning team closed), name each from a page the builder reads, and pair each with a same-platform decoy. Internal Fireflies meetings use `call_type` `other` and `customer_company` "Redwood Inference (internal)"; Drive docs are mostly `draft`, so a `final` one is a real authority signal.

## Generator fields

EnterpriseRAG-Bench bookkeeping: `dataset_doc_uuid`, `title_field_name`, and `content_field_names` on every JSON record, `original_location` on misfiled records, and `dataset_noise_document` on noise records.

None of the three committed trees has them. They were removed from every record (full Redwood 511,962, of which 39,434 misfiled and 199 noise; redwood-mini 10,550; redwood-nano 1,094), formatting unchanged, and every other field is unchanged. The NOTICEs, `split_manifest.json`, and `slice_manifest.json` (`generator_fields_removed`) record this.

- `scripts/split_redwood.py` `strip_generator_fields` does the removal; the split runs it after moving files, and `slice_redwood.py` runs it when copying.
- Links to misfiled records resolve through their original locations, which are kept only in `environments/redwood-mini/original_locations.json`. `load()` reads it, so a re-split or slice resolves the same links (checked on redwood-mini: 8,053 links before and after; 35 are lost without it). It lists which records are misfiled: never copy it into a release folder, image, or builder workspace.
- A builder that sees these fields can read off which records are noise or misfiled and look up each record in the public benchmark. Strip them from any new tree before mounting it.

Do not treat those fields as company knowledge. Do not copy EnterpriseRAG-Bench questions, gold answers, or cited document ids into this repo.

## Harness used by tasks

`redwood-billing-support` is the pattern later tasks should extend, not a blank runtime.

The image build context is the task directory. Docker additional context `redwood` is `environments/redwood-mini`. `R0/` is copied to `/app/R`. `R1/` is copied to `/opt/redwood-overlays/R1` and is not visible as `/app/R` until `build_r0`'s `test.sh` runs `/usr/local/libexec/redwood-advance`. That command copies the overlay in, sets permissions `root:harbor` mode `0440`/`0550`, writes `R1` to `/etc/redwood-release`, and seals `/tests` and `/logs/verifier`. A second run does nothing. `R2` is in all three trees, and no task step reveals it.

`/app/R` is readable by the builder (`harbor`) and not by the user that runs the finished agent. Other-readable entries under `/app/R` make the run invalid. At evaluation time the workspace is frozen and becomes the agent's only readable directory. `/app/R`, credentials, `/opt/redwood-baseline`, `/opt/redwood-overlays`, `/tests`, and `/logs/verifier` are outside that view. Anything the agent must know has to be written into `workspace/` during the build.

`workspace/agent.py` must export `create_agent()`. `get_agent_context()` exists only while that factory runs. It provides `action_interface`, `resources` (frozen workspace only), `model_gateway`, and `runtime_config` `{"company": "Redwood Inference", "role": "billing_support"}`. Each case is a conversation: the fixed greeting `Hi! How can I help you today?`, then a simulated developer who answers each agent reply until it ends the conversation. After each developer message the agent may call tools and then returns text. An assistant message is text or tool calls, never both; the gateway drops narration that accompanies tool calls. A tool that raises becomes a failed tool result and does not end the case.

The deployed support model is the run's `SUPPORT_MODEL` (a LiteLLM id, default `openai/gpt-6-luna`), a Docker build arg the image writes into `framework/deployment_manifest.json` and the starter agent; `environment/redwood_kit/gateway.py` serves it (and `USER_MODEL`, the developer's wording) through LiteLLM in practice and grading. The builder is Harbor `claude-code` with `claude-opus-5-5` (`run-config.claude.json`, subscription OAuth via `CLAUDE_CODE_OAUTH_TOKEN`); `run-config.oracle.json` runs the oracle. Luna is rate-limited: grade with `WORKERS=1` and never run two gradings at once (a parallel grading voided a Q run with 429s, 2026-10-23).

Workspace roles, from `framework/README.md`: `system_prompt.md` is who the agent is; `AGENTS.md` is standing instructions a developer cannot override; `policies.md` is the decision rulebook and does not change through chat; `tools.py` is `@is_tool` methods on `Tools`; `agent.py` loads those files and calls the gateway. The starter `tools.py` provides `escalate(account_id, team, summary, details=None, priority="normal")` through the billing console and paged `read_record(record_id, offset=0, limit=8000)`. At least one functional billing-support skill, subagent, and hook guardrail are required, with their responsibilities and integration chosen by the builder. An index is a design choice rather than a required artifact. Component quality requires trace review beyond file presence; the existing conversation reward has no separate artifact gate. The starter `AGENTS.md` and `policies.md` are empty on purpose.

`redwood_kit` also runs the harness features a task can exercise (`framework/agent_contract.md`, "Hooks" onward): `hooks.py` (`user_message`, `pre_tool`, `post_tool`; `Blocked` refuses a call), `skills/<name>/SKILL.md` with a `SkillTools.load_skill` tool, `subagents/<name>.md` with a `SubagentTools.delegate` tool, and Hyper-τ's optional `agent.is_stop(message)` (default: never stop; the conversation ends after that message's tool calls, as `agent_stop`). Every tool call, including a subagent's, goes through `hooks.ToolRunner`, so hooks hold whatever `agent.py` does. Embeddings are served: `redwood_kit.embeddings.embed` at build time and `model_gateway.embed` at runtime, for the models in `deployment_manifest.json` `allowed_embedding_models`. The kit does not chunk, index, or search, and it does not implement a retrieval architecture. The builder writes that into the workspace. `numpy` is installed; retrieval libraries are not. Memory, compaction, planning, and routing have no kit support; the builder writes them, and model routing needs more models in `deployment_manifest.json`. Subagent tool calls and hook firings happen inside the candidate and are not all in the top-level transcript. Model-call evidence now records complete input messages and responses, including nested model contexts and consumed follow-up reads; this is not a trace of every internal Python operation. Handoffs use `redwood_kit.billing`'s generic `describe`, `query`, `insert`, and `update` operations, attached as `self.billing` on `Tools` and `billing` on the build context. Writes go to the host's per-conversation store, so a subagent's case insert is recorded and a call a hook blocks never arrives. An escalated case is final: the team receives the case and conversation up to the handoff, later billing calls are rejected, and required facts count if they were in the case or the conversation before the handoff. A builder who hands off first and asks for details afterward may lose those details, because gpt-5.6-sol can end on a confirmed handoff even when told not to (first two Pi runs, 2026-09-25). Workspace code's stdout goes to the agent log (stdout is the host protocol; a `print()` used to end every case as `agent_error`), and hooks returning `""` or `{}` are honored; only `None` keeps the input.

`build_r0/tests/test.sh` runs the verifier and then reveals `R1` even when the verifier returns non-zero, and exits with the verifier's status unless `redwood-advance` itself fails. `maintain_r1` must keep the same Pi session file and id, and the session must grow. Step verifiers share the container (`environment_mode = "shared"`). `task.toml` uses Harbor schema 1.4 and `multi_step_reward_strategy = "mean"`.

## Verification

Grading runs in `maintain_r1`'s verifier on the two frozen workspaces: Q00 (A0, R0 cases), Q10 (A1, R0 cases on the R1 console), Q11 (A1, R1); `scripts/grade_snapshots.sh <trial>` regrades after the job. Every check is code, no judge: the billing end state replayed against gold and alternatives, console actions, `communicate`/`never_say` on normalised text (case, commas, `$`, markdown, typographic quotes and dashes), and consent order. `reward.json` is a gate over Q00/Q10/Q11; `scores.json` adds invalid cases, info types and twins, behaviour robustness, drift, regression, health and sizes (`tests/README.md`).

Methods proved on redwood-devsupport (2026-10-23) that another Redwood task should reuse:
- One conversation loop for grading and practice: `redwood_kit.conversation` (agent process, freeze, turn loop) and `redwood_kit.customer` (scripted developer, model-worded lines); a parity test runs the same turns through both and requires identical transcripts.
- Practice on the graded model without exposing the key: a `practice-model` compose service holds the provider key and serves `generate`/`say` over a socket on a shared volume, with per-release budgets; Harbor puts every service behind the same egress sidecar, so the provider host must be in the `[agent]` allowlist. The first practice conversation on a frozen builder workspace reproduced a tool bug (a 200-character summary limit) that grading had charged as 20 failures.
- The clock: cases happen on their own dates, so the agent process runs under libfaketime at the case's `today` plus `now` (`clock_env`); without it a builder that injects `date.today()` failed time-based cases (run 4's a0-03-T).
- The scripted developer answers only through `ask_developer`; its wording model never sees facts. A case is invalid, not failed, when the provider fails or the wording says a value the agent must say before the agent does (echoing the agent is fine).
- Behaviour variants (missing_info, ambiguous, conflict) copy their base's gold; a conflict claim must be checkably false and must not add a request (a "refund both lines" claim created a legitimate Finance request; a claim about the customer's own contract was defensibly routed for verification). Grep every claim against the corpus first.
- Replay one failure with the rule in the system prompt (`REDWOOD_REPLAY_HINT`) to separate "lacked the rule" from "cannot apply it".

## Existing task coverage

| Task | Condition it covers | What it does not cover |
|---|---|---|
| `redwood-billing-support` | A builder reads mini `R0` and delivers a billing support agent, then updates that same workspace after mini `R1` is added and nothing is removed. Each step is scored on hidden billing conversations. While building, the builder can use release-aware practice diagnostics, grouped into billing and general retrieval. Those questions are not the hidden billing scenarios. | Other support areas (keys, SDKs, limits). `R2`. The nano slice or full Redwood. Pre-sales work. |

There is one runnable task. A new task needs a condition this one does not already score: a real developer-support decision whose evidence is in the mounted release, checked independently of the agent's reply, or a later release (`R2`) with the same closure rules.

## Known limits

- Generator fields, `uuid_index.json`, and the per-platform `agents.md` are gone from all three trees, but full Redwood `R0` still holds `source_tree.txt`.
- redwood-nano drops material redwood-mini keeps, including records the curation note describes as dated after 2027, and it rewrites four company files. A claim about "Redwood" has to name the tree.
- `scripts/slice_redwood.py` is not a reproducible builder of the committed slice.
- Search connectors, a sidecar index, and the pre-sales file bundle are not in this repo.
- Calibration scores are single runs; judge and simulator variance across repeats is unmeasured.
- A lazy agent that copies the developer's first message into a ticket passes the facts check when the simulator volunteers IDs up front: live, it happened in a0-29 (the invoice number came with the first request), where a must-say check still failed the scenario.
- `redwood-billing-support/Task.md` (Draft, 1.1.0) is the current contract; paragraphs it marks as superseded are design history.
- Grounded retrieval does not establish source authority. Preserve source status, scope, and release when checking policy claims; a proposal can support a question about the proposal without proving current policy. Fixed chunks and document ids alone cannot certify that the complete workflow found sufficient evidence.

## Update this skill

When a task is designed, built, or audited:

1. Add a fact only when another Redwood task would need it, and only with a path, command, or run as evidence.
2. Leave that task's request, focal records, expected result, and exact checks in its `Task.md`.
3. Prefer a new sentence here. Add `references/`, `scripts/`, `assets/`, or `tests/` under this skill only when the contents are reused and this file would be the wrong place.
4. Delete a rule the task disproved, including the generator-field and slice-edit rules if the corpus changes.
5. Show the skill diff with the task audit. Do not mark an edit human-approved unless a person reviewed it.
