#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only status dump for a MiniDLNA / ReadyMedia installation.

Invoked by the Cockpit front-end via cockpit.spawn() with superuser. Emits a
single JSON object describing every facet the UI needs: service state, the
parsed configuration, the built-in status page (file counts + connected
clients), database/cache info and a tail of the log. Strictly read-only.
"""

import json
import os
import re
import socket
import subprocess
import sys

CONFIG = os.environ.get("MINIDLNA_CONF", "/etc/minidlna.conf")

# Known single-value directives (everything else is treated as single-value
# too, but media_dir / album_art_names are explicitly multi-value).
MULTI_KEYS = {"media_dir", "album_art_names"}


def run(args):
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=10)
        return out.stdout.strip(), out.returncode
    except Exception:
        return "", 1


def service_state():
    active, _ = run(["systemctl", "is-active", "minidlna"])
    enabled, _ = run(["systemctl", "is-enabled", "minidlna"])
    return {"active": active or "unknown", "enabled": enabled or "unknown"}


def version():
    out, _ = run(["minidlnad", "-V"])
    if not out:
        out, _ = run(["/usr/sbin/minidlnad", "-V"])
    m = re.search(r"[Vv]ersion\s+([0-9.]+)", out)
    return m.group(1) if m else (out or None)


def parse_media_dir(value):
    """media_dir is '[types,]path' where types is a subset of A/V/P."""
    types, path = "AVP", value
    if "," in value:
        head, rest = value.split(",", 1)
        if re.fullmatch(r"[AVPavp]+", head):
            types, path = head.upper(), rest
    return {"types": types, "path": path}


def parse_config():
    """Return active directives plus the set of keys that exist commented-out."""
    cfg = {"singles": {}, "media_dir": [], "album_art_names": [],
           "available_keys": [], "raw_exists": os.path.exists(CONFIG)}
    if not cfg["raw_exists"]:
        return cfg
    seen = set()
    with open(CONFIG, "r", errors="replace") as fh:
        for line in fh:
            s = line.strip()
            commented = s.startswith("#")
            body = s.lstrip("#").strip()
            m = re.match(r"([a-z_]+)\s*=\s*(.*)$", body)
            if not m:
                continue
            key, val = m.group(1), m.group(2).strip()
            seen.add(key)
            if commented:
                continue
            if key == "media_dir":
                cfg["media_dir"].append(parse_media_dir(val))
            elif key == "album_art_names":
                cfg["album_art_names"].append(val)
            else:
                cfg["singles"][key] = val
    cfg["available_keys"] = sorted(seen)
    return cfg


def status_page(port):
    """Query MiniDLNA's built-in status page. It rejects HTTP/1.1 and any
    extra headers, so we speak bare HTTP/1.0 over a raw socket."""
    res = {"reachable": False, "audio": None, "video": None, "image": None,
           "clients": []}
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=4) as sock:
            sock.sendall(b"GET / HTTP/1.0\r\n\r\n")
            chunks = []
            while True:
                b = sock.recv(4096)
                if not b:
                    break
                chunks.append(b)
        html = b"".join(chunks).decode("utf-8", "replace")
    except Exception:
        return res
    res["reachable"] = True
    text = re.sub(r"<[^>]+>", " ", html)
    for label, key in (("Audio files", "audio"), ("Video files", "video"),
                       ("Image files", "image")):
        m = re.search(label + r"\s*</?[^>]*>?\s*(\d+)", html) or \
            re.search(label + r"\s+(\d+)", text)
        if m:
            res[key] = int(m.group(1))
    # client rows: ID, Type, IP, HW Address, Connections
    for row in re.findall(
            r"(\d+)\s+([\w/().+-]+)\s+(\d{1,3}(?:\.\d{1,3}){3})\s+"
            r"([0-9A-Fa-f:]{17})\s+(\d+)", text):
        res["clients"].append({
            "id": row[0], "type": row[1], "ip": row[2],
            "hw": row[3], "connections": int(row[4])})
    return res


def db_info(db_dir):
    path = os.path.join(db_dir, "files.db")
    info = {"path": path, "exists": os.path.exists(path), "size": None,
            "objects": None}
    if not info["exists"]:
        return info
    info["size"] = os.path.getsize(path)
    out, rc = run(["sqlite3", path, "SELECT count(*) FROM DETAILS;"])
    if rc == 0 and out.isdigit():
        info["objects"] = int(out)
    return info


def log_tail(log_dir, lines=80):
    path = os.path.join(log_dir, "minidlna.log")
    if not os.path.exists(path):
        return {"path": path, "exists": False, "text": ""}
    out, _ = run(["tail", "-n", str(lines), path])
    return {"path": path, "exists": True, "text": out}


def main():
    cfg = parse_config()
    singles = cfg["singles"]
    port = int(singles.get("port", "8200") or "8200")
    db_dir = singles.get("db_dir", "/var/cache/minidlna")
    log_dir = singles.get("log_dir", "/var/log/minidlna")
    data = {
        "config_path": CONFIG,
        "version": version(),
        "service": service_state(),
        "config": cfg,
        "status_page": status_page(port),
        "db": db_info(db_dir),
        "log": log_tail(log_dir),
    }
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
