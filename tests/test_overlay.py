"""Keeps the overlay in :mod:`evilflowers_protocol.forms` honest against the generated schema.

When ``schema.py`` is regenerated:

* a test in ``TestUpstreamBugsStillPresent`` fails once upstream fixes a field.
  Delete the matching ``# upstream:`` override in ``forms.py``.
* the other tests fail when the generated models drift away from the overlay
  (a field renamed or removed). Update the overlay or the projection.
"""

from __future__ import annotations

from types import NoneType
from typing import Any, get_args
from uuid import UUID

import pytest
from pydantic import AnyUrl, BaseModel

from evilflowers_protocol import forms, schema


def union_args(model: type[BaseModel], field: str) -> set[Any]:
    annotation = model.model_fields[field].annotation
    return set(get_args(annotation)) or {annotation}


class TestUpstreamBugsStillPresent:
    @pytest.mark.parametrize(
        ("field", "wrong"),
        [
            ("identifiers", {str, NoneType}),
            ("image", {AnyUrl, NoneType}),
            ("category_ids", {UUID, NoneType}),
            ("author_ids", {UUID, NoneType}),
            ("feeds", {UUID, NoneType}),
        ],
    )
    def test_entry_form_field(self, field: str, wrong: set[Any]) -> None:
        assert union_args(schema.EntryForm, field) == wrong, (
            f"schema.EntryForm.{field} changed upstream; delete its override in forms.EntryCreate"
        )

    def test_acquisition_type_lacks_restricted_access(self) -> None:
        values = {member.value for member in schema.AcquisitionType}
        assert "restricted-access" not in values, (
            "schema.AcquisitionType gained restricted-access; drop the str override in forms.AcquisitionMeta"
        )


@pytest.mark.parametrize(
    ("overlay", "generated"),
    [
        (forms.AuthorRef, schema.AuthorForm),
        (forms.CategoryRef, schema.CategoryForm),
        (forms.EntryConfig, schema.EntryConfigForm),
        (forms.EntryCreate, schema.EntryForm),
        (forms.EntryUpdate, schema.EntryForm),
        (forms.AcquisitionMeta, schema.AcquisitionMetaForm),
    ],
)
def test_request_overlays_add_no_fields(overlay: type[BaseModel], generated: type[BaseModel]) -> None:
    assert issubclass(overlay, generated)
    assert set(overlay.model_fields) == set(generated.model_fields)


@pytest.mark.parametrize(
    ("projection", "generated"),
    [
        (forms.CatalogRef, schema.AppsApiSerializersCatalogsCatalogSerializerBase),
        (forms.AcquisitionRef, schema.AppsApiSerializersEntriesAcquisitionSerializerBase),
        (forms.EntryRef, schema.AppsApiSerializersEntriesEntrySerializerDetailed),
        (forms.Page, schema.PaginationModel),
    ],
)
def test_projections_exist_in_generated_serializers(
    projection: type[BaseModel], generated: type[BaseModel]
) -> None:
    missing = set(projection.model_fields) - set(generated.model_fields) - {"items"}
    assert not missing, f"{projection.__name__} projects fields the spec no longer has: {sorted(missing)}"


def test_entry_list_projection_matches_base_serializer() -> None:
    base_fields = set(schema.AppsApiSerializersEntriesEntrySerializerBase.model_fields)
    # identifiers is Detailed-only by design; EntryRef defaults it to {}.
    assert set(forms.EntryRef.model_fields) - {"identifiers"} <= base_fields
