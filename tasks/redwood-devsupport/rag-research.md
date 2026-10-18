# Making Redwood's builder RAG setup fair

Implementation follow-up: the approved repair direction is now implemented;
see [Task.md](Task.md) for the current contract and pending live-run validation.
The findings below describe the pre-repair setup.

Research date: 2026-09-27. Repository baseline: `fb5124d162`, plus the existing
Draft audit in [Task.md](Task.md). This is a research and repair proposal, not
an implemented contract or a completed Harbor calibration.

**Recommendation: keep the workspace-only deployment model, make the hybrid
recipe optional, repair reference authority, and treat retrieval scores as
diagnostics. Validate the finished support agent on independent conversations.**

Working assumption: the intended capability is building and maintaining a good
billing-support agent, including choosing its retrieval approach. If the aim
is specifically to implement prescribed hybrid retrieval, keeping that recipe
is legitimate, but the benchmark should describe that narrower capability.

## What the research changes about the earlier audit

The concern is not that hybrid RAG is a bad setup. BM25 is a strong historical
baseline across heterogeneous retrieval tasks, and hybrid retrieval has useful
first-party experimental support. Neither establishes that a fixed fusion
constant, chunk size, or mandatory second ranker is optimal for Redwood.
[BEIR](https://arxiv.org/abs/2104.08663),
[Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval).

Likewise, a draft is not useless evidence: it can correctly answer what a
proposal says. The defect is treating its contents as established current
policy without proving that authority. Reusing an R0 reference in R1 is also
not inherently wrong if its facts remain valid or the question explicitly
asks about R0. These distinctions should drive the repair.

No reviewed source validates the current 0.7/0.8 thresholds for this corpus,
judge, and builder budget. Published experiments support local calibration;
they are not a replacement for it. ARES, for example, explicitly uses human
annotations to calibrate automated evaluation and quantify uncertainty. Its
full method is substantially larger than this project's practice check and
should not be adopted wholesale. [ARES](https://arxiv.org/html/2311.09476v2).

## Findings verified in this repository

| Finding | Evidence | Consequence |
|---|---|---|
| The builder must implement hybrid retrieval and a prescribed tuning process. | [Build instruction](steps/build_r0/instruction.md), [RAG.md](environment/app/framework/RAG.md). | The procedure constrains the design capability being measured. |
| Four of five billing probes depend on one `in_review` pricing proposal. | Source inspection and focal records recorded in [Task.md](Task.md). | 40% of the overall practice mean depends on one policy document; that is not four independent areas of coverage. |
| Half the practice questions concern other support domains. | [retrieval_probes.json](environment/app/framework/retrieval_probes.json). | They can test general retrieval, but their equal weight is not justified as a billing-support objective. |
| Scoring takes only the first 1500 characters of each nonblank chunk. | `evaluate` in [retrieval_score.py](environment/redwood_kit/retrieval_score.py); reproduced locally. | A successful full-record or neighbor read can contain evidence this diagnostic never sees. |
| Scoring eight chunks takes nine sequential judge invocations. | Pinned RAGAS 0.4.3 executed with a fake judge. | Ten probes and two rankers require 180 judge invocations before retries; both steps require 360 if both perform those sweeps. |
| The scorer client allows 300 seconds for a response; each judge request separately allows 300 seconds plus retries. | `score`, `JudgeClient`, and sequential scoring in [retrieval_score.py](environment/redwood_kit/retrieval_score.py). | A request can outlive its caller. The total deadline does not match its constituent work. |
| The combined mean can prefer a ranker that violates a stated target. | Reproduced with precision/recall 0.70/0.80 versus 1.00/0.60. | The winner-selection rule and the instruction to clear both targets disagree. |
| Retrieval practice scores do not determine reward. | [verify.py](tests/verify.py). | Keep this separation; misleading practice still wastes builder time even without directly changing reward. |
| Saved sessions inspected in the audit predate the retrieval contract. | [Task.md](Task.md), saved Pi initial instruction and session search. | Earlier support rewards do not validate the current RAG service or budget. |

The mini corpus has 3513 source JSON records in R0 and 3520 additional records
in R1, counted locally. A targeted search found no R1 record explicitly
referencing the pricing-page slug or matching the tested burst-rate string.
This narrow search does **not** prove that no policy conflict or alternative
authoritative source exists. Do a claim-level provenance review before editing
the references.

## 1. Replace the procedure with an outcome contract

Keep the real deployment constraints: the frozen workspace, supported models,
available dependencies, corpus release, permissions, and resource limits.
Remove the mandatory architecture and second-ranker exercise. Keep the hybrid
example as a starting point the builder may use.

Proposed wording for review:

> Build a support agent that uses the supplied company knowledge and remains
> correct when that knowledge changes. Choose the retrieval design. All
> runtime knowledge and indexes must be available from the frozen workspace.
> Practice retrieval scores are optional diagnostics, not pass requirements.
> Final evaluation uses held-out support conversations. Distinguish proposals,
> historical facts, and current policy; ask or escalate when the available
> evidence is insufficient. Follow the published model and resource limits.

The versioned Task Spec must settle the exact wording and budgets before
implementation. Do not silently change the experimental condition mid-run.

Leave SQLite FTS5, dense vectors, and RRF in the documentation as examples.
Research on fusion finds that RRF is parameter-sensitive and that tuned score
fusion can outperform it in studied settings. That supports testing alternatives,
not replacing one compulsory fusion method with another.
[Fusion analysis](https://arxiv.org/html/2210.11934v2).

Correct two method names while shortening `RAG.md`:

- Rescoring candidates with a larger embedding model is bi-encoder rescoring.
  It is not the same method as a cross-encoder reranker, and it cannot recover
  evidence that was excluded from its candidate pool.
- The current “CRAG” example is a conditional query retry. The published
  Corrective RAG method includes a retrieval evaluator, multiple decision
  branches, refinement, and web retrieval. Call this local option “one
  corrective retry”; do not add web access to the frozen company benchmark.
  [Corrective RAG](https://arxiv.org/html/2401.15884v3).

HyDE can stay optional. Its hypothetical document is a retrieval aid, never
source evidence. Its research results do not prove a benefit for this corpus
or justify forcing its use after a particular score.
[HyDE](https://aclanthology.org/2023.acl-long.99/).

## 2. Repair the references and broaden useful coverage

Use the existing root-only reference file. Each probe needs an independently
reviewed source locator, relevant excerpt/field, release, status, applicable
time or scope, and required claims. Accept alternative sufficient sources.
The schema is bookkeeping in the existing fixtures, not a new knowledge service.
Keep exact records and expected answers in `Task.md` and verifier fixtures.

For the draft-derived probes, either explicitly ask about the proposal and
retain its qualification, or replace the reference with verified current
policy evidence. Do not relabel a draft as published to make a test pass.
An answer's support, completeness, and source authority are distinct checks.
[Anthropic's eval guidance](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).

Keep the practice bank small initially. Replace redundant cases before adding
many more. Prioritize billing evidence spread across the following conditions:

| Condition | What the check should distinguish |
|---|---|
| Exact identifier or number | The relevant source versus semantically similar distractors. |
| Natural paraphrase | The same business need expressed without source terminology. |
| Draft versus authoritative policy | Correct qualification versus treating a proposal as settled. |
| R1 update plus unchanged R0 rule | Updating the changed rule without regressing the stable one. |
| Evidence across two records | A complete answer versus one correct but incomplete fragment. |
| Insufficient or conflicting evidence | Appropriate clarification/escalation versus invention. |

Retain non-billing probes only as a separately reported general-retrieval
diagnostic unless cross-domain support is part of the task's declared goal.

References should be keyed or checked against the release. For a stable claim,
record that it was reviewed for both releases; for a changed claim, select
the appropriate reference after the transition. Do not use the host's date as
the scenario's date. Query time and temporal variability are explicit design
dimensions in the Comprehensive RAG Benchmark.
[CRAG benchmark](https://arxiv.org/html/2406.04744v1).

Visible practice questions are development data. Final hidden conversations
must test the intended generalization. New questions over the same visible
policy are valid for within-company generalization; claims about unseen
policy families require holding out those families. Do not confuse keeping
answers secret with obtaining independent evaluation coverage.
[AI Agents That Matter](https://arxiv.org/html/2407.01502v1).

## 3. Separate evidence coverage, source authority, and final outcomes

Keep three outputs separate instead of combining everything into a new score:

| Output | Evidence | Role |
|---|---|---|
| Retrieval diagnostic | Actual retrieved passages and any follow-up reads; reviewed required claims. | Find missing or buried evidence. |
| Authority diagnostic | Source status, scope, applicable time, and qualifications in the answer. | Find stale or draft policy presented as current. |
| Support outcome | Existing conversation, policy checks, and escalated billing cases. | Continue to determine task reward. |

RAGChecker separates overall quality from retrieval and generation diagnostics.
That is the useful design lesson here; installing its framework is unnecessary.
[RAGChecker](https://arxiv.org/html/2408.08067v1).

RAGAS context precision is a ranking measure, not ordinary precision@k.
With binary relevance `[1, 0, 0, 0, 0, 0, 0, 0]`, its result rounds to **1.0**;
ordinary precision@8 is **0.125**. Both numbers describe different properties.
Our local execution reproduced this. Do not interpret the existing target as
“70% of retrieved chunks are useful.”
[Pinned precision implementation](https://github.com/vibrantlabsai/ragas/blob/v0.4.3/src/ragas/metrics/collections/context_precision/metric.py).

RAGAS recall tests attribution of reference statements to context. It does
not separately establish that the reference is current policy. Extra optional
facts in a reference can also change the denominator, so annotate only what
the question actually requires, with optional facts clearly separate.
[Recall definition](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_recall/).

Stop silently clipping passages. For the smallest compatible repair, retain
the eight-passage request bound but allow flexible lengths within a declared
aggregate character budget, returning an explicit error if exceeded. Existing
8 × 1500 characters gives a possible compatibility budget of 12,000 total
characters; it is **not** an empirically validated optimum or a token budget.
Choose the real limit using measured workload and publish it before runs.

Score the exact submitted evidence. To independently compare shipped
retrievers, have a maintainer-run adapter invoke the frozen artifact and record
its actual outputs, including follow-up reads. Source IDs help validate
provenance, but the ID alone does not prove the passage contains the answer.
Keep self-submitted chunks labeled as self-reported development feedback.

An aggregate budget reduces dependence on arbitrary chunk boundaries. Chroma's
token-based study supports measuring both evidence coverage and irrelevant
text, while acknowledging synthetic-data and scale limits. It does not supply
the right chunk size for Redwood.
[Chunking study](https://www.trychroma.com/research/evaluating-chunking).

Still test final answers: historical work shows context placement can affect
whether a model uses retrieved evidence. The magnitude for this pinned support
model remains unmeasured. [Lost in the Middle](https://arxiv.org/abs/2307.03172).

## 4. Make the score service predictable

Keep the socket service and root-only gold data. Fix these properties before
requiring any use of it:

1. **Bound the complete operation.** Use an end-to-end deadline enclosing all
   judge calls and retries, shorter than the client's deadline. Handle
   cancellation and disconnects; a dead caller must not leave the service
   indefinitely busy. Merely increasing the socket timeout does not solve
   the budget problem.
2. **Avoid compulsory expensive sweeps.** Eight chunks incur eight precision
   calls plus one recall call in pinned RAGAS. At an illustrative 20 seconds
   per call, two ten-probe sweeps consume 60 minutes—the entire R1 builder
   timeout—before indexing or implementation. This is arithmetic, **not a
   measured provider latency**. Make scoring optional first; measure it before
   deciding whether concurrency, batching, or a cheaper judge is justified.
   [Pinned recall implementation](https://github.com/vibrantlabsai/ragas/blob/v0.4.3/src/ragas/metrics/collections/context_recall/metric.py).
3. **Return distinct errors.** Invalid request, over-budget input, timeout,
   provider failure, and invalid judge output should not all look like poor
   retrieval. Reject nonfinite and out-of-range scores. Do not replace failures
   with zero or silently average them away.
4. **Record safe diagnostic evidence.** Server-owned logs should include
   release, scorer/model/prompt version, ordered-context hash, duration, call
   counts, verdicts, and failure category. Do not log API keys or expose hidden
   reference text in builder-facing errors. The builder needs status and timing;
   full judge rationales remain maintainer-only if they reveal references.
5. **Treat outages consistently.** An optional scorer outage is a diagnostic
   failure, not a zero retrieval score. If required infrastructure prevents a
   comparable timed run, mark the trial invalid and rerun it; an agent exceeding
   a functioning, published resource budget remains a legitimate task result.

Resource configuration can change both infrastructure reliability and which
solving strategies succeed. Record build and runtime budgets separately, and
track judge overhead separately from agent work.
[Infrastructure study](https://www.anthropic.com/engineering/infrastructure-noise).

Remove “one zero is noise,” the compulsory second ranker, and the combined-mean
winner rule. Leave the two practice metrics descriptive until human review
establishes useful boundaries. Do not replace them with an equally uncalibrated
weighted average or F1. Fix the probe file's stale instruction to supply an
answer: the current API accepts only question and chunks.

## 5. Validate with the smallest useful experiment

First run fixtures through the real scorer entry point with fake judge/provider
responses. They should include valid evidence, equivalent evidence, irrelevant
evidence, a fact after character 1500, an empty result, an oversized request,
malformed judge output, timeout, and service recovery. Add a draft/current
authority fixture to the semantic audit. These checks catch plumbing defects;
they do not measure real judge accuracy.

Next human-review the reference claims and expected decisions. Run a small
live audit on fixed outputs and inspect disagreements. Repeating the same
output tests judge instability; rebuilding or rerunning the support agent tests
agent instability. Keep those experiments separate. Repeatability matters for
support conversations, as illustrated by τ-bench, but a few repeats are only
a calibration smoke test. [τ-bench](https://arxiv.org/html/2406.12045v1).

For maintainer calibration, use one frozen corpus and a reviewed development
set with three simple retrieval conditions: BM25, dense, and their hybrid.
Reuse the same chunks/embeddings where possible. Match evidence budgets and
record build time, calls, tokens, index size, and runtime latency. Add one
refinement only when a diagnosed failure justifies it. **The maintainer runs
this experiment; each evaluated builder is not required to build three systems.**

Finally run the actual builder through both Harbor steps with the revised
contract. Inspect the complete trace and actual workspace artifact. Check
source authority, R1 updates, stable-rule regressions, and infrastructure
status, not just the aggregate reward. One run establishes plumbing evidence,
not superiority. Any comparison of recipe versus free-design builders needs
independent repeated runs with identical declared budgets and uncertainty
reported at the policy/situation level; four questions about one document
must not masquerade as four independent policy samples.

No live run is authorized or priced by this report. The model, judge, trial
count, timeout, and cost ceiling must be settled in the run plan. The current
configured models can remain the starting candidates; changing them would
create another experimental difference.

## Concrete patch order

| Order | Existing files | Change and acceptance evidence |
|---|---|---|
| 1 | `Task.md`, both `steps/*/instruction.md`, `environment/app/framework/RAG.md` | Agree on the capability; make recipe and practice optional; remove unsupported score rules. All instructions describe the same condition. |
| 2 | `environment/app/framework/retrieval_probes.json`, `environment/probe_key/references.json` | Repair authority, required claims, scope, release mapping, and redundant coverage. Every reference has visible supporting evidence and valid alternatives are accepted. |
| 3 | `environment/redwood_kit/retrieval_score.py`, `tests/redwood_devsupport/test_retrieval_score.py` | Explicit input budget, complete deadline, distinct errors, safe evidence logs, and fake-provider tests. No false zeros on infrastructure failures. |
| 4 | Existing task fixtures and run configuration | Calibrate retrieval and graders, then run the actual builder. Preserve independent held-out conversations and inspect the full evidence. |

If release-specific references are needed, update `environment/advance.py`
or have the service select them from `/etc/redwood-release` on each request;
the current service loads references only at startup. For retrieval evaluation
adapters, first reuse top-level tool outputs already captured in `run_agent.py`.
Nested tools run inside the candidate and are not fully represented there, so
do not claim complete evidence capture without checking that path.

Keep this research out of the image. The current Dockerfile copies selected
`environment/` paths, not this report or `Task.md`.

## Project knowledge proposal, not yet accepted

The existing World Skill already captures workspace-only access, mini versus
nano, model gateways, and practice scores outside reward. Preserve those facts.
Add only the reusable lessons after review: retrieval diagnostics need a
declared evidence budget; reference authority and release applicability require
independent checks; historic runs validate their recorded contract; grader
failures and agent failures require separate accounting. Fix its image-routing
row to point to the Dockerfile/Compose mini context rather than the nano
manifest. Exact probe truth remains in the collocated Task Spec and fixtures.

## Evidence, reproducibility, and limits

Research combined primary papers, first-party studies, official metric docs,
and the pinned RAGAS source. Two parallel research passes covered retrieval
methods and evaluation validity; local inspection checked the actual scorer,
instructions, corpus, release transition, and transcript capture. Exa/Firecrawl
were unavailable, so web search and direct source reads were used. Older
retrieval experiments establish possible tradeoffs, not rankings of current
models. No study directly validates this Redwood configuration.

The existing six scorer tests pass with a placeholder credential and no live
API calls:

```sh
OPENAI_API_KEY=sk-test-placeholder uv run --python 3.12 --no-project \
  --with 'openai>=1.100,<3' --with 'pydantic>=2.9,<3' \
  --with 'ragas==0.4.3' --with 'langchain-community==0.3.31' --with pytest \
  pytest -q tests/redwood_devsupport/test_retrieval_score.py
```

Additional local characterization executed pinned precision/recall methods
with a fake judge (constructor validation bypassed, metric methods unchanged):

```text
8 retrieved chunks: 8 ContextPrecisionOutput + 1 ContextRecallOutput calls
RAGAS precision([1, 0, 0, 0, 0, 0, 0, 0]) -> 1.0 after rounding
ordinary precision for that list          -> 0.125
RAGAS precision([0, 1])                   -> 0.5 after rounding
evaluate(chunk='x' * 1500 + evidence)     -> evidence discarded
mean(0.70, 0.80) = 0.75; mean(1.00, 0.60) = 0.80
```

These checks establish call count, arithmetic, and clipping, not live LLM
judgment, Docker isolation, latency distributions, or end-to-end fairness.
No paid model requests or new Harbor runs were performed, and no runtime
implementation was changed.
