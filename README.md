# collinear-candidate/redwood-billing-support

This repo contains exactly one Harbor task directory:
[`redwood-billing-support/`](redwood-billing-support/).

Start here:
- [`redwood-billing-support/README.md`](redwood-billing-support/README.md): task idea, how to run it, verifier
  design, provenance and licences
- [`redwood-billing-support/DESIGN_DOC.md`](redwood-billing-support/DESIGN_DOC.md): what the task is, the designed
  traps, the fairness audit against the rubric, reproducibility requirements, economics (also as
  [`DESIGN_DOC.pdf`](redwood-billing-support/DESIGN_DOC.pdf))
- [`redwood-billing-support/RUN_REPORT.md`](redwood-billing-support/RUN_REPORT.md): oracle result, Claude Opus 5.5
  result, failure analysis, fairness audit, realism and economics, limitations
- [`redwood-billing-support/Task.md`](redwood-billing-support/Task.md): the full design spec (every rule, case,
  check and fairness decision)
- [`redwood-billing-support/evidence/`](redwood-billing-support/evidence/): raw builder traces, verifier output,
  scores and every graded conversation for the reported runs

`jobs/` (Harbor's local output, several GB) is gitignored; `evidence/` is the curated subset.
