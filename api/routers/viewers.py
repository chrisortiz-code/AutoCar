"""
Viewer pages served from the unified API.

Repackages the standalone HTML viewers (lidar, camera, obstacles) with
fetch URLs rewritten to use /api/* endpoints so only the API needs to run.
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/viewer", tags=["viewers"])

# ── Lidar polar plot ─────────────────────────────────────────────────

_LIDAR_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>RPLIDAR Viewer</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #1a1a1a; color: #d4d4d4;
         font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         display: flex; flex-direction: column; align-items: center;
         min-height: 100vh; padding: 16px; }
  header { display: flex; align-items: baseline; gap: 12px; width: min(640px, 96vw);
           margin-bottom: 8px; }
  header h1 { font-size: 14px; font-weight: 600; color: #e0e0e0; }
  #badge { font-size: 11px; margin-left: auto; }
  #badge.ok { color: #5a9a5a; }
  #badge.err { color: #c87830; }
  #plot-wrap { position: relative; border: 1px solid #333; background: #111; }
  canvas { display: block; }
  #stats { margin-top: 8px; width: min(640px, 96vw); font-size: 12px; color: #888;
           display: flex; flex-wrap: wrap; gap: 14px 18px; padding: 6px 0;
           border-bottom: 1px solid #2a2a2a; }
  #stats .v { color: #b0b0b0; }
  #error { margin-top: 8px; width: min(640px, 96vw); font-size: 12px; color: #c87830;
           min-height: 18px; }
  #legend { margin-top: 8px; width: min(640px, 96vw); font-size: 11px; color: #666; }
  .bar { height: 8px; width: 160px; display: inline-block; vertical-align: middle;
         margin-left: 8px; border-radius: 2px;
         background: linear-gradient(90deg, #e6194b, #ffe119, #3cb44b); }
</style>
</head>
<body>
  <header>
    <h1>RPLIDAR Scan</h1>
    <span id="badge" class="err">connecting</span>
  </header>
  <div id="plot-wrap">
    <canvas id="plot" width="640" height="640"></canvas>
  </div>
  <div id="stats">
    <span>points <span class="v" id="s-cnt">0</span></span>
    <span>scan <span class="v" id="s-hz">--</span> Hz</span>
    <span>range <span class="v" id="s-range">--</span> m</span>
    <span>port <span class="v" id="s-port">--</span></span>
  </div>
  <div id="error"></div>
  <div id="legend">near <span class="bar"></span> far</div>
<script>
const canvas = document.getElementById('plot');
const ctx = canvas.getContext('2d');
const badge = document.getElementById('badge');
const sCnt = document.getElementById('s-cnt');
const sHz = document.getElementById('s-hz');
const sRange = document.getElementById('s-range');
const sPort = document.getElementById('s-port');
const errEl = document.getElementById('error');

const W = canvas.width;
const H = canvas.height;
const CX = W / 2;
const CY = H / 2;
const R_MAX = Math.min(W, H) * 0.46;
let rangeM = 1;

function distColor(m, maxM) {
  const t = Math.min(1, Math.max(0, m / maxM));
  const r = Math.round(255 * (1 - t));
  const g = Math.round(180 * t);
  const b = Math.round(60 * t);
  return 'rgb(' + r + ',' + g + ',' + b + ')';
}

function niceStep(maxM) {
  if (maxM <= 1) return 0.25;
  if (maxM <= 3) return 0.5;
  if (maxM <= 8) return 1;
  if (maxM <= 20) return 2;
  return 5;
}

function drawGrid(maxM) {
  ctx.fillStyle = '#111';
  ctx.fillRect(0, 0, W, H);
  ctx.strokeStyle = '#2a2a2a';
  ctx.lineWidth = 1;
  const step = niceStep(maxM);
  for (let m = step; m <= maxM; m += step) {
    const r = (m / maxM) * R_MAX;
    ctx.beginPath();
    ctx.arc(CX, CY, r, 0, Math.PI * 2);
    ctx.stroke();
  }
  ctx.strokeStyle = '#333';
  ctx.beginPath();
  ctx.moveTo(CX, CY - R_MAX); ctx.lineTo(CX, CY + R_MAX);
  ctx.moveTo(CX - R_MAX, CY); ctx.lineTo(CX + R_MAX, CY);
  ctx.stroke();
  ctx.fillStyle = '#666';
  ctx.font = '11px sans-serif';
  ctx.fillText('0 m', CX + 6, CY - 4);
  ctx.fillText(maxM.toFixed(1) + ' m', CX + 6, CY - R_MAX + 14);
  ctx.beginPath();
  ctx.fillStyle = '#888';
  ctx.arc(CX, CY, 4, 0, Math.PI * 2);
  ctx.fill();
}

const MIN_POINTS = 20;
const MIN_RANGE_M = 1.0;

function drawScan(points) {
  const valid = points.filter(p => p.dist_mm > 0);
  if (valid.length < MIN_POINTS) { drawGrid(rangeM); return; }
  let maxDist = 0;
  for (const p of valid) {
    if (p.dist_mm > maxDist) maxDist = p.dist_mm;
  }
  const dataMaxM = maxDist / 1000;
  const targetM = Math.max(MIN_RANGE_M, dataMaxM * 1.1);
  rangeM = rangeM + (targetM - rangeM) * 0.3;
  if (rangeM < MIN_RANGE_M) rangeM = MIN_RANGE_M;

  drawGrid(rangeM);
  for (const p of valid) {
    const m = p.dist_mm / 1000;
    const rad = (p.angle - 90) * Math.PI / 180;
    const r = (m / rangeM) * R_MAX;
    const x = CX + r * Math.cos(rad);
    const y = CY + r * Math.sin(rad);
    ctx.fillStyle = distColor(m, rangeM);
    ctx.fillRect(x - 1.5, y - 1.5, 3, 3);
  }
}

drawGrid(rangeM);

let pollTimer = null;

async function poll() {
  try {
    const ctrl = new AbortController();
    const tid = setTimeout(() => ctrl.abort(), 3000);
    const r = await fetch('/api/sensors/lidar/scan', { signal: ctrl.signal });
    clearTimeout(tid);
    const s = await r.json();
    sCnt.textContent = s.count;
    sHz.textContent = s.scan_hz ? s.scan_hz.toFixed(1) : '--';
    sPort.textContent = s.port || '--';
    if (s.min_mm != null && s.max_mm != null) {
      sRange.textContent = (s.min_mm / 1000).toFixed(2) + ' \\u2013 ' + (s.max_mm / 1000).toFixed(2);
    } else {
      sRange.textContent = '--';
    }
    if (s.connected) {
      badge.textContent = 'live';
      badge.className = 'ok';
      errEl.textContent = '';
    } else {
      badge.textContent = 'disconnected';
      badge.className = 'err';
      errEl.textContent = s.error || 'waiting for lidar...';
    }
    drawScan(s.points || []);
    schedulePoll(100);
  } catch (e) {
    badge.textContent = 'reconnecting';
    badge.className = 'err';
    errEl.textContent = 'connection lost \\u2014 retrying...';
    schedulePoll(2000);
  }
}

function schedulePoll(ms) {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = setTimeout(poll, ms);
}

poll();
</script>
</body>
</html>"""


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


# ── Endpoints ────────────────────────────────────────────────────────

@router.get("/lidar", response_class=HTMLResponse)
def viewer_lidar():
    return _LIDAR_HTML


@router.get("/camera", response_class=HTMLResponse)
def viewer_camera():
    return _CAMERA_HTML


@router.get("/obstacles", response_class=HTMLResponse)
def viewer_obstacles():
    return _OBSTACLES_HTML
