#!/usr/bin/env python3

import ipaddress
import json
import urllib.error
import urllib.request

from pykms_Blacklist import normalize_ip_text

UNKNOWN_COUNTRY = {
    'countryCode': '',
    'countryName': 'Unknown',
    'status': 'unknown'
}

def _country_code_to_flag(country_code):
    if not country_code or len(country_code) != 2 or not country_code.isalpha():
        return '🏳️'
    base = 127397
    return chr(base + ord(country_code[0].upper())) + chr(base + ord(country_code[1].upper()))

def country_display(country_code, country_name):
    normalized_name = (country_name or '').strip() or 'Unknown'
    return f"{_country_code_to_flag(country_code)} {normalized_name}"

def _is_lookup_candidate(ip_text):
    if not ip_text:
        return False
    try:
        parsed = ipaddress.ip_address(ip_text)
    except ValueError:
        return False
    # Only query internet-routable addresses.
    return parsed.is_global

def lookup_country(ip_text, provider = 'ipapi.co', timeout_seconds = 2):
    normalized_ip = normalize_ip_text(ip_text)
    if not _is_lookup_candidate(normalized_ip):
        data = dict(UNKNOWN_COUNTRY)
        data['status'] = 'skipped'
        return data

    provider_name = (provider or 'ipapi.co').strip().lower()
    if provider_name != 'ipapi.co':
        data = dict(UNKNOWN_COUNTRY)
        data['status'] = 'skipped'
        return data

    url = f"https://ipapi.co/{normalized_ip}/json/"
    request = urllib.request.Request(url, headers = {'User-Agent': 'py-kms-webui/geoip'})
    try:
        with urllib.request.urlopen(request, timeout = max(1, float(timeout_seconds))) as response:
            payload = json.loads(response.read().decode('utf-8', errors='ignore'))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        data = dict(UNKNOWN_COUNTRY)
        data['status'] = 'error'
        return data

    country_code = str(payload.get('country_code') or '').strip().upper()
    country_name = str(payload.get('country_name') or '').strip()
    if not country_code and not country_name:
        data = dict(UNKNOWN_COUNTRY)
        data['status'] = 'error'
        return data
    return {
        'countryCode': country_code,
        'countryName': country_name or 'Unknown',
        'status': 'success'
    }
