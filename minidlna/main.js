/* SPDX-License-Identifier: GPL-3.0-or-later
 *
 * MiniDLNA — Cockpit module front-end.
 *
 * Read path:  minidlna-status.py3  (read-only)
 * Write path: minidlna-action.py3  (service / scan / config, with conf backup)
 */
"use strict";

const STATUS = "/usr/share/cockpit/minidlna/minidlna-status.py3";
const ACTION = "/usr/share/cockpit/minidlna/minidlna-action.py3";

let DATA = null;

const el = (id) => document.getElementById(id);
function setStatus(t) { el("status").textContent = t || ""; }
function showError(m) { const b = el("error"); b.textContent = m; b.hidden = false; }
function clearError() { el("error").hidden = true; }
function showNotice(m) {
    const b = el("notice"); b.textContent = m; b.hidden = false;
    clearTimeout(b._t); b._t = setTimeout(() => { b.hidden = true; }, 6000);
}

function node(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
}
function humanSize(b) {
    if (b == null) return "—";
    const u = ["B", "KiB", "MiB", "GiB"]; let n = b, i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
}

/* ---------- helper invocation ---------- */

function runAction(action, params) {
    return new Promise((resolve, reject) => {
        const proc = cockpit.spawn(["python3", ACTION, action],
            { superuser: "require", err: "message" });
        proc.input(JSON.stringify(params || {}));
        proc.then((out) => {
            let res; try { res = JSON.parse(out); }
            catch (e) { reject("Bad helper output: " + out); return; }
            res.ok ? resolve(res) : reject(res.message || "Action failed");
        }).catch((ex) => {
            const o = (ex && ex.message) || String(ex);
            try { reject(JSON.parse(o).message); } catch (e) { reject("Helper error: " + o); }
        });
    });
}

function doAction(action, params, opts) {
    opts = opts || {};
    const go = () => runAction(action, params)
        .then((res) => {
            showNotice(res.message + (res.restart_needed
                ? "  — restart MiniDLNA for changes to take effect." : ""));
            load();
        })
        .catch(showError);
    if (opts.confirm) confirmDialog(opts.confirm, go);
    else { clearError(); go(); }
}

/* ---------- service card ---------- */

function pill(state, okText) {
    const cls = state === okText ? "pill ok" : (state === "unknown" ? "pill warn" : "pill off");
    return node("span", cls, state);
}

function serviceCard(d) {
    const c = node("section", "card");
    const h = node("h2", null, "Service");
    h.append(pill(d.service.active, "active"));
    h.append(pill(d.service.enabled, "enabled"));
    c.append(h);

    const dl = node("dl", "kv");
    const add = (k, v) => { dl.append(node("dt", null, k)); dl.append(node("dd", "mono", v)); };
    add("Version", d.version || "—");
    add("Config", d.config_path);
    add("Active", d.service.active);
    add("Enabled", d.service.enabled);
    c.append(dl);

    const running = d.service.active === "active";
    const row = node("div", "btnrow");
    const btn = (label, verb, cls) => {
        const b = node("button", "btn sm " + (cls || "ghost"), label);
        b.addEventListener("click", () => doAction("service", { verb }));
        return b;
    };
    row.append(btn(running ? "Restart" : "Start", running ? "restart" : "start", "primary"));
    if (running) row.append(btn("Stop", "stop", "danger"));
    row.append(btn("Reload", "reload"));
    row.append(d.service.enabled === "enabled" ? btn("Disable", "disable")
                                               : btn("Enable", "enable"));
    c.append(row);
    return c;
}

/* ---------- library / status card ---------- */

function libraryCard(d) {
    const c = node("section", "card");
    c.append(node("h2", null, "Media library"));
    const sp = d.status_page;
    if (!sp.reachable) {
        c.append(node("p", "empty", "Status page not reachable (service stopped?)."));
    } else {
        const stat = node("div", "stat");
        [["Audio", sp.audio], ["Video", sp.video], ["Image", sp.image]].forEach(([l, n]) => {
            const box = node("div");
            box.append(node("div", "num", n == null ? "—" : String(n)));
            box.append(node("div", "lbl", l));
            stat.append(box);
        });
        c.append(stat);
    }
    const dl = node("dl", "kv");
    dl.append(node("dt", null, "Database"));
    dl.append(node("dd", "mono", `${d.db.path} (${d.db.exists ? humanSize(d.db.size) : "absent"}, ` +
        `${d.db.objects == null ? "?" : d.db.objects} objects)`));
    c.append(dl);

    const row = node("div", "btnrow");
    const rescan = node("button", "btn sm primary", "Rescan");
    rescan.addEventListener("click", () => doAction("rescan", {}));
    const rebuild = node("button", "btn sm danger", "Rebuild index");
    rebuild.addEventListener("click", () => doAction("rebuild",
        { db_dir: d.config.singles.db_dir || "/var/cache/minidlna" },
        { confirm: "Rebuild deletes the index database (files.db) and forces a full " +
            "re-scan of all media. Media files are not touched. Continue?" }));
    row.append(rescan); row.append(rebuild);
    c.append(row);
    return c;
}

/* ---------- connected clients ---------- */

function clientsCard(d) {
    const c = node("section", "card");
    const list = d.status_page.clients || [];
    c.append(node("h2", null, `Connected clients (${list.length})`));
    if (!list.length) { c.append(node("p", "empty", "No clients seen.")); return c; }
    const t = node("table", "tbl");
    const hr = node("tr");
    ["ID", "Type", "IP address", "HW address", "Conn"].forEach((h) => hr.append(node("th", null, h)));
    t.append(hr);
    list.forEach((cl) => {
        const tr = node("tr");
        [cl.id, cl.type, cl.ip, cl.hw, cl.connections].forEach((v, i) =>
            tr.append(node("td", i ? "mono" : null, String(v))));
        t.append(tr);
    });
    c.append(t);
    return c;
}

/* ---------- media directories ---------- */

function mediaDirsCard(d) {
    const c = node("section", "card full");
    c.append(node("h2", null, "Media directories"));
    const dirs = d.config.media_dir || [];
    if (!dirs.length) c.append(node("p", "empty", "No media_dir configured."));
    dirs.forEach((m) => {
        const r = node("div", "mdir");
        r.append(node("span", "types", m.types));
        r.append(node("span", "path mono", m.path));
        const del = node("button", "btn sm danger", "Remove");
        del.addEventListener("click", () => doAction("remove-media-dir", { path: m.path },
            { confirm: `Remove media directory "${m.path}" from the configuration?` }));
        r.append(del);
        c.append(r);
    });

    const form = node("form", "row-inline");
    form.style.marginTop = "0.75rem";
    const types = node("select");
    [["AVP", "All types"], ["A", "Audio"], ["V", "Video"], ["P", "Pictures"],
     ["AV", "Audio+Video"]].forEach(([v, l]) => {
        const o = document.createElement("option"); o.value = v; o.textContent = l; types.append(o);
    });
    const path = node("input"); path.type = "text"; path.placeholder = "/path/to/media";
    const add = node("button", "btn sm", "Add"); add.type = "submit";
    form.append(types, path, add);
    form.addEventListener("submit", (e) => {
        e.preventDefault();
        if (!path.value.trim()) { showError("Enter a media directory path."); return; }
        doAction("add-media-dir", { types: types.value, path: path.value.trim() });
    });
    c.append(form);
    return c;
}

/* ---------- settings ---------- */

const SETTINGS = [
    ["friendly_name", "Friendly name", "text"],
    ["port", "Port", "text"],
    ["network_interface", "Network interface(s)", "text"],
    ["user", "Run as user", "text"],
    ["db_dir", "Database dir", "text"],
    ["log_dir", "Log dir", "text"],
    ["log_level", "Log level", "text"],
    ["inotify", "inotify (yes/no)", "bool"],
    ["merge_media_dirs", "Merge media dirs", "bool"],
    ["root_container", "Root container", "text"],
    ["notify_interval", "Notify interval (s)", "text"],
    ["max_connections", "Max connections", "text"],
    ["strict_dlna", "Strict DLNA", "bool"],
    ["enable_tivo", "Enable TiVo", "bool"],
    ["wide_links", "Allow wide links", "bool"],
    ["enable_subtitles", "Enable subtitles", "bool"],
    ["model_name", "Model name", "text"],
    ["serial", "Serial", "text"],
    ["presentation_url", "Presentation URL", "text"],
];

function settingsCard(d) {
    const c = node("section", "card full");
    c.append(node("h2", null, "Settings"));
    c.append(node("p", "empty", "Blank values fall back to MiniDLNA defaults. Changes need a restart."));
    const singles = d.config.singles || {};
    const grid = node("div", "cards");
    SETTINGS.forEach(([key, label, kind]) => {
        const f = node("form", "field");
        f.append(node("label", "flabel", label + "  (" + key + ")"));
        const cur = singles[key] !== undefined ? singles[key] : "";
        let input;
        if (kind === "bool") {
            input = node("select");
            [["", "(default)"], ["yes", "yes"], ["no", "no"]].forEach(([v, l]) => {
                const o = document.createElement("option"); o.value = v; o.textContent = l;
                if (v === cur) o.selected = true; input.append(o);
            });
        } else {
            input = node("input"); input.type = "text"; input.value = cur;
            input.placeholder = "(default)";
        }
        const row = node("div", "row-inline");
        row.append(input);
        const save = node("button", "btn sm", "Save"); save.type = "submit";
        row.append(save);
        f.append(row);
        f.addEventListener("submit", (e) => {
            e.preventDefault();
            const v = input.value.trim();
            if (v === "") doAction("unset", { key });
            else doAction("set", { key, value: v });
        });
        grid.append(f);
    });
    c.append(grid);
    return c;
}

/* ---------- logs ---------- */

function logCard(d) {
    const c = node("section", "card full");
    c.append(node("h2", null, "Log"));
    if (!d.log.exists) {
        c.append(node("p", "empty", `No log file at ${d.log.path}.`));
        return c;
    }
    c.append(node("dl", "kv")).append(node("dd", "mono", d.log.path));
    c.append(node("pre", "logbox", d.log.text || "(empty)"));
    return c;
}

/* ---------- confirm dialog ---------- */

function confirmDialog(message, onYes) {
    clearError();
    const overlay = node("div", "overlay");
    const dlg = node("div", "dialog");
    dlg.append(node("h3", null, "Confirm"));
    dlg.append(node("p", null, message));
    const bar = node("div", "dialog-bar");
    const cancel = node("button", "btn ghost", "Cancel");
    const ok = node("button", "btn danger", "Proceed");
    bar.append(cancel, ok); dlg.append(bar); overlay.append(dlg);
    document.body.append(overlay);
    const close = () => overlay.remove();
    cancel.addEventListener("click", close);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
    ok.addEventListener("click", () => { close(); onYes(); });
}

/* ---------- render ---------- */

function render(d) {
    DATA = d;
    const content = el("content");
    content.textContent = "";
    const top = node("div", "cards");
    top.append(serviceCard(d));
    top.append(libraryCard(d));
    content.append(top);
    content.append(clientsCard(d));
    content.append(mediaDirsCard(d));
    content.append(settingsCard(d));
    content.append(logCard(d));
}

function load() {
    clearError();
    setStatus("Loading…");
    cockpit.spawn(["python3", STATUS], { superuser: "require", err: "message" })
        .then((out) => {
            let d; try { d = JSON.parse(out); }
            catch (e) { showError("Bad status output: " + e); setStatus(""); return; }
            if (d.error) { showError(d.error); setStatus(""); return; }
            render(d);
            setStatus("Updated " + new Date().toLocaleTimeString());
        })
        .catch((ex) => { showError("Failed to load status: " + (ex.message || ex)); setStatus(""); });
}

document.addEventListener("DOMContentLoaded", () => {
    el("refresh").addEventListener("click", load);
    load();
});
