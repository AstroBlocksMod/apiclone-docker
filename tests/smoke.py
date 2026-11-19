#!/usr/bin/env python3
"""
End-to-end smoke test for a running stack, in either mode.

It does what a browser on scratch-www does: loads the home page and a bundle,
logs in with a CSRF token from the scratch-www origin (which catches CORS and
CSRF mistakes), creates and reloads a project, and sets a cloud variable on one
WebSocket while watching for it on another (which catches channel layer and
proxy mistakes). Standard library only, so it runs anywhere Python 3.12+ does.

Usage:
  tests/smoke.py --www http://localhost:8333 --api http://localhost:8000 \
      --username admin --password secret
"""

import argparse
import base64
import http.cookiejar
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

PROJECT = {
    "targets": [
        {
            "isStage": True,
            "name": "Stage",
            "variables": {"smokevar": ["☁ smoke", 0, True]},
            "lists": {},
            "broadcasts": {},
            "blocks": {},
            "comments": {},
            "currentCostume": 0,
            "costumes": [],
            "sounds": [],
            "volume": 100,
            "layerOrder": 0,
            "tempo": 60,
            "videoTransparency": 50,
            "videoState": "on",
            "textToSpeechLanguage": None,
        }
    ],
    "monitors": [],
    "extensions": [],
    "meta": {"semver": "3.0.0", "vm": "0.2.0", "agent": "apiclone-docker smoke test"},
}


class Client:
    def __init__(self, www, api):
        self.www = www.rstrip("/")
        self.api = api.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, method, url, body=None, headers=None):
        headers = {"Origin": self.www, "Referer": self.www + "/", **(headers or {})}
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=30) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def cookie(self, name):
        return next((c.value for c in self.jar if c.name == name), None)


def check(condition, message):
    if not condition:
        print(f"FAIL: {message}", file=sys.stderr)
        sys.exit(1)
    print(f"ok: {message}")


def wait_for(url, seconds):
    deadline = time.monotonic() + seconds
    while True:
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        if time.monotonic() > deadline:
            check(False, f"{url} answers within {seconds} seconds")
        time.sleep(3)


class WebSocket:
    """Just enough of RFC 6455 to talk to the cloud data host."""

    def __init__(self, url, headers):
        parts = urlsplit(url)
        self.sock = socket.create_connection((parts.hostname, parts.port or 80), timeout=15)
        self.file = self.sock.makefile("rb")
        key = base64.b64encode(os.urandom(16)).decode()
        lines = [
            f"GET {parts.path} HTTP/1.1",
            f"Host: {parts.netloc}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
            *(f"{k}: {v}" for k, v in headers.items()),
        ]
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        status = self.file.readline().decode()
        while self.file.readline() not in (b"\r\n", b""):
            pass
        check(" 101 " in status, f"WebSocket upgrade to {url} (got {status.strip()!r})")

    def send(self, data):
        payload = json.dumps(data).encode()
        mask = os.urandom(4)
        length = len(payload)
        if length < 126:
            header = bytes([0x81, 0x80 | length])
        else:
            header = bytes([0x81, 0x80 | 126]) + length.to_bytes(2, "big")
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def receive(self):
        while True:
            first, second = self.file.read(2)
            length = second & 0x7F
            if length == 126:
                length = int.from_bytes(self.file.read(2), "big")
            elif length == 127:
                length = int.from_bytes(self.file.read(8), "big")
            payload = self.file.read(length)
            opcode = first & 0x0F
            if opcode == 0x8:
                raise ConnectionError("the server closed the WebSocket")
            if opcode == 0x1:
                return payload.decode()

    def close(self):
        self.sock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--www", required=True, help="where scratch-www is served")
    parser.add_argument("--api", required=True, help="where APIClone is served")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--wait", type=int, default=0, help="seconds to wait for the stack")
    parser.add_argument(
        "--skip-cloud-broadcast",
        action="store_true",
        help="only check that cloud WebSockets connect. APIClone at the pinned commit never"
        " sends on them (its consumer's send() does not await), so the broadcast check fails"
        " until that is fixed upstream",
    )
    args = parser.parse_args()
    client = Client(args.www, args.api)

    if args.wait:
        wait_for(client.api + "/health/", args.wait)
        wait_for(client.www + "/", args.wait)

    # scratch-www
    status, body = client.request("GET", client.www + "/")
    page = body.decode(errors="replace")
    check(status == 200, f"scratch-www home page loads (HTTP {status})")
    bundles = re.findall(r'<script[^>]+src="([^"]+\.js)"', page)
    check(bundles, "the home page references JavaScript bundles")
    bundle = bundles[-1] if bundles[-1].startswith("http") else client.www + bundles[-1]
    status, _ = client.request("GET", bundle)
    check(status == 200, f"{bundles[-1]} loads (HTTP {status})")

    # APIClone
    status, body = client.request("GET", client.api + "/health/")
    check(status == 200 and json.loads(body).get("healthy"), "APIClone reports healthy")
    status, _ = client.request("GET", client.api + "/csrf_token/")
    csrf = client.cookie("scratchcsrftoken")
    check(status == 200 and csrf, "APIClone sets a CSRF cookie")

    status, body = client.request(
        "POST",
        client.api + "/accounts/login/",
        json.dumps(
            {"username": args.username, "password": args.password, "useMessages": True}
        ).encode(),
        {
            "Content-Type": "application/json",
            "X-CSRFToken": csrf,
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    check(status == 200, f"login request from {client.www} is accepted (HTTP {status})")
    check(json.loads(body)[0].get("success") == 1, f"logging in as {args.username} works")

    status, body = client.request("GET", client.api + "/session/")
    session = json.loads(body) if status == 200 else {}
    check(session.get("user", {}).get("username") == args.username, "the session knows the user")
    token = session["user"]["token"]

    # Projects
    project_json = json.dumps(PROJECT).encode()
    status, body = client.request(
        "POST",
        client.api + "/hosts/projects/?is_remix=0&title=Smoke%20test",
        project_json,
        {"Content-Type": "application/json", "X-Token": token},
    )
    check(status == 200, f"creating a project works (HTTP {status})")
    project_id = json.loads(body)["content-name"]
    status, body = client.request("GET", f"{client.api}/hosts/projects/{project_id}?token={token}")
    check(status == 200 and json.loads(body) == PROJECT, f"project {project_id} reloads unchanged")

    # Cloud variables
    api = urlsplit(client.api)
    ws_url = f"ws://{api.netloc}/hosts/clouddata"
    headers = {
        "Origin": client.www,
        "Cookie": f"scratchsessionsid={client.cookie('scratchsessionsid')}",
    }
    handshake = {"method": "handshake", "user": args.username, "project_id": project_id}
    sender, watcher = WebSocket(ws_url, headers), WebSocket(ws_url, headers)
    for ws in (sender, watcher):
        ws.send(handshake)
    value = str(int(time.time()))
    sender.send(
        {
            "method": "set",
            "user": args.username,
            "project_id": project_id,
            "name": "☁ smoke",
            "value": value,
        }
    )
    if args.skip_cloud_broadcast:
        print("skipped: a cloud variable set on one connection reaches another")
    else:
        deadline = time.monotonic() + 15
        seen = False
        while not seen and time.monotonic() < deadline:
            for line in watcher.receive().splitlines():
                if line.strip():
                    message = json.loads(line)
                    seen = message.get("method") == "set" and message.get("value") == value
        check(seen, "a cloud variable set on one connection reaches another")
    sender.close()
    watcher.close()

    print("Smoke test passed.")


if __name__ == "__main__":
    main()
