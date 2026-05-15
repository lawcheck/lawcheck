"""Загрузка YAML-словарей. Кэшируется — словари читаются один раз на процесс."""
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_DICT_DIR = Path(__file__).parent


@lru_cache(maxsize=None)
def load(name: str) -> dict[str, Any]:
    path = _DICT_DIR / f"{name}.yaml"
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def policy_sections() -> dict[str, dict[str, Any]]:
    return load("policy_sections")["sections"]


def trackers() -> list[dict[str, Any]]:
    return load("trackers")["trackers"]


def pd_field_patterns() -> dict[str, dict[str, Any]]:
    return load("pd_field_names")["pd_field_patterns"]


def consent_markers() -> dict[str, list[str]]:
    return load("consent_markers")
