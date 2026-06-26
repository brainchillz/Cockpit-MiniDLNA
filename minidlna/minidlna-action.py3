#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Management actions for a MiniDLNA / ReadyMedia installation.

Invoked by the Cockpit front-end via cockpit.spawn() with superuser. Reads a
JSON parameter object from stdin and an action name from argv[1]; emits a
single JSON object: {"ok": bool, "message": str, ...}.

Facets controlled:
  * service lifecycle  - start / stop / restart / reload / enable / disable
  * scan               - rescan (restart) / rebuild (wipe files.db + restart)
  * configuration      - set / unset single-value directives,
                         add / remove media_dir entries

SAFETY:
  * Every configuration change first writes a timestamped backup of
    /etc/minidlna.conf under /etc/minidlna.backups/.
  * The only destructive action is 'rebuild', which deletes the index
    database (files.db) so MiniDLNA regenerates it; media files are never
    touched. The front-end gates it behind an explicit confirmation.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time

CONFIG = os.environ.get("MINIDLNA_CONF", "/etc/minidlna.conf")
BACKUP_DIR = "/etc/minidlna.backups"
SERVICE = "minidlna"

SERVICE_VERBS = {"start", "stop", "restart", "reload", "enable", "disable"}


def respond(ok, message, **extra):
    out = {"ok": bool(ok), "message": message}
    out.update(extra)
    print(json.dumps(out))
    sys.exit(0 if ok else 1)


def fail(message):
    respond(False, message)


def systemctl(verb):
    p = subprocess.run(["systemctl", verb, SERVICE],
                       capture_output=True, text=True, timeout=30)
    if p.returncode != 0:
        fail("systemctl %s failed: %s" % (verb, (p.stderr or p.stdout).strip()))


def backup_conf():
    if not os.path.exists(CONFIG):
        return None
    os.makedirs(BACKUP_DIR, mode=0o700, exist_ok=True)
    # Include milliseconds so several edits within the same second each keep a
    # distinct backup rather than overwriting one another.
    stamp = time.strftime("%Y%m%d-%H%M%S") + "-%03d" % (int(time.time() * 1000) % 1000)
    dest = os.path.join(BACKUP_DIR, "minidlna.conf-%s" % stamp)
    shutil.copy2(CONFIG, dest)
    return dest


def read_lines():
    if not os.path.exists(CONFIG):
        fail("config file %s does not exist" % CONFIG)
    with open(CONFIG, "r", errors="replace") as fh:
        return fh.readlines()


def write_lines(lines):
    tmp = CONFIG + ".tmp"
    with open(tmp, "w") as fh:
        fh.writelines(lines)
    os.replace(tmp, CONFIG)


def media_dir_value(types, path):
    types = (types or "").upper()
    if types and types != "AVP" and re.fullmatch(r"[AVP]+", types):
        return "%s,%s" % (types, path)
    return path


def parse_media_path(value):
    if "," in value:
        head, rest = value.split(",", 1)
        if re.fullmatch(r"[AVPavp]+", head):
            return rest
    return value


# ---- actions -------------------------------------------------------------

def act_service(p):
    verb = (p.get("verb") or "").strip()
    if verb not in SERVICE_VERBS:
        fail("unknown service verb '%s'" % verb)
    systemctl(verb)
    respond(True, "Service %s: %s succeeded" % (SERVICE, verb))


def act_rescan(p):
    # The systemd unit passes -r, so a restart triggers an incremental rescan.
    systemctl("restart")
    respond(True, "Triggered rescan (service restarted with -r)")


def act_rebuild(p):
    db_dir = (p.get("db_dir") or "/var/cache/minidlna").strip()
    db = os.path.join(db_dir, "files.db")
    systemctl("stop")
    removed = False
    if os.path.exists(db):
        os.remove(db)
        removed = True
    # cached artwork/thumbnails regenerate as well
    systemctl("start")
    respond(True, "Rebuild started: %s%s and service restarted"
            % ("removed " + db if removed else "no existing " + db,
               " (full re-index in progress)" if removed else ""))


def act_set(p):
    key = (p.get("key") or "").strip()
    value = p.get("value")
    if not re.fullmatch(r"[a-z_]+", key):
        fail("invalid directive key '%s'" % key)
    if value is None:
        fail("value is required")
    value = str(value)
    backup_conf()
    lines = read_lines()
    new_line = "%s=%s\n" % (key, value)
    active = re.compile(r"^\s*%s\s*=" % re.escape(key))
    commented = re.compile(r"^\s*#\s*%s\s*=" % re.escape(key))
    for i, line in enumerate(lines):
        if active.match(line):
            lines[i] = new_line
            write_lines(lines)
            respond(True, "Set %s=%s" % (key, value), restart_needed=True)
    for i, line in enumerate(lines):       # uncomment a default if present
        if commented.match(line):
            lines[i] = new_line
            write_lines(lines)
            respond(True, "Set %s=%s" % (key, value), restart_needed=True)
    lines.append(new_line)                 # otherwise append
    write_lines(lines)
    respond(True, "Set %s=%s" % (key, value), restart_needed=True)


def act_unset(p):
    key = (p.get("key") or "").strip()
    if not re.fullmatch(r"[a-z_]+", key):
        fail("invalid directive key '%s'" % key)
    backup_conf()
    lines = read_lines()
    active = re.compile(r"^\s*%s\s*=" % re.escape(key))
    changed = False
    for i, line in enumerate(lines):
        if active.match(line):
            lines[i] = "#" + line if not line.startswith("#") else line
            changed = True
    if not changed:
        respond(True, "%s was not set; nothing to do" % key)
    write_lines(lines)
    respond(True, "Commented out %s" % key, restart_needed=True)


def act_add_media_dir(p):
    path = (p.get("path") or "").strip()
    types = (p.get("types") or "").strip().upper()
    if not path:
        fail("path is required")
    if types and not re.fullmatch(r"[AVP]+", types):
        fail("types must be a combination of A, V, P")
    warn = "" if os.path.isdir(path) else " (warning: path is not a directory)"
    value = media_dir_value(types, path)
    backup_conf()
    lines = read_lines()
    # refuse duplicates of the same path
    for line in lines:
        m = re.match(r"^\s*media_dir\s*=\s*(.*)$", line)
        if m and parse_media_path(m.group(1).strip()) == path:
            fail("media_dir for '%s' already exists" % path)
    new_line = "media_dir=%s\n" % value
    # insert after the last active media_dir line, else append
    insert_at = None
    for i, line in enumerate(lines):
        if re.match(r"^\s*media_dir\s*=", line):
            insert_at = i + 1
    if insert_at is None:
        lines.append(new_line)
    else:
        lines.insert(insert_at, new_line)
    write_lines(lines)
    respond(True, "Added media_dir '%s'%s" % (value, warn), restart_needed=True)


def act_remove_media_dir(p):
    path = (p.get("path") or "").strip()
    if not path:
        fail("path is required")
    backup_conf()
    lines = read_lines()
    kept, removed = [], 0
    for line in lines:
        m = re.match(r"^\s*media_dir\s*=\s*(.*)$", line)
        if m and parse_media_path(m.group(1).strip()) == path:
            removed += 1
            continue
        kept.append(line)
    if not removed:
        fail("no media_dir matching '%s'" % path)
    write_lines(kept)
    respond(True, "Removed media_dir '%s'" % path, restart_needed=True)


ACTIONS = {
    "service": act_service,
    "rescan": act_rescan,
    "rebuild": act_rebuild,
    "set": act_set,
    "unset": act_unset,
    "add-media-dir": act_add_media_dir,
    "remove-media-dir": act_remove_media_dir,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ACTIONS:
        fail("unknown action; expected one of: %s" % ", ".join(sorted(ACTIONS)))
    raw = sys.stdin.read()
    try:
        params = json.loads(raw) if raw.strip() else {}
    except Exception as exc:
        fail("invalid JSON parameters: %s" % exc)
    try:
        ACTIONS[sys.argv[1]](params)
    except SystemExit:
        raise
    except Exception as exc:
        fail("%s: %s" % (type(exc).__name__, exc))


if __name__ == "__main__":
    main()
