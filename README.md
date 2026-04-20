# THIS IS A TOY IMPLEMENTATION - USE AT YOUR OWN RISK

# nosy-neighbour

Look up Danish property records from [tinglysning.dk](https://www.tinglysning.dk) via a map-based browser UI or a command-line tool.

Given any freeform Danish address you get:

- **Owners** (ejere) with ownership share
- **Valuation** (vurdering) — property value, land value, municipality
- **Estimated equity** (friværdi) — valuation minus total registered mortgage principals
- **Mortgages and liens** (hæftelser) — including estimated loan type (F-kort / F1 / F3 / F5) for variable-rate realkreditlån
- **Easements** (servitutter)

Loan type estimation works by matching the registered coupon rate against [Nationalbanken rate statistics](https://www.dst.dk/da/Statistik/emner/penge-og-kapitalmarked/renter/realkreditrenter) (DST table DNRNURI). When an ISIN is known, the type is confirmed definitively via [ESMA FIRDS](https://registers.esma.europa.eu).

---

## Running locally

**Requirements:** Python 3.10+

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Web UI (http://localhost:8000)
python server.py

# CLI
python nosy_neighbour.py "Stålhøjen 24, 8240 Risskov"
```

---

## Running with Docker

```bash
# Build
docker build -t nosy-neighbour .

# Run (web UI on http://localhost:8000)
docker run -p 8000:8000 nosy-neighbour
```

### Production deployment (Docker Compose + Caddy)

A `compose.yml` is included that runs the app behind a [Caddy](https://caddyserver.com) reverse proxy with automatic HTTPS via DuckDNS.

```bash
cp .env.example .env
# edit .env with your values
docker compose up -d
```

Caddy listens on port **18000** and proxies to the app. Set `DUCKDNS_DOMAIN` to your own DuckDNS subdomain (e.g. `yourname.duckdns.org`).

---

## Web UI

Open `http://localhost:8000` in a browser.

- **Search** by typing an address in the sidebar — autocomplete is powered by [DAWA](https://dawadocs.dataforsyningen.dk).
- **Click the map** to look up the property at that location (reverse geocoding via [Dataforsyningen](https://dataforsyningen.dk)).
- Up to **10 addresses** can be pinned simultaneously. Each gets a numbered marker; click a marker to scroll to its details in the sidebar.
- Remove any address with the **×** button in its header.

---

## API

| Endpoint | Parameters | Description |
|---|---|---|
| `GET /api/autocomplete` | `q` | DAWA address autocomplete |
| `GET /api/reverse` | `lat`, `lng` | Reverse geocode a map click to an address |
| `GET /api/lookup` | `q` | Full property lookup by freeform address |

### Example

```bash
curl "http://localhost:8000/api/lookup?q=Stålhøjen+24+Risskov"
```

```json
{
  "adresse": "Stålhøjen 24, 8240 Risskov",
  "ejendomstype": "Ejerlejlighed",
  "matrikler": [{ "matrikelnummer": "...", "landsejerlavnavn": "..." }],
  "vurdering": {
    "vurderingsdato": "2022-01-01",
    "ejendomsvaerdi": 2650000,
    "grundvaerdi": 312500,
    "kommune": "Aarhus"
  },
  "ejere": [{ "navn": "...", "andel": "1/1" }],
  "haeftelser": [
    {
      "prioritet": "1",
      "haeftelsestype": "Realkreditpantebrev",
      "hovedstol": "1.716.000 kr.",
      "rente": "1.5",
      "fastvariabel": "variabel",
      "kreditorer": ["Totalkredit A/S"],
      "loan_type_info": {
        "source": "estimated",   // or "esma_firds" when confirmed via ISIN
        "loan_type": "F3",
        "uncertain": false,      // true when the rate falls close to a boundary
        "close_to": [],          // nearby loan types when uncertain is true
        "candidates": [...]      // all rate-matched candidates considered
      }
    }
  ],
  "servitutter": [...]
}
```

---

## MCP server

The service exposes an [MCP](https://modelcontextprotocol.io) server at `POST /mcp` using the Streamable HTTP transport. Connect any MCP-compatible client (Claude Desktop, Claude Code, etc.) to `http://localhost:8000/mcp`.

### Tool: `lookup_property`

| Parameter | Type | Description |
|---|---|---|
| `address` | `string` | Freeform Danish address |

Returns the same JSON payload as `GET /api/lookup`.

### Claude Desktop config

```json
{
  "mcpServers": {
    "nosy-neighbour": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

### Claude Code

```bash
claude mcp add --transport http nosy-neighbour http://localhost:8000/mcp
```

---

## CLI

```
usage: nosy_neighbour.py [-h] [--isin PRIORITY:ISIN] address [address ...]

positional arguments:
  address              Freeform address, e.g. "Molsvej 38 6950 Ringkøbing"

options:
  --isin PRIORITY:ISIN  ISIN for a specific mortgage, as priority:ISIN
                        (e.g. --isin 1:DK0004632486). Can be repeated.
```

When an ISIN is supplied for a mortgage, the loan type is resolved definitively from ESMA FIRDS instead of estimated from rate statistics.
