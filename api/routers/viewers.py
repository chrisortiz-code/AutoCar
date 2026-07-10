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
