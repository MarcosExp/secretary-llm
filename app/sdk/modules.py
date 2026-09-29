"""Module discovery. Adding a capability means adding a folder:

    modules/<name>/
    ├── module.yaml        manifest (validated below)
    ├── prompt.md          instructions for the module's subagent
    ├── tools.py           functions decorated with @tool
    └── migrations/        NNN_*.sql, tables prefixed <name>_

Nothing in the core or the orchestrator changes when a module is added.
"""

import importlib.util
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.db.migrations import MODULES_DIR
from app.sdk.tools import Tool, tools_in

TABLE_NAME = re.compile(r"^[a-z][a-z0-9]*_[a-z0-9_]+$")


class ModuleConfigError(Exception):
    pass


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = Field(min_length=20)
    version: int = 1
    model: str = "haiku"
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    reads: list[str] = []
    enabled: bool = True
    schedule: list[dict] = []  # used by the scheduler in a later phase

    @field_validator("reads")
    @classmethod
    def check_reads(cls, reads: list[str]) -> list[str]:
        for table in reads:
            if not TABLE_NAME.match(table):
                raise ValueError(f"'{table}' is not a <module>_<table> name")
        return reads


@dataclass(frozen=True)
class Module:
    name: str
    description: str
    model: str
    effort: str | None
    reads: frozenset[str]
    prompt: str
    tools: dict[str, Tool]


def load_modules(modules_dir: Path = MODULES_DIR, config: dict | None = None) -> dict[str, Module]:
    """Enabled modules by name. A module needs module.yaml, prompt.md and tools.py."""
    overrides = (config or {}).get("modules") or {}
    loaded = {}
    for manifest_path in sorted(modules_dir.glob("*/module.yaml")):
        folder = manifest_path.parent
        manifest = _read_manifest(manifest_path, overrides.get(folder.name) or {})
        if manifest.name != folder.name:
            raise ModuleConfigError(f"{manifest_path}: name '{manifest.name}' must match the folder")
        if not manifest.enabled:
            continue
        loaded[manifest.name] = Module(
            name=manifest.name,
            description=" ".join(manifest.description.split()),
            model=manifest.model,
            effort=manifest.effort,
            reads=frozenset(manifest.reads),
            prompt=_read_required(folder / "prompt.md"),
            tools=_load_tools(folder),
        )
    return loaded


def _read_manifest(path: Path, override: dict) -> Manifest:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw.update({k: v for k, v in override.items() if k in {"enabled", "model", "effort"}})
    try:
        return Manifest.model_validate(raw)
    except ValidationError as exc:
        raise ModuleConfigError(f"{path}: {exc}") from None


def _read_required(path: Path) -> str:
    if not path.exists():
        raise ModuleConfigError(f"missing {path}")
    return path.read_text(encoding="utf-8").strip()


def _load_tools(folder: Path) -> dict[str, Tool]:
    path = folder / "tools.py"
    if not path.exists():
        raise ModuleConfigError(f"missing {path}")
    # Loaded by path so a module folder works wherever it lives (tests use temporary folders).
    spec = importlib.util.spec_from_file_location(f"secretary_modules.{folder.name}.tools", path)
    code = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(code)
    tools = tools_in(vars(code))
    if not tools:
        raise ModuleConfigError(f"{path} defines no @tool functions")
    return tools
