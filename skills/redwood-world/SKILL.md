---
name: redwood-world
description: Reusable Redwood Inference world knowledge for observe-bench. Use with eval-engineering when designing or building Harbor tasks over the Redwood developer-support corpus, its R0–R2 releases, the curated slice, or the redwood-devsupport agent contract.
---

# Redwood World Knowledge

Read `$eval-engineering` first. Use its broad references as guidance. Use this skill for how that guidance applies to Redwood Inference in this repository.

Written from this checkout only: `environments/redwood`, `environments/redwood-mini`, `environments/redwood-nano`, and `tasks/redwood-devsupport`. Not from another worktree. Drafted on 2026-09-25. It has not been reconciled with a scored task audit.

## Start here

Redwood Inference is a fictional ~150-person company that sells LLM inference: a hosted API, dedicated capacity, and private (VPC/on-prem) deployments. The corpus is EnterpriseRAG-Bench `generated_data` at commit `d36685e273713975ee20299bbf1ab64165575b3c`, MIT, converted by moving or copying files and removing the generator fields described below. Every other field is unchanged.

Three committed trees, largest first. Do not treat them as the same knowledge:

| Tree | Path | What a task may assume |
|---|---|---|
| Full Redwood | `environments/redwood/R0`, `R1`, `R2` | 511,968 of the 511,978 upstream files (all but `uuid_index.json` and the nine `sources/<platform>/agents.md`), each in the release the split assigns it: redwood-mini's records plus every record the split left out. Byte-compared against the pinned upstream tarball on 2026-09-25, before the generator fields were removed. R0 also holds the upstream catalogs described below. |
| Developer-support split (mini) | `environments/redwood-mini/R0`, `R1`, `R2` | What `tasks/redwood-devsupport` builds into the image. Kept records for each release. R0 also has the five upstream company files. Counts and rules are in `environments/redwood-mini/split_manifest.json`. |
| Curated slice (nano) | `environments/redwood-nano/` | Hand-edited after `scripts/slice_redwood.py`, and generator fields removed. Rerunning that script does not reproduce it. Not mounted by the current task. |

- Read `scripts/split_redwood.py` (module docstring) and `environments/redwood-mini/split_manifest.json` (`rules`, `kept`, `checks`) before changing membership, versions, or links. Run both scripts with Python 3.12 or later (`uv run --python 3.12 --no-project python ...`); the system `python3` here is 3.9 and fails on import.
- Read `environments/redwood-nano/slice_manifest.json` before mounting or subsetting the slice. `company_files_in_R0` and `curation` say what the hand edit changed.
- Read `tasks/redwood-devsupport/environment/app/framework/agent_contract.md` and `framework/README.md` before changing the evaluated agent.
- `docs/redwood-presales-R.md` is a local, gitignored draft of a different pre-sales bundle. It is not built, and it disagrees with the committed corpus on generator-field stripping. Do not use it as the world.

Rebuild commands, when a human asks to regenerate data:

- `scripts/import_enterprise_rag.sh` downloads the pinned `generated_data` into gitignored `environments/redwood-mini/.upstream/`.
- `python scripts/split_redwood.py` moves files into `environments/redwood-mini` and gitignored `redwood-left/`, one version folder each, then removes the generator fields and updates `original_locations.json`. Their per-release union is `environments/redwood`.
- `python scripts/slice_redwood.py` refuses to run if `environments/redwood-nano/` exists, and would not match the committed slice anyway.

## Knowledge routing

| Current need | Read | What it provides |
|---|---|---|
| Who is in which release, and why | `environments/redwood-mini/split_manifest.json` | Pin, seed `redwood-split-v1`, pools, quotas, cutoffs, achieved `kept` counts, closure checks |
| What the image actually contains | `tasks/redwood-devsupport/environment/docker-compose.yaml` and `environment/Dockerfile` | Mini R0 initially, with the mini R1 overlay sealed until the transition |
| Link, window, closure, and fairness rules | `scripts/split_redwood.py` | How a record is assigned and which identifiers count as links |
| How R1 appears after R0 is scored | `tasks/redwood-devsupport/environment/advance.py` and `environment/Dockerfile` | Overlay copy, permissions, release file, what is sealed |
| Builder contract and runtime sandbox | `tasks/redwood-devsupport/environment/app/framework/agent_contract.md` | `create_agent`, tools, model gateway, what the frozen agent can read |
| Offline engineering references | `tasks/redwood-devsupport/environment/app/framework/offline-rag/README.md` | Versioned architecture, chunking, retrieval, runtime examples, and testing guidance supplied to every builder; no task answers |
| Retrieval facilities and diagnostics | `tasks/redwood-devsupport/environment/app/framework/RAG.md` | Optional methods, evidence budget, metric interpretation, and release-aware practice API; the builder chooses its architecture |
| What the current verifier scores | `tasks/redwood-devsupport/tests/verify.py` | Smoke reward, invalid-run exit, session continuity |
| Which records were planted, and for which rule | `tasks/redwood-devsupport/tests/provenance.json` | Per-record role (partial, contradicting), authority, and knowledge condition; hidden with the verifier |
## Corpus rules

Records live at `sources/<platform>/<relative path>.json` for `slack`, `gmail`, `jira`, `linear`, `confluence`, `google_drive`, `hubspot`, `fireflies`, and `github`. One file is one source record.

`R0`, `R1`, and `R2` are additive releases. `R1` and `R2` contain only records new in that release. A kept record's links resolve to kept records in the same release or an earlier one. The split checks record `kept_links_to_left_out_records: 0` and `links_to_later_versions: 0`. The slice repeats both checks at 0. An `R1` file must not occupy an `R0` path; `advance.py` refuses that clash.

A link counts only when one corpus record is named unambiguously, by ticket or issue key, GitHub PR URL, wiki-style path (including a misfiled page's original location), Confluence or Drive URL title, Fireflies meeting id, Gmail thread id, or a customer name that matches one HubSpot company. Identifiers shared by several records, and names that point outside the corpus, name nothing. Closure can pull records in from outside the developer-support pools; those counts are `kept_from_outside_the_pools`. Use the manifest's `kept` arrays as the achieved sizes. The script's per-version quotas are targets, not the final counts.

Company files exist only at the top of `R0`: `company_overview.md`, `initiatives.md`, `project_list.txt`, `employee_directory.yaml`, and `visual_employee_directory.txt`. In `environments/redwood` and redwood-mini they are the upstream files. In redwood-nano, `company_overview.md` is byte-identical to upstream and the other four were edited. Read `slice_manifest.json` for which edit. Do not describe a person, project, or policy as true for both trees unless both files agree.

`environments/redwood/R0` holds `source_tree.txt`, the upstream generator's directory tree, not in redwood-mini or redwood-nano. Mount full Redwood without it. Two other upstream catalogs were deleted from full Redwood: `uuid_index.json`, which maps every `dataset_doc_uuid` to a path and so ties records to EnterpriseRAG-Bench's answer set, and the per-platform `sources/<platform>/agents.md` generator instructions. A fresh split still places them in gitignored `redwood-left/R0` (`split_manifest.json` `left_R0_metadata`); do not copy them back.

## Generator fields

EnterpriseRAG-Bench bookkeeping: `dataset_doc_uuid`, `title_field_name`, and `content_field_names` on every JSON record, `original_location` on misfiled records, and `dataset_noise_document` on noise records.

None of the three committed trees has them. They were removed from every record (full Redwood 511,962, of which 39,434 misfiled and 199 noise; redwood-mini 10,550; redwood-nano 1,094), formatting unchanged, and every other field is unchanged. The NOTICEs, `split_manifest.json`, and `slice_manifest.json` (`generator_fields_removed`) record this.

- `scripts/split_redwood.py` `strip_generator_fields` does the removal; the split runs it after moving files, and `slice_redwood.py` runs it when copying.
- Links to misfiled records resolve through their original locations, which are kept only in `environments/redwood-mini/original_locations.json`. `load()` reads it, so a re-split or slice resolves the same links (checked on redwood-mini: 8,053 links before and after; 35 are lost without it). It lists which records are misfiled: never copy it into a release folder, image, or builder workspace.
- A builder that sees these fields can read off which records are noise or misfiled and look up each record in the public benchmark. Strip them from any new tree before mounting it.

Do not treat those fields as company knowledge. Do not copy EnterpriseRAG-Bench questions, gold answers, or cited document ids into this repo.

## Harness used by tasks

`tasks/redwood-devsupport` is the pattern later tasks should extend, not a blank runtime.

The image build context is the task directory. Docker additional context `redwood` is `environments/redwood-mini`. `R0/` is copied to `/app/R`. `R1/` is copied to `/opt/redwood-overlays/R1` and is not visible as `/app/R` until `build_r0`'s `test.sh` runs `/usr/local/libexec/redwood-advance`. That command copies the overlay in, sets permissions `root:harbor` mode `0440`/`0550`, writes `R1` to `/etc/redwood-release`, and seals `/tests` and `/logs/verifier`. A second run does nothing. `R2` is in all three trees, and no task step reveals it.

`/app/R` is readable by the builder (`harbor`) and not by the user that runs the finished agent. Other-readable entries under `/app/R` make the run invalid. At evaluation time the workspace is frozen and becomes the agent's only readable directory. `/app/R`, credentials, `/opt/redwood-baseline`, `/opt/redwood-overlays`, `/tests`, and `/logs/verifier` are outside that view. Anything the agent must know has to be written into `workspace/` during the build.

`workspace/agent.py` must export `create_agent()`. `get_agent_context()` exists only while that factory runs. It provides `action_interface`, `resources` (frozen workspace only), `model_gateway`, and `runtime_config` `{"company": "Redwood Inference", "role": "billing_support"}`. Each case is a conversation: the fixed greeting `Hi! How can I help you today?`, then a simulated developer who answers each agent reply until it ends the conversation. After each developer message the agent may call tools and then returns text. An assistant message is text or tool calls, never both; the gateway drops narration that accompanies tool calls. A tool that raises becomes a failed tool result and does not end the case.

`framework/deployment_manifest.json` pins the runtime model `gpt-6-sol` to `reasoning_effort` `high`; the gateway applies it and rejects any other value, including a subagent's frontmatter override. That is the model inside the built agent; `MODELS` in `tests/run_agent.py` mirrors it. The checked-in builder config `tasks/redwood-devsupport/run-config.json` is a different loop: Harbor agent `pi`, model `openai/gpt-6-sol`, `resume_trajectory: true`. `run-config.oracle.json` uses Harbor agent `oracle` and does not resume.

Workspace roles, from `framework/README.md`: `system_prompt.md` is who the agent is; `AGENTS.md` is standing instructions a developer cannot override; `policies.md` is the decision rulebook and does not change through chat; `tools.py` is `@is_tool` methods on `Tools`; `agent.py` loads those files and calls the gateway. The starter `tools.py` provides `escalate(account_id, team, summary, details=None, priority="normal")` through the billing console and paged `read_record(record_id, offset=0, limit=8000)`. At least one functional billing-support skill, subagent, and hook guardrail are required, with their responsibilities and integration chosen by the builder. An index is a design choice rather than a required artifact. Component quality requires trace review beyond file presence; the existing conversation reward has no separate artifact gate. The starter `AGENTS.md` and `policies.md` are empty on purpose.

`redwood_kit` also runs the harness features a task can exercise (`framework/agent_contract.md`, "Hooks" onward): `hooks.py` (`user_message`, `pre_tool`, `post_tool`; `Blocked` refuses a call), `skills/<name>/SKILL.md` with a `SkillTools.load_skill` tool, `subagents/<name>.md` with a `SubagentTools.delegate` tool, and Hyper-τ's optional `agent.is_stop(message)` (default: never stop; the conversation ends after that message's tool calls, as `agent_stop`). Every tool call, including a subagent's, goes through `hooks.ToolRunner`, so hooks hold whatever `agent.py` does. Embeddings are served: `redwood_kit.embeddings.embed` at build time and `model_gateway.embed` at runtime, for the models in `deployment_manifest.json` `allowed_embedding_models`. The kit does not chunk, index, or search, and it does not implement a retrieval architecture. The builder writes that into the workspace. `numpy` is installed; retrieval libraries are not. Memory, compaction, planning, and routing have no kit support; the builder writes them, and model routing needs more models in `deployment_manifest.json`. Subagent tool calls and hook firings happen inside the candidate and are not all in the top-level transcript. Model-call evidence now records complete input messages and responses, including nested model contexts and consumed follow-up reads; this is not a trace of every internal Python operation. Handoffs use `redwood_kit.billing`'s generic `describe`, `query`, `insert`, and `update` operations, attached as `self.billing` on `Tools` and `billing` on the build context. Writes go to the host's per-conversation store, so a subagent's case insert is recorded and a call a hook blocks never arrives. An escalated case is final: the team receives the case and conversation up to the handoff, later billing calls are rejected, and required facts count if they were in the case or the conversation before the handoff. A builder who hands off first and asks for details afterward may lose those details, because gpt-5.6-sol can end on a confirmed handoff even when told not to (first two Pi runs, 2026-09-25). Workspace code's stdout goes to the agent log (stdout is the host protocol; a `print()` used to end every case as `agent_error`), and hooks returning `""` or `{}` are honored; only `None` keeps the input.

`build_r0/tests/test.sh` runs the verifier and then reveals `R1` even when the verifier returns non-zero, and exits with the verifier's status unless `redwood-advance` itself fails. `maintain_r1` must keep the same Pi session file and id, and the session must grow. Step verifiers share the container (`environment_mode = "shared"`). `task.toml` uses Harbor schema 1.4 and `multi_step_reward_strategy = "mean"`.

## Verification

`tests/verify.py` gates the step, freezes the workspace, and scores it with `tests/grade.py`. Each hidden scenario is one simulated conversation; its `ticket` (required, allowed, or none; accepted `teams`; `facts` the escalated case must hold) is decided in code from escalated cases in the shared billing store, and each check is a yes/no `judge` question about what the agent did, with an `expect`ed answer and optional `"scored": false`. Accepted teams come from R's escalation records (the answer key's 'Who handles it' table); R0 had no owner for invoice disputes or metering questions until three records were planted (a published routing page, the #support thread it defers to for credits and contracts, and a retired draft RACI as the trap), so check R names the owner before narrowing a scenario's teams; every escalation must go to one, which stops spraying tickets. The judge (`gpt-5.6-sol`, reasoning `high`, strict JSON schema) reads only the rendered transcript and the questions, never the answer key; every yes must quote a span that code finds in the transcript, or the judge is asked again. A scenario passes only when all its scored checks pass; `max_steps` and `agent_error` fail unjudged; `agent_stop` and `customer_out_of_scope` are graded like any other ending. The reward averages a scenario with its `realization_of` rewordings first, then across situations. During the build, `redwood_kit.retrieval_score.score` optionally returns context precision, context recall, release, group, and elapsed seconds. Practice applicability follows `/etc/redwood-release`, read on every call. References and source provenance stay in root-only `/opt/redwood-probes/references.json`. Those scores are advisory, not the reward; no threshold or mandatory experiment is imposed. See `RAG.md` for request limits. Over-budget evidence is rejected rather than clipped, and provider/timeout/judge failures raise classified errors rather than zero scores. The scorer logs only safe metadata and evidence hashes to root-only `/logs/verifier/retrieval-score.jsonl`. Simulator and judge failures are retried and then make the run invalid (exit `2`), as do a wrong release, `/app/R` exposed to the agent user, a missing or restarted Pi session, or the verifier not running as root. `evidence.json` carries pass counts by category, rule, and purpose; `cases/<id>.json` holds each transcript and grade.

Conversations run 8 at a time (32 in sequence would exceed the verifier timeout, now 3600 s). Two sandbox facts came from a leaky test agent run inside the task image: `preexec_fn` is unsafe with those threads, so the child sets its own hard limits before importing workspace code; and processes started with `start_new_session` outlive `Candidate.close` while files the agent writes to `/tmp` persist into the builder's next session. Each case therefore gets a private `HOME`/`TMPDIR` that is deleted afterwards, and `run_agent.scrub()` kills every `nobody` process and deletes every `nobody`-owned file after each step. In that check, 8 leaked files and 8 surviving processes were all removed.

`python tasks/redwood-devsupport/tests/grade.py <step> <dir> [<dir> ...]` layers workspace directories and grades them locally (needs `OPENAI_API_KEY`). The oracle workspaces are `steps/<step>/solution/workspace/`; Harbor uploads a step's `solution/` to `/solution` and `solve.sh` copies it over `/app/workspace`. Calibration agents and the commands to run them are in `tests/redwood_devsupport/fixtures/agents/README.md`.

The customer simulator follows Hyper-τ's: `Customer` in `tests/run_agent.py`, pinned to `gpt-5.6-sol` at the API's default effort (medium), prompted with `tests/customer_guidelines.md` plus the case's `scenario` (`today`, `persona`, `reason_for_contact`, `known_info`, `unknown_info`, `task_instructions`), ending with `###STOP###` (an answer it can act on), `###TRANSFER###` (the agent handed it to an engineer or team, after the simulator has made any asks left in its instructions), or `###OUT-OF-SCOPE###`. The agent is never told the date, so a scenario that depends on elapsed time must state it. Use `today` just after the release's latest record (R0 `2026-09-15`, R1 `2027-04-08`). Put every fact the agent might ask about into `known_info` or `unknown_info`: the first live R0 run invented "raw HTTP" until the scenario said how the services call the API. `tests/redwood_devsupport/test_run_agent.py` checks the loop with scripted turns: `uv run --python 3.12 --no-project --with openai --with pydantic --with pytest --with ragas==0.4.3 --with langchain-community==0.3.31 pytest -q tests/redwood_devsupport`. Harbor 0.22.0's `user_agent` simulates the user of the Harbor agent (Pi) over ACP, which Pi does not support; it cannot play Redwood's customers.

Do not copy scenario files, the answer key, oracle policies, or `Task.md` into `/app`, the image, or this skill. Hidden checks belong in the task's `tests/` tree, which the Dockerfile keeps unreadable by `harbor`; the answer key lives in gitignored `docs/`.

Harbor evidence (gitignored `jobs/`): `calib-oracle-2053` ran the oracle through both steps with the grader in about 4 minutes (8 conversations at a time): `solve.sh` installed the workspace, R1 was revealed, the Pi session continued, and the rewards were 0.97 (A0) and 1.0 (A1). Run it with `harbor run -y -c tasks/redwood-devsupport/run-config.oracle.json -o "$PWD/jobs"` and `OPENAI_API_KEY` in the environment; without `-y` Harbor stops at an interactive prompt about loading host environment variables. Job output is not a source of company facts.

Calibration on 2026-09-25 (single runs, agent gpt-6-sol): refuse-all, stop-early, and a judge-injection agent scored 0; escalate-only scored 0.10, only where routing a credit request without a promise is correct; the A0 oracle graded on A1 (stale A1) passed only regression scenarios. The starting workspace, a one-line prompt with no Redwood knowledge, scored 0.64 on A0: gpt-6-sol's own caution passes most don't-promise, ask-first, and escalate scenarios, so A0's knowledge signal sits in the scenarios that need Redwood facts (model tokenizer, self-serve has no SLA, the 30-minute threshold, the intake list). The simulator disclosed IDs only when asked in 4 of 5 required-handoff scenarios. Judge lessons: a check that names a team ('billing or finance team') rejected a correct 'reviewed under your contract'; a must-say check accepted a hedge ('could explain') until it asked for the rule 'rather than a guess'; and a stale claim passed until a must-not check named it ('cached tokens are not billed').

## Existing task coverage

| Task | Condition it covers | What it does not cover |
|---|---|---|
| `tasks/redwood-devsupport` | A builder reads mini `R0` and delivers a billing support agent, then updates that same workspace after mini `R1` is added and nothing is removed. Each step is scored on hidden billing conversations. While building, the builder can use release-aware practice diagnostics, grouped into billing and general retrieval. Those questions are not the hidden billing scenarios. | Other support areas (keys, SDKs, limits). `R2`. The nano slice or full Redwood. Pre-sales work. |

There is one runnable task. A new task needs a condition this one does not already score: a real developer-support decision whose evidence is in the mounted release, checked independently of the agent's reply, or a later release (`R2`) with the same closure rules.

## Known limits

- Generator fields, `uuid_index.json`, and the per-platform `agents.md` are gone from all three trees, but full Redwood `R0` still holds `source_tree.txt`.
- redwood-nano drops material redwood-mini keeps, including records the curation note describes as dated after 2027, and it rewrites four company files. A claim about "Redwood" has to name the tree.
- `scripts/slice_redwood.py` is not a reproducible builder of the committed slice.
- Search connectors, a sidecar index, and the pre-sales file bundle are not in this repo.
- Calibration scores are single runs; judge and simulator variance across repeats is unmeasured.
- A lazy agent that copies the developer's first message into a ticket passes the facts check when the simulator volunteers IDs up front: live, it happened in a0-29 (the invoice number came with the first request), where a must-say check still failed the scenario.
- `tasks/redwood-devsupport/Task.md` is a Draft of the outcome-based builder contract (v0.3.0, with offline reference pack 1.0.0). It records the current practice truth and validation limits. Prior saved model runs predate that contract and cannot calibrate it. The hidden conversation fixtures and their existing reward rules remain unchanged.
- Grounded retrieval does not establish source authority. Preserve source status, scope, and release when checking policy claims; a proposal can support a question about the proposal without proving current policy. Fixed chunks and document ids alone cannot certify that the complete workflow found sufficient evidence.

## Update this skill

When a task is designed, built, or audited:

1. Add a fact only when another Redwood task would need it, and only with a path, command, or run as evidence.
2. Leave that task's request, focal records, expected result, and exact checks in its `Task.md`.
3. Prefer a new sentence here. Add `references/`, `scripts/`, `assets/`, or `tests/` under this skill only when the contents are reused and this file would be the wrong place.
4. Delete a rule the task disproved, including the generator-field and slice-edit rules if the corpus changes.
5. Show the skill diff with the task audit. Do not mark an edit human-approved unless a person reviewed it.
