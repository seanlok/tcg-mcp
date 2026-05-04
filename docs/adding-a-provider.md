# Adding a New Grading Provider

This walkthrough adds a hypothetical `SGCProvider` (Sportscard Guaranty Corp).
The same recipe works for any other grader.

## 1. Add the literal name

In `src/grading_mcp/models.py`:

```diff
- ProviderName = Literal["psa", "cgc", "bgs"]
+ ProviderName = Literal["psa", "cgc", "bgs", "sgc"]
```

## 2. Write the provider

Create `src/grading_mcp/providers/sgc.py`:

```python
from __future__ import annotations

import httpx

from grading_mcp.models import CertResult, ImageResult
from grading_mcp.providers.base import BaseProvider


class SGCProvider(BaseProvider):
    name = "sgc"

    def __init__(self, token: str, base_url: str = "https://api.example/sgc"):
        self._token = token
        self._base = base_url.rstrip("/")

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=30.0,
        )

    async def get_cert(self, cert_number: str) -> CertResult:
        async with self._client() as client:
            resp = await client.get(f"/certs/{cert_number}")
            resp.raise_for_status()
            data = resp.json()

        if not data.get("found"):
            return CertResult(provider="sgc", cert_number=cert_number, found=False, raw=data)

        return CertResult(
            provider="sgc",
            cert_number=cert_number,
            found=True,
            year=data.get("year"),
            grade=data.get("grade"),
            grade_numeric=data.get("grade_value"),
            total_population=data.get("pop"),
            raw=data,
        )

    async def get_images(self, cert_number: str) -> list[ImageResult]:
        async with self._client() as client:
            resp = await client.get(f"/certs/{cert_number}/images")
            resp.raise_for_status()
            return [
                ImageResult(
                    provider="sgc",
                    cert_number=cert_number,
                    url=item["url"],
                    is_front=item["side"] == "front",
                )
                for item in resp.json()
            ]
```

You only have to implement what your provider supports. `search_cards` and
`get_population` will fall through to `BaseProvider`'s `NotSupportedError`
defaults.

## 3. Add a credential to settings

In `src/grading_mcp/config.py`:

```diff
   bgs_api_token: str | None = None
+
+  # SGC — required to enable the SGC provider.
+  sgc_api_token: str | None = None
+  sgc_api_base_url: str = "https://api.example/sgc"
```

## 4. Register it conditionally

In `src/grading_mcp/providers/__init__.py`, inside `_build_registry`:

```diff
   registry["bgs"] = BGSProvider()
+
+  if settings.sgc_api_token:
+      from grading_mcp.providers.sgc import SGCProvider
+      registry["sgc"] = SGCProvider(
+          token=settings.sgc_api_token,
+          base_url=settings.sgc_api_base_url,
+      )
```

If your provider is a stub initially (no creds), register it unconditionally
the way `cgc` and `bgs` already are.

## 5. Update `grading_list_providers`

In `src/grading_mcp/server.py`, extend the `info` dict so users discovering
the server see SGC's capabilities and required env var.

## 6. Add tests

Create `tests/test_providers_sgc.py` and mock the upstream API with
`pytest-httpx`. Mirror the structure of `test_providers_psa.py`.

## 7. Document the env var

Add `SGC_API_TOKEN` to the README's "Configuration" section.

## 8. Bump version

`pyproject.toml` and `server.json` both carry the version. Bump them
together, tag the release, and let the publish workflow ship to PyPI. Then
re-run `mcp-publisher publish` to update the registry entry.
