import json
from pathlib import Path

import pytest

from cfb_offers.config import load_schools

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def schools():
    return load_schools()


@pytest.fixture(scope="session")
def tweets():
    return json.loads((FIXTURES / "tweets.json").read_text())
