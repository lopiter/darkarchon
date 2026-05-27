"""Shared pytest fixtures for darkarchon module tests."""

import sys
from pathlib import Path

import pytest

# Add darkarchon root (one level up from tests/) to sys.path so
# `lib.detectors.claude`, `dashboard`, etc. are importable.
DARKARCHON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DARKARCHON_ROOT))

# Evict any cached `dashboard` module (could be a different project's app).
sys.modules.pop("dashboard", None)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture():
    """Return a function that loads a fixture file's text content by filename."""
    def _load(name: str) -> str:
        return (FIXTURES_DIR / name).read_text()
    return _load
