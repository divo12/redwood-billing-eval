#!/usr/bin/env python3
"""Reveal R1 into /app/R after build_r0 is scored.

Installed as /usr/local/libexec/redwood-advance and run as root by the
build_r0 verifier. Everything R1-only waits in the root-only overlay
/opt/redwood-overlays/R1 until now:

    R/                       new records, copied into /app/R (root:harbor, not overwriting R0)
    framework/               R1 builder docs, copied over /app/framework
    development_seed.json    R1 practice records, read by the practice service at R1
    db_r1.json               R1 graded records, read by the verifier

It then records the release in /etc/redwood-release, writes the R1 console schema
to /app/framework/client_api/schema.json, and seals /tests and /logs/verifier so
the next builder session cannot read the verifier. Running it again does nothing.
"""
from __future__ import annotations

import grp
import json
import os
import shutil
import sys
from pathlib import Path

from redwood_kit.billing.schema import Release
from redwood_kit.billing.store import Billing

R = Path("/app/R")
OVERLAY = Path("/opt/redwood-overlays/R1")
RELEASE = Path("/etc/redwood-release")
FRAMEWORK = Path("/app/framework")
SEALED = (Path("/tests"), Path("/logs/verifier"))


def reveal(overlay: Path, root: Path, gid: int) -> int:
    files = sorted(path for path in overlay.rglob("*") if path.is_file())
    clashes = [path for path in files if (root / path.relative_to(overlay)).exists()]
    if clashes:
        raise FileExistsError(f"R1 would overwrite an R0 record: {clashes[0].relative_to(overlay)}")
    shutil.copytree(overlay, root, dirs_exist_ok=True)  # also copies the overlay's modes onto root
    for path in (root, *(root / p.relative_to(overlay) for p in overlay.rglob("*"))):
        os.chown(path, 0, gid)
        path.chmod(0o550 if path.is_dir() else 0o440)
    return len(files)


def publish_framework(overlay: Path, framework: Path) -> None:
    for source in sorted(path for path in overlay.rglob("*") if path.is_file()):
        target = framework / source.relative_to(overlay)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.unlink(missing_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o444)
    schema = framework / "client_api/schema.json"
    schema.unlink(missing_ok=True)
    schema.write_text(json.dumps(Billing(release=Release.R1).describe(), indent=2) + "\n")
    schema.chmod(0o444)


def main() -> int:
    if os.geteuid() != 0:
        print("redwood-advance must run as root", file=sys.stderr)
        return 2
    if RELEASE.read_text().strip() == "R1":
        return 0
    count = reveal(OVERLAY / "R", R, grp.getgrnam("harbor").gr_gid)
    RELEASE.write_text("R1\n")
    publish_framework(OVERLAY / "framework", FRAMEWORK)
    for tree in SEALED:
        if tree.is_dir():
            tree.chmod(0o700)
    print(f"revealed {count} R1 records in {R}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
