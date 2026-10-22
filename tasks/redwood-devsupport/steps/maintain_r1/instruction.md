# Maintain Redwood Inference's Billing Support Agent

Continue from your previous session and the workspace you delivered.
`/app/R/` has grown: new records have been added and nothing removed. Review
what changed, then update the support agent's knowledge and implementation.
Newer material may refine or contradict earlier material; determine its scope
and authority rather than assuming that the latest text always wins.

The agent must handle both new situations and the billing questions it already
supports. Test the updates and check for regressions. How you organize,
retrieve, and maintain its knowledge is your choice.

`/app/framework/agent_building.md` includes optional maintenance, component
integration, and regression-testing guidance. It is the same reference as R0.

The runtime interface, allowed models, and workspace-only deployment boundary
are unchanged. You have 3600 seconds in this step. The support agent still
cannot read `/app/R`; all knowledge it needs must ship in the workspace.

Optional practice questions in `framework/retrieval_probes.json` list the
releases they apply to; use the ones for the current release in
`/etc/redwood-release`. What you read from them is advisory and separate from
the held-out billing conversations that determine success.

Use `redwood_kit.billing.development_billing()` to test console-facing
tools and hooks against release-matched synthetic accounts listed in
`framework/client_api/practice_cases.json`. Writes stay local and are not
graded.
