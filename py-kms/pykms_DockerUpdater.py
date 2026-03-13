#!/usr/bin/env python3

import base64
import json
import os
import sys
import time
import urllib.parse

from pykms_Docker import DockerManagerError, _docker_request, pull_image


def _load_config():
    raw = os.environ.get('PYKMS_DOCKER_UPDATE_CONFIG_B64', '').strip()
    if not raw:
        raise DockerManagerError('Missing PYKMS_DOCKER_UPDATE_CONFIG_B64 for updater helper.')
    return json.loads(base64.b64decode(raw).decode('utf-8'))


def _remove_container(container_id):
    try:
        _docker_request('DELETE', f'/containers/{urllib.parse.quote(container_id, safe="")}?force=1', timeout = 30)
    except DockerManagerError as e:
        if 'HTTP 404' not in str(e):
            raise


def main():
    config = _load_config()
    delay_seconds = max(1, int(config.get('delay_seconds', 3)))
    time.sleep(delay_seconds)

    target_image = config['target_image']
    pull_image(target_image)

    target_container_id = config['target_container_id']
    target_container_name = config['target_container_name']
    create_payload = config['create_payload']

    try:
        _docker_request('POST', f'/containers/{urllib.parse.quote(target_container_id, safe="")}/stop?t=10', timeout = 30)
    except DockerManagerError as e:
        if 'HTTP 304' not in str(e) and 'HTTP 404' not in str(e):
            raise

    _remove_container(target_container_id)

    created, _ = _docker_request(
        'POST',
        f'/containers/create?name={urllib.parse.quote(target_container_name, safe="")}',
        body = create_payload,
        timeout = 30
    )
    new_container_id = created.get('Id')
    if not new_container_id:
        raise DockerManagerError('Failed to create replacement container.')
    _docker_request('POST', f'/containers/{urllib.parse.quote(new_container_id, safe="")}/start', timeout = 30)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        print(f'pykms_DockerUpdater failed: {e}', file = sys.stderr)
        sys.exit(1)
