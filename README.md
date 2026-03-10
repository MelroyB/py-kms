# py-kms
![repo-size](https://img.shields.io/github/repo-size/Py-KMS-Organization/py-kms)
![open-issues](https://img.shields.io/github/issues/Py-KMS-Organization/py-kms)
![last-commit](https://img.shields.io/github/last-commit/Py-KMS-Organization/py-kms/master)
![docker-pulls](https://img.shields.io/docker/pulls/pykmsorg/py-kms)

_This project is intended for testing and learning, not for production use._

## Overview
`py-kms` is a Python KMS server emulator forked from the original `SystemRage/py-kms` project.
It supports KMS protocol v4/v5/v6, includes a Docker-first runtime, and has a modern WebUI for client, product, and security management.

## Key Features
- KMS protocol support: `v4`, `v5`, `v6`
- Product data from `KmsDataBase.xml` (Windows + Office)
- SQLite persistence for client activations
- WebUI with:
  - clients overview
  - products catalog
  - settings page
  - built-in login protection
- Source IP tracking in the clients table
- Country lookup (flag + name) next to Source IP in clients table
- Startup source-IP backfill from server logs
- Persistent blacklist management (single IP, CIDR, range)
- Blacklist attempt counters (per rule + per source IP)

## Quick Start

### Run from source
- Server:
```bash
python3 pykms_Server.py [IPADDRESS] [PORT]
```
- Help:
```bash
python3 pykms_Server.py -h
python3 pykms_Client.py -h
```

### Run with Docker
```bash
docker run -d \
  --name py-kms \
  --restart always \
  -p 1688:1688 \
  -p 8080:8080 \
  ghcr.io/py-kms-organization/py-kms
```

For Docker-specific details (env vars, volume behavior), see:
- [docker/README.md](./docker/README.md)

## Important Environment Variables

### Core
- `IP` (default `::`)
- `PORT` (default `1688`)
- `LOGLEVEL` (default `INFO`)
- `WEBUI` (default `1`)

### WebUI Authentication
- `PYKMS_WEBUI_PASSWORD` (required to enable login)
- `PYKMS_WEBUI_USERNAME` (optional, default `admin`)
- `PYKMS_WEBUI_SECRET_KEY` (optional, recommended)

### Blacklist
- `PYKMS_BLACKLIST_PATH`  
  default: `/home/py-kms/db/pykms_blacklist.txt`
- `PYKMS_BLACKLIST_STATS_PATH`  
  default: `/home/py-kms/db/pykms_blacklist_stats.json`

### Source IP Backfill
- `PYKMS_SOURCEIP_BACKFILL_ON_START` (default `1`)
- `PYKMS_SOURCEIP_BACKFILL_GLOB` (default `/home/py-kms/db/pykms_logserver.log*`)
- `PYKMS_SOURCEIP_BACKFILL_LOGS` (optional explicit comma-separated list; overrides glob)

### GeoIP (Country in Clients WebUI)
- `PYKMS_GEOIP_ENABLED` (default `1`)
- `PYKMS_GEOIP_PROVIDER` (default `ipapi.co`)
- `PYKMS_GEOIP_TIMEOUT_SECONDS` (default `2`)
- `PYKMS_GEOIP_CACHE_TTL_SECONDS` (default `604800`, 7 days)

## Notes
- If you use only `LOGFILE=STDOUT`, startup source-IP backfill has no log files to parse.
- For persistent sqlite/blacklist data, mount `/home/py-kms/db` as a Docker volume.
- Blacklist entries can be managed in the WebUI settings page.
- GeoIP lookup uses an external provider by default (`ipapi.co`). Public source IPs may be sent to that provider.


## License
- `py-kms` is released under [The Unlicense](./LICENSE)
