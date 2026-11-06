from __future__ import annotations

from collections.abc import Iterator

import pytest

from evilflowers_protocol.client import CatalogClient
from tests.fake_catalog import TOKEN, FakeCatalog


@pytest.fixture
def fake() -> Iterator[FakeCatalog]:
    server = FakeCatalog()
    server.start()
    yield server
    server.stop()


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def client(fake: FakeCatalog, sleeps: list[float]) -> CatalogClient:
    # Trailing slash on purpose: joining must not produce "//".
    catalog_client = CatalogClient(f"{fake.base_url}/", TOKEN, timeout=5, retries=3)
    catalog_client.transport.sleep = sleeps.append  # no real backoff in tests
    return catalog_client
