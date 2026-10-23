# Knowledge index (maintainers)

Build-time only; the agent never reads `/app/R`.

    cd /app/workspace && python3 indexing/build_index.py /app/R

- `corpus.py` flattens each source record (fields, threads, comments) into text, prefixes every chunk with
  `[source id] title | status | updated`, and splits it into ~1,000-character windows (the embedding limit).
- Records in billing policy/process directories (`BILLING_DIRS`) or with billing-term density >= 2.5 per
  1k chars get dense vectors (table `billing`, ~5.2k chunks, ~3 min on 2 CPUs). Everything else is
  full-text only (table `corpus`). Vectors are cached in /tmp by chunk-text hash, so a rebuild only embeds
  changed chunks.
- `index/manifest.json` records each source's content hash: diff it against a new export to see what
  changed at a release.
- Runtime search (`retrieval.py`): exact record ids first, then a policy/procedure pass (Confluence and
  Drive pages, plus the curated billing directories), then everything else; reciprocal rank fusion of
  dense + full-text lists; at most 2 chunks per source.

After a rebuild, run the practice questions (`framework/retrieval_probes.json`) and re-check
`hooks.py` (SUPPORT_CREDIT_REASONS) and the skills against any changed policy pages.
