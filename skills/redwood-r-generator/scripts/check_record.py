#!/usr/bin/env python3
"""Check generated Redwood records before committing them.

    python skills/redwood-r-generator/scripts/check_record.py <tree> <record.json>...

<tree> is e.g. environments/redwood-nano. Prints one line per problem, or OK.
Exits 1 on any problem. Also fails if any file named agents.md sits in the
tree's release folders, where a builder could read it.
"""
import json
import sys
from pathlib import Path

GENERATOR_FIELDS = {"dataset_doc_uuid", "title_field_name", "content_field_names", "original_location",
                    "dataset_noise_document"}
PLATFORMS = {"slack", "gmail", "jira", "linear", "confluence", "google_drive", "hubspot", "fireflies", "github"}
RELEASES = ("R0", "R1", "R2")


def problems(tree: Path, path: Path) -> list[str]:
    try:
        parts = path.resolve().relative_to(tree.resolve()).parts
    except ValueError:
        return [f"not inside {tree}"]
    if len(parts) < 4 or parts[0] not in RELEASES or parts[1] != "sources" or parts[2] not in PLATFORMS:
        return ["path is not R<n>/sources/<platform>/..."]
    if path.suffix != ".json":
        return ["not a .json record"]
    text = path.read_text(encoding="utf-8")
    try:
        record = json.loads(text)
    except json.JSONDecodeError as error:
        return [f"invalid JSON: {error}"]
    if not isinstance(record, dict):
        return ["not a JSON object"]
    found = []
    if leaked := GENERATOR_FIELDS & record.keys():
        found.append(f"generator fields {sorted(leaked)}")
    if not any(json.dumps(record, indent=2, ensure_ascii=a) == text for a in (True, False)):
        found.append("formatting differs from the corpus: json.dumps(indent=2), no trailing newline")
    if clashes := [r for r in RELEASES if r != parts[0] and tree.joinpath(r, *parts[1:]).exists()]:
        found.append(f"same path already exists in {clashes}")
    return found


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    tree = Path(argv[1])
    found = [f"{arg}: {problem}" for arg in argv[2:] for problem in problems(tree, Path(arg))]
    found += [f"{p}: agents.md inside a release folder" for r in RELEASES
              for p in tree.joinpath(r).rglob("*") if p.name.lower() == "agents.md"]
    print("\n".join(found) or "OK")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
