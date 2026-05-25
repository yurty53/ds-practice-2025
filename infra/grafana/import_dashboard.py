#!/usr/bin/env python3
# Simple importer: waits for Grafana admin API and imports all JSON files
import time
import os
import json
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

GRAFANA_URL = os.environ.get('GRAFANA_URL', 'http://observability:3000')
USER = os.environ.get('GRAFANA_USER', 'admin')
PASS = os.environ.get('GRAFANA_PASS', 'admin')
DASH_DIR = os.environ.get('DASH_DIR', '/dashboards')

auth_header = 'Basic ' + (USER + ':' + PASS).encode('ascii').hex()  # placeholder, we'll set differently

# create basic auth header properly
import base64
basic_token = base64.b64encode(f"{USER}:{PASS}".encode()).decode()
headers = {
    'Content-Type': 'application/json',
    'Authorization': f'Basic {basic_token}'
}


def grafana_ready():
    try:
        req = Request(GRAFANA_URL + '/api/health', headers=headers)
        with urlopen(req, timeout=5) as r:
            data = r.read().decode()
            return True
    except Exception:
        return False


def import_dashboard(file_path):
    with open(file_path, 'r') as f:
        dashboard = json.load(f)
    payload = {
        'dashboard': dashboard,
        'overwrite': True
    }
    data = json.dumps(payload).encode()
    req = Request(GRAFANA_URL + '/api/dashboards/db', data=data, headers=headers, method='POST')
    try:
        with urlopen(req, timeout=10) as r:
            print(f"Imported {file_path}: {r.status}")
            return True
    except HTTPError as e:
        print(f"Failed to import {file_path}: {e.code} {e.reason}")
        try:
            print(e.read().decode())
        except Exception:
            pass
        return False
    except URLError as e:
        print(f"Network error while importing {file_path}: {e}")
        return False


if __name__ == '__main__':
    # Wait for Grafana to become ready
    for i in range(60):
        if grafana_ready():
            print('Grafana ready, importing dashboards')
            break
        print('Waiting for Grafana...')
        time.sleep(2)
    else:
        print('Grafana did not become ready in time, exiting')
        raise SystemExit(1)

    if not os.path.isdir(DASH_DIR):
        print('Dashboards directory not found:', DASH_DIR)
        raise SystemExit(1)

    for fname in os.listdir(DASH_DIR):
        if not fname.endswith('.json'):
            continue
        path = os.path.join(DASH_DIR, fname)
        print('Importing', path)
        ok = import_dashboard(path)
        if not ok:
            print('Import failed for', path)

    print('Importer finished')
