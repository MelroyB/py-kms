#!/usr/bin/env python3

import ipaddress
import os


def get_blacklist_path():
    return os.environ.get('PYKMS_BLACKLIST_PATH', '/home/py-kms/db/pykms_blacklist.txt')


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
                    'end': end_ip
                })
                entries.append(f'{start_ip}-{end_ip}')
            elif '/' in line:
                network = ipaddress.ip_network(line, strict = False)
                rules.append({
                    'type': 'network',
                    'network': network
                })
                entries.append(str(network))
            else:
                address = ipaddress.ip_address(line)
                rules.append({
                    'type': 'address',
                    'address': address
                })
                entries.append(str(address))
        except ValueError as error:
            errors.append(f'Line {index}: "{line}" ({error})')

    return rules, errors, entries


def parse_blacklist_text(text):
    return parse_blacklist_lines(text.splitlines())


def is_ip_blocked(ip_value, rules):
    try:
        ip_obj = ipaddress.ip_address(ip_value)
    except ValueError:
        return False

    for rule in rules:
        if rule['type'] == 'address':
            if ip_obj == rule['address']:
                return True
        elif rule['type'] == 'network':
            if ip_obj in rule['network']:
                return True
        elif rule['type'] == 'range':
            if rule['start'].version != ip_obj.version:
                continue
            ip_int = int(ip_obj)
            if int(rule['start']) <= ip_int <= int(rule['end']):
                return True
    return False
