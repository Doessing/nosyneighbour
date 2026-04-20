"""
Boligsiden client for fetching historical sale prices of a Danish address.

Flow:
  1. Resolve a freeform address to its DAWA "adresse" UUID via the DAWA API.
  2. Fetch /addresses/{uuid} from api.boligsiden.dk — the response contains
     a `registrations` array with all recorded sales for that exact address.

Notes:
  * Boligsiden uses the same UUIDs that DAWA publishes, so one DAWA lookup is
    sufficient to bridge between the two APIs.
  * The /addresses/{uuid} endpoint does not require any auth or API key
    (verified 2026-04-20). If that ever changes, see
    ~/.config/opencode/context-server-motd.md for the known public key.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

log = logging.getLogger(__name__)

DAWA_ADRESSER_URL = "https://api.dataforsyningen.dk/adresser"
BOLIGSIDEN_ADDRESS_URL = "https://api.boligsiden.dk/addresses/{uuid}"

# Translate Boligsiden's registration types to user-facing Danish.
REGISTRATION_TYPE_LABELS = {
    "normal": "Almindeligt salg",
    "family": "Familiehandel",
    "auction": "Tvangsauktion",
    "other": "Andet",
}


def resolve_address_uuid(postnr: str, vejnavn: str, husnr: str) -> str | None:
    """Return the DAWA "adresse" UUID for a given structured address, or None."""
    resp = requests.get(
        DAWA_ADRESSER_URL,
        params={
            "postnr": postnr,
            "vejnavn": vejnavn,
            "husnr": husnr,
            "struktur": "mini",
        },
        timeout=10,
    )
    resp.raise_for_status()
    hits = resp.json()
    if not hits:
        return None
    # If multiple addresses share the same house number (e.g. flats with etage/dør),
    # return the first one without etage/dør — it's usually the ground/building entry.
    for h in hits:
        if not h.get("etage") and not h.get("dør"):
            return h["id"]
    return hits[0]["id"]


def fetch_sales_history(uuid: str) -> list[dict[str, Any]]:
    """Fetch the `registrations` array for a Boligsiden address UUID.

    Each entry has `date`, `amount`, `area`, `type`, plus a derived
    `perAreaPrice` (kr/m²) and `typeLabel` (Danish).

    Returns sorted newest-first. Returns [] if no data or address unknown.
    """
    resp = requests.get(BOLIGSIDEN_ADDRESS_URL.format(uuid=uuid), timeout=10)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    data = resp.json()
    regs = data.get("registrations") or []

    enriched: list[dict[str, Any]] = []
    for r in regs:
        amount = r.get("amount")
        area = r.get("area") or r.get("livingArea")
        per_m2 = round(amount / area) if amount and area else None
        enriched.append({
            "date": r.get("date"),
            "amount": amount,
            "area": area,
            "type": r.get("type"),
            "typeLabel": REGISTRATION_TYPE_LABELS.get(
                r.get("type", ""), r.get("type", "").capitalize() or "Ukendt"
            ),
            "perAreaPrice": per_m2,
        })
    # Sort newest first (dates are ISO YYYY-MM-DD so string sort works).
    enriched.sort(key=lambda e: e.get("date") or "", reverse=True)
    return enriched


def get_sales_history(postnr: str, vejnavn: str, husnr: str) -> dict[str, Any]:
    """Top-level: structured address -> {uuid, registrations}."""
    uuid = resolve_address_uuid(postnr, vejnavn, husnr)
    if not uuid:
        return {"uuid": None, "registrations": []}
    return {"uuid": uuid, "registrations": fetch_sales_history(uuid)}
