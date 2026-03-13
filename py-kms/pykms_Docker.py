#!/usr/bin/env python3

import base64
import copy
import datetime
import http.client
import json
import os
import re
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request


DOCKER_SOCKET_PATH = os.environ.get('PYKMS_DOCKER_SOCKET_PATH', '/var/run/docker.sock')
DOCKER_API_VERSION = os.environ.get('PYKMS_DOCKER_API_VERSION', 'v1.43')
DOCKER_UPDATE_ENABLED = os.environ.get('PYKMS_DOCKER_UPDATE_ENABLED', '0').strip().lower() in ('1', 'true', 'yes', 'on')
DOCKER_UPDATE_CHECK_INTERVAL_SECONDS = max(60, int(os.environ.get('PYKMS_DOCKER_UPDATE_CHECK_INTERVAL_SECONDS', '21600')))
DOCKER_UPDATE_HELPER_DELAY_SECONDS = max(1, int(os.environ.get('PYKMS_DOCKER_UPDATE_HELPER_DELAY_SECONDS', '3')))

_status_cache = {
    'checked_at_ts': 0,
    'status': None
}
_status_lock = threading.Lock()


class DockerManagerError(Exception):
    pass


class UnixSocketHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path, timeout = 10):
        super().__init__('localhost', timeout = timeout)
        self.socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


def _docker_request(method, path, body = None, headers = None, timeout = 15):
    if not os.path.exists(DOCKER_SOCKET_PATH):
        raise DockerManagerError(f'Docker socket not found at {DOCKER_SOCKET_PATH}.')

    payload = body
    request_headers = {'Host': 'localhost'}
    if headers:
        request_headers.update(headers)

    if isinstance(body, (dict, list)):
        payload = json.dumps(body).encode('utf-8')
        request_headers.setdefault('Content-Type', 'application/json')
    elif isinstance(body, str):
        payload = body.encode('utf-8')

    conn = UnixSocketHTTPConnection(DOCKER_SOCKET_PATH, timeout = timeout)
    try:
        conn.request(method, f'/{DOCKER_API_VERSION}{path}', body = payload, headers = request_headers)
        response = conn.getresponse()
        raw = response.read()
    except PermissionError as e:
        raise DockerManagerError(f'Permission denied while talking to Docker socket {DOCKER_SOCKET_PATH}: {e}')
    except OSError as e:
        raise DockerManagerError(f'Failed to talk to Docker socket {DOCKER_SOCKET_PATH}: {e}')
    finally:
        conn.close()

    content_type = response.getheader('Content-Type', '')
    parsed = None
    if raw:
        if 'application/json' in content_type or raw[:1] in (b'{', b'['):
            try:
                parsed = json.loads(raw.decode('utf-8'))
            except Exception:
                parsed = raw.decode('utf-8', errors = 'replace')
        else:
            parsed = raw.decode('utf-8', errors = 'replace')

    if response.status >= 400:
        raise DockerManagerError(f'Docker API {method} {path} failed with HTTP {response.status}: {parsed}')
    return parsed, dict(response.getheaders())


def _registry_request(url, headers = None):
    request = urllib.request.Request(url, headers = headers or {})
    with urllib.request.urlopen(request, timeout = 15) as response:
        return response.read(), dict(response.info())


def _parse_www_authenticate(value):
    if not value:
        return {}
    parts = value.split(' ', 1)
    if len(parts) != 2:
        return {}
    params = {}
    for key, raw_value in re.findall(r'([a-zA-Z_]+)="([^"]*)"', parts[1]):
        params[key] = raw_value
    params['scheme'] = parts[0]
    return params


def _get_registry_headers(image_ref):
    parsed = parse_image_reference(image_ref)
    accept = 'application/vnd.docker.distribution.manifest.v2+json'
    manifest_url = f'https://{parsed["registry_api"]}/v2/{parsed["repository"]}/manifests/{parsed["reference"]}'
    headers = {'Accept': accept}
    request = urllib.request.Request(manifest_url, headers = headers)
    try:
        with urllib.request.urlopen(request, timeout = 15) as response:
            return dict(response.info())
    except urllib.error.HTTPError as e:
        if e.code != 401:
            raise
        auth_header = e.headers.get('WWW-Authenticate', '')
        auth_params = _parse_www_authenticate(auth_header)
        realm = auth_params.get('realm')
        if not realm:
            raise DockerManagerError(f'Unsupported registry auth flow for {image_ref}.')
        query = {}
        for key in ('service', 'scope'):
            if auth_params.get(key):
                query[key] = auth_params[key]
        token_url = realm
        if query:
            token_url += '?' + urllib.parse.urlencode(query)
        token_raw, _ = _registry_request(token_url)
        token_data = json.loads(token_raw.decode('utf-8'))
        token = token_data.get('token') or token_data.get('access_token')
        if not token:
            raise DockerManagerError(f'Could not fetch registry token for {image_ref}.')
        headers['Authorization'] = f'Bearer {token}'
        _, response_headers = _registry_request(manifest_url, headers = headers)
        return response_headers


def parse_image_reference(image_ref):
    ref = (image_ref or '').strip()
    if not ref:
        raise DockerManagerError('Docker image reference is empty.')
    if '@' in ref:
        name_part, digest = ref.split('@', 1)
        reference = digest
    else:
        name_part = ref
        digest = None
        reference = None

    registry = 'docker.io'
    remainder = name_part
    if '/' in name_part:
        first = name_part.split('/', 1)[0]
        if '.' in first or ':' in first or first == 'localhost':
            registry, remainder = name_part.split('/', 1)

    tag = None
    if digest is None:
        last_segment = remainder.rsplit('/', 1)[-1]
        if ':' in last_segment:
            remainder, tag = remainder.rsplit(':', 1)
        else:
            tag = 'latest'
        reference = tag

    repository = remainder
    if registry == 'docker.io' and '/' not in repository:
        repository = f'library/{repository}'

    registry_api = 'registry-1.docker.io' if registry == 'docker.io' else registry
    return {
        'original': ref,
        'registry': registry,
        'registry_api': registry_api,
        'repository': repository,
        'tag': tag,
        'digest': digest,
        'reference': reference
    }


def _match_repo_digest(repo_digests, image_ref):
    parsed = parse_image_reference(image_ref)
    expected_prefix = f'{parsed["registry"]}/{parsed["repository"]}@'
    fallback_prefix = f'{parsed["repository"]}@'
    for entry in repo_digests or []:
        if entry.startswith(expected_prefix) or entry.startswith(fallback_prefix):
            return entry.split('@', 1)[1]
    if repo_digests:
        return repo_digests[0].split('@', 1)[1]
    return None


def get_self_container_id():
    return (os.environ.get('HOSTNAME') or '').strip()


def inspect_self_container():
    container_id = get_self_container_id()
    if not container_id:
        raise DockerManagerError('Could not determine current container ID from HOSTNAME.')
    payload, _ = _docker_request('GET', f'/containers/{urllib.parse.quote(container_id, safe="")}/json')
    return payload


def inspect_image(image_ref):
    payload, _ = _docker_request('GET', f'/images/{urllib.parse.quote(image_ref, safe="")}/json')
    return payload


def get_remote_image_digest(image_ref):
    headers = _get_registry_headers(image_ref)
    lowered_headers = {str(key).lower(): value for key, value in headers.items()}
    digest = lowered_headers.get('docker-content-digest', '')
    if not digest:
        raise DockerManagerError(f'Registry did not return a manifest digest for {image_ref}.')
    return digest


def _extract_container_name(container_info):
    return (container_info.get('Name') or '').lstrip('/')


def _get_current_image_ref(container_info):
    return os.environ.get('PYKMS_DOCKER_IMAGE', '').strip() or (container_info.get('Config', {}) or {}).get('Image', '')


def _copy_present(source, target, keys):
    for key in keys:
        if key in source and source[key] not in (None, {}, [], ''):
            target[key] = copy.deepcopy(source[key])


def build_recreate_config(container_info, image_ref):
    config = container_info.get('Config', {}) or {}
    host_config = container_info.get('HostConfig', {}) or {}
    network_settings = container_info.get('NetworkSettings', {}) or {}

    create_payload = {'Image': image_ref}
    _copy_present(
        config,
        create_payload,
        ['Cmd', 'Entrypoint', 'Env', 'ExposedPorts', 'Hostname', 'Domainname', 'Labels', 'OpenStdin', 'StdinOnce', 'Tty', 'User', 'Volumes', 'WorkingDir']
    )

    labels = dict(create_payload.get('Labels', {}) or {})
    labels.pop('com.docker.compose.container-number', None)
    create_payload['Labels'] = labels

    host_payload = {}
    _copy_present(
        host_config,
        host_payload,
        [
            'AutoRemove', 'Binds', 'CapAdd', 'CapDrop', 'ConsoleSize', 'DeviceCgroupRules', 'DeviceRequests',
            'Devices', 'Dns', 'DnsOptions', 'DnsSearch', 'ExtraHosts', 'GroupAdd', 'Init', 'IpcMode',
            'Isolation', 'Links', 'LogConfig', 'MaskedPaths', 'Memory', 'MemoryReservation', 'MemorySwap',
            'NanoCpus', 'NetworkMode', 'OomKillDisable', 'PidMode', 'PidsLimit', 'PortBindings', 'Privileged',
            'PublishAllPorts', 'ReadonlyPaths', 'ReadonlyRootfs', 'RestartPolicy', 'SecurityOpt', 'ShmSize',
            'Tmpfs', 'UTSMode', 'Ulimits', 'UsernsMode', 'VolumesFrom'
        ]
    )
    if host_payload:
        create_payload['HostConfig'] = host_payload

    networks = network_settings.get('Networks') or {}
    endpoint_configs = {}
    for network_name, network_data in networks.items():
        endpoint = {}
        _copy_present(network_data, endpoint, ['Aliases', 'Links', 'NetworkID', 'EndpointID', 'Gateway', 'IPAddress', 'IPPrefixLen', 'IPv6Gateway', 'GlobalIPv6Address', 'GlobalIPv6PrefixLen', 'MacAddress'])
        endpoint.pop('NetworkID', None)
        endpoint.pop('EndpointID', None)
        endpoint.pop('Gateway', None)
        endpoint.pop('IPAddress', None)
        endpoint.pop('IPPrefixLen', None)
        endpoint.pop('IPv6Gateway', None)
        endpoint.pop('GlobalIPv6Address', None)
        endpoint.pop('GlobalIPv6PrefixLen', None)
        endpoint.pop('MacAddress', None)
        endpoint_configs[network_name] = endpoint
    if endpoint_configs:
        create_payload['NetworkingConfig'] = {'EndpointsConfig': endpoint_configs}

    return create_payload


def _update_status_error(message):
    return {
        'enabled': DOCKER_UPDATE_ENABLED,
        'supported': False,
        'can_update': False,
        'available': False,
        'reason': message,
        'current_image': '',
        'current_digest': '',
        'remote_digest': '',
        'checked_at': datetime.datetime.utcnow().isoformat(),
        'container_name': '',
        'socket_path': DOCKER_SOCKET_PATH
    }


def get_docker_update_status(force = False):
    now_ts = int(datetime.datetime.utcnow().timestamp())
    with _status_lock:
        if (not force) and _status_cache['status'] and now_ts - _status_cache['checked_at_ts'] < DOCKER_UPDATE_CHECK_INTERVAL_SECONDS:
            return copy.deepcopy(_status_cache['status'])

    if not DOCKER_UPDATE_ENABLED:
        status = _update_status_error('Docker update management is disabled. Set PYKMS_DOCKER_UPDATE_ENABLED=1 and mount the Docker socket.')
    elif not os.path.exists(DOCKER_SOCKET_PATH):
        status = _update_status_error(f'Docker socket is not mounted at {DOCKER_SOCKET_PATH}.')
    else:
        try:
            container_info = inspect_self_container()
            container_name = _extract_container_name(container_info)
            image_ref = _get_current_image_ref(container_info)
            image_info = inspect_image(image_ref)
            local_digest = _match_repo_digest(image_info.get('RepoDigests', []), image_ref)
            remote_digest = get_remote_image_digest(image_ref)
            parsed_ref = parse_image_reference(image_ref)
            available = bool(local_digest and remote_digest and local_digest != remote_digest)
            status = {
                'enabled': True,
                'supported': parsed_ref.get('digest') is None,
                'can_update': parsed_ref.get('digest') is None,
                'available': available,
                'reason': 'Update available.' if available else 'Already on the latest available image digest.',
                'current_image': image_ref,
                'current_digest': local_digest or '',
                'remote_digest': remote_digest or '',
                'checked_at': datetime.datetime.utcnow().isoformat(),
                'container_name': container_name,
                'socket_path': DOCKER_SOCKET_PATH
            }
            if parsed_ref.get('digest') is not None:
                status['supported'] = False
                status['can_update'] = False
                status['reason'] = 'Digest-pinned images are not auto-updated by this UI.'
        except Exception as e:
            status = _update_status_error(str(e))

    with _status_lock:
        _status_cache['checked_at_ts'] = now_ts
        _status_cache['status'] = copy.deepcopy(status)
    return status


def pull_image(image_ref):
    parsed = parse_image_reference(image_ref)
    if parsed.get('digest'):
        return
    query = urllib.parse.urlencode({'fromImage': f'{parsed["registry"]}/{parsed["repository"]}' if parsed['registry'] != 'docker.io' else parsed['repository'], 'tag': parsed['tag']})
    _docker_request('POST', f'/images/create?{query}', timeout = 120)


def _start_helper_container(helper_image, helper_env):
    helper_name = f'pykms-updater-{datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")}'
    binds = [f'{DOCKER_SOCKET_PATH}:{DOCKER_SOCKET_PATH}']
    payload = {
        'Image': helper_image,
        'Cmd': ['python3', '-u', '/home/py-kms/pykms_DockerUpdater.py'],
        'Env': helper_env,
        'Labels': {'com.melroy.pykms.role': 'updater-helper'},
        'HostConfig': {
            'AutoRemove': True,
            'Binds': binds,
            'NetworkMode': 'none'
        }
    }
    created, _ = _docker_request('POST', f'/containers/create?name={urllib.parse.quote(helper_name, safe="")}', body = payload)
    helper_id = created.get('Id')
    if not helper_id:
        raise DockerManagerError('Failed to create Docker updater helper container.')
    _docker_request('POST', f'/containers/{urllib.parse.quote(helper_id, safe="")}/start')
    return helper_id


def request_update_job():
    status = get_docker_update_status(force = True)
    if not status.get('enabled'):
        raise DockerManagerError(status.get('reason', 'Docker update management is disabled.'))
    if not status.get('can_update'):
        raise DockerManagerError(status.get('reason', 'Docker update is not available in this configuration.'))
    if not status.get('available'):
        raise DockerManagerError(status.get('reason', 'No newer Docker image digest is available right now.'))

    container_info = inspect_self_container()
    helper_image = _get_current_image_ref(container_info)
    job_config = {
        'delay_seconds': DOCKER_UPDATE_HELPER_DELAY_SECONDS,
        'target_container_id': get_self_container_id(),
        'target_container_name': _extract_container_name(container_info),
        'target_image': status.get('current_image') or helper_image,
        'create_payload': build_recreate_config(container_info, status.get('current_image') or helper_image)
    }
    encoded = base64.b64encode(json.dumps(job_config).encode('utf-8')).decode('ascii')
    helper_id = _start_helper_container(helper_image, [f'PYKMS_DOCKER_UPDATE_CONFIG_B64={encoded}', f'PYKMS_DOCKER_SOCKET_PATH={DOCKER_SOCKET_PATH}', f'PYKMS_DOCKER_API_VERSION={DOCKER_API_VERSION}'])
    return {'helper_container_id': helper_id, 'job': job_config}
