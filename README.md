# Cockpit-MiniDLNA

A [Cockpit](https://cockpit-project.org/) / 45Drives Houston web-UI module for
managing a [MiniDLNA / ReadyMedia](https://sourceforge.net/projects/minidlna/)
DLNA/UPnP-AV server. It surfaces every facet of a MiniDLNA installation as a
menu entry in the Cockpit web console.

## Status

Working proof-of-concept. Tested against MiniDLNA 1.3.3 on Cockpit 360
(Ubuntu 26.04) and Cockpit 329 (Rocky Linux 9).

## Features

- **Service control** — start / stop / restart / reload / enable / disable,
  with live active/enabled state.
- **Media library status** — audio / video / image counts and connected DLNA
  clients, read from MiniDLNA's built-in status page; plus database size and
  indexed-object count.
- **Scan control** — incremental rescan (service restart with `-r`) and full
  index rebuild (wipe `files.db` and re-scan). Media files are never touched.
- **Media directories** — list, add (with A/V/P type prefixes), and remove
  `media_dir` entries.
- **Settings** — edit the common `minidlna.conf` directives (friendly name,
  port, interface, inotify, log level, thumbnails, etc.); blank reverts to the
  MiniDLNA default.
- **Log** — tail of `minidlna.log`.

## Safety model

- Every configuration change first writes a timestamped backup of
  `/etc/minidlna.conf` to `/etc/minidlna.backups/`.
- The only destructive action is **Rebuild index**, which deletes the index
  database so MiniDLNA regenerates it — it never deletes media. It is gated
  behind an explicit confirmation in the UI.
- Directive keys are validated (`^[a-z_]+$`) before being written.

## Layout

```
minidlna/
  manifest.json         Cockpit menu registration
  index.html            Page shell
  main.js               Front-end (status, service, config, scan, logs)
  style.css             Styling
  minidlna-status.py3   Read-only JSON status dump
  minidlna-action.py3   Management actions (service / scan / config, with backup)
```

## Install

```bash
sudo cp -r minidlna /usr/share/cockpit/minidlna
sudo restorecon -R /usr/share/cockpit/minidlna   # if SELinux is enforcing
```

Open the Cockpit web console (`https://<host>:9090`) and select **MiniDLNA**.

### Requirements

- `cockpit` (tested with 329 and 360)
- `minidlna` / `minidlnad`
- `minidlna.service` (systemd-managed); `sqlite3` (optional, for object counts)

> **Note:** service control acts on the `minidlna.service` systemd unit. If
> MiniDLNA is started manually (outside systemd), the status/config/library
> views still work, but the start/stop buttons act on the unit rather than the
> manually-launched process.

## License

Licensed under the GNU General Public License v3.0 or later
([GPL-3.0-or-later](LICENSE)).
