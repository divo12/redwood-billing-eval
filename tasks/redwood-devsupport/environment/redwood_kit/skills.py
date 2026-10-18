"""Load workspace skills from Markdown files and expose them as tools."""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from pathlib import Path

from .jsontypes import JsonObject
from .toolkit import ToolType, is_tool


def frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split `---`-fenced `key: value` lines from the body. No nesting, no YAML."""
    head, fence, body = text.removeprefix("---\n").partition("\n---\n")
    if not text.startswith("---\n") or not fence:
        return {}, text.strip()
    pairs = (line.split(":", 1) for line in head.splitlines() if ":" in line)
    return {key.strip(): value.strip() for key, value in pairs}, body.strip()


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str


def load_skills(workspace: Path) -> dict[str, Skill]:
    skills: dict[str, Skill] = {}
    for path in sorted(Path(workspace, "skills").glob("*/SKILL.md")):
        meta, body = frontmatter(path.read_text())
        name = meta.get("name") or path.parent.name
        skills[name] = Skill(name, meta.get("description", ""), body)
    return skills


def workspace_of(toolkit: object) -> Path:
    """The directory of the module that defines the toolkit class: the workspace."""
    return Path(inspect.getfile(type(toolkit))).resolve().parent


class SkillTools:
    """Mix into `Tools` to give the model a `load_skill` tool over `workspace/skills/`."""

    @is_tool(ToolType.READ)
    def load_skill(self, name: str) -> JsonObject:
        """Load the full steps of one skill listed in your instructions.

        Args:
            name: The skill's name.
        """
        skills = load_skills(workspace_of(self))
        if name not in skills:
            raise ValueError(f"unknown skill {name!r}; available: {sorted(skills)}")
        return {"name": name, "steps": skills[name].body}
