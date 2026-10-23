# Authoring material (not used at run time)

These files record how the task's data was made. Nothing here is copied into the image or read by the verifier.

- `skills/redwood-r-generator/` is the skill used to write the planted billing records, the ones listed in
  `../tests/data/provenance.json`, in each platform's native format. `scripts/check_record.py` validates a record.
- `skills/redwood-world/` holds reusable knowledge about the Redwood corpus for writing further tasks.
- `rag_research.md` is the historical research note behind the offline RAG reference pack
  (`../environment/app/framework/offline-rag/`).

The skills were written in the larger workspace this task was assembled in, so they cite paths that are not in
this repository:

| Path in the skills | What ships here |
|---|---|
| `environments/redwood-mini/R0`, `R1` | `../environment/corpus/R0`, `R1`, the frozen output of the split |
| `environments/redwood`, `environments/redwood-nano`, `R2` | not included; the task uses only R0 and R1 |
| `scripts/split_redwood.py`, `slice_redwood.py`, `import_enterprise_rag.sh` | not included; the split is committed as data, and `../environment/corpus/NOTICE` records its upstream commit and the removed generator fields |

The record counts quoted in the skills are for the larger workspace. The shipped counts are in `../Task.md`.
