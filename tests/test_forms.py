from __future__ import annotations

import base64
import tomllib
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

import evilflowers_protocol
from evilflowers_protocol.forms import (
    AcquisitionMeta,
    CatalogRef,
    CategoryRef,
    EntryConfig,
    EntryCreate,
    EntryRef,
    EntryUpdate,
    Page,
    image_data_uri,
)

ROOT = Path(__file__).resolve().parent.parent


def test_entry_create_dump_excludes_none() -> None:
    entry = EntryCreate(title="Kniha", language_code="slk", categories=[CategoryRef(term="history")])

    assert entry.model_dump() == {
        "title": "Kniha",
        "language_code": "slk",
        "authors": [],
        "categories": [{"term": "history"}],
    }
    assert '"publisher"' not in entry.model_dump_json()


def test_entry_config_keeps_unknown_keys_and_drops_unset() -> None:
    config = EntryConfig.model_validate({"readium_enabled": False, "evilflowers_future_flag": True})
    assert config.model_dump() == {"readium_enabled": False, "evilflowers_future_flag": True}


def test_entry_update_sends_nothing_by_default() -> None:
    assert EntryUpdate().model_dump(mode="json") == {}


@pytest.mark.parametrize("image", ["https://example.org/cover.png", "data:text/plain;base64,AAAA", "iVBORw0KGgo="])
def test_image_must_be_a_data_uri(image: str) -> None:
    with pytest.raises(ValidationError, match="data URI"):
        EntryCreate(title="Kniha", language_code="sk", image=image)
    with pytest.raises(ValidationError, match="data URI"):
        EntryUpdate(image=image)


def test_request_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        EntryCreate.model_validate({"title": "Kniha", "language_code": "sk", "isbn": "123"})


@pytest.mark.parametrize("field", [{"title": "x" * 256}, {"language_code": "slov"}])
def test_entry_create_field_limits(field: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        EntryCreate.model_validate({"title": "Kniha", "language_code": "sk", **field})


def test_image_data_uri(tmp_path: Path) -> None:
    path = tmp_path / "cover.png"
    path.write_bytes(b"\x89PNG\r\n")

    uri = image_data_uri(path)

    assert uri == "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n").decode()
    assert EntryCreate(title="Kniha", language_code="sk", image=uri).image == uri
    with pytest.raises(ValueError, match="image type"):
        image_data_uri(tmp_path / "cover.bin")


def test_acquisition_meta_default() -> None:
    assert AcquisitionMeta().model_dump_json() == '{"relation":"open-access"}'


def test_entry_ref_tolerates_list_and_detail_shapes() -> None:
    entry_id = uuid4()
    from_list = EntryRef.model_validate({"id": str(entry_id), "title": "Kniha", "popularity": 3, "acquisitions": None})
    from_detail = EntryRef.model_validate(
        {"id": str(entry_id), "title": "Kniha", "identifiers": {"isbn": "978", "doi": None, "google": 12}}
    )

    assert from_list.identifiers == {}
    assert from_list.acquisitions == []
    assert from_detail.identifiers == {"isbn": "978", "google": "12"}


def test_page_lifts_metadata() -> None:
    catalog_id = uuid4()
    page = Page[CatalogRef].model_validate(
        {
            "items": [{"id": str(catalog_id), "url_name": "books", "title": "Books", "creator_id": str(uuid4())}],
            "metadata": {"page": 2, "limit": 10, "pages": 3, "total": 21},
        }
    )

    assert page.page == 2
    assert page.pages == 3
    assert page.total == 21
    assert page.items == [CatalogRef(id=catalog_id, url_name="books", title="Books")]


def test_version_matches_pyproject() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert evilflowers_protocol.__version__ == pyproject["tool"]["poetry"]["version"]


def test_public_exports_resolve() -> None:
    for name in evilflowers_protocol.__all__:
        assert getattr(evilflowers_protocol, name) is not None
