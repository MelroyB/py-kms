import os, uuid, datetime, hmac
from flask import Flask, render_template, request, redirect, url_for, session
from pykms_Sql import sql_get_all, sql_delete
from pykms_DB2Dict import kmsDB2Dict
from pykms_Blacklist import get_blacklist_path, parse_blacklist_text, normalize_ip_text, is_ip_blocked, load_blacklist_stats

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
app.jinja_env.globals['webui_auth_enabled'] = _webui_auth_enabled
app.jinja_env.globals['webui_auth_user'] = _webui_auth_username
app.jinja_env.globals['blacklist_path'] = get_blacklist_path()

_version_info_path = os.environ.get('PYKMS_VERSION_PATH', '../VERSION')
if os.path.exists(_version_info_path):
    with open(_version_info_path, 'r') as f:
        app.jinja_env.globals['version_info'] = {
            'hash': f.readline().strip(),
            'branch': f.readline().strip()
        }

_dbEnvVarName = 'PYKMS_SQLITE_DB_PATH'
def _env_check():
    if _dbEnvVarName not in os.environ:
        raise Exception(f'Environment variable is not set: {_dbEnvVarName}')

def _is_safe_next_url(path):
    return isinstance(path, str) and path.startswith('/') and not path.startswith('//')

def _set_ui_message(level, message):
    session['ui_message_level'] = level
    session['ui_message'] = message

@app.before_request
def _protect_webui():
    if not _webui_auth_enabled:
        return None
    public_endpoints = {'readyz', 'livez', 'login', 'logout', 'static'}
    if request.endpoint is None:
        return None
    if request.endpoint in public_endpoints:
        return None
    if session.get('pykms_webui_auth') is True:
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
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        if hmac.compare_digest(username, _webui_auth_username) and hmac.compare_digest(password, _webui_auth_password):
            session.clear()
            session['pykms_webui_auth'] = True
            session['pykms_webui_user'] = _webui_auth_username
            return redirect(next_url)
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
    
