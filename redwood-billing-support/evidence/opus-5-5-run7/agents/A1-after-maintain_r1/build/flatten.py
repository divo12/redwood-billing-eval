"""Build-time helpers: turn one R source record into readable text with metadata."""
import json
from pathlib import Path

R = Path("/app/R")


def walk(value, indent=0, key=None):
    """Yield readable lines for nested JSON, keeping keys as labels."""
    pad = "  " * indent
    if isinstance(value, dict):
        if key is not None:
            yield f"{pad}{key}:"
            indent += 1
            pad = "  " * indent
        for k, v in value.items():
            if isinstance(v, (dict, list)) and v:
                yield from walk(v, indent, k)
            elif v not in (None, "", [], {}):
                yield f"{pad}{k}: {v}"
    elif isinstance(value, list):
        if key is not None:
            yield f"{pad}{key}:"
            indent += 1
            pad = "  " * indent
        for item in value:
            if isinstance(item, (dict, list)):
                lines = list(walk(item, indent + 1))
                if lines:
                    yield pad + "- " + lines[0].strip()
                    yield from lines[1:]
            elif item not in (None, ""):
                yield f"{pad}- {item}"
    elif value not in (None, ""):
        yield f"{pad}{key}: {value}" if key else f"{pad}{value}"


def load(path):
    path = Path(path)
    if path.suffix == ".json":
        try:
            return json.loads(path.read_text())
        except Exception:
            return {"content": path.read_text(errors="replace")}
    return {"content": path.read_text(errors="replace")}


def text_of(path):
    return "\n".join(walk(load(path)))


def all_records():
    for p in sorted(R.rglob("*")):
        if p.is_file():
            yield p
