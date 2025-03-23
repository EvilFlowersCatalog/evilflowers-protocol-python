# EvilFlowers Protocol Python Client

This package contains **auto-generated Pydantic models** and a reusable `Event` abstraction based on the OpenAPI 
schema defined in the [EvilFlowers system](https://github.com/EvilFlowersCatalog/evilflowers-protocol).

It is designed to provide a structured and versioned Python client that can be integrated into various projects within 
the EvilFlowers ecosystem.

---

## Automated OpenAPI Sync

- The OpenAPI specification (`openapi.yaml`) is automatically synced from the upstream [`evilflowers-protocol`](https://github.com/EvilFlowersCatalog/evilflowers-protocol) repository.
- On every branch or release update, this repository will:
    - Fetch the latest OpenAPI schema for the matching branch/tag.
    - Auto-generate Python models using **datamodel-codegen**.
    - Commit the models into the corresponding branch here (matching the upstream version).

---
