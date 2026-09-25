"""Loads config/schools.yaml and reads environment/secret variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_SCHOOLS_PATH = Path(__file__).resolve().parents[2] / "config" / "schools.yaml"


@dataclass(frozen=True)
class School:
    name: str
    aliases: list[str]
    handles: list[str]
    coach_handles: list[str]


def load_schools(path: str | Path = DEFAULT_SCHOOLS_PATH) -> list[School]:
    data = yaml.safe_load(Path(path).read_text())
    return [
        School(
            name=s["name"],
            aliases=s.get("aliases", []),
            handles=s.get("handles", []),
            coach_handles=s.get("coach_handles", []),
        )
        for s in data["schools"]
    ]


def env(name: str, default: str | None = None, required: bool = False) -> str | None:
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value
