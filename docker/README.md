Both docker files must access the source code of this repository. Therefore the build context must be the root of the project directory.
Take a look into the build script for the normal py-kms version, as it demonstrates exactly that case and how to use these docker files.

## WebUI Authentication
If you run with `WEBUI=1`, you can protect the web interface with environment variables:

- `PYKMS_WEBUI_PASSWORD` (required to enable auth)
- `PYKMS_WEBUI_USERNAME` (optional, defaults to `admin`)
- `PYKMS_WEBUI_SECRET_KEY` (optional but recommended for stable Flask sessions)

## IP Blacklist (persistent)
The WebUI has a settings page where you can manage blacklist rules for source IPs and ranges.
The rules are persisted in:

- `PYKMS_BLACKLIST_PATH` (default: `/home/py-kms/db/pykms_blacklist.txt`)

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
