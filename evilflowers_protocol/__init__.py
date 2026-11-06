"""EvilFlowers protocol: Catalog REST API client on top of a data layer generated from OpenAPI.

Layers, bottom up:

* :mod:`evilflowers_protocol.schema`: Pydantic models generated from the OpenAPI spec
  (``evilflowers-protocol``) by ``.github/workflows/generate-client.yml``. Never hand-edited.
* :mod:`evilflowers_protocol.forms`: stable names for the generated models, plus
  patches for the fields the generator gets wrong.
* :mod:`evilflowers_protocol.transport`: HTTP plumbing (auth, retries, envelopes,
  pagination, multipart). Knows no endpoint and no model.
* :mod:`evilflowers_protocol.client`: :class:`CatalogClient`, one method per endpoint.
"""

from evilflowers_protocol._version import __version__
from evilflowers_protocol.client import CatalogClient
from evilflowers_protocol.errors import CatalogError, Conflict, NotFound, Unauthorized
from evilflowers_protocol.forms import (
    AcquisitionMeta,
    AcquisitionRef,
    AuthorRef,
    CatalogRef,
    CategoryRef,
    EntryConfig,
    EntryCreate,
    EntryRef,
    EntryUpdate,
    Page,
    image_data_uri,
)
from evilflowers_protocol.transport import Transport

__all__ = [
    "AcquisitionMeta",
    "AcquisitionRef",
    "AuthorRef",
    "CatalogClient",
    "CatalogError",
    "CatalogRef",
    "CategoryRef",
    "Conflict",
    "EntryConfig",
    "EntryCreate",
    "EntryRef",
    "EntryUpdate",
    "NotFound",
    "Page",
    "Transport",
    "Unauthorized",
    "__version__",
    "image_data_uri",
]
