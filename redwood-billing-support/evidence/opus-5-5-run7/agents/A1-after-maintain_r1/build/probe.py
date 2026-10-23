"""Run questions through the shipped retriever and print what comes back (build-time check)."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from redwood_kit.embeddings import embed
from retrieval import Retriever

class LocalGateway:
    def embed(self, model, inputs):
        return embed(model=model, inputs=inputs)

r = Retriever(LocalGateway())
qs = sys.argv[1:]
if not qs:
    release = Path("/etc/redwood-release").read_text().strip()
    qs = [p["question"] for p in json.loads(Path("/app/framework/retrieval_probes.json").read_text())["probes"] if release in p["releases"]]
for q in qs:
    print(f"\n## {q}")
    for i, c in enumerate(r.search(q, k=6), 1):
        print(f"{i}. {c['source'][8:]} [{c['status']}, {c['date']}] {c['text'][:150]!r}")
