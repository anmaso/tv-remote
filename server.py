#!/usr/bin/env python3
"""Local web remote for Philips TVs that speak the JointSpace API v6.

The TV only accepts HTTPS with a self-signed certificate and HTTP digest
auth, which a phone browser cannot do directly. This server sits in the
middle: the phone talks plain HTTP to it, and it talks to the TV.

Run:  python3 server.py   then open http://<this-mac-ip>:8888 on your phone.
Only the Python standard library is used.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import ssl
import string
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
STATIC_DIR = HERE / "static"
PORT = int(os.environ.get("PORT", "8888"))

# Shared secret used by Philips TVs to sign the pairing request.
# It is public and the same for every TV (taken from the official app).
PAIRING_SECRET = base64.b64decode(
    "ZmVay1EQVFOaZhwQ4Kv81ypLAZNczV9sG4KkseXWn1NEk6cXmPKO/MCa9sryslvLCFMnNe4Z4CPXzToowvhHvA=="
)

# The TV uses a self-signed certificate, so certificate checks are off.
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

# Actions the page may ask for. Anything else is refused.
ALLOWED_KEYS = {
    "Standby", "VolumeUp", "VolumeDown", "Mute", "ChannelStepUp", "ChannelStepDown",
    "CursorUp", "CursorDown", "CursorLeft", "CursorRight", "Confirm", "Back", "Home",
    "Options", "Info", "Find", "Adjust", "Source", "Online", "WatchTV", "Viewmode",
    "Teletext", "Subtitle", "AmbilightOnOff", "PlayPause", "Play", "Pause", "Stop",
    "Rewind", "FastForward", "Next", "Previous", "Record", "RedColour", "GreenColour",
    "YellowColour", "BlueColour", "Digit0", "Digit1", "Digit2", "Digit3", "Digit4",
    "Digit5", "Digit6", "Digit7", "Digit8", "Digit9", "Dot",
}


# ---------------------------------------------------------------- config

def load_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    # The file holds the TV password, so only this user may read it.
    os.chmod(CONFIG_PATH, 0o600)


CONFIG = load_config()
CONFIG.setdefault("tv_ip", os.environ.get("TV_IP", "192.168.1.22"))
CONFIG.setdefault("tv_mac", os.environ.get("TV_MAC", "f8:4f:ad:a0:00:64"))
if "device_id" not in CONFIG:
    alphabet = string.ascii_letters + string.digits
    CONFIG["device_id"] = "".join(secrets.choice(alphabet) for _ in range(16))
save_config(CONFIG)


def device_info():
    return {
        "device_name": "Web Remote",
        "device_os": "Android",
        "app_name": "Philips TV Web Remote",
        "type": "native",
        "app_id": "app.id",
        "id": CONFIG["device_id"],
    }


# ---------------------------------------------------------------- TV client

class TVError(Exception):
    pass


def tv_request(method, path, body=None, auth=True, timeout=5):
    """Call the TV API. Returns parsed JSON, or {} for an empty reply."""
    url = f"https://{CONFIG['tv_ip']}:1926/6/{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")

    handlers = [urllib.request.HTTPSHandler(context=SSL_CTX)]
    if auth:
        if "auth_key" not in CONFIG:
            raise TVError("Not paired with the TV yet.")
        pw = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        pw.add_password(None, url, CONFIG["device_id"], CONFIG["auth_key"])
        handlers.append(urllib.request.HTTPDigestAuthHandler(pw))
    opener = urllib.request.build_opener(*handlers)

    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise TVError("The TV rejected our credentials. Pair again.") from e
        raise TVError(f"TV answered HTTP {e.code} for {path}.") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise TVError(f"Cannot reach the TV at {CONFIG['tv_ip']} ({e}).") from e

    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw.decode(errors="replace")}


def pair_start():
    """Step 1: ask the TV to show a PIN on screen."""
    body = {"scope": ["read", "write", "control"], "device": device_info()}
    reply = tv_request("POST", "pair/request", body, auth=False)
    if reply.get("error_id") != "SUCCESS":
        raise TVError(f"TV refused pairing: {reply}")
    CONFIG["pending_auth_key"] = reply["auth_key"]
    CONFIG["pending_timestamp"] = reply["timestamp"]
    save_config(CONFIG)
    return {"timeout": reply.get("timeout", 60)}


def pair_finish(pin):
    """Step 2: send back the PIN the TV showed."""
    if "pending_auth_key" not in CONFIG:
        raise TVError("Start pairing first.")
    ts = CONFIG["pending_timestamp"]
    sig = hmac.new(PAIRING_SECRET, f"{ts}{pin}".encode(), hashlib.sha1).digest()
    body = {
        "auth": {
            "auth_AppId": "1",
            "pin": str(pin),
            "auth_timestamp": ts,
            "auth_signature": base64.b64encode(sig).decode(),
        },
        "device": device_info(),
    }
    # The grant call already uses the new key for digest auth.
    CONFIG["auth_key"] = CONFIG.pop("pending_auth_key")
    CONFIG.pop("pending_timestamp", None)
    try:
        reply = tv_request("POST", "pair/grant", body)
    except TVError:
        CONFIG.pop("auth_key", None)
        save_config(CONFIG)
        raise
    if reply.get("error_id") not in (None, "SUCCESS"):
        CONFIG.pop("auth_key", None)
        save_config(CONFIG)
        raise TVError(f"Wrong PIN or pairing expired: {reply}")
    save_config(CONFIG)
    return {"paired": True}


def status():
    out = {"tv_ip": CONFIG["tv_ip"], "paired": "auth_key" in CONFIG}
    try:
        out["system"] = tv_request("GET", "system", auth=False, timeout=3).get("name")
        out["online"] = True
    except TVError:
        out["online"] = False
        return out
    if out["paired"]:
        for key, path in (("power", "powerstate"), ("volume", "audio/volume")):
            try:
                out[key] = tv_request("GET", path, timeout=3)
            except TVError as e:
                out[key] = {"error": str(e)}
    return out


def list_apps():
    reply = tv_request("GET", "applications")
    apps = []
    for a in reply.get("applications", []):
        apps.append({"label": a.get("label"), "intent": a.get("intent")})
    apps.sort(key=lambda a: (a["label"] or "").lower())
    return {"apps": apps}


def set_volume(level):
    current = tv_request("GET", "audio/volume")
    lo, hi = current.get("min", 0), current.get("max", 60)
    level = max(lo, min(hi, int(level)))
    tv_request("POST", "audio/volume", {"muted": False, "current": level})
    return {"current": level}


def set_power(on):
    tv_request("POST", "powerstate", {"powerstate": "On" if on else "Standby"})
    return {"ok": True}


def wake_on_lan():
    """Send a Wake-on-LAN magic packet so the TV can turn on from standby.

    The TV must have "Switch on with Wi-Fi (WoWLAN)" enabled in its settings.
    """
    mac = CONFIG.get("tv_mac", "").replace(":", "").replace("-", "")
    if len(mac) != 12:
        raise TVError("No TV MAC address in config.json (tv_mac).")
    packet = b"\xff" * 6 + bytes.fromhex(mac) * 16
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for port in (7, 9):
            s.sendto(packet, ("255.255.255.255", port))
    return {"ok": True}


def set_ambilight(on):
    tv_request("POST", "ambilight/power", {"power": "On" if on else "Off"})
    return {"ok": True}


# ---------------------------------------------------------------- HTTP server

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def send_json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length))

    def do_GET(self):
        routes = {"/api/status": status, "/api/apps": list_apps}
        if self.path in routes:
            return self.run(routes[self.path])
        self.serve_static()

    def do_POST(self):
        try:
            body = self.read_json()
        except json.JSONDecodeError:
            return self.send_json(400, {"error": "Bad JSON."})

        if self.path == "/api/pair/start":
            return self.run(pair_start)
        if self.path == "/api/pair/finish":
            return self.run(lambda: pair_finish(str(body.get("pin", "")).strip()))
        if self.path == "/api/key":
            key = body.get("key")
            if key not in ALLOWED_KEYS:
                return self.send_json(400, {"error": f"Unknown key {key!r}."})
            return self.run(lambda: tv_request("POST", "input/key", {"key": key}) or {"ok": True})
        if self.path == "/api/volume":
            return self.run(lambda: set_volume(body.get("level", 0)))
        if self.path == "/api/power":
            return self.run(lambda: set_power(bool(body.get("on"))))
        if self.path == "/api/wake":
            return self.run(wake_on_lan)
        if self.path == "/api/ambilight":
            return self.run(lambda: set_ambilight(bool(body.get("on"))))
        if self.path == "/api/launch":
            intent = body.get("intent")
            if not isinstance(intent, dict):
                return self.send_json(400, {"error": "Missing app intent."})
            return self.run(lambda: tv_request("POST", "activities/launch", intent) or {"ok": True})
        self.send_json(404, {"error": "Not found."})

    def run(self, fn):
        try:
            self.send_json(200, fn())
        except TVError as e:
            self.send_json(502, {"error": str(e)})

    def serve_static(self):
        name = "index.html" if self.path in ("/", "") else self.path.lstrip("/").split("?")[0]
        target = (STATIC_DIR / name).resolve()
        # Refuse paths that escape the static folder.
        if STATIC_DIR.resolve() not in target.parents or not target.is_file():
            return self.send_json(404, {"error": "Not found."})
        types = {".html": "text/html; charset=utf-8", ".js": "text/javascript",
                 ".css": "text/css", ".json": "application/manifest+json",
                 ".svg": "image/svg+xml", ".png": "image/png"}
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", types.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Philips TV remote on http://0.0.0.0:{PORT}  (TV at {CONFIG['tv_ip']})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
