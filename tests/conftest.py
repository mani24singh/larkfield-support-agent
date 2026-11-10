"""Shared pytest fixtures and import-path bootstrap."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "data"
INBOX_PATH = PROJECT_ROOT / "tickets" / "support_inbox_export.json"
PUBLIC_PATH = PROJECT_ROOT / "inbox" / "public_tickets.json"
SEED_PATH = PROJECT_ROOT / "tickets" / "eval_seed_labelled.json"


def _read_json(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


@pytest.fixture(scope="session")
def dataset():
    """The real data pack, loaded once per test session."""
    from src.ingest import load_dataset

    return load_dataset(data_dir=DATA_DIR)


@pytest.fixture(scope="session")
def inbox_rows() -> list[dict]:
    return _read_json(INBOX_PATH)


@pytest.fixture(scope="session")
def public_rows() -> list[dict]:
    return _read_json(PUBLIC_PATH)


@pytest.fixture(scope="session")
def labelled_rows() -> list[dict]:
    return _read_json(SEED_PATH)


@pytest.fixture(scope="session")
def make_ticket() -> Callable[..., "object"]:
    """Build a validated ``Ticket`` from a payload copy with overrides."""
    from src.models import Ticket

    def build(base: dict, **overrides) -> Ticket:
        payload = {**base, **overrides}
        return Ticket.model_validate(payload)

    return build

