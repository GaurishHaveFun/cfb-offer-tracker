"""Loads config/schools.yaml and reads environment/secret variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

# Where config/schools.yaml is when running from a source checkout (e.g. an
# editable install). A regular `pip install .` (as in GitHub Actions) puts the
# package in site-packages, where this path doesn't exist - see
# find_schools_path().
DEFAULT_SCHOOLS_PATH = Path(__file__).resolve().parents[2] / "config" / "schools.yaml"


def find_schools_path() -> Path:
    """$CFB_SCHOOLS_CONFIG if set, else config/schools.yaml in the current
    directory (the repo checkout in Actions), else next to the source."""
    override = os.environ.get("CFB_SCHOOLS_CONFIG")
    if override:
        return Path(override)
    in_cwd = Path.cwd() / "config" / "schools.yaml"
    if in_cwd.is_file():
        return in_cwd
    if DEFAULT_SCHOOLS_PATH.is_file():
        return DEFAULT_SCHOOLS_PATH
    raise FileNotFoundError(
        "config/schools.yaml not found: run from the repo root, or set CFB_SCHOOLS_CONFIG"
    )


@dataclass(frozen=True)
class School:
    name: str
    aliases: list[str]
    handles: list[str]
    coach_handles: list[str]
    # Nicknames shared with other schools ("Bulldogs", "Tigers"): never
    # enough to name the school on their own, but they do count as naming it
    # near an offer/commit phrase when the tweet also names the school with a
    # main alias somewhere ("on Georgia's radar ... an offer from the Bulldogs").
    context_aliases: tuple[str, ...] = ()

    @property
    def mention_handles(self) -> list[str]:
        """Every @handle that names this school when tagged: its football and
        coach handles plus any "@..." alias ("@UMich"). Aliases are only
        search terms and names, so they never make an account the school's
        own (see is_school_account)."""
        at_aliases = [a for a in self.aliases if a.startswith("@")]
        return [*self.handles, *self.coach_handles, *at_aliases]


def load_schools(path: str | Path | None = None) -> list[School]:
    data = yaml.safe_load(Path(path or find_schools_path()).read_text())
    return [
        School(
            name=s["name"],
            aliases=s.get("aliases", []),
            handles=s.get("handles", []),
            coach_handles=s.get("coach_handles", []),
            context_aliases=tuple(s.get("context_aliases", [])),
        )
        for s in data["schools"]
    ]


def env(name: str, default: str | None = None, required: bool = False) -> str | None:
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value
