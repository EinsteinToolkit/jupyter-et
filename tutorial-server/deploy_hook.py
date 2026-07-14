#!/usr/bin/env python3
# Copyright (c) 2019 Steven R. Brandt, and Roland Haas
#
# Distributed under the LGPL
#
# Listens for a Docker Hub webhook (proxied in by JupyterHub's own
# "services" route, so no separate port needs to be exposed) and pulls +
# recreates a single named docker-compose service.
import hmac
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

SECRET = os.environ.get("DEPLOY_HOOK_SECRET")
COMPOSE_FILE = os.environ.get("COMPOSE_FILE", "docker-compose.cilogon.yml")
COMPOSE_SERVICE = os.environ.get("COMPOSE_SERVICE", "et-cilogon")
PORT = int(os.environ.get("PORT", "9000"))

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
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
