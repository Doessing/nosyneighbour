"""
nosy-neighbour web server.

Serves a map-based UI, a JSON REST API, and an MCP server at POST /mcp.
"""

import logging
import os
import subprocess
from contextlib import asynccontextmanager

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from mcp.server.fastmcp import FastMCP
import uvicorn

from nosy_neighbour import TinglysningClient, get_loan_type_info
from boligsiden import get_sales_history
from resolver import resolve as resolve_address, ResolveError

DAWA_REVERSE_URL = "https://api.dataforsyningen.dk/adgangsadresser/reverse"

log = logging.getLogger(__name__)

_client = TinglysningClient()


def _git_version() -> str:
    """Return a short 'sha branch date' string for injection into the HTML.

    Best-effort — returns 'unknown' if git is unavailable or the directory is
    not a checkout (e.g. when running from a container or tarball).
    """
    try:
        def g(*args: str) -> str:
            return subprocess.check_output(
                ["git", *args], stderr=subprocess.DEVNULL, cwd=os.path.dirname(os.path.abspath(__file__))
            ).decode().strip()
        sha = g("rev-parse", "--short", "HEAD")
        branch = g("rev-parse", "--abbrev-ref", "HEAD")
        date = g("log", "-1", "--format=%cI")
        return f"{sha} {branch} {date}"
    except Exception:
        return "unknown"


_VERSION = _git_version()

with open("templates/index.html") as f:
    _index_html = f.read()

# Inject a version marker at the top so `Ctrl+U` + search for "version" reveals
# exactly which commit is serving the page. Purely for operator use.
_index_html = f"<!-- version: {_VERSION} -->\n{_index_html}"


def _annotate_loan_types(tingbog: dict) -> dict:
    for h in tingbog.get("haeftelser") or []:
        rente = float(h.get("rente") or 0)
        if (h.get("fastvariabel") == "variabel"
                and h.get("haeftelsestype") in ("Realkreditpantebrev", "Afgiftspantebrev")
                and rente > 0):
            h["loan_type_info"] = get_loan_type_info(rente, alias=h.get("alias"))
    return tingbog


# ── MCP server ────────────────────────────────────────────────────────────────
mcp_server = FastMCP("nosy-neighbour", stateless_http=True, json_response=True)


@mcp_server.tool()
def lookup_property(address: str) -> dict:
    """Look up Danish property records from tinglysning.dk.

    Given a freeform Danish address, returns owners (ejere), official
    valuation (vurdering) with equity estimate, mortgages and liens
    (hæftelser) with loan-type estimation for variable-rate realkreditlån,
    and easements (servitutter).
    """
    try:
        postnummer, vejnavn, husnummer = _client.resolve_address(address)
        tingbog = _client.lookup_address(postnummer, vejnavn, husnummer)
    except RuntimeError as e:
        return {"error": str(e)}
    if tingbog is None:
        return {"error": "No property data found"}
    return _annotate_loan_types(tingbog)


@mcp_server.tool()
def lookup_sales_history(address: str) -> dict:
    """Look up historical sale prices for a Danish address from Boligsiden.

    Given a freeform Danish address, returns every recorded sale of that
    exact address with date, price (DKK), area (m²), price per m², and
    sale type (normal=Almindeligt salg, family=Familiehandel,
    auction=Tvangsauktion). Sorted newest first.
    """
    try:
        resolved = resolve_address(address)
    except ResolveError as e:
        return {"error": str(e)}
    try:
        return get_sales_history(resolved)
    except requests.RequestException as e:
        return {"error": f"Boligsiden unreachable: {e}"}


# ── FastAPI app ───────────────────────────────────────────────────────────────
_mcp_asgi = mcp_server.streamable_http_app()  # lazily initialises session_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp_server.session_manager.run():
        yield


app = FastAPI(title="nosy-neighbour", lifespan=lifespan)


@app.get("/api/autocomplete")
def autocomplete(q: str = Query(...)):
    results = _client.autocomplete_address(q)
    return [
        {
            "label": r["forslagstekst"],
            "postnr": d["postnr"],
            "vejnavn": d["vejnavn"],
            "husnr": d["husnr"],
            "lat": d["y"],
            "lng": d["x"],
        }
        for r in results
        if (d := r.get("data", {})) and d.get("postnr") and d.get("vejnavn") and d.get("husnr")
    ]


@app.get("/api/reverse")
def reverse(lat: float = Query(...), lng: float = Query(...)):
    resp = requests.get(DAWA_REVERSE_URL, params={"x": lng, "y": lat})
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="No address found at this location")
    resp.raise_for_status()
    d = resp.json()
    return {
        "label": d["adressebetegnelse"],
        "postnr": d["postnummer"]["nr"],
        "vejnavn": d["vejstykke"]["navn"],
        "husnr": d["husnr"],
        "lat": d["adgangspunkt"]["koordinater"][1],
        "lng": d["adgangspunkt"]["koordinater"][0],
    }


@app.get("/api/lookup")
def lookup(q: str = Query(...)):
    try:
        postnummer, vejnavn, husnummer = _client.resolve_address(q)
        tingbog = _client.lookup_address(postnummer, vejnavn, husnummer)
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if tingbog is None:
        raise HTTPException(status_code=404, detail="No property data found")
    return _annotate_loan_types(tingbog)


@app.get("/api/sales-history")
def sales_history(q: str = Query(...)):
    """Historical sale prices for a given address, sourced from Boligsiden."""
    try:
        resolved = resolve_address(q)
    except ResolveError as e:
        raise HTTPException(status_code=404, detail=str(e))
    try:
        return get_sales_history(resolved)
    except requests.RequestException as e:
        log.warning("sales-history upstream error: %s", e)
        raise HTTPException(status_code=502, detail="Boligsiden unreachable")


@app.get("/api/resolve")
def resolve_endpoint(q: str = Query(...)):
    """Return the structured identifiers nosy-neighbour derives for an address.

    Useful for debugging (why does source X not find my address?) and as a
    shared primitive for any client that wants to call multiple data-source
    endpoints without re-paying the DAWA round-trip cost.
    """
    try:
        return resolve_address(q).to_dict()
    except ResolveError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except requests.RequestException as e:
        log.warning("resolver upstream error: %s", e)
        raise HTTPException(status_code=502, detail="DAWA unreachable")


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(content=_index_html)


@app.get("/api/version")
def version():
    return {"version": _VERSION}


# Mount MCP last so FastAPI routes take priority when matching paths.
# streamable_http_app() registers its handler at /mcp inside the sub-app;
# mounting the sub-app at / keeps the final endpoint at POST /mcp.
app.mount("/", _mcp_asgi)


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        ssl_certfile=os.environ.get("SSL_CERTFILE"),
        ssl_keyfile=os.environ.get("SSL_KEYFILE"),
    )
