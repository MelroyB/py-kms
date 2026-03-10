#!/usr/bin/python3 -u

# This replaces the old start.sh and ensures all arguments are bound correctly from the environment variables...
import glob
import logging
import os
import subprocess
import sys
import time

PYTHON3 = '/usr/bin/python3'
argumentVariableMapping = {
  '-l': 'LCID',
  '-c': 'CLIENT_COUNT',
  '-a': 'ACTIVATION_INTERVAL',
  '-r': 'RENEWAL_INTERVAL',
  '-w': 'HWID',
  '-V': 'LOGLEVEL',
  '-F': 'LOGFILE',
  '-S': 'LOGSIZE',
  '-e': 'EPID'
}

db_path = os.path.join(os.sep, 'home', 'py-kms', 'db', 'pykms_database.db')
blacklist_path = os.environ.get('PYKMS_BLACKLIST_PATH', os.path.join(os.sep, 'home', 'py-kms', 'db', 'pykms_blacklist.txt'))
blacklist_stats_path = os.environ.get('PYKMS_BLACKLIST_STATS_PATH', os.path.join(os.sep, 'home', 'py-kms', 'db', 'pykms_blacklist_stats.json'))
log_file = os.environ.get('LOGFILE', 'STDOUT')
listen_ip = os.environ.get('IP', '::').split()
listen_port = os.environ.get('PORT', '1688')
want_webui = os.environ.get('WEBUI', '0') == '1' # if the variable is not provided, we assume the user does not want the webui

def _env_bool(env_name, default='1'):
  value = os.environ.get(env_name, default).strip().lower()
  return value in ['1', 'true', 'yes', 'y', 'on']

def _ensure_parent(path):
  parent = os.path.dirname(path)
  if parent:
    os.makedirs(parent, exist_ok = True)

def _touch(path):
  _ensure_parent(path)
  with open(path, 'a'):
    os.utime(path, None)

def _is_real_log_path(value):
  if not value:
    return False
  # Keep compatibility with py-kms logging modes that are not filesystem paths.
  return value.upper() not in ['STDOUT', 'STDOUTOFF', 'FILESTDOUT', 'FILEOFF']

def ensure_runtime_files(logger):
  required_files = []

  # WebUI mode requires sqlite storage file for startup path and data persistence.
  if want_webui:
    required_files.append(db_path)

  # Blacklist files are used by WebUI settings and runtime blacklist stats.
  required_files.append(blacklist_path)
  required_files.append(blacklist_stats_path)

  if _is_real_log_path(log_file):
    required_files.append(log_file)

  for file_path in required_files:
    try:
      if not os.path.exists(file_path):
        _touch(file_path)
        logger.info("Created missing runtime file: %s", file_path)
      else:
        _ensure_parent(file_path)
    except Exception as e:
      logger.error("Failed to prepare runtime file %s: %s", file_path, e)
      raise

def run_source_ip_backfill(logger):
  if not want_webui:
    return
  if not _env_bool('PYKMS_SOURCEIP_BACKFILL_ON_START', '1'):
    logger.info("Source IP startup backfill disabled by PYKMS_SOURCEIP_BACKFILL_ON_START.")
    return
  if not os.path.isfile(db_path):
    logger.debug("No sqlite db found at %s, skipping source IP backfill.", db_path)
    return

  logs_override = os.environ.get('PYKMS_SOURCEIP_BACKFILL_LOGS', '').strip()
  if logs_override:
    candidates = [entry.strip() for entry in logs_override.split(',') if entry.strip()]
  else:
    log_glob = os.environ.get('PYKMS_SOURCEIP_BACKFILL_GLOB', '/home/py-kms/db/pykms_logserver.log*')
    candidates = glob.glob(log_glob)

  log_files = sorted({path for path in candidates if os.path.isfile(path)}, key = os.path.getmtime)
  if len(log_files) == 0:
    logger.info("No log files found for source IP backfill, skipping.")
    return

  command = [PYTHON3, '-u', 'pykms_BackfillSourceIp.py', '--db', db_path, '--logs'] + log_files
  logger.info("Running source IP startup backfill with %d log file(s).", len(log_files))
  completed = subprocess.run(command, text = True, capture_output = True)
  if completed.returncode == 0:
    output = completed.stdout.strip()
    if output:
      logger.info("Source IP startup backfill result:\n%s", output)
    else:
      logger.info("Source IP startup backfill completed.")
  else:
    logger.warning("Source IP startup backfill failed (exit code %s).", completed.returncode)
    if completed.stdout:
      logger.warning("Backfill stdout:\n%s", completed.stdout.strip())
    if completed.stderr:
      logger.warning("Backfill stderr:\n%s", completed.stderr.strip())

def start_kms(logger):
  ensure_runtime_files(logger)
  run_source_ip_backfill(logger)

  # Build the command to execute
  command = [PYTHON3, '-u', 'pykms_Server.py', listen_ip[0], listen_port]
  for (arg, env) in argumentVariableMapping.items():
    if env in os.environ and os.environ.get(env) != '':
      command.append(arg)
      command.append(os.environ.get(env))
  if want_webui: # add this command directly before the "connect" subparser - otherwise you'll get silent crashes!
    command.append('-s')
    command.append(db_path)
  if len(listen_ip) > 1:
    command.append("connect")
    for i in range(1, len(listen_ip)):
      command.append("-n")
      command.append(listen_ip[i] + "," + listen_port)
    if dual := os.environ.get('DUALSTACK'):
      command.append("-d")
      command.append(dual)

  logger.debug("server_cmd: %s" % (" ".join(str(x) for x in command).strip()))
  pykms_process = subprocess.Popen(command)
  pykms_webui_process = None

  try:
    if want_webui:
      time.sleep(2) # Wait for the server to start up
      pykms_webui_env = os.environ.copy()
      pykms_webui_env['PYKMS_SQLITE_DB_PATH'] = db_path
      pykms_webui_env['PORT'] = '8080'
      pykms_webui_env['PYKMS_LICENSE_PATH'] = '/LICENSE'
      pykms_webui_env['PYKMS_VERSION_PATH'] = '/VERSION'
      pykms_webui_env['PYKMS_BLACKLIST_PATH'] = blacklist_path
      pykms_webui_process = subprocess.Popen(['gunicorn', '--log-level', os.environ.get('LOGLEVEL'), 'pykms_WebUI:app'], env=pykms_webui_env)
  except Exception as e:
    logger.error("Failed to start webui (ignoring and continuing anyways): %s" % e)

  try:
    pykms_process.wait()
  except Exception:
    # In case of any error - just shut down
    pass
  except KeyboardInterrupt:
    pass

  if pykms_webui_process:
    pykms_webui_process.terminate()
  pykms_process.terminate()


# Main
if __name__ == "__main__":
  log_level_bootstrap = log_level = os.environ.get('LOGLEVEL', 'INFO')
  if log_level_bootstrap == "MININFO":
    log_level_bootstrap = "INFO"
  loggersrv = logging.getLogger('start.py')
  loggersrv.setLevel(log_level_bootstrap)
  streamhandler = logging.StreamHandler(sys.stdout)
  streamhandler.setLevel(log_level_bootstrap)
  formatter = logging.Formatter(fmt='\x1b[94m%(asctime)s %(levelname)-8s %(message)s', datefmt='%a, %d %b %Y %H:%M:%S')
  streamhandler.setFormatter(formatter)
  loggersrv.addHandler(streamhandler)
  loggersrv.debug("user id: %s" % os.getuid())

  start_kms(loggersrv)
