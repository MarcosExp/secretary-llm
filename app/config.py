"""Optional user configuration: SECRETARY_CONFIG, else <SECRETARY_DATA_DIR>/config/config.yaml.

    orchestrator:
      model: haiku            # haiku | sonnet | opus, or a full model ID
    modules:
      studies:
        enabled: true
        model: sonnet
        effort: low           # only for models that support effort

Everything is optional; missing keys fall back to each module's manifest.
Environment variable SECRETARY_ORCHESTRATOR_MODEL overrides orchestrator.model.
"""

import os
from pathlib import Path

import yaml

DEFAULT_ORCHESTRATOR_MODEL = "haiku"


def config_path() -> Path | None:
    if path := os.getenv("SECRETARY_CONFIG"):
        return Path(path)
    if data_dir := os.getenv("SECRETARY_DATA_DIR"):
        return Path(data_dir) / "config" / "config.yaml"
    return None


def load_config() -> dict:
    path = config_path()
    if path is None or not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def orchestrator_model(config: dict) -> str:
    return (
        os.getenv("SECRETARY_ORCHESTRATOR_MODEL")
        or (config.get("orchestrator") or {}).get("model")
        or DEFAULT_ORCHESTRATOR_MODEL
    )
