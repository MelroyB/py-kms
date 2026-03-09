#!/usr/bin/env python3

import ipaddress
import json
import os
from datetime import datetime, timezone


def get_blacklist_path():
    return os.environ.get('PYKMS_BLACKLIST_PATH', '/home/py-kms/db/pykms_blacklist.txt')


def get_blacklist_stats_path():
    return os.environ.get('PYKMS_BLACKLIST_STATS_PATH', '/home/py-kms/db/pykms_blacklist_stats.json')


def normalize_ip_text(ip_value):
    try:
        ip_obj = ipaddress.ip_address(str(ip_value))
    except ValueError:
        return str(ip_value)
    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped:
        return str(ip_obj.ipv4_mapped)
    return str(ip_obj)


def _ip_candidates(ip_value):
    ip_obj = ipaddress.ip_address(str(ip_value))
    candidates = [ip_obj]
    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped:
        candidates.append(ip_obj.ipv4_mapped)
    return candidates


def parse_blacklist_lines(lines):
    rules = []
    errors = []
    entries = []

    for index, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue

        # Allow comments at the end of a rule.
        line = line.split('#', 1)[0].strip()
        if not line:
            continue

        try:
            if '-' in line:
                start_raw, end_raw = [part.strip() for part in line.split('-', 1)]
                start_ip = ipaddress.ip_address(start_raw)
                end_ip = ipaddress.ip_address(end_raw)
                if start_ip.version != end_ip.version:
                    raise ValueError('range must use same IP version')
                if int(start_ip) > int(end_ip):
                    raise ValueError('range start must be <= range end')
                rules.append({
                    'type': 'range',
                    'start': start_ip,
                    'end': end_ip,
                    'entry': f'{start_ip}-{end_ip}'
                })
                entries.append(f'{start_ip}-{end_ip}')
            elif '/' in line:
                network = ipaddress.ip_network(line, strict = False)
                rules.append({
                    'type': 'network',
                    'network': network,
                    'entry': str(network)
                })
                entries.append(str(network))
            else:
                address = ipaddress.ip_address(line)
                rules.append({
                    'type': 'address',
                    'address': address,
                    'entry': str(address)
                })
                entries.append(str(address))
        except ValueError as error:
            errors.append(f'Line {index}: "{line}" ({error})')

    return rules, errors, entries


def parse_blacklist_text(text):
    return parse_blacklist_lines(text.splitlines())


def find_matching_rule(ip_value, rules):
    try:
        ip_candidates = _ip_candidates(ip_value)
    except ValueError:
        return None

    for rule in rules:
        for ip_obj in ip_candidates:
            if rule['type'] == 'address':
                if ip_obj == rule['address']:
                    return rule.get('entry')
            elif rule['type'] == 'network':
                if ip_obj in rule['network']:
                    return rule.get('entry')
            elif rule['type'] == 'range':
                if rule['start'].version != ip_obj.version:
                    continue
                ip_int = int(ip_obj)
                if int(rule['start']) <= ip_int <= int(rule['end']):
                    return rule.get('entry')
    return None


def is_ip_blocked(ip_value, rules):
    return find_matching_rule(ip_value, rules) is not None


def _stats_default():
    return {
        'version': 1,
        'updated_at': None,
        'total_blocked_attempts': 0,
        'by_rule': {},
        'by_source_ip': {}
    }


def load_blacklist_stats(path = None):
    stats_path = path or get_blacklist_stats_path()
    if not os.path.isfile(stats_path):
        return _stats_default()
    try:
        with open(stats_path, 'r') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _stats_default()
        base = _stats_default()
        base.update(data)
        if not isinstance(base.get('by_rule'), dict):
            base['by_rule'] = {}
        if not isinstance(base.get('by_source_ip'), dict):
            base['by_source_ip'] = {}
        if not isinstance(base.get('total_blocked_attempts'), int):
            base['total_blocked_attempts'] = 0
        return base
    except Exception:
        return _stats_default()


def record_blacklist_attempt(source_ip, matched_rule, path = None):
    stats_path = path or get_blacklist_stats_path()
    stats = load_blacklist_stats(stats_path)
    source_ip = normalize_ip_text(source_ip)
    matched_rule = matched_rule or 'unknown'

    stats['total_blocked_attempts'] += 1
    stats['by_source_ip'][source_ip] = int(stats['by_source_ip'].get(source_ip, 0)) + 1
    stats['by_rule'][matched_rule] = int(stats['by_rule'].get(matched_rule, 0)) + 1
    stats['updated_at'] = datetime.now(timezone.utc).isoformat()

    target_dir = os.path.dirname(stats_path)
    if target_dir:
        os.makedirs(target_dir, exist_ok = True)
    with open(stats_path, 'w') as f:
        json.dump(stats, f, indent = 2, sort_keys = True)
        f.write('\n')

    return stats
