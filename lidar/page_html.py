"""Shared HTML template for the lidar polar plot viewer.

Used by both lidar/viewer.py (standalone) and api/routers/viewers.py.
Call get_html(scan_url) with the fetch endpoint path.
"""

_TEMPLATE = """<!DOCTYPE html>
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
const SCAN_URL = '__SCAN_URL__';
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

// Segment threshold from URL: ?seg=12 (pixels, 0=off)
const urlParams = new URLSearchParams(window.location.search);
const SEG_PX = parseInt(urlParams.get('seg') ?? '12', 10);

function drawScan(points) {
  const valid = points.filter(p => p.dist_mm > 0);
  if (valid.length < MIN_POINTS) { drawGrid(rangeM); return; }
  // Use 95th percentile distance to ignore sparse noise at extreme ranges
  const dists = valid.map(p => p.dist_mm).sort((a, b) => a - b);
  const p95 = dists[Math.min(Math.floor(dists.length * 0.95), dists.length - 1)];
  const dataMaxM = p95 / 1000;
  const targetM = Math.max(MIN_RANGE_M, dataMaxM * 1.1);
  rangeM = rangeM + (targetM - rangeM) * 0.3;
  if (rangeM < MIN_RANGE_M) rangeM = MIN_RANGE_M;

  drawGrid(rangeM);
  valid.sort((a, b) => a.angle - b.angle);
  let prevX = null, prevY = null, prevAngle = -999;
  for (const p of valid) {
    const m = p.dist_mm / 1000;
    const rad = (p.angle - 90) * Math.PI / 180;
    const r = (m / rangeM) * R_MAX;
    const x = CX + r * Math.cos(rad);
    const y = CY + r * Math.sin(rad);
    const color = distColor(m, rangeM);
    const distGap = prevX !== null ? Math.hypot(x - prevX, y - prevY) : 999;
    if (SEG_PX > 0 && prevX !== null && (p.angle - prevAngle) < 2 && distGap < SEG_PX) {
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(prevX, prevY);
      ctx.lineTo(x, y);
      ctx.stroke();
    }
    ctx.fillStyle = color;
    ctx.fillRect(x - 2, y - 2, 4, 4);
    prevX = x; prevY = y; prevAngle = p.angle;
  }
}

drawGrid(rangeM);

let pollTimer = null;

async function poll() {
  try {
    const ctrl = new AbortController();
    const tid = setTimeout(() => ctrl.abort(), 3000);
    const r = await fetch(SCAN_URL, { signal: ctrl.signal });
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


def get_html(scan_url="/scan"):
    """Return the viewer HTML with the given scan endpoint URL."""
    return _TEMPLATE.replace("__SCAN_URL__", scan_url)
