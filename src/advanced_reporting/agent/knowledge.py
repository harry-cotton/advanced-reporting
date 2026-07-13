"""Loaders for the ``system/`` knowledge base (guidelines + per-client context).

The agent system runs on curated, human-authored knowledge — never on vibes.
``guidelines/`` is committed, reusable IP; ``context/`` is gitignored and
per-engagement. Both are plain markdown so the analyst can diff exactly what
steered a run. READMEs and ``*.template.*`` files are scaffolding, not knowledge,
and are never fed to a model.
"""
from __future__ import annotations

from pathlib import Path

from ..utils import project_root

# Stable feed order: classification rules first, then trust rules, then bands, then
# the recommendation rails — the order an analyst would brief a new hire.
GUIDELINE_ORDER = ("campaign_types.md", "conversion_types.md",
                   "metrics_playbook.md", "recommendation_menu.md",
                   "commentary_style.md", "commentary_examples.md")


def _read_dir(d: Path, order: tuple[str, ...] = ()) -> dict[str, str]:
    if not d.is_dir():
        return {}
    files = {p.name: p for p in sorted(d.glob("*.md"))
             if not p.name.lower().startswith("readme")
             and ".template." not in p.name.lower()}
    ordered = [files.pop(n) for n in order if n in files] + list(files.values())
    return {p.name: p.read_text(encoding="utf-8") for p in ordered}


def load_guidelines(root: Path | None = None) -> dict[str, str]:
    """``system/guidelines/*.md`` in briefing order. Empty dict if absent."""
    return _read_dir((root or project_root()) / "system" / "guidelines", GUIDELINE_ORDER)


def load_context(root: Path | None = None) -> dict[str, str]:
    """``system/context/*.md`` (client brief, macro notes). Empty dict if absent —
    a missing context folder is normal (no engagement configured), never an error."""
    return _read_dir((root or project_root()) / "system" / "context")


def as_block(docs: dict[str, str]) -> str:
    """Render loaded docs as one prompt block, each under its filename heading."""
    if not docs:
        return "(none provided)"
    return "\n\n".join(f"### {name}\n{text.strip()}" for name, text in docs.items())
