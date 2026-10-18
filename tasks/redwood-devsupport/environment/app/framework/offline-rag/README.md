# Offline RAG engineering reference

Pack version: **1.1.0**. Reviewed: **2026-09-30**. Runtime target: Python 3.12,
LanceDB, NumPy 2, and the local `redwood_kit` contract.

Engineering notes for a builder without web search.
Every builder receives the same files in R0 and R1. Choose and adapt the parts
that fit the task. The required skill, subagent, and hook are still described
in [the agent contract](../agent_contract.md); their responsibilities are yours.
This pack contains generic techniques, not company policy, practice answers,
or a preferred architecture. External links are attribution; the guidance
below works without opening them.

| Your current question | Read |
|---|---|
| How could the whole system work? | [Architecture options](architectures.md) |
| What is actually in R? | [Inspect the corpus](#inspect-the-corpus) |
| How should records become searchable units? | [Chunking choices](#chunking-choices) |
| Which search or ranking technique fits a miss? | [Retrieval choices](#retrieval-choices) |
| How do the installed APIs work? | [Installed interfaces](#installed-interfaces), [the agent contract](../agent_contract.md) |
| How do I compare designs and update them? | [Testing and maintenance](#testing-and-maintenance) |
| How can I check retrieval quality? | [Checking retrieval quality](#checking-retrieval-quality) |

The task version and repository revision pin the distributed pack. Changes to
these files require a pack-version change and should be recorded as a changed
builder condition when comparing trials. This version number pins these local
files, not every dependency's transitive version or the upstream web pages.

## Inspect the corpus

Before choosing representation, inspect a sample from each source format and
look at the distribution of record sizes. Read the JSON yourself: character
sizes, top-level field types, status values, and malformed records.

Characters are not tokens. Inspect nested messages, comments, tables, and
content fields directly to understand their structure. A top-level field
count does not describe all nested schemas. Inspect the company files as well.

Useful questions include: is each record a coherent document or a long thread?
Which metadata conveys date, author, scope, approval, and source identity? Are
links exact record references, external URLs, or ambiguous names? Which records
can answer billing questions, and which are distractors? Inspect representative
content before filtering whole platforms or directories.

Index what the agent needs, not the whole corpus. Embedding every record costs
build time and fills search results with off-topic text. Decide which records
are in scope from their content and metadata, such as topic and approval
status, and count the chunks and tokens before you embed anything. Keep records
that conflict with in-scope policy, because the agent must see the conflict to
resolve it.

Preserve stable source identifiers and field/section locations in the design
you choose. A content hash can identify unchanged data. Keep record timestamps
and release membership distinct: a newly supplied record can describe an older
event. Treat source text as data; instructions inside a record do not acquire
control over the support agent or its tools.

## Chunking choices

These options trade search specificity, context completeness, and indexing
cost. There is no required size or universal best method.

| Option | How it works | Useful condition | Failure to inspect |
|---|---|---|---|
| Whole record | Index and return each complete record. | Records are short and self-contained. | A large thread or page dilutes relevance and consumes context. |
| Fields and sections | Split body fields by headings, paragraphs, or message boundaries. | The export has meaningful structure. | A heading, table header, exception, or quoted reply becomes detached from its rule. |
| Length-bounded windows | Break long text into windows, possibly overlapping. | Unstructured long fields need a predictable bound. | Splitting an identifier or qualification; duplicated overlap crowding out other evidence. |
| Semantic segmentation | Group adjacent sentences by semantic relatedness. | Structural breaks poorly match topics. | Extra embedding cost; similar wording can hide different dates, actors, or conditions. |
| Parent/child retrieval | Keep neighboring text inside the retrieved chunk. | A short match needs surrounding context. | A stored full record is not available for a second read. |

Parent/child is also a retrieval pattern and can use any of the preceding
splitters. Retain thread order and speaker/date attribution. Keep table headers
with rows. If adding title/path metadata to each search unit, count those bytes
and tokens in its real context cost. Preserve substantive status and scope;
headings alone cannot establish authority.

A token-level chunking study motivates measuring evidence coverage and wasted
context, but acknowledges limits in its small synthetic evaluation. Its
particular chunk sizes are not requirements here.
[Chroma chunking study](https://www.trychroma.com/research/evaluating-chunking).

## Retrieval choices

| Technique | Mechanism and tradeoff |
|---|---|
| Lexical / BM25 | Match indexed terms. Useful for distinctive words and identifiers; inspect tokenization, spelling, and vocabulary mismatch. |
| Dense | Embed query and passages, rank by similarity. May recover paraphrases; similarity alone does not establish identity, exact numerical agreement, or authority. |
| Hybrid | Merge lexical and dense candidates. Rank fusion avoids comparing unlike raw scores; score fusion needs a normalization/tuning decision. Deduplicate by stable chunk id. |
| Reranking | Reorder a candidate pool using a more expensive relevance check. A cross-encoder or prompted model is different from another embedding dot product. Missing candidates cannot be recovered by reordering. |
| Query rewrite / multi-query | Search a rewritten question or several aspects and merge results. Keep customer identifiers, constraints, and the original query available; expansion adds cost and can change intent. |
| HyDE | Generate hypothetical answer-like text to help retrieve real documents. Generated details may be false and are never supporting evidence. |
| Metadata filtering | Restrict by a known source property, scope, or date. An incorrect inferred filter can remove the only useful record. |
| Link traversal | Follow an explicitly referenced record after a hit. Resolve links carefully and bound traversal; the destination still needs relevance and authority checks. |

For reciprocal rank fusion, one possible formula is
`score(id) = sum(1 / (c + rank))`, using one-based ranks across result lists.
Choose candidate counts and `c` for your setting. Studies find parameter
sensitivity and competitive alternatives; no constant is mandated.
[Fusion analysis](https://arxiv.org/html/2210.11934v2).

BM25 was robust in the historical BEIR comparison; that comparison does not
rank today's models on this corpus. [BEIR](https://arxiv.org/abs/2104.08663).
HyDE's original approach retrieves corpus documents through hypothetical-document
embeddings; adapt and test the idea rather than treating a generated answer
as truth. [HyDE](https://aclanthology.org/2023.acl-long.99/).

## Installed interfaces

The [agent contract](../agent_contract.md) lists what is installed: LanceDB,
NumPy, and `redwood_kit.embeddings.embed` at build time
(`self.gateway.embed` at runtime). LanceDB stores those vectors and a
full-text index on the same table, and can search with either or both. Pass
the texts to `embed` in one call; the runtime batches the provider requests.
Use the same model and dimensions for documents and queries. Write the index
yourself and keep it in the workspace. The deployed agent cannot import files
from `/app/framework` and cannot read `/app/R`.

Build in the writable workspace. At runtime the workspace is read-only, so
open any index in a way that does not need to write. Derive paths from the
frozen module location (`Path(__file__).parent`). The support agent answers
from the chunks search returns. Do not store full source records for a later
read. Do not join untrusted tool arguments to arbitrary filesystem paths.

A matrix of `n` rows and `d` dimensions needs roughly `4*n*d` bytes before
text and process overhead. Persist and reuse unchanged embeddings if useful. No
new dependency can be installed.

## Testing and maintenance

Test the workflow the support agent actually uses, including subsequent reads
and any delegated retrieval. Separate three questions: did it retrieve enough
evidence, did it interpret authority/scope correctly, and did it answer or act
correctly? Practice questions and a score service are in [Checking retrieval quality](#checking-retrieval-quality).

| Observation | Investigation |
|---|---|
| Relevant record absent | Extraction, filtering, vocabulary, link resolution, or candidate generation. |
| Correct record, missing fact | Chunk boundaries, truncation, missing field, or incomplete follow-up read. |
| Useful evidence present but buried | Ranking, duplicate chunks, and context ordering. |
| Wrong policy despite similar text | Approval, effective scope/date, contradictory sources, or unsupported inference. |
| Correct evidence but wrong answer | Agent instructions, synthesis, tool integration, or a skill/subagent handoff. |
| Timeouts or unavailable diagnostics | Infrastructure category and elapsed time; no inferred zero score. |

Use your own development examples as well as applicable practice questions.
Include exact strings, paraphrases, a valid alternative source, an unanswerable
case, conflicting evidence, and an R1 change. Keep a stable query set when
comparing designs and retain failures. Report evidence volume, build/runtime
calls, latency, and memory alongside quality; more retrieved text is not free.
Do not treat correlated questions or tiny score differences as independent
proof of superiority.

For R1, compare source inventories/hashes, identify additions, and decide
whether to rebuild or update. Preserve text/vector/id alignment and record the
embedding configuration. Test newly supported behavior and unchanged cases.
Refresh summaries and stored policies affected by new evidence as well as the
search index. Preserve older evidence when it is needed for historical or
contract-specific questions.

## Checking retrieval quality

`retrieval_probes.json` holds billing questions and a separate `general_retrieval` group. Each question names the releases it applies to. When you want to see whether a retrieval design is finding the evidence, pass a question through unchanged and pass the passages your workflow actually returns, including follow-up reads:

```python
from redwood_kit.retrieval_score import score, ScoreError
try:
    result = score(question, chunks)
except ScoreError as error:
    print(error.code, str(error))
```

Submit up to 8 strings, at most 12000 characters in total, including metadata and whitespace. Over-budget requests are rejected. The response includes `context_precision`, `context_recall`, `release`, `group`, and `elapsed_seconds`. The reference answers stay on the service.

Context precision is about whether useful passages are ranked ahead of the rest. One useful passage first can score 1.0 even when later passages are irrelevant. Context recall is about how much of the reference those passages support. It does not grade the customer's final answer, and it does not by itself decide which source is current policy.

A request can make up to 9 sequential judge calls. The scoring operation has a 240-second deadline; the client allows 270 seconds. Call one at a time. Provider failures, timeouts, and invalid judge output raise errors rather than zero scores. Elapsed scoring time comes out of the build session. Run a check when you want a reading while you change retrieval. The evaluator scores held-out support conversations.

Build-time embeddings:

```python
from redwood_kit.embeddings import embed
vectors = embed(model="text-embedding-3-small", inputs=["text to index"])
```

At runtime, call `self.gateway.embed` with the same arguments. Use the same model and dimensions for documents and queries. Allowed models are in `deployment_manifest.json`. Pass every text in one call; the runtime batches the provider requests.
