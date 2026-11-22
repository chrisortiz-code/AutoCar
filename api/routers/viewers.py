"""
Viewer pages served from the unified API.

Repackages the standalone HTML viewers (lidar, camera, obstacles, drive,
paths) with fetch URLs rewritten to use /api/* endpoints so only the API
needs to run.
"""

import os

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from lidar.page_html import get_html as get_lidar_html

router = APIRouter(prefix="/viewer", tags=["viewers"])

# ── Load and rewrite GUI HTML files ──────────────────────────────────

_GUI_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "gui")


def _load_gui(filename, replacements):
    """Load an HTML file from gui/ and apply URL rewrites."""
    path = os.path.join(_GUI_DIR, filename)
    with open(path, encoding="utf-8") as f:
        html = f.read()
    for old, new in replacements:
        html = html.replace(old, new)
    return html

# ── Lidar polar plot (shared template from lidar.page_html) ──────────

_LIDAR_HTML = get_lidar_html("/api/sensors/lidar/scan")


# ── Camera RGB + depth ───────────────────────────────────────────────

_CAMERA_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>D435 Camera Viewer</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #1a1a1a; color: #d4d4d4;
         font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         display: flex; flex-direction: column; align-items: center;
         min-height: 100vh; padding: 16px; }
  header { display: flex; align-items: baseline; gap: 12px; width: min(1320px, 96vw);
           margin-bottom: 8px; }
  header h1 { font-size: 14px; font-weight: 600; color: #e0e0e0; }
  #badge { font-size: 11px; margin-left: auto; }
  #badge.ok { color: #5a9a5a; }
  #badge.err { color: #c87830; }
  .feeds { display: flex; gap: 12px; flex-wrap: wrap; justify-content: center; }
  .feed { display: flex; flex-direction: column; align-items: center; }
  .feed-label { font-size: 12px; font-weight: 600; color: #888; margin-bottom: 4px;
                text-transform: uppercase; letter-spacing: 1px; }
  .feed img { border: 1px solid #333; background: #111; display: block; }
  #stats { margin-top: 8px; width: min(1320px, 96vw); font-size: 12px; color: #888;
           display: flex; flex-wrap: wrap; gap: 14px 18px; padding: 6px 0;
           border-bottom: 1px solid #2a2a2a; }
  #stats .v { color: #b0b0b0; }
  #error { margin-top: 8px; width: min(1320px, 96vw); font-size: 12px; color: #c87830;
           min-height: 18px; }
</style>
</head>
<body>
  <header>
    <h1>RealSense D435</h1>
    <span id="badge" class="err">connecting</span>
  </header>
  <div class="feeds">
    <div class="feed">
      <span class="feed-label">RGB</span>
      <img id="rgb" src="/api/sensors/camera/rgb" width="640" height="480" />
    </div>
    <div class="feed">
      <span class="feed-label">Depth</span>
      <img id="depth" src="/api/sensors/camera/depth" width="640" height="480" />
    </div>
  </div>
  <div id="stats">
    <span>fps <span class="v" id="s-fps">--</span></span>
    <span>frames <span class="v" id="s-frames">0</span></span>
    <span>resolution <span class="v" id="s-res">--</span></span>
  </div>
  <div id="error"></div>
<script>
const badge = document.getElementById('badge');
const sFps = document.getElementById('s-fps');
const sFrames = document.getElementById('s-frames');
const sRes = document.getElementById('s-res');
const errEl = document.getElementById('error');

async function pollStatus() {
  try {
    const r = await fetch('/api/sensors/status');
    const s = await r.json();
    const cam = s.camera || {};
    sFps.textContent = cam.fps || '--';
    sFrames.textContent = cam.frame_count || 0;
    if (cam.resolution) {
      sRes.textContent = cam.resolution[0] + 'x' + cam.resolution[1];
    }
    if (cam.connected) {
      badge.textContent = 'live';
      badge.className = 'ok';
      errEl.textContent = '';
    } else {
      badge.textContent = 'disconnected';
      badge.className = 'err';
      errEl.textContent = cam.error || 'waiting for camera...';
    }
  } catch (e) {}
}

setInterval(pollStatus, 1000);
pollStatus();
</script>
</body>
</html>"""


# ── Obstacle detection viewer ────────────────────────────────────────

_OBSTACLES_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Obstacle Viewer</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #1a1a1a; color: #d4d4d4;
         font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         display: flex; flex-direction: column; align-items: center;
         min-height: 100vh; padding: 16px; }
  header { display: flex; align-items: baseline; gap: 12px; width: 640px;
           margin-bottom: 8px; }
  header h1 { font-size: 14px; font-weight: 600; color: #e0e0e0; }
  #badge { font-size: 11px; color: #777; margin-left: auto; }
  #feed { border: 1px solid #333; display: block; }
  #stats { margin-top: 8px; width: 640px; font-size: 12px; color: #888;
           display: flex; gap: 18px; padding: 6px 0;
           border-bottom: 1px solid #2a2a2a; }
  #stats .v { color: #b0b0b0; }
  #detections { margin-top: 4px; width: 640px; max-height: 260px;
                overflow-y: auto; }
  .det-row { display: flex; align-items: center; padding: 4px 0;
             border-bottom: 1px solid #222; font-size: 12px; }
  .det-row:last-child { border-bottom: none; }
  .det-cls { font-weight: 500; min-width: 100px; }
  .det-bar { height: 2px; margin: 0 12px; flex: 1; max-width: 140px;
             background: #2a2a2a; }
  .det-bar-fill { height: 100%; }
  .det-pct { color: #777; font-size: 11px; min-width: 32px; text-align: right; }
  .empty { padding: 12px 0; color: #555; font-size: 12px; }
</style>
</head>
<body>
  <header>
    <h1>Obstacle Viewer</h1>
    <span id="badge">--</span>
  </header>
  <img id="feed" src="/api/obstacles/stream" width="640" height="480" />
  <div id="stats">
    <span>latency <span class="v" id="s-lat">--</span></span>
    <span>fps <span class="v" id="s-fps">--</span></span>
    <span>objects <span class="v" id="s-cnt">0</span></span>
  </div>
  <div id="detections"><div class="empty">waiting for detections</div></div>
<script>
const badge = document.getElementById('badge');
const sLat = document.getElementById('s-lat');
const sFps = document.getElementById('s-fps');
const sCnt = document.getElementById('s-cnt');
const detsEl = document.getElementById('detections');

setInterval(async () => {
  try {
    const r = await fetch('/api/obstacles/status');
    const s = await r.json();
    badge.textContent = s.count > 0 ? s.count + ' detected' : 'scanning';
    sLat.textContent = s.inf_ms.toFixed(1) + 'ms';
    sFps.textContent = s.fps.toFixed(0);
    sCnt.textContent = s.count;

    if (s.detections.length === 0) {
      detsEl.innerHTML = '<div class="empty">no objects detected</div>';
      return;
    }
    let html = '';
    for (const d of s.detections) {
      const pct = (d.conf * 100).toFixed(0);
      html += '<div class="det-row">' +
        '<span class="det-cls" style="color:' + d.colour + '">' + d.cls + '</span>' +
        '<div class="det-bar"><div class="det-bar-fill" style="width:' +
        pct + '%;background:' + d.colour + '"></div></div>' +
        '<span class="det-pct">' + pct + '%</span></div>';
    }
    detsEl.innerHTML = html;
  } catch(e) {}
}, 150);
</script>
</body>
</html>"""


# ── Dashboard — read-only mirror of the Android app ─────────────────

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>AutoCar Dashboard</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #1a1a1a; color: #d4d4d4;
         font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         display: flex; flex-direction: column; align-items: center;
         min-height: 100vh; padding: 16px; }
  h1 { font-size: 22px; font-weight: 700; color: #d4a843; margin-bottom: 12px; }

  /* Status cards */
  .cards { display: flex; flex-direction: column; gap: 8px; width: min(600px, 96vw);
           margin-bottom: 12px; }
  .card { display: flex; align-items: center; background: #222; border: 1px solid #333;
          border-radius: 8px; padding: 12px 16px; }
  .dot { width: 12px; height: 12px; border-radius: 50%; margin-right: 12px; flex-shrink: 0; }
  .dot.on  { background: #5a9a5a; box-shadow: 0 0 6px #5a9a5a88; }
  .dot.off { background: #c44; box-shadow: 0 0 6px #c4444488; }
  .card-title { font-size: 14px; font-weight: 600; color: #d4a843; min-width: 80px; }
  .card-detail { font-size: 13px; color: #aaa; margin-left: auto; }

  /* Motor grid */
  .motors-label { font-size: 14px; font-weight: 600; color: #d4a843; margin: 8px 0 4px;
                  width: min(600px, 96vw); }
  .motors { display: flex; gap: 8px; width: min(600px, 96vw); margin-bottom: 12px; }
  .motor { flex: 1; background: #222; border: 1px solid #333; border-radius: 8px;
           padding: 10px; text-align: center; }
  .motor-role { font-size: 13px; font-weight: 600; display: flex; align-items: center;
                justify-content: center; gap: 4px; }
  .motor-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
  .motor-current { font-size: 11px; color: #888; margin-top: 2px; }
  .motor-err { font-size: 10px; color: #c44; }

  /* Feed toggles */
  .toggles { display: flex; gap: 8px; width: min(600px, 96vw); margin-bottom: 8px; }
  .toggle-btn { background: #2a2a2a; border: 1px solid #444; color: #aaa;
                font-size: 11px; font-weight: 600; text-transform: uppercase;
                letter-spacing: 1px; padding: 5px 14px; border-radius: 4px;
                cursor: pointer; transition: all 0.15s; user-select: none; }
  .toggle-btn.active { background: #333; border-color: #d4a843; color: #d4d4d4; }

  /* Live feeds */
  .feeds { display: flex; flex-direction: column; gap: 10px; align-items: center;
           width: min(600px, 96vw); }
  .feed { width: 100%; }
  .feed.hidden { display: none; }
  .feed-label { font-size: 11px; font-weight: 600; color: #888; text-transform: uppercase;
                letter-spacing: 1px; margin-bottom: 4px; }
  .feed img { border: 1px solid #333; background: #111; display: block;
              width: 100%; height: auto; }
  .feed iframe { border: 1px solid #333; background: #111; display: block;
                 width: 100%; aspect-ratio: 1; }

  /* WS status */
  #ws-badge { font-size: 11px; color: #888; margin-bottom: 12px; }
  #ws-badge.ok { color: #5a9a5a; }
  #ws-badge.err { color: #c87830; }
</style>
</head>
<body>
  <h1>AutoCar Dashboard</h1>
  <span id="ws-badge" class="err">connecting...</span>

  <div class="cards">
    <div class="card" id="c-api">
      <span class="dot on"></span>
      <span class="card-title">API</span>
      <span class="card-detail">Online</span>
    </div>
    <div class="card" id="c-lidar">
      <span class="dot off"></span>
      <span class="card-title">Lidar</span>
      <span class="card-detail" id="d-lidar">--</span>
    </div>
    <div class="card" id="c-camera">
      <span class="dot off"></span>
      <span class="card-title">Camera</span>
      <span class="card-detail" id="d-camera">--</span>
    </div>
    <div class="card" id="c-drive">
      <span class="dot off"></span>
      <span class="card-title">Drive</span>
      <span class="card-detail" id="d-drive">--</span>
    </div>
  </div>

  <div class="motors-label" id="motors-label" style="display:none">Motors</div>
  <div class="motors" id="motors" style="display:none">
    <div class="motor" id="m-0"><div class="motor-role"><span class="motor-dot"></span> BL</div><div class="motor-current">--</div></div>
    <div class="motor" id="m-1"><div class="motor-role"><span class="motor-dot"></span> FL</div><div class="motor-current">--</div></div>
    <div class="motor" id="m-2"><div class="motor-role"><span class="motor-dot"></span> BR</div><div class="motor-current">--</div></div>
    <div class="motor" id="m-3"><div class="motor-role"><span class="motor-dot"></span> FR</div><div class="motor-current">--</div></div>
  </div>

  <div class="toggles">
    <button class="toggle-btn" data-feed="feed-rgb" id="btn-rgb">RGB</button>
    <button class="toggle-btn" data-feed="feed-depth" id="btn-depth">Depth</button>
    <button class="toggle-btn" data-feed="feed-lidar" id="btn-lidar">Lidar</button>
  </div>

  <div class="toggles" style="margin-top:0;">
    <a class="toggle-btn" href="/viewer/drive" target="_blank" style="text-decoration:none;text-align:center;">Drive</a>
    <a class="toggle-btn" href="/viewer/paths" target="_blank" style="text-decoration:none;text-align:center;">Paths</a>
    <a class="toggle-btn" href="/viewer/obstacles" target="_blank" style="text-decoration:none;text-align:center;">Obstacles</a>
  </div>

  <div class="feeds">
    <div class="feed hidden" id="feed-rgb">
      <div class="feed-label">RGB</div>
      <img id="img-rgb" src="" />
    </div>
    <div class="feed hidden" id="feed-depth">
      <div class="feed-label">Depth</div>
      <img id="img-depth" src="" />
    </div>
    <div class="feed hidden" id="feed-lidar">
      <div class="feed-label">Lidar</div>
      <iframe src="" frameborder="0" scrolling="no" id="iframe-lidar"></iframe>
    </div>
  </div>

<script>
// ── Toggle buttons ──
document.querySelectorAll('.toggle-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    btn.classList.toggle('active');
    const feed = document.getElementById(btn.dataset.feed);
    const on = btn.classList.contains('active');
    feed.classList.toggle('hidden', !on);
    // Start/stop MJPEG streams to save bandwidth
    if (btn.id === 'btn-rgb') {
      document.getElementById('img-rgb').src = on ? '/api/sensors/camera/rgb' : '';
    } else if (btn.id === 'btn-depth') {
      document.getElementById('img-depth').src = on ? '/api/sensors/camera/depth' : '';
    } else if (btn.id === 'btn-lidar') {
      document.getElementById('iframe-lidar').src = on ? '/viewer/lidar' : '';
    }
  });
});

// ── WebSocket ──
const wsBadge = document.getElementById('ws-badge');

function setCard(id, on, detail) {
  const card = document.getElementById(id);
  const dot = card.querySelector('.dot');
  dot.className = 'dot ' + (on ? 'on' : 'off');
  const detailEl = card.querySelector('.card-detail');
  if (detailEl) detailEl.textContent = detail;
}

function updateMotors(m) {
  const wrap = document.getElementById('motors');
  const label = document.getElementById('motors-label');
  wrap.style.display = 'flex';
  label.style.display = 'block';
  for (let nid = 0; nid < 4; nid++) {
    const el = document.getElementById('m-' + nid);
    const motor = m.motors[String(nid)];
    const isConn = m.connected.includes(nid);
    const dot = el.querySelector('.motor-dot');
    const curr = el.querySelector('.motor-current');
    let errEl = el.querySelector('.motor-err');
    if (motor) {
      const role = motor.role || ['BL','FL','BR','FR'][nid];
      el.querySelector('.motor-role').innerHTML =
        '<span class="motor-dot"></span> ' + role;
      const newDot = el.querySelector('.motor-dot');
      if (motor.error && motor.error !== 0) {
        newDot.style.background = '#c44';
        if (!errEl) { errEl = document.createElement('div'); errEl.className = 'motor-err'; el.appendChild(errEl); }
        errEl.textContent = 'ERR';
      } else if (!isConn) {
        newDot.style.background = '#c44';
        if (errEl) errEl.remove();
      } else if (motor.armed) {
        newDot.style.background = '#5a9a5a';
        if (errEl) errEl.remove();
      } else {
        newDot.style.background = '#c87830';
        if (errEl) errEl.remove();
      }
      if (isConn) {
        curr.textContent = motor.current.toFixed(1) + 'A';
      } else {
        curr.textContent = 'N/C';
        curr.style.color = '#c44';
      }
    }
  }
}

function onMessage(msg) {
  // Lidar
  const li = msg.lidar || {};
  setCard('c-lidar', li.connected,
    li.connected ? li.scan_hz.toFixed(1) + ' Hz' : 'Disconnected');

  // Camera
  const cam = msg.camera || {};
  setCard('c-camera', cam.connected,
    cam.connected ? Math.round(cam.fps) + ' FPS' : 'Disconnected');

  // Drive
  const dr = msg.drive || {};
  const mot = msg.motors || {};
  const recvOnline = mot.receiver_online || false;
  const nConn = (mot.connected || []).length;
  let driveDetail = '--';
  if (recvOnline) {
    const state = dr.moving ? 'Moving' : 'Idle';
    driveDetail = state + ' | ' + nConn + '/4 motors';
  } else if (dr.moving !== undefined) {
    driveDetail = dr.moving ? 'Moving (' + dr.mode + ')' : 'Idle';
  }
  setCard('c-drive', recvOnline, driveDetail);

  // Motors
  if (mot.motors) updateMotors(mot);
}

let ws = null;
let wsRetry = null;

function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = proto + '//' + location.host + '/api/ws/sensors';
  ws = new WebSocket(url);
  ws.onopen = () => {
    wsBadge.textContent = 'live';
    wsBadge.className = 'ok';
  };
  ws.onmessage = (e) => {
    try { onMessage(JSON.parse(e.data)); } catch (_) {}
  };
  ws.onclose = ws.onerror = () => {
    wsBadge.textContent = 'reconnecting...';
    wsBadge.className = 'err';
    if (wsRetry) clearTimeout(wsRetry);
    wsRetry = setTimeout(connectWS, 2000);
  };
}

connectWS();
</script>
</body>
</html>"""


# ── Drive GUI (mecanum controller) ───────────────────────────────────

_DRIVE_HTML = _load_gui("mecanum_gui.html", [
    # Rewrite endpoint URLs in template literals (before clearing API base)
    ("${API}/move_polar", "${API}/api/drive/polar"),
    ("${API}/move_translate_rotate", "${API}/api/drive/translate-rotate"),
    ("${API}/estop", "${API}/api/drive/estop"),
    ("${API}/home", "${API}/api/drive/stop"),
    ("${API}/status", "${API}/api/drive/status"),
    # Endpoint string in variable assignment
    ("'/move_polar'", "'/api/drive/polar'"),
    ("'/move_translate_rotate'", "'/api/drive/translate-rotate'"),
    # Clear API base (after rewriting, so ${API} prefix disappears cleanly)
    ("const API = `${window.location.protocol}//${window.location.host}`;",
     "const API = '';"),
])

# ── Path GUI (path controller) ──────────────────────────────────────

_PATHS_HTML = _load_gui("path_gui.html", [
    # Rewrite API base — inject /api prefix so api() helper builds correct URLs
    ("const API = location.origin;", "const API = '';"),
    # Single-quoted paths in api() calls
    ("'/paths'", "'/api/paths'"),
    ("'/record/", "'/api/record/"),
    ("'/stop'", "'/api/drive/stop'"),
    ("'/status'", "'/api/status'"),
    # Template-literal paths in api() calls: api(`/paths/${id}`)
    ("`/paths/", "`/api/paths/"),
    # Mecanum GUI link
    ("const MECANUM_GUI_URL = `${location.protocol}//${location.hostname}:5000`;",
     "const MECANUM_GUI_URL = '/viewer/drive';"),
])


# ── Endpoints ────────────────────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
def viewer_dashboard():
    return _DASHBOARD_HTML


@router.get("/lidar", response_class=HTMLResponse)
def viewer_lidar():
    return _LIDAR_HTML


@router.get("/camera", response_class=HTMLResponse)
def viewer_camera():
    return _CAMERA_HTML


@router.get("/obstacles", response_class=HTMLResponse)
def viewer_obstacles():
    return _OBSTACLES_HTML


@router.get("/drive", response_class=HTMLResponse)
def viewer_drive():
    return _DRIVE_HTML


@router.get("/paths", response_class=HTMLResponse)
def viewer_paths():
    return _PATHS_HTML


# ── RL Viewport ─────────────────────────────────────────────────────

_RL_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>RL Viewport</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a0a14;color:#e0e0e0;font-family:system-ui,sans-serif;padding:8px}
h1{color:#c8a94e;font-size:1.2rem;margin-bottom:8px}
.row{display:flex;gap:6px;margin-bottom:6px;flex-wrap:wrap}
.col{flex:1;min-width:0}
.feed-box{position:relative;background:#111;border:1px solid #c8a94e44;border-radius:8px;overflow:hidden}
.feed-box img{width:100%;height:100%;object-fit:contain;display:block}
.feed-label{position:absolute;top:4px;left:6px;font-size:11px;color:#c8a94e;font-weight:600;z-index:2;
  text-shadow:0 0 4px #000}
.feed-box.rgb{height:260px;cursor:crosshair}
.feed-box.small{height:180px}
#lidar-canvas{width:100%;height:100%;display:block}
.controls{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:6px}
.controls select,.controls button,.controls input{
  background:#1a1a2e;color:#e0e0e0;border:1px solid #c8a94e66;border-radius:6px;padding:6px 10px;font-size:13px}
.controls button{cursor:pointer}
.controls button:hover{background:#c8a94e33}
.controls button.stop{background:#c44;color:#fff;border-color:#c44}
.controls button.stop:hover{background:#a33}
.stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(80px,1fr));gap:4px;margin-bottom:6px}
.stat{background:#1a1a2e;border-radius:6px;padding:4px 6px;text-align:center}
.stat .label{font-size:10px;color:#c8a94e}
.stat .val{font-size:13px;font-family:monospace;font-weight:600}
.bar-row{display:flex;align-items:center;gap:6px;margin-bottom:3px}
.bar-label{width:40px;font-size:11px;color:#c8a94e;text-align:right}
.bar-track{flex:1;height:12px;background:#1a1a2e;border-radius:6px;position:relative;overflow:hidden}
.bar-fill{height:100%;border-radius:6px;transition:width .15s}
.bar-val{width:44px;font-size:11px;font-family:monospace;text-align:left}
.section-label{font-size:11px;color:#c8a94e;margin:4px 0 2px;font-weight:600}
.latency-bar{height:16px;border-radius:6px;display:flex;overflow:hidden;margin-bottom:4px}
.lat-csrt{background:#4488cc}
.lat-policy{background:#8844cc}
.lat-label{font-size:10px;color:#fff;padding:0 4px;line-height:16px;white-space:nowrap}
.safety-warn{color:#c44;font-weight:700;text-align:center;font-size:13px;margin:4px 0}
.status-chip{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}
.status-chip.idle{background:#333;color:#888}
.status-chip.streaming{background:#c8a94e33;color:#c8a94e}
.status-chip.tracking{background:#5a9a5a33;color:#5a9a5a}
.status-chip.lost{background:#c4433b33;color:#c44}
.hint{color:#c8a94e;font-size:12px;margin:4px 0}
</style>
</head>
<body>
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
  <h1>RL Viewport</h1>
  <span class="status-chip idle" id="chip">idle</span>
</div>

<div id="idle-panel">
  <div class="controls">
    <select id="sel-ckpt"><option value="">Default model</option></select>
    <select id="sel-backend">
      <option value="csrt">CSRT</option><option value="orb">ORB</option>
      <option value="akaze">AKAZE</option><option value="sift">SIFT</option>
      <option value="lightglue">LightGlue</option>
    </select>
    <label style="display:flex;align-items:center;gap:4px;font-size:12px">
      <input type="checkbox" id="chk-safety"/> Safety
    </label>
    <button id="btn-start">Start</button>
  </div>
</div>

<div id="active-panel" style="display:none">
  <!-- RGB stream -->
  <div class="feed-box rgb" id="rgb-box">
    <span class="feed-label">RGB</span>
    <img id="img-rgb" src=""/>
    <canvas id="draw-canvas" style="position:absolute;top:0;left:0;width:100%;height:100%;z-index:1"></canvas>
    <span class="hint" id="draw-hint" style="position:absolute;top:4px;right:6px;z-index:2"></span>
  </div>

  <!-- Depth + LiDAR -->
  <div class="row" style="margin-top:6px">
    <div class="col">
      <div class="feed-box small">
        <span class="feed-label">DEPTH</span>
        <img id="img-depth" src=""/>
      </div>
    </div>
    <div class="col">
      <div class="feed-box small" style="background:#0a0a14">
        <span class="feed-label">LIDAR</span>
        <canvas id="lidar-canvas"></canvas>
      </div>
    </div>
  </div>

  <!-- Telemetry -->
  <div id="telem">
    <div class="stats" id="model-stats"></div>
    <div id="latency-section" style="display:none">
      <div class="section-label">Pipeline</div>
      <div class="latency-bar" id="latency-bar"></div>
    </div>
    <div id="actions-section" style="display:none">
      <div class="section-label">Actions</div>
      <div id="action-bars"></div>
    </div>
    <div class="stats" id="motor-cmd-stats"></div>
    <div class="stats" id="depth-stats"></div>
    <div id="motor-hw-section">
      <div class="section-label">Motors</div>
      <div class="stats" id="motor-hw-stats"></div>
    </div>
    <div class="safety-warn" id="safety-warn" style="display:none">SAFETY ACTIVE</div>
  </div>

  <div class="controls" style="margin-top:6px">
    <button id="btn-reset">Reset</button>
    <button id="btn-stop" class="stop">Stop</button>
  </div>
</div>

<script>
const BASE = '';
let polling = null, lidarPoll = null, motorPoll = null;

// ── Init ──
fetch(BASE+'/api/rl/checkpoints').then(r=>r.json()).then(d=>{
  const sel = document.getElementById('sel-ckpt');
  (d.checkpoints||[]).forEach(c=>{
    const o = document.createElement('option');
    o.value = c; o.textContent = c.replace('.pt','');
    sel.appendChild(o);
  });
}).catch(()=>{});

fetch(BASE+'/api/safety/status').then(r=>r.json()).then(d=>{
  document.getElementById('chk-safety').checked = d.enabled;
}).catch(()=>{});

// ── Start / Stop ──
document.getElementById('btn-start').onclick = async ()=>{
  const ckpt = document.getElementById('sel-ckpt').value;
  const backend = document.getElementById('sel-backend').value;
  const safety = document.getElementById('chk-safety').checked;
  try {
    if (safety) await fetch(BASE+'/api/safety/enable',{method:'POST'});
    else await fetch(BASE+'/api/safety/disable',{method:'POST'});
  } catch(e){}
  const body = {desired_dist:1.0, backend};
  if (ckpt) body.checkpoint = 'rl_policy/checkpoints/'+ckpt;
  const r = await fetch(BASE+'/api/rl/start',{method:'POST',
    headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  if (r.ok) activateUI();
};

document.getElementById('btn-stop').onclick = async ()=>{
  await fetch(BASE+'/api/rl/stop',{method:'POST'});
  deactivateUI();
};

document.getElementById('btn-reset').onclick = ()=>{
  fetch(BASE+'/api/rl/reset',{method:'POST'});
};

function activateUI(){
  document.getElementById('idle-panel').style.display='none';
  document.getElementById('active-panel').style.display='block';
  document.getElementById('img-rgb').src = BASE+'/api/rl/stream';
  document.getElementById('img-depth').src = BASE+'/api/rl/depth_stream';
  startPolling();
  startLidarPolling();
  startMotorPolling();
}

function deactivateUI(){
  stopPolling();
  document.getElementById('idle-panel').style.display='block';
  document.getElementById('active-panel').style.display='none';
  document.getElementById('img-rgb').src='';
  document.getElementById('img-depth').src='';
}

// ── Check if already running on load ──
fetch(BASE+'/api/rl/status').then(r=>r.json()).then(d=>{
  if (d.status && d.status!=='idle') activateUI();
}).catch(()=>{});

// ── Status polling ──
function startPolling(){
  if (polling) return;
  polling = setInterval(async ()=>{
    try {
      const r = await fetch(BASE+'/api/rl/status');
      const s = await r.json();
      updateTelemetry(s);
    } catch(e){}
  }, 250);
}

function startMotorPolling(){
  if (motorPoll) return;
  motorPoll = setInterval(async ()=>{
    try {
      const r = await fetch(BASE+'/api/drive/status');
      const d = await r.json();
      updateMotorHw(d);
    } catch(e){}
  }, 1000);
}

function stopPolling(){
  clearInterval(polling); polling=null;
  clearInterval(lidarPoll); lidarPoll=null;
  clearInterval(motorPoll); motorPoll=null;
}

function updateTelemetry(s){
  // Chip
  const chip = document.getElementById('chip');
  chip.textContent = s.status;
  chip.className = 'status-chip '+(s.status||'idle');

  // Hint
  const hint = document.getElementById('draw-hint');
  hint.textContent = s.status==='streaming' ? 'Draw box on target' : '';

  // Model stats
  const ms = document.getElementById('model-stats');
  ms.innerHTML = stat('Model', s.model_name||s.model_variant||'--')
    + stat('Actions', s.action_dim+'D')
    + stat('LSTM', '#'+s.lstm_step)
    + stat('FPS', (s.fps||0).toFixed(1));

  // Latency
  const ls = document.getElementById('latency-section');
  if (s.total_ms > 0){
    ls.style.display='block';
    const pct_c = (s.csrt_ms/s.total_ms*100).toFixed(0);
    const pct_p = (s.policy_ms/s.total_ms*100).toFixed(0);
    document.getElementById('latency-bar').innerHTML =
      '<div class="lat-csrt" style="width:'+pct_c+'%"><span class="lat-label">CSRT '+s.csrt_ms.toFixed(1)+'ms</span></div>'
      +'<div class="lat-policy" style="width:'+pct_p+'%"><span class="lat-label">Policy '+s.policy_ms.toFixed(1)+'ms</span></div>';
  } else ls.style.display='none';

  // Actions
  const as = document.getElementById('actions-section');
  const ab = document.getElementById('action-bars');
  if (s.actions && s.actions.length){
    as.style.display='block';
    const names = s.action_dim===2 ? ['Fwd','Yaw'] : ['Fwd','Strafe','Yaw'];
    ab.innerHTML = s.actions.map((v,i)=>{
      const pct = ((v+1)/2*100).toFixed(0);
      const col = v>=0 ? '#5a9a5a' : '#c44';
      return '<div class="bar-row"><span class="bar-label">'+names[i]+'</span>'
        +'<div class="bar-track"><div class="bar-fill" style="width:'+pct+'%;background:'+col+'"></div></div>'
        +'<span class="bar-val">'+v.toFixed(2)+'</span></div>';
    }).join('');
  } else as.style.display='none';

  // Motor commands
  if (s.motor_cmd && s.motor_cmd.length===4){
    document.getElementById('motor-cmd-stats').innerHTML =
      stat('Vx',s.motor_cmd[0].toFixed(2))+stat('Vy',s.motor_cmd[1].toFixed(2))
      +stat('Spd',s.motor_cmd[2].toFixed(2))+stat('Rot',s.motor_cmd[3].toFixed(2));
  }

  // Depth
  document.getElementById('depth-stats').innerHTML =
    stat('Conf',(s.confidence*100).toFixed(0)+'%')
    +stat('Target',s.target_depth>0?s.target_depth+'mm':'--')
    +stat('Depth',s.cur_depth>0?s.cur_depth+'mm':'--')
    +stat('Dist',(s.desired_dist||0).toFixed(2)+'m');

  // Safety
  const sw = document.getElementById('safety-warn');
  sw.style.display = s.safety_active ? 'block' : 'none';
}

function updateMotorHw(d){
  const el = document.getElementById('motor-hw-stats');
  const roles = {0:'BL',1:'FL',2:'BR',3:'FR'};
  let html = '';
  const rcvColor = d.receiver_online ? '#5a9a5a' : '#c44';
  html += '<div class="stat"><div class="label">Receiver</div><div class="val" style="color:'+rcvColor+'">'
    +(d.receiver_online?'Online':'Offline')+'</div></div>';
  for (let nid=0; nid<4; nid++){
    const m = d.motors && d.motors[String(nid)];
    const conn = (d.connected||[]).includes(nid);
    const armed = m && m.armed;
    let color = '#c44', label = 'N/C';
    if (conn && armed){ color='#5a9a5a'; label=m.current.toFixed(1)+'A'; }
    else if (conn){ color='#c87830'; label='Idle'; }
    html += '<div class="stat"><div class="label">'+(m?m.role:roles[nid])+'</div>'
      +'<div class="val" style="color:'+color+'">'+label+'</div></div>';
  }
  el.innerHTML = html;
}

function stat(label,val){
  return '<div class="stat"><div class="label">'+label+'</div><div class="val">'+val+'</div></div>';
}

// ── Bbox drawing on RGB canvas ──
// The image uses object-fit:contain, so we must map canvas coords
// to the actual image content rect (excluding letterbox bars).
const drawCanvas = document.getElementById('draw-canvas');
const drawCtx = drawCanvas.getContext('2d');
const rgbImg = document.getElementById('img-rgb');
let drawing=false, sx=0, sy=0;

function imageRect(){
  // Compute the rendered image rect within the container
  // accounting for object-fit: contain letterboxing
  const box = rgbImg.getBoundingClientRect();
  const natW = rgbImg.naturalWidth || 640;
  const natH = rgbImg.naturalHeight || 480;
  const scale = Math.min(box.width/natW, box.height/natH);
  const imgW = natW*scale, imgH = natH*scale;
  const offX = (box.width-imgW)/2, offY = (box.height-imgH)/2;
  return {left: box.left+offX, top: box.top+offY, width: imgW, height: imgH};
}

function imgCoords(e){
  const r = imageRect();
  const x = Math.max(0, Math.min(1, (e.clientX-r.left)/r.width));
  const y = Math.max(0, Math.min(1, (e.clientY-r.top)/r.height));
  return [x, y];
}

drawCanvas.onmousedown = e=>{ drawing=true; [sx,sy]=imgCoords(e); };
drawCanvas.onmousemove = e=>{
  if(!drawing) return;
  const [cx,cy] = imgCoords(e);
  const cRect = drawCanvas.getBoundingClientRect();
  drawCanvas.width = cRect.width; drawCanvas.height = cRect.height;
  // Draw the selection rect mapped back to canvas pixels
  const ir = imageRect();
  const offX = ir.left - cRect.left, offY = ir.top - cRect.top;
  drawCtx.strokeStyle='#0f0'; drawCtx.lineWidth=2; drawCtx.setLineDash([6,4]);
  drawCtx.strokeRect(
    offX + sx*ir.width, offY + sy*ir.height,
    (cx-sx)*ir.width, (cy-sy)*ir.height
  );
};
drawCanvas.onmouseup = e=>{
  if(!drawing) return; drawing=false;
  const [ex,ey] = imgCoords(e);
  const x1=Math.min(sx,ex), y1=Math.min(sy,ey), x2=Math.max(sx,ex), y2=Math.max(sy,ey);
  if (x2-x1>0.02 && y2-y1>0.02){
    fetch(BASE+'/api/rl/set_ref',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({x1,y1,x2,y2})});
  }
  const cRect = drawCanvas.getBoundingClientRect();
  drawCanvas.width = cRect.width; drawCanvas.height = cRect.height;
};

// Touch support for mobile browsers
drawCanvas.ontouchstart = e=>{
  e.preventDefault();
  const t = e.touches[0];
  drawing=true; [sx,sy]=imgCoords(t);
};
drawCanvas.ontouchmove = e=>{
  e.preventDefault();
  if(!drawing) return;
  const t = e.touches[0];
  const [cx,cy] = imgCoords(t);
  const cRect = drawCanvas.getBoundingClientRect();
  drawCanvas.width = cRect.width; drawCanvas.height = cRect.height;
  const ir = imageRect();
  const offX = ir.left - cRect.left, offY = ir.top - cRect.top;
  drawCtx.strokeStyle='#0f0'; drawCtx.lineWidth=2; drawCtx.setLineDash([6,4]);
  drawCtx.strokeRect(
    offX + sx*ir.width, offY + sy*ir.height,
    (cx-sx)*ir.width, (cy-sy)*ir.height
  );
};
drawCanvas.ontouchend = e=>{
  e.preventDefault();
  if(!drawing) return; drawing=false;
  const t = e.changedTouches[0];
  const [ex,ey] = imgCoords(t);
  const x1=Math.min(sx,ex), y1=Math.min(sy,ey), x2=Math.max(sx,ex), y2=Math.max(sy,ey);
  if (x2-x1>0.02 && y2-y1>0.02){
    fetch(BASE+'/api/rl/set_ref',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({x1,y1,x2,y2})});
  }
  const cRect = drawCanvas.getBoundingClientRect();
  drawCanvas.width = cRect.width; drawCanvas.height = cRect.height;
};

// ── LiDAR polar plot ──
function startLidarPolling(){
  if(lidarPoll) return;
  lidarPoll = setInterval(async ()=>{
    try{
      const r = await fetch(BASE+'/api/sensors/lidar/scan');
      const d = await r.json();
      drawLidar(d.points||[]);
    }catch(e){}
  }, 250);
}

function drawLidar(points){
  const c = document.getElementById('lidar-canvas');
  const box = c.parentElement.getBoundingClientRect();
  c.width = box.width; c.height = box.height;
  const ctx2 = c.getContext('2d');
  const cx = c.width/2, cy = c.height/2;
  const maxR = Math.min(cx,cy)-10;

  // Find max distance for scaling
  let maxDist = 3000;
  points.forEach(p=>{ if(p.dist_mm>maxDist) maxDist=p.dist_mm; });

  // Background
  ctx2.fillStyle='#0a0a14'; ctx2.fillRect(0,0,c.width,c.height);

  // Range rings
  ctx2.strokeStyle='#333'; ctx2.lineWidth=0.5;
  for(let i=1;i<=4;i++){
    ctx2.beginPath(); ctx2.arc(cx,cy,maxR*i/4,0,Math.PI*2); ctx2.stroke();
  }

  // Points
  points.forEach(p=>{
    if(p.dist_mm<=0) return;
    const r = (p.dist_mm/maxDist)*maxR;
    const a = (p.angle-90)*Math.PI/180;
    const px = cx + r*Math.cos(a);
    const py = cy + r*Math.sin(a);
    const ratio = p.dist_mm/maxDist;
    const red = Math.min(255, Math.floor(255*ratio));
    const green = Math.min(255, Math.floor(255*(1-ratio)));
    ctx2.fillStyle = 'rgb('+red+','+green+',80)';
    ctx2.fillRect(px-1,py-1,2,2);
  });

  // Center dot
  ctx2.fillStyle='#c8a94e'; ctx2.beginPath(); ctx2.arc(cx,cy,3,0,Math.PI*2); ctx2.fill();
}
</script>
</body>
</html>"""


@router.get("/rl", response_class=HTMLResponse)
def viewer_rl():
    return _RL_HTML
