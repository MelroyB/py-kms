#!/usr/bin/env python3

import argparse
import os
import re
import sqlite3
from typing import Dict, Iterable, List, Optional, Tuple


CLIENT_ID_RE = re.compile(r"Client Machine ID:\s*([0-9a-fA-F-]{36})\s*$")
APP_ID_RE = re.compile(r"Application ID:\s*(.+?)\s*$")
CONN_ACCEPTED_RE = re.compile(r"Connection accepted:\s*(.+?)\s*$")
CONN_CLOSED_RE = re.compile(r"Connection closed:\s*(.+?)\s*$")


def parse_ip_from_hostport(hostport: str) -> Optional[str]:
    # Log format is "<ip>:<port>" for IPv4 and "<ipv6>:<port>" for IPv6.
    match = re.match(r"^(.+):(\d+)$", hostport.strip())
    if not match:
        return None
    return match.group(1).strip()


def extract_ip_map(log_paths: Iterable[str]) -> Dict[Tuple[str, str], str]:
    ip_map: Dict[Tuple[str, str], str] = {}
    current_ip: Optional[str] = None
    pending_cmid: Optional[str] = None

    for path in log_paths:
        with open(path, "r", encoding="utf-8", errors="replace") as log_file:
            for line in log_file:
                accepted = CONN_ACCEPTED_RE.search(line)
                if accepted:
                    current_ip = parse_ip_from_hostport(accepted.group(1))
                    pending_cmid = None
                    continue

                if CONN_CLOSED_RE.search(line):
                    current_ip = None
                    pending_cmid = None
                    continue

                cmid = CLIENT_ID_RE.search(line)
                if cmid:
                    pending_cmid = cmid.group(1).lower()
                    continue

                app = APP_ID_RE.search(line)
                if app and pending_cmid and current_ip:
                    app_name = app.group(1).strip()
                    ip_map[(pending_cmid, app_name)] = current_ip
                    pending_cmid = None

    return ip_map


def ensure_source_ip_column(cur: sqlite3.Cursor) -> None:
    cur.execute("PRAGMA table_info(clients)")
    columns = {row[1] for row in cur.fetchall()}
    if "sourceIp" not in columns:
        cur.execute("ALTER TABLE clients ADD COLUMN sourceIp TEXT")


def backfill_db(db_path: str, ip_map: Dict[Tuple[str, str], str], dry_run: bool = False) -> Tuple[int, int, int]:
    updated = 0
    skipped = 0
    missing_in_logs = 0

    with sqlite3.connect(db_path) as con:
        cur = con.cursor()
        ensure_source_ip_column(cur)
        cur.execute("SELECT clientMachineId, applicationId, sourceIp FROM clients")
        rows = cur.fetchall()

        for cmid, app_id, source_ip in rows:
            if source_ip:
                skipped += 1
                continue

            key = (str(cmid).lower(), str(app_id))
            if key not in ip_map:
                missing_in_logs += 1
                continue

            if not dry_run:
                cur.execute(
                    "UPDATE clients SET sourceIp=? WHERE clientMachineId=? AND applicationId=?",
                    (ip_map[key], cmid, app_id),
                )
            updated += 1

        if not dry_run:
            con.commit()

    return updated, skipped, missing_in_logs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill clients.sourceIp in pykms sqlite database from pykms server logs."
    )
    parser.add_argument(
        "-d",
        "--db",
        required=True,
        help="Path to pykms sqlite database (e.g. ./pykms_database.db).",
    )
    parser.add_argument(
        "-l",
        "--logs",
        required=True,
        nargs="+",
        help="One or more log files in chronological order (oldest -> newest).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be updated without changing the database.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not os.path.isfile(args.db):
        print(f"Database file not found: {args.db}")
        return 1

    missing_logs: List[str] = [path for path in args.logs if not os.path.isfile(path)]
    if missing_logs:
        print("Missing log file(s):")
        for path in missing_logs:
            print(f"- {path}")
        return 1

    ip_map = extract_ip_map(args.logs)
    updated, skipped, missing_in_logs = backfill_db(args.db, ip_map, dry_run=args.dry_run)

    mode = "DRY-RUN" if args.dry_run else "APPLIED"
    print(f"[{mode}] log mappings found: {len(ip_map)}")
    print(f"[{mode}] rows updated: {updated}")
    print(f"[{mode}] rows already had sourceIp: {skipped}")
    print(f"[{mode}] rows without log match: {missing_in_logs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
