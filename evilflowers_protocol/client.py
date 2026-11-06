"""Endpoint layer for the EvilFlowers Catalog REST API (``/api/v1``).

Each method maps one backend endpoint onto :class:`~evilflowers_protocol.transport.Transport`.
Transport concerns (auth, retries, errors, envelopes, pagination, multipart) live in
:mod:`evilflowers_protocol.transport`. Models come from the generated data layer
through :mod:`evilflowers_protocol.forms`.

One :class:`CatalogClient` can be shared by the threads of a process. Its only
mutable state is the catalog cache, which a lock guards.

Retries repeat a ``POST`` the server may already have completed (see
:mod:`evilflowers_protocol.transport`). For :meth:`CatalogClient.create_entry` the
repeat surfaces as :class:`Conflict`. For :meth:`CatalogClient.upload_acquisition`
it can attach the same file twice, so check :meth:`CatalogClient.get_entry` first.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any
from uuid import UUID

from evilflowers_protocol.errors import CatalogError, Conflict, NotFound, Unauthorized
from evilflowers_protocol.forms import (
    AcquisitionMeta,
    AcquisitionRef,
    CatalogRef,
    EntryCreate,
    EntryRef,
    EntryUpdate,
)
from evilflowers_protocol.transport import DEFAULT_USER_AGENT, Transport

__all__ = ["CatalogClient", "CatalogError", "Conflict", "NotFound", "Unauthorized"]


class CatalogClient:
    """Client for one EvilFlowers Catalog instance.

    ``base_url`` is the API root, for example
    ``https://dev.evilflowers.elvira.stuba.sk/api/v1``. ``token`` is a JWT: an access
    token from ``POST /api/v1/token``, or an API key from ``POST /api/v1/api_keys``
    (which does not expire). ``retries`` counts extra attempts, so ``retries=3``
    means at most 4 requests.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 60,
        retries: int = 3,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self._transport = Transport(base_url, token, timeout=timeout, retries=retries, user_agent=user_agent)
        self._catalogs: dict[str, CatalogRef] = {}
        self._catalogs_lock = threading.Lock()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(base_url={self._transport.base_url!r})"

    @property
    def transport(self) -> Transport:
        """The underlying transport, for endpoints this class does not wrap yet."""
        return self._transport

    def ping(self) -> None:
        """Check that the API is reachable and the token is valid (``GET /users/me``).

        Raises :class:`Unauthorized` for a bad token and :class:`CatalogError` otherwise.
        """
        self._transport.request("GET", "users/me")

    def resolve_catalog(self, url_name_or_uuid: str) -> CatalogRef:
        """Find a catalog by UUID or by exact ``url_name``. Results are cached per client.

        The server's ``url_name`` filter matches substrings, so this pages through
        every result and keeps only the exact match. Raises :class:`NotFound` when
        there is none.
        """
        key = url_name_or_uuid.strip()
        if not key:
            raise ValueError("url_name_or_uuid must not be empty")
        catalog_id: UUID | None
        try:
            catalog_id = UUID(key)
        except ValueError:
            catalog_id = None
        else:
            key = str(catalog_id)

        with self._catalogs_lock:
            cached = self._catalogs.get(key)
        if cached is not None:
            return cached

        catalog = self._fetch_catalog(key, catalog_id)
        with self._catalogs_lock:
            for alias in (key, str(catalog.id), catalog.url_name):
                self._catalogs[alias] = catalog
        return catalog

    def create_entry(self, catalog_id: UUID, entry: EntryCreate) -> EntryRef:
        """Create an entry. Raises :class:`Conflict` (with ``.body``) on a duplicate title, ISBN or DOI."""
        return self._transport.send("POST", f"catalogs/{catalog_id}/entries", entry, EntryRef)

    def update_entry(self, catalog_id: UUID, entry_id: UUID, entry: EntryUpdate) -> EntryRef:
        """Update an entry. Only fields set on ``entry`` are sent; see :class:`EntryUpdate`."""
        return self._transport.send("PUT", f"catalogs/{catalog_id}/entries/{entry_id}", entry, EntryRef)

    def get_entry(self, catalog_id: UUID, entry_id: UUID) -> EntryRef:
        """Fetch one entry, including its identifiers and acquisitions."""
        return self._transport.get(f"catalogs/{catalog_id}/entries/{entry_id}", EntryRef)

    def find_entries(self, catalog_id: UUID, *, title: str) -> list[EntryRef]:
        """Entries in ``catalog_id`` whose title equals ``title``, ignoring case.

        The server's ``title`` filter matches substrings (accent-insensitively), so
        this pages through every result and keeps exact case-insensitive matches.
        List results carry no ``identifiers``; call :meth:`get_entry` for them.
        """
        if not title.strip():
            raise ValueError("title must not be empty")
        wanted = title.casefold()
        query = {"catalog_id": str(catalog_id), "title": title}
        return [
            entry
            for entry in self._transport.iter_pages("entries", EntryRef, query=query)
            if entry.title.casefold() == wanted
        ]

    def upload_acquisition(
        self,
        catalog_id: UUID,
        entry_id: UUID,
        path: Path,
        *,
        mime: str = "application/pdf",
        relation: str = "open-access",
    ) -> AcquisitionRef:
        """Attach a file to an entry (multipart ``POST /catalogs/{c}/entries/{e}``).

        The file is streamed from disk. The backend stores ``mime`` without checking
        it, so pass the real type (pdf, epub, mobi or webpub).
        """
        metadata = AcquisitionMeta(relation=relation).model_dump_json()
        return self._transport.upload(
            f"catalogs/{catalog_id}/entries/{entry_id}",
            Path(path),
            AcquisitionRef,
            file_field="content",
            mime=mime,
            fields={"metadata": metadata},
        )

    def introspect(self, driver: str, identifier: str) -> dict[str, Any]:
        """Look a publication up in an external source (``GET /entry-introspection``).

        ``driver`` names the source (for example ``isbn`` or ``doi``). Returns the
        backend's result object as a dict.
        """
        response = self._transport.request(
            "GET", "entry-introspection", query={"driver": driver, "identifier": identifier}
        )
        result = response.unwrap()
        if not isinstance(result, dict):
            raise CatalogError(response.status, response.body, response.method, response.url, "expected an object")
        return result

    def _fetch_catalog(self, key: str, catalog_id: UUID | None) -> CatalogRef:
        if catalog_id is not None:
            return self._transport.get(f"catalogs/{catalog_id}", CatalogRef)
        for catalog in self._transport.iter_pages("catalogs", CatalogRef, query={"url_name": key}):
            if catalog.url_name == key:
                return catalog
        url = self._transport.url("catalogs", {"url_name": key})
        raise NotFound(None, None, "GET", url, f"no catalog has url_name {key!r}")
