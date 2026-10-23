"""The starter workspace as the image delivers it: {{SUPPORT_MODEL}} filled in (environment/Dockerfile)."""
import shutil
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "environment" / "app"
STARTER = APP / "workspace"
SUPPORT_MODEL = "openai/gpt-6-luna"  # gateway.DEFAULT_MODEL, the model tests run without SUPPORT_MODEL set


def _fill(root: Path) -> Path:
    for path in [root] if root.is_file() else root.rglob("*"):
        if path.is_file() and "{{SUPPORT_MODEL}}" in (text := path.read_text()):
            path.write_text(text.replace("{{SUPPORT_MODEL}}", SUPPORT_MODEL))
    return root


def copy_starter(workspace: Path) -> Path:
    shutil.copytree(STARTER, workspace, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return _fill(workspace)


def copy_manifest(path: Path) -> Path:
    shutil.copy(APP / "framework" / "deployment_manifest.json", path)
    return _fill(path)
