from __future__ import annotations

import json
import logging
import os
import pickle
import socket
import threading
from email.message import Message
from email.parser import HeaderParser
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from evilflowers_protocol import __version__
from evilflowers_protocol.client import (
    CatalogClient,
    CatalogError,
    Conflict,
    NotFound,
    Unauthorized,
)
from evilflowers_protocol.forms import (
    AcquisitionRef,
    AuthorRef,
    EntryConfig,
    EntryCreate,
    EntryUpdate,
)
from evilflowers_protocol.transport import CHUNK_SIZE as _CHUNK_SIZE
from evilflowers_protocol.transport import stream_multipart as _stream_multipart
from tests.fake_catalog import TOKEN, FakeCatalog, Recorded, Reply, envelope, paginated

CATALOG_ID = UUID("6f1a5bfe-6d47-4f86-9d5e-5d6f0d6a9b01")
ENTRY_ID = UUID("0c7e2c43-2a3b-4a8e-8b6e-2f7f3e0b1c02")
ACQUISITION_ID = UUID("9d3a1f0e-1111-4c2b-9a7e-3b5c6d7e8f03")


def entry_payload(title: str = "Kniha", **extra: object) -> dict[str, object]:
    return {"id": str(ENTRY_ID), "catalog_id": str(CATALOG_ID), "title": title, "acquisitions": [], **extra}


def catalog_payload(url_name: str) -> dict[str, object]:
    return {"id": str(uuid4()), "url_name": url_name, "title": url_name.title(), "is_public": True}


def parse_multipart(content_type: str, body: bytes) -> list[tuple[Message, bytes]]:
    """Split a multipart/form-data body into (headers, content) per part, byte-exact."""
    prefix = "multipart/form-data; boundary="
    assert content_type.startswith(prefix)
    boundary = content_type.removeprefix(prefix).encode()
    assert body.startswith(b"--" + boundary + b"\r\n")
    assert body.endswith(b"\r\n--" + boundary + b"--\r\n")
    segments = (b"\r\n" + body).split(b"\r\n--" + boundary)
    assert segments[0] == b""
    assert segments[-1] == b"--\r\n"
    parts = []
    for segment in segments[1:-1]:
        assert segment.startswith(b"\r\n")
        raw_headers, content = segment[2:].split(b"\r\n\r\n", 1)
        parts.append((HeaderParser().parsestr(raw_headers.decode()), content))
    return parts


# ---------------------------------------------------------------------------- upload


@pytest.mark.parametrize(
    ("mime", "relation"),
    [("application/pdf", "open-access"), ("application/epub+zip", "acquisition")],
)
def test_upload_acquisition_multipart_framing(
    tmp_path: Path, fake: FakeCatalog, client: CatalogClient, mime: str, relation: str
) -> None:
    data = os.urandom(3 * _CHUNK_SIZE + 123)
    path = tmp_path / "Kniha 1.pdf"
    path.write_bytes(data)
    fake.reply_in_order(
        Reply(201, envelope({"id": str(ACQUISITION_ID), "relation": relation, "mime": mime, "url": None}))
    )

    acquisition = client.upload_acquisition(CATALOG_ID, ENTRY_ID, path, mime=mime, relation=relation)

    assert acquisition == AcquisitionRef(id=ACQUISITION_ID, relation=relation, mime=mime)
    [request] = fake.requests
    assert request.method == "POST"
    assert request.path == f"/api/v1/catalogs/{CATALOG_ID}/entries/{ENTRY_ID}"
    assert int(request.headers["Content-Length"]) == len(request.body)
    assert request.headers["Transfer-Encoding"] is None

    (meta_headers, meta_body), (file_headers, file_body) = parse_multipart(
        request.headers["Content-Type"], request.body
    )
    assert meta_headers.get_param("name", header="content-disposition") == "metadata"
    assert json.loads(meta_body) == {"relation": relation}
    assert file_headers.get_param("name", header="content-disposition") == "content"
    assert file_headers.get_param("filename", header="content-disposition") == "Kniha 1.pdf"
    assert file_headers["Content-Type"] == mime
    assert file_body == data


def test_upload_streams_file_in_bounded_chunks(tmp_path: Path) -> None:
    data = os.urandom(2 * _CHUNK_SIZE + 7)
    path = tmp_path / "book.pdf"
    path.write_bytes(data)

    chunks = list(_stream_multipart(b"HEAD", path, len(data), b"TAIL"))

    assert chunks[0] == b"HEAD"
    assert chunks[-1] == b"TAIL"
    assert all(len(chunk) <= _CHUNK_SIZE for chunk in chunks[1:-1])
    assert len(chunks) == 2 + 3
    assert b"".join(chunks[1:-1]) == data


def test_upload_retry_resends_the_whole_body(
    tmp_path: Path, fake: FakeCatalog, client: CatalogClient
) -> None:
    data = os.urandom(_CHUNK_SIZE + 1)
    path = tmp_path / "book.pdf"
    path.write_bytes(data)
    fake.reply_in_order(
        Reply(503, {"title": "busy"}),
        Reply(201, envelope({"id": str(ACQUISITION_ID), "relation": "open-access", "mime": "application/pdf"})),
    )

    client.upload_acquisition(CATALOG_ID, ENTRY_ID, path)

    first, second = fake.requests
    assert len(first.body) == len(second.body) == int(second.headers["Content-Length"])
    assert parse_multipart(second.headers["Content-Type"], second.body)[1][1] == data


def test_upload_rejects_header_injection_in_mime(tmp_path: Path, client: CatalogClient) -> None:
    path = tmp_path / "book.pdf"
    path.write_bytes(b"%PDF")
    with pytest.raises(ValueError, match="MIME"):
        client.upload_acquisition(CATALOG_ID, ENTRY_ID, path, mime="application/pdf\r\nX-Evil: 1")


def test_upload_missing_file(tmp_path: Path, client: CatalogClient) -> None:
    with pytest.raises(FileNotFoundError):
        client.upload_acquisition(CATALOG_ID, ENTRY_ID, tmp_path / "missing.pdf")


# --------------------------------------------------------------------------- entries


def test_create_entry_sends_json_without_nulls(fake: FakeCatalog, client: CatalogClient) -> None:
    fake.reply_in_order(Reply(201, envelope(entry_payload(identifiers={"isbn": "9788022345678", "doi": None}))))
    entry = EntryCreate(
        title="Kniha",
        language_code="sk",
        authors=[AuthorRef(name="Ján", surname="Kollár")],
        identifiers={"isbn": "9788022345678"},
        config=EntryConfig(evilflowers_ocr_enabled=True),
        image="data:image/png;base64,iVBORw0KGgo=",
    )

    created = client.create_entry(CATALOG_ID, entry)

    assert created.id == ENTRY_ID
    assert created.identifiers == {"isbn": "9788022345678"}
    [request] = fake.requests
    assert request.method == "POST"
    assert request.path == f"/api/v1/catalogs/{CATALOG_ID}/entries"
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.body) == {
        "title": "Kniha",
        "language_code": "sk",
        "authors": [{"name": "Ján", "surname": "Kollár"}],
        "categories": [],
        "identifiers": {"isbn": "9788022345678"},
        "config": {"evilflowers_ocr_enabled": True},
        "image": "data:image/png;base64,iVBORw0KGgo=",
    }


def test_conflict_carries_parsed_body(fake: FakeCatalog, client: CatalogClient) -> None:
    problem = {
        "title": "Entry already exists!",
        "type": "/conflict",
        "detail": "Entry with same title, isbn or DOI already exists in catalog Knižnica",
    }
    fake.reply_in_order(Reply(409, problem))

    with pytest.raises(Conflict) as caught:
        client.create_entry(CATALOG_ID, EntryCreate(title="Kniha", language_code="sk"))

    assert caught.value.status == 409
    assert caught.value.body == problem
    assert caught.value.method == "POST"
    assert "Entry already exists!" in str(caught.value)
    assert len(fake.requests) == 1


def test_update_entry_puts_only_set_fields(fake: FakeCatalog, client: CatalogClient) -> None:
    fake.reply_in_order(Reply(200, envelope(entry_payload(title="Nová"))))

    updated = client.update_entry(CATALOG_ID, ENTRY_ID, EntryUpdate(title="Nová", language_code="sk"))

    assert updated.title == "Nová"
    [request] = fake.requests
    assert request.method == "PUT"
    assert request.path == f"/api/v1/catalogs/{CATALOG_ID}/entries/{ENTRY_ID}"
    assert json.loads(request.body) == {"title": "Nová", "language_code": "sk"}


def test_get_entry_includes_acquisitions(fake: FakeCatalog, client: CatalogClient) -> None:
    acquisition = {"id": str(ACQUISITION_ID), "relation": "open-access", "mime": "application/pdf", "url": "x"}
    fake.reply_in_order(Reply(200, envelope(entry_payload(acquisitions=[acquisition], identifiers=None))))

    entry = client.get_entry(CATALOG_ID, ENTRY_ID)

    assert entry.acquisitions == [AcquisitionRef(id=ACQUISITION_ID, relation="open-access", mime="application/pdf")]
    assert entry.identifiers == {}
    assert fake.requests[0].path == f"/api/v1/catalogs/{CATALOG_ID}/entries/{ENTRY_ID}"


def test_find_entries_exact_casefold_match_across_pages(fake: FakeCatalog, client: CatalogClient) -> None:
    pages = {
        "1": [entry_payload("Foo"), entry_payload("Foo Bar"), entry_payload("Straße")],
        "2": [entry_payload("foo"), entry_payload("A Foo"), entry_payload("STRASSE")],
    }

    def responder(request: Recorded) -> Reply:
        number = request.query["page"][0]
        return Reply(200, paginated(pages[number], int(number), 2))

    fake.responder = responder

    assert [entry.title for entry in client.find_entries(CATALOG_ID, title="FOO")] == ["Foo", "foo"]
    assert [entry.title for entry in client.find_entries(CATALOG_ID, title="strasse")] == ["Straße", "STRASSE"]
    first = fake.requests[0]
    assert first.path == "/api/v1/entries"
    assert first.query["catalog_id"] == [str(CATALOG_ID)]
    assert first.query["title"] == ["FOO"]
    assert [request.query["page"] for request in fake.requests[:2]] == [["1"], ["2"]]


# -------------------------------------------------------------------------- catalogs


def test_resolve_catalog_exact_url_name_across_pages(fake: FakeCatalog, client: CatalogClient) -> None:
    wanted = catalog_payload("books")
    pages = {
        "1": [catalog_payload("old-books"), catalog_payload("books-archive")],
        "2": [catalog_payload("ebooks"), wanted],
        "3": [catalog_payload("books-2")],
    }

    def responder(request: Recorded) -> Reply:
        number = request.query["page"][0]
        return Reply(200, paginated(pages[number], int(number), 3))

    fake.responder = responder

    catalog = client.resolve_catalog("books")

    assert catalog.url_name == "books"
    assert str(catalog.id) == wanted["id"]
    assert [request.query["page"] for request in fake.requests] == [["1"], ["2"]]
    assert all(request.query["url_name"] == ["books"] for request in fake.requests)

    # Cached by url_name and by id: no further requests.
    assert client.resolve_catalog("books") is catalog
    assert client.resolve_catalog(str(catalog.id)) is catalog
    assert len(fake.requests) == 2


def test_resolve_catalog_not_found_without_probing_past_last_page(
    fake: FakeCatalog, client: CatalogClient
) -> None:
    fake.reply_in_order(Reply(200, paginated([catalog_payload("books-old")], 1, 1)))

    with pytest.raises(NotFound):
        client.resolve_catalog("books")

    assert len(fake.requests) == 1


def test_resolve_catalog_by_uuid(fake: FakeCatalog, client: CatalogClient) -> None:
    fake.reply_in_order(Reply(200, envelope({"id": str(CATALOG_ID), "url_name": "books", "title": "Books"})))

    catalog = client.resolve_catalog(str(CATALOG_ID).upper())

    assert catalog.id == CATALOG_ID
    assert fake.requests[0].path == f"/api/v1/catalogs/{CATALOG_ID}"
    assert client.resolve_catalog("books") is catalog
    assert len(fake.requests) == 1


# --------------------------------------------------------------------------- retries


def test_retries_then_succeeds_on_503(fake: FakeCatalog, client: CatalogClient, sleeps: list[float]) -> None:
    fake.reply_in_order(Reply(503), Reply(503), Reply(200, envelope({"id": str(uuid4())})))

    client.ping()

    assert len(fake.requests) == 3
    assert len(sleeps) == 2
    assert 0.25 <= sleeps[0] <= 0.5
    assert 0.5 <= sleeps[1] <= 1.0


@pytest.mark.parametrize("status", [502, 503, 504])
def test_gives_up_after_retries(fake: FakeCatalog, client: CatalogClient, status: int) -> None:
    fake.reply_in_order(Reply(status, {"title": "Bad gateway"}))

    with pytest.raises(CatalogError) as caught:
        client.ping()

    assert caught.value.status == status
    assert len(fake.requests) == 4  # 1 try + 3 retries


@pytest.mark.parametrize("status", [400, 422, 500])
def test_no_retry_on_other_errors(
    fake: FakeCatalog, client: CatalogClient, sleeps: list[float], status: int
) -> None:
    fake.reply_in_order(Reply(status, {"title": "Invalid request parameters"}))

    with pytest.raises(CatalogError) as caught:
        client.ping()

    assert type(caught.value) is CatalogError
    assert caught.value.status == status
    assert caught.value.body == {"title": "Invalid request parameters"}
    assert len(fake.requests) == 1
    assert sleeps == []


def test_connection_error_is_retried_then_raised(sleeps: list[float]) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    client = CatalogClient(f"http://127.0.0.1:{port}/api/v1", TOKEN, timeout=2, retries=2)
    client.transport.sleep = sleeps.append

    with pytest.raises(CatalogError) as caught:
        client.ping()

    assert caught.value.status is None
    assert len(sleeps) == 2
    assert "3 attempt(s)" in str(caught.value)


def test_validation_error_message_lists_fields(fake: FakeCatalog, client: CatalogClient) -> None:
    fake.reply_in_order(
        Reply(
            422,
            {
                "title": "Invalid request parameters",
                "type": "/validation-error",
                "validation_errors": [{"code": "not-found", "message": "Language not found.", "path": ["language_code"]}],
            },
            headers={"Content-Type": "application/problem+json"},
        )
    )

    with pytest.raises(CatalogError) as caught:
        client.create_entry(CATALOG_ID, EntryCreate(title="Kniha", language_code="xx"))

    assert "language_code: Language not found." in str(caught.value)


def test_redirect_is_not_followed(fake: FakeCatalog, client: CatalogClient) -> None:
    fake.reply_in_order(Reply(302, headers={"Location": "http://example.invalid/steal"}))

    with pytest.raises(CatalogError) as caught:
        client.ping()

    assert caught.value.status == 302
    assert len(fake.requests) == 1


# ----------------------------------------------------------------- auth and secrecy


def test_bearer_header_sent_and_token_never_leaks(
    fake: FakeCatalog, client: CatalogClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="evilflowers_protocol")
    fake.reply_in_order(Reply(401, {"title": "Unauthorized", "detail": f"Token {TOKEN} has expired"}))

    with pytest.raises(Unauthorized) as caught:
        client.ping()

    [request] = fake.requests
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["Accept"] == "application/json"
    assert request.headers["User-Agent"] == f"evilflowers-protocol/{__version__}"
    assert "GET /api/v1/users/me -> 401" in caplog.text
    for text in (caplog.text, repr(client), str(caught.value), repr(caught.value)):
        assert TOKEN not in text


def test_forbidden_is_unauthorized(fake: FakeCatalog, client: CatalogClient) -> None:
    fake.reply_in_order(Reply(403, {"title": "Insufficient permissions"}))
    with pytest.raises(Unauthorized):
        client.ping()


def test_debug_log_has_method_path_status_and_duration(
    fake: FakeCatalog, client: CatalogClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="evilflowers_protocol")
    fake.reply_in_order(Reply(200, envelope({"id": str(uuid4())})))

    client.ping()

    [record] = [r for r in caplog.records if r.name == "evilflowers_protocol"]
    assert record.levelno == logging.DEBUG
    assert record.getMessage().startswith("GET /api/v1/users/me -> 200 (")
    assert record.getMessage().endswith(" ms)")


# ---------------------------------------------------------------------------- misc


def test_introspect_unwraps_response(fake: FakeCatalog, client: CatalogClient) -> None:
    fake.reply_in_order(Reply(200, envelope({"title": "Kniha", "authors": []})))

    assert client.introspect("isbn", "9788022345678") == {"title": "Kniha", "authors": []}
    [request] = fake.requests
    assert request.path == "/api/v1/entry-introspection"
    assert request.query == {"driver": ["isbn"], "identifier": ["9788022345678"]}


def test_shared_across_threads(fake: FakeCatalog, client: CatalogClient) -> None:
    fake.reply_in_order(Reply(200, envelope(entry_payload())))
    errors: list[BaseException] = []

    def work() -> None:
        try:
            for _ in range(5):
                assert client.get_entry(CATALOG_ID, ENTRY_ID).id == ENTRY_ID
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(fake.requests) == 40


@pytest.mark.parametrize(
    ("base_url", "token"),
    [("ftp://host/api/v1", TOKEN), ("/api/v1", TOKEN), ("https://host/api/v1?x=1", TOKEN), ("https://h", "a\nb")],
)
def test_constructor_rejects_bad_arguments(base_url: str, token: str) -> None:
    with pytest.raises(ValueError) as caught:
        CatalogClient(base_url, token)
    assert "a\nb" not in str(caught.value)


def test_errors_survive_pickling() -> None:
    error = Conflict(409, {"title": "dup"}, "POST", "https://h/api/v1/catalogs/x/entries")
    clone = pickle.loads(pickle.dumps(error))
    assert type(clone) is Conflict
    assert (clone.status, clone.body, clone.method, clone.url) == (409, {"title": "dup"}, error.method, error.url)
    assert str(clone) == str(error)
