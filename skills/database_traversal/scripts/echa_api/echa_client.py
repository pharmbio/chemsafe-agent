import asyncio
import logging
import httpx

log = logging.getLogger(__name__)

# Global config
BASE_URL = "https://chem.echa.europa.eu"
RETRIES = 3
_TIMEOUT = httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=30.0)
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Errors might happens, worth retrying
_RETRYABLE = (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError)


class ECHAClient:
    """Async wrapper over ECHA CHEM endpoints."""

    def __init__(self):
        self._session = None

    async def _connection(self):
        if self._session is None or self._session.is_closed:
            self._session = httpx.AsyncClient(
                base_url=BASE_URL,
                headers={"User-Agent": _USER_AGENT},
                timeout=_TIMEOUT,
                limits=httpx.Limits(
                    max_connections=10,
                    max_keepalive_connections=5,
                    keepalive_expiry=30,
                ),
                verify=False,
                trust_env=False,
                proxy=None,
            )
        return self._session

    async def close(self):
        if self._session and not self._session.is_closed:
            await self._session.aclose()
            self._session = None

    async def _fetch(self, path, params=None, retries=RETRIES):
        """GET `path` and hand back a live 200 response, returned `None` when error
        """
        session = await self._connection()
        for attempt in range(retries):
            try:
                resp = await session.get(path, params=params)
                if resp.status_code == 200:
                    return resp
                await resp.aclose()
                if resp.status_code == 404:
                    log.warning("Not found: %s", path)
                    return None
                log.warning(
                    "HTTP %s for %s (%d/%d)",
                    resp.status_code, path, attempt + 1, retries,
                )
            except _RETRYABLE as exc:
                log.warning(
                    "Request error for %s (%d/%d): %s",
                    path, attempt + 1, retries, exc,
                )
            if attempt + 1 < retries:
                await asyncio.sleep(attempt + 1)
        return None

    async def get_json(self, path, params=None, retries=RETRIES):
        resp = await self._fetch(path, params, retries)
        if resp is None:
            return None
        try:
            # A few endpoints answer 200 with an empty body (e.g. no m-factors).
            if not resp.content:
                return None
            return resp.json()
        except ValueError:
            log.warning("Expected JSON, got something else from %s", path)
            return None
        finally:
            await resp.aclose()

    async def get_html(self, path, retries=RETRIES):
        resp = await self._fetch(path, retries=retries)
        if resp is None:
            return None
        try:
            return resp.text
        finally:
            await resp.aclose()

    # Rendered dossier pages
    async def get_dossier_index(self, asset_id):
        return await self.get_html(f"/html-pages-prod/{asset_id}/index.html")

    async def get_document_html(self, asset_id, doc_id):
        return await self.get_html(
            f"/html-pages-prod/{asset_id}/documents/{doc_id}.html"
        )

    # Substance, substance search, and dossier listing
    async def get_substance_info(self, substance_index):
        return await self.get_json(f"/api-substance/v1/substance/{substance_index}")

    async def search_substances(self, search_text, page_size=10, page_index=1):
        """Free-text search over the substance inventory (name, CAS, EC, ...)"""
        return await self.get_json(
            "/api-substance/v1/substance",
            params={
                "searchText": search_text,
                "pageIndex": page_index,
                "pageSize": page_size,
            },
        )

    async def get_dossier_list(self, substance_index, status="Active"):
        return await self.get_json(
            "/api-dossier-list/v1/dossier",
            params={
                "rmlId": substance_index,
                "legislation": "REACH",
                "registrationStatuses": status,
                "pageIndex": 1,
                "pageSize": 100,
            },
        )


    # Both the industry (CLP notification) and harmonised trees share the same URL shape, so route them through one helper.
    async def _cnl(self, tree, tail):
        return await self.get_json(f"/api-cnl-inventory/{tree}/{tail}")

    async def get_clp_classifications(self, substance_index):
        return await self._cnl("industry", f"{substance_index}/classifications")

    async def get_clp_classification_detail(self, cid):
        return await self._cnl("industry", f"classification/{cid}")

    async def get_clp_labelling(self, cid):
        return await self._cnl("industry", f"labelling/{cid}")

    async def get_clp_pictograms(self, cid):
        return await self._cnl("industry", f"pictograms/{cid}")

    async def get_clp_scl(self, cid):
        return await self._cnl("industry", f"specific-concentration-limits/{cid}")

    async def get_clp_m_factors(self, cid):
        return await self._cnl("industry", f"m-factors/{cid}")

    async def get_harmonised_classifications(self, substance_index):
        return await self._cnl("harmonized", f"{substance_index}/classifications")

    async def get_harmonised_classification_detail(self, cid):
        return await self._cnl("harmonized", f"classification/{cid}")

    async def get_harmonised_labelling(self, cid):
        return await self._cnl("harmonized", f"labelling/{cid}")

    async def get_harmonised_pictograms(self, cid):
        return await self._cnl("harmonized", f"pictograms/{cid}")

    async def get_harmonised_scl(self, cid):
        return await self._cnl("harmonized", f"specific-concentration-limits/{cid}")

    async def get_harmonised_m_factors(self, cid):
        return await self._cnl("harmonized", f"m-factors/{cid}")

    async def get_harmonised_ate(self, cid):
        return await self._cnl("harmonized", f"acute-toxicity-estimates/{cid}")

    async def get_harmonised_notes(self, cid):
        return await self._cnl("harmonized", f"notes/{cid}")


_INSTANCE = None


def get_client():
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = ECHAClient()
    return _INSTANCE
