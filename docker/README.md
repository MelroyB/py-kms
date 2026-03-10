The Docker build must access the source code of this repository. Therefore the build context must be the root of the project directory.

Container image links:
- GitHub Container Registry image tag: `ghcr.io/melroyb/py-kms:latest`
- Docker Hub image tag: `melroy/py-kms:latest`

## Runtime file preflight
On startup, the container checks and creates missing runtime files (touch) before launching py-kms.
This includes sqlite/log/blacklist-related files that are required for a successful start.

## WebUI Authentication
If you run with `WEBUI=1`, you can protect the web interface with environment variables:

- `PYKMS_WEBUI_PASSWORD` (required to enable auth)
- `PYKMS_WEBUI_USERNAME` (optional, defaults to `admin`)
- `PYKMS_WEBUI_SECRET_KEY` (optional but recommended for stable Flask sessions)

If a default password is detected, the WebUI shows a security warning banner.

## IP Blacklist (persistent)
The WebUI has a settings page where you can manage blacklist rules for source IPs and ranges.
The rules are persisted in:

- `PYKMS_BLACKLIST_PATH` (default: `/home/py-kms/db/pykms_blacklist.txt`)
- `PYKMS_BLACKLIST_STATS_PATH` (default: `/home/py-kms/db/pykms_blacklist_stats.json`) for blocked-attempt counters

Supported rule formats:
- single IP (`192.168.1.10`)
- CIDR range (`10.0.0.0/24`)
- explicit range (`172.16.1.10-172.16.1.50`)

## Source IP backfill on startup
When the container starts, py-kms can backfill older sqlite client rows that still have `sourceIp` empty by parsing existing server log files.

Environment variables:
- `PYKMS_SOURCEIP_BACKFILL_ON_START` (default: `1`)
- `PYKMS_SOURCEIP_BACKFILL_GLOB` (default: `/home/py-kms/db/pykms_logserver.log*`)
- `PYKMS_SOURCEIP_BACKFILL_LOGS` (optional explicit comma-separated list of log files; overrides glob)

Important:
- Backfill only works from log files that are present in the mounted volume.
- If you only log to `STDOUT`, there is no log file for backfill.

## GeoIP country lookup in Clients page
The clients table can show a country flag + name next to each source IP.
Lookup uses an external provider without API key by default (`ipapi.co`), and results are cached in sqlite.

Environment variables:
- `PYKMS_GEOIP_ENABLED` (default: `1`)
- `PYKMS_GEOIP_PROVIDER` (default: `ipapi.co`)
- `PYKMS_GEOIP_TIMEOUT_SECONDS` (default: `2`)
- `PYKMS_GEOIP_CACHE_TTL_SECONDS` (default: `604800`, 7 days)
- `PYKMS_GEOIP_ERROR_CACHE_TTL_SECONDS` (default: `900`)
- `PYKMS_GEOIP_MAX_LOOKUPS_PER_REQUEST` (default: `20`)

## Clients table pagination and sorting
The clients page supports server-side pagination and sorting.

Environment variables:
- `PYKMS_WEBUI_CLIENTS_PER_PAGE` (default: `100`)
- `PYKMS_WEBUI_CLIENTS_MAX_PER_PAGE` (default: `500`)

Privacy note:
- For public IP addresses, the source IP may be sent to the configured GeoIP provider.
