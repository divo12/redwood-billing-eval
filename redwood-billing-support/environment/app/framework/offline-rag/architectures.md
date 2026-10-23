# RAG architecture options

Part of offline pack **1.1.0**. These are composable patterns, not an ordered
curriculum or a checklist of required features. “Naive,” “advanced,” and
“modular” describe broad families; they do not guarantee performance.

| Pattern | Typical flow | When it may fit | Main tradeoff |
|---|---|---|---|
| Single-pass / basic RAG | Question → search → selected context → answer. | A question can be resolved from a small set of records. | A single search can miss ambiguity, exceptions, or links. |
| Retrieve–rerank–read | Search a broader pool → rerank → fetch evidence → answer. | Relevant passages appear in the pool but rank poorly. | Extra cost; reranking cannot recover an absent candidate. |
| Parent/child or hierarchical retrieval | Search short passages that already include the neighboring text they need. | Precise matching needs a larger context for interpretation. | A stored full record is not available for a second read. |
| Routed / modular RAG | Determine the need → select one or more indexes/tools → combine evidence. | Different sources or question types benefit from different retrieval. | Routing mistakes can exclude evidence; cross-source questions need a fallback. |
| Iterative / agentic RAG | Inspect results → identify a gap → search/read again → answer or clarify. | Evidence needs several hops or a query must be refined. | Loops, latency, and query drift; choose a stopping condition. |
| Corrective / reflective retrieval | Assess evidence sufficiency → accept, retry, or state the gap. | First-pass results are sometimes irrelevant or contradictory. | The evidence assessor can be wrong; self-confidence is not independent verification. |
| Graph-assisted RAG | Find a source/entity → traverse relationships → gather source passages. | The needed facts are distributed across linked records. | Ambiguous edges and extraction errors propagate; keep source provenance. |
| Summary-tree retrieval | Build summaries at multiple levels → select relevant summaries/leaves → read evidence. | Questions require synthesis over larger collections. | Summary generation cost, omissions, and maintenance after updates. |
| Contextualized retrieval | Add source-derived context to chunks during indexing → retrieve enriched chunks. | Small passages lose the document, actor, or time they describe. | Generated context can invent scope; distinguish it from original evidence. |
| Long-context retrieval | Load a selected set of source records into context. | The records that answer the question are few enough to read together. | Context limits and stale copies. A rewritten rulebook does not stand in for those records. |

Lexical, dense, and hybrid retrieval are search choices inside these patterns.
Query rewriting, HyDE, metadata filters, and reranking are techniques that can
be combined with several architectures. A hybrid retriever can still be
single-pass; an agentic workflow can use only lexical search. See the
[retrieval reference](README.md#retrieval-choices) for their mechanics.

## What published names imply

The original RAG work studies retrieval combined with generation; implementing
a retrieve-then-prompt workflow here does not require retraining that research
model. [RAG](https://arxiv.org/abs/2005.11401v1).

Self-RAG trains a model to use reflection tokens. Adding a prompted self-check
to the available gateway model is a different implementation. Use a bounded
reflection pattern if useful, without assuming the paper's training or results
are available here. [Self-RAG](https://arxiv.org/abs/2310.11511v1).

Corrective RAG includes a retrieval evaluator, refinement, and external web
search. A local conditional retry can be useful but is not the complete paper
method. This task's frozen corpus and network rules still apply.
[Corrective RAG](https://arxiv.org/html/2401.15884v3).

GraphRAG's query-focused summarization approach constructs a graph and community
summaries for global questions. Following existing JSON links is a simpler,
distinct graph-assisted pattern; it need not recreate that pipeline.
[GraphRAG](https://arxiv.org/abs/2404.16130v1).

RAPTOR recursively clusters and summarizes text into a retrieval tree. That is
different from simply retaining a heading/parent relationship between chunks.
Choose whether summary generation and update cost is justified.
[RAPTOR](https://arxiv.org/abs/2401.18059v1).

Contextual Retrieval prepends explanatory context before indexing. Source
metadata is a low-cost way to preserve some context; model-generated explanations
require checking and regeneration when their underlying source changes.
[Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval).

These papers and studies describe their own systems and experiments. They
supply design ideas, not expected scores or guaranteed improvements here.

## Connect the architecture to the required components

The skill might describe a reusable investigation procedure; the subagent
might investigate evidence or check a bounded aspect of an answer; a hook
might enforce a tool or data-handling constraint. These are examples, not
assigned roles. Decide responsibilities based on R and the support job.

Pass a delegated task enough context and define the expected result. Preserve
source references and unresolved uncertainty across the handoff. A second
model agreeing with the first is not independent source evidence. Test the
skill-loading path, subagent tools/results, and hook behavior in the design
you deliver. Their presence alone does not establish that the support agent
uses them correctly.
