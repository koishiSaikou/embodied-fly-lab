"""Shared application identity; no simulation imports, installation or Git writes."""
import json
from functools import lru_cache
from pathlib import Path

ROOT=Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def _identity_text():
    return (ROOT/'版本信息.json').read_text(encoding='utf-8')


def project_info():
    """Freeze identity for this process; callers get independent dictionaries."""
    return json.loads(_identity_text())
