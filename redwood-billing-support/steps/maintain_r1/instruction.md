# Maintain Redwood Inference's Billing Support Agent

Continue from your previous session and the workspace you delivered.
`/app/R/` has changed: new records have been added, some existing records have been
revised in place (same path, later update date), and none have been deleted. Review
what changed, then update the support agent's knowledge and implementation.
Newer material may refine or contradict earlier material; determine its scope
and authority rather than assuming that the latest text always wins.

The agent must handle both new situations and the billing questions it already
supports. Test the updates and check for regressions. How you organize,
retrieve, and maintain its knowledge is your choice.

`/app/framework/agent_building.md` includes optional maintenance, component
integration, and regression-testing guidance. It is the same reference as R0.

The runtime interface, allowed models (the run's `SUPPORT_MODEL`, named in
`framework/deployment_manifest.json`), and workspace-only deployment boundary
are unchanged. Dense embeddings still run on this container's CPUs with no
external embedding API: embed only the records you judge relevant to the
billing-support domain, not all of `/app/R`. You have 3600 seconds in this step. The support agent still
cannot read `/app/R`; all knowledge it needs must ship in the workspace.

Optional practice questions in `framework/retrieval_probes.json` list the
releases they apply to; use the ones for the current release in
`/etc/redwood-release`. What you read from them is advisory and separate from
the held-out billing conversations that determine success.

Use `redwood_kit.billing.development_billing()` to test console-facing
tools and hooks against release-matched synthetic accounts listed in
`framework/client_api/practice_cases.json`. Writes stay local and are not
graded. `python -m redwood_kit.practice SCRIPTS.json` runs whole practice
conversations through the graded conversation loop with the deployed support
model; `framework/client_api/practice_conversations.json` has example scripts
for this release, and R1 has its own 5 practice conversations.
