#!/usr/bin/env python3
# Copyright (c) 2019 Steven R. Brandt, and Roland Haas
#
# Distributed under the LGPL
#
# Listens for a Docker Hub webhook (proxied in by JupyterHub's own
# "services" route, so no separate port needs to be exposed) and pulls +
# recreates a single named docker-compose service. Also polls the SSL cert
# files and restarts the container when they change, since JupyterHub's
# proxy only reads them once at startup.
import hashlib
import hmac
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

SECRET = os.environ.get("DEPLOY_HOOK_SECRET")
COMPOSE_FILE = os.environ.get("COMPOSE_FILE", "docker-compose.cilogon.yml")
COMPOSE_SERVICE = os.environ.get("COMPOSE_SERVICE", "et-cilogon")
PORT = int(os.environ.get("PORT", "9000"))

RESTART_CONTAINER = os.environ.get("RESTART_CONTAINER", "et-juphub")
CERT_FILES = [
    p
    for p in os.environ.get(
        "CERT_FILES",
        "/etc/ssl/certs/etk.cct.lsu.edu.cer,/etc/ssl/private/etk.cct.lsu.edu.key",
    ).split(",")
    if p
]
CERT_CHECK_INTERVAL = int(os.environ.get("CERT_CHECK_INTERVAL", "300"))

if not SECRET:
    sys.exit("DEPLOY_HOOK_SECRET must be set")

deploy_lock = threading.Lock()


def run_deploy():
    if not deploy_lock.acquire(blocking=False):
        print("deploy already in progress, ignoring trigger", flush=True)
        return
    try:
        print(f"pulling {COMPOSE_SERVICE} via {COMPOSE_FILE}", flush=True)
        subprocess.run(
            ["docker", "compose", "-f", COMPOSE_FILE, "pull", COMPOSE_SERVICE],
            check=True,
        )
        print(f"recreating {COMPOSE_SERVICE}", flush=True)
        subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                COMPOSE_FILE,
                "up",
                "-d",
                "--no-deps",
                COMPOSE_SERVICE,
            ],
            check=True,
        )
        print("deploy finished", flush=True)
    except subprocess.CalledProcessError as e:
        print(f"deploy failed: {e}", flush=True)
    finally:
        deploy_lock.release()


def hash_cert_files():
    h = hashlib.sha256()
    for path in CERT_FILES:
        try:
            with open(path, "rb") as fd:
                h.update(fd.read())
        except OSError as e:
            h.update(f"<missing:{path}:{e}>".encode())
    return h.hexdigest()


def restart_for_cert_change():
    # Shares deploy_lock with run_deploy() so a cert-triggered restart never
    # races a webhook-triggered pull+recreate of the same container.
    if not deploy_lock.acquire(blocking=False):
        print("deploy in progress, deferring cert-triggered restart", flush=True)
        return False
    try:
        print(f"cert files changed, restarting {RESTART_CONTAINER}", flush=True)
        subprocess.run(["docker", "restart", RESTART_CONTAINER], check=True)
        print("restart finished", flush=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"restart failed: {e}", flush=True)
        return False
    finally:
        deploy_lock.release()


def watch_certs():
    last_hash = hash_cert_files()
    print(
        f"watching {CERT_FILES} for changes every {CERT_CHECK_INTERVAL}s",
        flush=True,
    )
    while True:
        time.sleep(CERT_CHECK_INTERVAL)
        current_hash = hash_cert_files()
        if current_hash == last_hash:
            continue
        # Only advance last_hash on a successful restart, so a
        # deferred/failed attempt gets retried on the next tick instead of
        # silently giving up.
        if restart_for_cert_change():
            last_hash = current_hash


class Handler(BaseHTTPRequestHandler):
    def _token_ok(self):
        qs = parse_qs(urlsplit(self.path).query)
        token = qs.get("token", [""])[0]
        return hmac.compare_digest(token, SECRET)

    def do_GET(self):
        # plain healthcheck, never triggers a deploy
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def do_POST(self):
        if not self._token_ok():
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(body) if body else {}
            repo = payload.get("repository", {}).get("repo_name", "<unknown>")
            print(f"webhook received for repo={repo}", flush=True)
        except json.JSONDecodeError:
            print("webhook received (unparseable payload)", flush=True)
        threading.Thread(target=run_deploy, daemon=True).start()
        self.send_response(202)
        self.end_headers()
        self.wfile.write(b"accepted")

    def log_message(self, fmt, *args):
        print("deploy-hook: " + (fmt % args), flush=True)


if __name__ == "__main__":
    print(f"listening on :{PORT}, watching {COMPOSE_SERVICE} in {COMPOSE_FILE}", flush=True)
    threading.Thread(target=watch_certs, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
