import os, uuid, datetime, hmac, threading
from flask import Flask, render_template, request, redirect, url_for, session
from pykms_Sql import sql_get_all, sql_delete, sql_geoip_get_cached, sql_geoip_upsert
from pykms_DB2Dict import kmsDB2Dict
from pykms_Blacklist import get_blacklist_path, parse_blacklist_text, normalize_ip_text, is_ip_blocked, load_blacklist_stats
from pykms_GeoIP import lookup_country, country_display, UNKNOWN_COUNTRY

def _random_uuid():
    return str(uuid.uuid4()).replace('-', '_')

_serve_count = 0
def _increase_serve_count():
    global _serve_count
    _serve_count += 1

def _get_serve_count():
    return _serve_count

_kms_items = None
_kms_items_noglvk = None
def _get_kms_items_cache():
    global _kms_items, _kms_items_noglvk
    if _kms_items is None:
        _kms_items = {} # {group: str -> {product: str -> gvlk: str}}
        _kms_items_noglvk = 0
        for section in kmsDB2Dict():
            for element in section:
                if "KmsItems" in element:
                    for product in element["KmsItems"]:
                        group_name = product["DisplayName"]
                        items = {}
                        for item in product["SkuItems"]:
                            items[item["DisplayName"]] = item["Gvlk"]
                            if not item["Gvlk"]:
                                _kms_items_noglvk += 1
                        if len(items) == 0:
                            continue
                        if group_name not in _kms_items:
                            _kms_items[group_name] = {}
                        _kms_items[group_name].update(items)
                elif "DisplayName" in element and "BuildNumber" in element and "PlatformId" in element:
                    pass # these are WinBuilds
                elif "DisplayName" in element and "Activate" in element:
                    pass # these are CsvlkItems
                else:
                    raise NotImplementedError(f'Unknown element: {element}')
    return _kms_items, _kms_items_noglvk

app = Flask('pykms_webui')
app.jinja_env.globals['start_time'] = datetime.datetime.now()
app.jinja_env.globals['get_serve_count'] = _get_serve_count
app.jinja_env.globals['random_uuid'] = _random_uuid
app.jinja_env.globals['version_info'] = None

_webui_auth_password = os.environ.get('PYKMS_WEBUI_PASSWORD', '')
_webui_auth_username = os.environ.get('PYKMS_WEBUI_USERNAME', 'admin')
_webui_auth_enabled = bool(_webui_auth_password)
if _webui_auth_enabled:
    app.secret_key = os.environ.get('PYKMS_WEBUI_SECRET_KEY') or uuid.uuid5(uuid.NAMESPACE_OID, _webui_auth_password).hex
_webui_default_password_values = {'change-me', 'changeme', 'admin', 'password', 'py-kms'}
_webui_uses_default_password = _webui_auth_password.strip().lower() in _webui_default_password_values

_cookie_secure_env = os.environ.get('PYKMS_WEBUI_COOKIE_SECURE', 'false').strip().lower()
app.config.update(
    SESSION_COOKIE_HTTPONLY = True,
    SESSION_COOKIE_SAMESITE = os.environ.get('PYKMS_WEBUI_COOKIE_SAMESITE', 'Lax'),
    SESSION_COOKIE_SECURE = _cookie_secure_env in ('1', 'true', 'yes', 'on'),
    PERMANENT_SESSION_LIFETIME = datetime.timedelta(
        seconds = max(60, int(os.environ.get('PYKMS_WEBUI_SESSION_TTL_SECONDS', '43200')))
    )
)

_login_rate_limit_attempts = max(1, int(os.environ.get('PYKMS_WEBUI_LOGIN_RATE_LIMIT_ATTEMPTS', '5')))
_login_rate_limit_window_seconds = max(10, int(os.environ.get('PYKMS_WEBUI_LOGIN_RATE_LIMIT_WINDOW_SECONDS', '300')))
_login_rate_limit_block_seconds = max(10, int(os.environ.get('PYKMS_WEBUI_LOGIN_RATE_LIMIT_BLOCK_SECONDS', '900')))
_login_attempt_state = {} # {ip: {'attempts': [datetime], 'blocked_until': datetime|None}}
_login_attempt_state_lock = threading.Lock()

app.jinja_env.globals['webui_auth_enabled'] = _webui_auth_enabled
app.jinja_env.globals['webui_auth_user'] = _webui_auth_username
app.jinja_env.globals['webui_uses_default_password'] = _webui_uses_default_password
app.jinja_env.globals['blacklist_path'] = get_blacklist_path()

_version_info_path = os.environ.get('PYKMS_VERSION_PATH', '../VERSION')
if os.path.exists(_version_info_path):
    with open(_version_info_path, 'r') as f:
        app.jinja_env.globals['version_info'] = {
            'hash': f.readline().strip(),
            'branch': f.readline().strip()
        }

_dbEnvVarName = 'PYKMS_SQLITE_DB_PATH'
_geoip_enabled = os.environ.get('PYKMS_GEOIP_ENABLED', '1').strip().lower() in ('1', 'true', 'yes', 'on')
_geoip_provider = os.environ.get('PYKMS_GEOIP_PROVIDER', 'ipapi.co').strip().lower()
_geoip_timeout_seconds = max(1, int(os.environ.get('PYKMS_GEOIP_TIMEOUT_SECONDS', '2')))
_geoip_cache_ttl_seconds = max(60, int(os.environ.get('PYKMS_GEOIP_CACHE_TTL_SECONDS', '604800')))

def _env_check():
    if _dbEnvVarName not in os.environ:
        raise Exception(f'Environment variable is not set: {_dbEnvVarName}')

def _is_safe_next_url(path):
    return isinstance(path, str) and path.startswith('/') and not path.startswith('//')

def _set_ui_message(level, message):
    session['ui_message_level'] = level
    session['ui_message'] = message

def _resolve_country_for_ip(db_path, ip_value, now_ts):
    if not _geoip_enabled:
        return dict(UNKNOWN_COUNTRY)

    normalized_ip = normalize_ip_text(ip_value)
    if not normalized_ip:
        return dict(UNKNOWN_COUNTRY)

    cached = sql_geoip_get_cached(db_path, normalized_ip)
    if cached and (now_ts - int(cached.get('updatedAt') or 0) < _geoip_cache_ttl_seconds):
        return {
            'countryCode': cached.get('countryCode', ''),
            'countryName': cached.get('countryName', 'Unknown') or 'Unknown'
        }

    resolved = lookup_country(
        normalized_ip,
        provider = _geoip_provider,
        timeout_seconds = _geoip_timeout_seconds
    )
    sql_geoip_upsert(
        db_path,
        normalized_ip,
        resolved.get('countryCode', ''),
        resolved.get('countryName', 'Unknown'),
        now_ts
    )
    return resolved

def _get_client_ip():
    return normalize_ip_text(request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip())

def _ensure_csrf_token():
    token = session.get('pykms_csrf_token')
    if not token:
        token = uuid.uuid4().hex
        session['pykms_csrf_token'] = token
    return token

def _validate_csrf_token():
    session_token = session.get('pykms_csrf_token', '')
    request_token = request.form.get('csrf_token', '')
    return bool(session_token) and bool(request_token) and hmac.compare_digest(session_token, request_token)

def _prune_login_state(now_utc):
    stale_before = now_utc - datetime.timedelta(seconds = _login_rate_limit_window_seconds * 4)
    stale_ips = []
    for ip, state in _login_attempt_state.items():
        state['attempts'] = [ts for ts in state['attempts'] if ts >= stale_before]
        if state['blocked_until'] and state['blocked_until'] <= now_utc and not state['attempts']:
            stale_ips.append(ip)
    for ip in stale_ips:
        _login_attempt_state.pop(ip, None)

def _is_login_rate_limited(ip):
    now_utc = datetime.datetime.utcnow()
    with _login_attempt_state_lock:
        _prune_login_state(now_utc)
        state = _login_attempt_state.get(ip)
        if state and state['blocked_until'] and state['blocked_until'] > now_utc:
            return True
        return False

def _record_login_failure(ip):
    now_utc = datetime.datetime.utcnow()
    with _login_attempt_state_lock:
        _prune_login_state(now_utc)
        state = _login_attempt_state.setdefault(ip, {'attempts': [], 'blocked_until': None})
        state['attempts'] = [ts for ts in state['attempts'] if ts >= now_utc - datetime.timedelta(seconds = _login_rate_limit_window_seconds)]
        state['attempts'].append(now_utc)
        if len(state['attempts']) >= _login_rate_limit_attempts:
            state['blocked_until'] = now_utc + datetime.timedelta(seconds = _login_rate_limit_block_seconds)
            state['attempts'].clear()

def _clear_login_failures(ip):
    with _login_attempt_state_lock:
        _login_attempt_state.pop(ip, None)

@app.context_processor
def _inject_csrf_token():
    return {'csrf_token': _ensure_csrf_token()}

@app.before_request
def _protect_webui():
    public_endpoints = {'readyz', 'livez', 'login', 'static'}
    if request.endpoint is None:
        return None

    if not _webui_auth_enabled:
        return None

    if request.endpoint in public_endpoints:
        return None
    if session.get('pykms_webui_auth') is True:
        csrf_protected_endpoints = {'logout', 'settings', 'clients_action'}
        if request.method == 'POST' and request.endpoint in csrf_protected_endpoints:
            if not _validate_csrf_token():
                return 'Invalid CSRF token.', 400
        return None
    return redirect(url_for('login', next = request.path))

@app.route('/login', methods = ['GET', 'POST'])
def login():
    if not _webui_auth_enabled:
        return redirect(url_for('root'))

    error = None
    next_url = request.values.get('next', '/')
    if not _is_safe_next_url(next_url):
        next_url = '/'
    if session.get('pykms_webui_auth') is True:
        return redirect(next_url)

    if request.method == 'POST':
        client_ip = _get_client_ip()
        if _is_login_rate_limited(client_ip):
            error = 'Too many failed login attempts. Try again later.'
            return render_template(
                'login.html',
                path='/login/',
                error=error,
                next_url=next_url
            ), 429

        username = request.form.get('username', '')
        password = request.form.get('password', '')
        if hmac.compare_digest(username, _webui_auth_username) and hmac.compare_digest(password, _webui_auth_password):
            session.clear()
            session['pykms_webui_auth'] = True
            session['pykms_webui_user'] = _webui_auth_username
            session['pykms_csrf_token'] = uuid.uuid4().hex
            _clear_login_failures(client_ip)
            return redirect(next_url)
        _record_login_failure(client_ip)
        error = 'Invalid username or password.'

    return render_template(
        'login.html',
        path='/login/',
        error=error,
        next_url=next_url
    )

@app.route('/logout', methods = ['POST'])
def logout():
    session.clear()
    if _webui_auth_enabled:
        return redirect(url_for('login'))
    return redirect(url_for('root'))

@app.route('/settings', methods = ['GET', 'POST'])
def settings():
    _increase_serve_count()
    blacklist_path = get_blacklist_path()
    error = None
    success = None
    entries_text = ''
    active_entries = []
    parse_errors = []

    if os.path.isfile(blacklist_path):
        with open(blacklist_path, 'r') as f:
            existing_text = f.read()
        _, _, active_entries = parse_blacklist_text(existing_text)
        entries_text = '\n'.join(active_entries)

    if request.method == 'POST':
        submitted_text = request.form.get('blacklist_entries', '')
        _, errors, entries = parse_blacklist_text(submitted_text)
        active_entries = entries
        if errors:
            error = 'Invalid entries detected. Please fix the lines shown below.'
            parse_errors = errors
            entries_text = submitted_text
        else:
            try:
                target_dir = os.path.dirname(blacklist_path)
                if target_dir:
                    os.makedirs(target_dir, exist_ok = True)
                with open(blacklist_path, 'w') as f:
                    payload = '\n'.join(entries)
                    if payload:
                        payload += '\n'
                    f.write(payload)
                entries_text = '\n'.join(entries)
                success = f'Blacklist saved. {len(entries)} rule(s) active.'
            except Exception as e:
                error = f'Failed to save blacklist: {e}'

    blacklist_stats = load_blacklist_stats()
    stats_by_rule = sorted(blacklist_stats.get('by_rule', {}).items(), key = lambda kv: kv[1], reverse = True)
    stats_by_source_ip = sorted(blacklist_stats.get('by_source_ip', {}).items(), key = lambda kv: kv[1], reverse = True)

    return render_template(
        'settings.html',
        path='/settings/',
        blacklist_path=blacklist_path,
        blacklist_entries=entries_text,
        blacklist_count=len(active_entries),
        blacklist_stats=blacklist_stats,
        stats_by_rule=stats_by_rule,
        stats_by_source_ip=stats_by_source_ip,
        error=error,
        success=success,
        parse_errors=parse_errors
    )

@app.route('/clients/action', methods = ['POST'])
def clients_action():
    _increase_serve_count()
    dbPath = os.environ.get(_dbEnvVarName)
    if not dbPath:
        _set_ui_message('danger', f'Action failed: environment variable missing ({_dbEnvVarName}).')
        return redirect(url_for('root'))

    action = request.form.get('action', '').strip().lower()
    clientMachineId = request.form.get('clientMachineId', '').strip()
    appId = request.form.get('appId', '').strip()
    sourceIp = request.form.get('sourceIp', '').strip()

    if action not in ['delete', 'block']:
        _set_ui_message('danger', 'Action failed: invalid action.')
        return redirect(url_for('root'))
    if not clientMachineId or not appId:
        _set_ui_message('danger', 'Action failed: missing client identifiers.')
        return redirect(url_for('root'))

    if action == 'block':
        normalized_source_ip = normalize_ip_text(sourceIp)
        _, parse_errors_ip, parsed_ip_entries = parse_blacklist_text(normalized_source_ip)
        if (not normalized_source_ip) or parse_errors_ip or len(parsed_ip_entries) == 0:
            _set_ui_message('danger', 'Block failed: client source IP is missing or invalid.')
            return redirect(url_for('root'))

        blacklist_path = get_blacklist_path()
        existing_text = ''
        if os.path.isfile(blacklist_path):
            with open(blacklist_path, 'r') as f:
                existing_text = f.read()
        existing_rules, parse_errors_existing, existing_entries = parse_blacklist_text(existing_text)
        if parse_errors_existing:
            _set_ui_message('danger', 'Block failed: current blacklist file contains invalid rules. Fix settings first.')
            return redirect(url_for('root'))

        if not is_ip_blocked(normalized_source_ip, existing_rules):
            existing_entries.append(normalized_source_ip)
        try:
            target_dir = os.path.dirname(blacklist_path)
            if target_dir:
                os.makedirs(target_dir, exist_ok = True)
            with open(blacklist_path, 'w') as f:
                payload = '\n'.join(existing_entries)
                if payload:
                    payload += '\n'
                f.write(payload)
        except Exception as e:
            _set_ui_message('danger', f'Block failed while saving blacklist: {e}')
            return redirect(url_for('root'))

    deleted_rows = sql_delete(dbPath, clientMachineId, appId)
    if deleted_rows and action == 'block':
        _set_ui_message('success', f'Blocked {sourceIp} and deleted client entry.')
    elif deleted_rows and action == 'delete':
        _set_ui_message('success', 'Client entry deleted.')
    elif action == 'block':
        _set_ui_message('warning', f'Blocked {sourceIp}, but client entry was not found in sqlite.')
    else:
        _set_ui_message('warning', 'Client entry was not found in sqlite.')

    return redirect(url_for('root'))

@app.route('/')
def root():
    _increase_serve_count()
    error = None
    # Get the db name / path
    dbPath = None
    if _dbEnvVarName in os.environ:
        dbPath = os.environ.get(_dbEnvVarName)
    else:
        error = f'Environment variable is not set: {_dbEnvVarName}'
    # Fetch all clients from the database.
    clients = None
    try:
        if dbPath:
            clients = sql_get_all(dbPath)
            now_ts = int(datetime.datetime.utcnow().timestamp())
            country_by_ip = {}
            if clients:
                for client in clients:
                    source_ip = client.get('sourceIp', '')
                    if source_ip not in country_by_ip:
                        country_by_ip[source_ip] = _resolve_country_for_ip(dbPath, source_ip, now_ts)
                    country_data = country_by_ip[source_ip]
                    client['countryCode'] = country_data.get('countryCode', '')
                    client['countryName'] = country_data.get('countryName', 'Unknown')
                    client['countryDisplay'] = country_display(client['countryCode'], client['countryName'])
    except Exception as e:
        error = f'Error while loading database: {e}'
    countClients = len(clients) if clients else 0
    countClientsWindows = len([c for c in clients if c['applicationId'] == 'Windows']) if clients else 0
    countClientsOffice = countClients - countClientsWindows
    ui_message = session.pop('ui_message', None)
    ui_message_level = session.pop('ui_message_level', 'success')
    return render_template(
        'clients.html',
        path='/',
        error=error,
        clients=clients,
        ui_message=ui_message,
        ui_message_level=ui_message_level,
        count_clients=countClients,
        count_clients_windows=countClientsWindows,
        count_clients_office=countClientsOffice,
        count_projects=sum([len(entries) for entries in _get_kms_items_cache()[0].values()])
    ), 200 if error is None else 500

@app.route('/readyz')
def readyz():
    try:
        _env_check()
    except Exception as e:
        return f'Whooops! {e}', 503
    if (datetime.datetime.now() - app.jinja_env.globals['start_time']).seconds > 10: # Wait 10 seconds before accepting requests
        return 'OK', 200
    else:
        return 'Not ready', 503

@app.route('/livez')
def livez():
    try:
        _env_check()
        return 'OK', 200 # There are no checks for liveness, so we just return OK
    except Exception as e:
        return f'Whooops! {e}', 503

@app.route('/license')
def license():
    _increase_serve_count()
    with open(os.environ.get('PYKMS_LICENSE_PATH', '../LICENSE'), 'r') as f:
        return render_template(
            'license.html',
            path='/license/',
            license=f.read()
        )

@app.route('/products')
def products():
    _increase_serve_count()
    items, noglvk = _get_kms_items_cache()
    countProducts = sum([len(entries) for entries in items.values()])
    countProductsWindows = sum([len(entries) for (name, entries) in items.items() if 'windows' in name.lower()])
    countProductsOffice = sum([len(entries) for (name, entries) in items.items() if 'office' in name.lower()])
    return render_template(
        'products.html',
        path='/products/',
        products=items,
        filtered=noglvk,
        count_products=countProducts,
        count_products_windows=countProductsWindows,
        count_products_office=countProductsOffice
    )
    
