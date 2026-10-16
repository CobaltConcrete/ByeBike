const $ = id => document.getElementById(id);
const canvas = $('pathCanvas');
const ctx = canvas.getContext('2d');
const colors = {pedestrian: '#42b0e1', cycling: '#76ba57'};
let state = null, zones = [], draft = [], history = [], selected = -1;
let tool = 'polygon', kind = 'pedestrian', background = null, dirty = false;
let dragging = null, tracing = false, initialized = false, needsStill = false;
let loadedSource = null, evidenceSignature = '', toastTimer, frameBusy = false;
let browserCapture = null;

async function api(path, value) {
  const response = await fetch(path, value === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(value)
  });
  const result = await response.json();
  if (response.status === 401) location.assign('/login');
  if (!response.ok) throw new Error(result.error || `Request failed (${response.status})`);
  return result;
}
function toast(message, error = false) {
  $('toast').textContent = message;
  $('toast').classList.toggle('error', error);
  $('toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('toast').hidden = true, error ? 7000 : 3500);
}
function action(fn) { return async event => { try { await fn(event); } catch (error) { toast(error.message, true); } }; }
function clone(value) { return JSON.parse(JSON.stringify(value)); }
function remember() { history.push(clone(zones)); if (history.length > 60) history.shift(); }
function changed() { dirty = true; renderZones(); draw(); }
function syncRanges() {
  $('overlapValue').textContent = `${$('overlap').value}%`;
  $('dwellValue').textContent = `${Number($('dwell').value).toFixed(1)} s`;
  $('confidenceValue').textContent = `${$('confidence').value}%`;
}
function cameraFields() {
  const type = $('cameraKind').value;
  $('sourceField').hidden = ['demo','browser'].includes(type);
  $('cameraAdvanced').hidden = ['demo','browser'].includes(type);
  $('browserCameraField').hidden = type !== 'browser';
  $('sourceLabel').textContent = {usb:'Camera index', stream:'Camera stream URL', video:'Absolute video path'}[type] || 'Source';
  $('sourceHint').textContent = {usb:'Start with 0. Try 1 or 2 for another camera.', stream:'Use rtsp://… or a direct HTTP/MJPEG feed. Opened by this computer.', video:'A file on this computer, e.g. C:/Videos/pathway.mp4. Playback pauses during Path Setup.'}[type] || '';
  $('cameraSource').placeholder = {usb:'0', stream:'rtsp://user:password@192.168.1.10:554/stream', video:'C:/Videos/pathway.mp4'}[type] || '';
  $('cameraBackend').disabled = type === 'stream';
  if (type === 'stream') $('cameraBackend').value = 'ffmpeg';
}
function initializeFields(data) {
  const c = data.camera, r = data.rules, cloud = data.cloud.config;
  $('cameraKind').value = c.kind; $('cameraSource').value = c.source; $('cameraLabel').value = c.label;
  $('cameraBackend').value = c.backend; $('cameraWidth').value = c.width; $('cameraHeight').value = c.height;
  $('overlap').value = r.overlap * 100; $('dwell').value = r.dwell; $('confidence').value = r.confidence * 100;
  $('rearm').value = r.rearm; $('model').value = r.model;
  $('cloudEnabled').checked = cloud.enabled; $('cloudBucket').value = cloud.bucket; $('cloudProject').value = cloud.project;
  $('cloudPrefix').value = cloud.prefix; $('cloudCredentials').value = cloud.credentials_file;
  cameraFields(); syncRanges();
  if (data.hosted) {
    for (const option of $('cameraKind').options) if (['usb','video'].includes(option.value)) option.remove();
    $('hostingLabel').textContent = 'CLOUD RUN'; $('logoutButton').hidden = false;
    $('storageLabel').textContent = 'SAVED TO PRIVATE CLOUD STORAGE';
    $('storageFolder').textContent = `gs://${cloud.bucket}/${cloud.prefix}/`;
    $('cloudIntro').textContent = 'Cloud storage is configured by the deployment. Crops, metadata and saved paths survive container restarts. Full scene images are not stored.';
    for (const input of $('cloudDetails').querySelectorAll('input,button')) input.disabled = true;
    $('cloudCredentials').parentElement.querySelector('label[for="cloudCredentials"]').hidden = true;
    $('cloudCredentials').hidden = true;
    $('sourceHint').textContent = 'The cloud server must be able to reach this stream. Home/LAN camera addresses need a private network connection.';
  }
}

async function listBrowserDevices(selected = '') {
  const devices = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === 'videoinput');
  $('browserDevice').replaceChildren(new Option('Default camera', ''));
  devices.forEach((device, i) => $('browserDevice').add(new Option(device.label || `Camera ${i + 1}`, device.deviceId)));
  $('browserDevice').value = selected;
}
function stopBrowserCapture() {
  if (!browserCapture) return;
  const capture = browserCapture;
  browserCapture = null;
  clearTimeout(capture.timer);
  capture.stream.getTracks().forEach(track => track.stop());
  capture.video.srcObject = null;
}
async function startBrowserCapture(config) {
  if (!navigator.mediaDevices?.getUserMedia) throw new Error('Browser camera requires HTTPS or localhost.');
  const selected = $('browserDevice').value;
  const stream = await navigator.mediaDevices.getUserMedia({audio:false, video:{
    width:{ideal:1280}, height:{ideal:720}, ...(selected ? {deviceId:{exact:selected}} : {})}});
  const capture = {stream, video:document.createElement('video'), canvas:document.createElement('canvas'), token:null};
  browserCapture = capture;
  try {
    capture.video.muted = true; capture.video.playsInline = true; capture.video.srcObject = stream;
    await capture.video.play();
    const deviceId = stream.getVideoTracks()[0].getSettings().deviceId || 'default';
    await listBrowserDevices(deviceId);
    const result = await api('/api/camera', {...config, source:deviceId, backend:'auto'});
    capture.token = result.capture_token;
    state = {...state, ...result};
    stream.getVideoTracks()[0].addEventListener('ended', () => {
      if (browserCapture === capture) { stopBrowserCapture(); toast('Camera permission or device connection ended. Reconnect the camera.', true); }
    });
    const send = async () => {
      if (browserCapture !== capture) return;
      try {
        const scale = Math.min(1, 1280 / Math.max(capture.video.videoWidth, capture.video.videoHeight));
        capture.canvas.width = Math.round(capture.video.videoWidth * scale);
        capture.canvas.height = Math.round(capture.video.videoHeight * scale);
        capture.canvas.getContext('2d').drawImage(capture.video, 0, 0, capture.canvas.width, capture.canvas.height);
        const blob = await new Promise(resolve => capture.canvas.toBlob(resolve, 'image/jpeg', .8));
        if (browserCapture !== capture || !blob) return;
        const response = await fetch('/api/camera/frame', {method:'POST', headers:{'Content-Type':'image/jpeg', 'X-Camera-Token':capture.token}, body:blob});
        if (!response.ok) {
          const error = await response.json(); throw new Error(error.error || 'Camera upload failed.');
        }
        if (browserCapture === capture) capture.timer = setTimeout(send, 180);
      } catch (error) {
        if (browserCapture === capture) { stopBrowserCapture(); toast(`${error.message} Reconnect the camera to resume.`, true); }
      }
    };
    await send();
  } catch (error) { stopBrowserCapture(); throw error; }
}

async function poll() {
  try {
    const data = await api('/api/state');
    const previousMode = state?.mode;
    state = data;
    if (!initialized) { initializeFields(data); initialized = true; }
    if (loadedSource !== data.source_key) {
      loadedSource = data.source_key; zones = clone(data.zones); draft = []; history = []; selected = -1; dirty = false;
      background = null; needsStill = true; renderZones();
    }
    if (!dirty && !draft.length && JSON.stringify(zones) !== JSON.stringify(data.zones)) {
      zones = clone(data.zones); renderZones(); draw();
    }
    updateState(data);
    if (data.has_frame && data.mode === 'setup' && (needsStill || !background || previousMode === 'monitor')) await refreshStill();
    renderEvidence(data.events);
  } catch (error) {
    $('connectionStatus').textContent = 'App disconnected';
    $('connectionDot').className = 'status-dot';
    $('statusMessage').textContent = 'Cannot reach the app. Check your connection and refresh this page.';
    $('statusMessage').classList.add('error');
  }
  setTimeout(poll, 900);
}
function updateState(data) {
  const monitor = data.mode === 'monitor', connected = data.status === 'connected';
  $('setupMode').classList.toggle('active', !monitor); $('monitorMode').classList.toggle('active', monitor);
  $('drawingPanel').hidden = monitor; $('setupNote').hidden = monitor; $('monitorStats').hidden = !monitor;
  $('rulesFields').disabled = monitor; $('refreshFrame').hidden = monitor; $('refreshFrame').disabled = !connected;
  $('pathCanvas').hidden = monitor || !background; $('liveImage').hidden = !monitor;
  $('emptyState').hidden = Boolean(background) || monitor;
  $('cameraForm').querySelectorAll('input,select').forEach(input => input.disabled = monitor);
  if (!monitor) $('cameraBackend').disabled = $('cameraKind').value === 'stream';
  $('connectButton').disabled = monitor || data.status === 'connecting';
  $('disconnectButton').disabled = data.status === 'disconnected';
  $('connectionStatus').textContent = {connected:'Camera connected', connecting:'Connecting…', error:'Camera unavailable', disconnected:'Camera offline'}[data.status];
  $('connectionDot').className = `status-dot${connected ? ' online' : data.status === 'error' ? ' alert' : ''}`;
  $('viewDot').className = `status-dot${connected ? ' online' : ''}`;
  $('viewName').textContent = data.camera.label;
  $('frameDimensions').textContent = data.frame_size ? data.frame_size.join(' × ') : '';
  $('viewBadge').textContent = connected ? (data.camera.kind === 'demo' ? 'SAMPLE IMAGE' : monitor ? 'LIVE' : 'FROZEN FRAME') : data.status.toUpperCase();
  $('modeEyebrow').textContent = monitor ? 'WATCHING THE WALKWAY' : 'DEFINE THE SPACE';
  $('modeTitle').textContent = monitor ? 'Keep the walkway clear.' : 'A safer path starts here.';
  $('modeDescription').textContent = monitor ? 'Cyclists entering pedestrian areas are flagged and captured automatically.' : 'Connect your camera, then outline the cycling and pedestrian areas.';
  $('statusMessage').textContent = data.message;
  $('statusMessage').classList.toggle('error', data.status === 'error' || data.message.startsWith('Monitor paused'));
  $('cyclistCount').textContent = data.metrics.cyclists;
  $('encroachingCount').textContent = data.metrics.encroaching;
  $('encroachingCount').classList.toggle('danger', data.metrics.encroaching > 0);
  $('evidenceCount').textContent = data.event_count; $('eventTotal').textContent = data.event_count;
  $('detectionFps').textContent = data.metrics.fps ? `${data.metrics.fps} fps` : '—';
  $('viewerAlert').hidden = !monitor || data.metrics.encroaching === 0;
  $('viewerHelp').textContent = monitor ? `Alert at ≥ ${Math.round(data.rules.overlap * 100)}% contact overlap for ${data.rules.dwell} s. Line under bicycle = measured edge.` : background ? 'Frozen reference · click points, trace a shape, or drag vertices in Edit.' : data.hosted ? 'Browser camera frames go to Cloud Run. Confirmed cyclist crops are saved in GCS.' : 'Detection runs on this computer. Optional cloud upload saves evidence crops.';
  $('cloudStatus').textContent = data.cloud.status;
  $('cloudBadge').textContent = data.cloud.config.enabled ? 'ON' : 'OFF';
  $('savePaths').disabled = !connected || !background;
  $('startMonitor').disabled = !connected; $('monitorMode').disabled = !connected;
}
async function refreshStill() {
  if (!state?.has_frame) return;
  const response = await fetch(`/api/frame.jpg?raw=1&t=${Date.now()}`);
  if (!response.ok) throw new Error('No camera frame available yet.');
  const bitmap = await createImageBitmap(await response.blob());
  background?.close?.(); background = bitmap;
  canvas.width = bitmap.width; canvas.height = bitmap.height;
  needsStill = false;
  $('emptyState').hidden = true; canvas.hidden = state.mode !== 'setup';
  draw();
}
async function liveLoop() {
  if (state?.mode === 'monitor' && state.has_frame && !frameBusy) {
    frameBusy = true;
    try {
      const response = await fetch(`/api/frame.jpg?t=${Date.now()}`);
      if (response.ok) {
        const url = URL.createObjectURL(await response.blob());
        const previous = $('liveImage').src;
        $('liveImage').src = url;
        if (previous.startsWith('blob:')) URL.revokeObjectURL(previous);
      }
    } catch (error) {
      $('statusMessage').textContent = 'Waiting for the camera stream…';
    } finally { frameBusy = false; }
  }
  setTimeout(liveLoop, 150);
}

function point(event) {
  const rect = canvas.getBoundingClientRect();
  return [Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)), Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height))];
}
function distance(a, b) {
  const rect = canvas.getBoundingClientRect();
  return Math.hypot((a[0] - b[0]) * rect.width, (a[1] - b[1]) * rect.height);
}
function shapePath(points, close = true) {
  ctx.beginPath(); points.forEach((p, index) => index ? ctx.lineTo(p[0] * canvas.width, p[1] * canvas.height) : ctx.moveTo(p[0] * canvas.width, p[1] * canvas.height));
  if (close) ctx.closePath();
}
function draw() {
  if (!background) return;
  ctx.clearRect(0, 0, canvas.width, canvas.height); ctx.drawImage(background, 0, 0);
  const scale = canvas.width / Math.max(1, canvas.getBoundingClientRect().width);
  zones.forEach((zone, index) => {
    shapePath(zone.points); ctx.fillStyle = colors[zone.kind] + '40'; ctx.fill();
    ctx.strokeStyle = colors[zone.kind]; ctx.lineWidth = (index === selected ? 3 : 2) * scale; ctx.stroke();
    const p = zone.points[0];
    ctx.font = `600 ${12 * scale}px Segoe UI`; ctx.fillStyle = '#142c26';
    const text = `${zone.kind === 'pedestrian' ? 'Pedestrian' : 'Cycling'} ${index + 1}`;
    const x = Math.max(3, Math.min(p[0] * canvas.width, canvas.width - ctx.measureText(text).width - 18));
    const y = Math.max(26 * scale, p[1] * canvas.height);
    ctx.fillStyle = colors[zone.kind]; ctx.fillRect(x, y - 25 * scale, ctx.measureText(text).width + 14 * scale, 22 * scale);
    ctx.fillStyle = '#17312a'; ctx.fillText(text, x + 7 * scale, y - 10 * scale);
    if (tool === 'edit' && selected === index) zone.points.forEach(p => handle(p, '#fff', scale));
  });
  if (draft.length) {
    shapePath(draft, false); ctx.lineWidth = 2 * scale; ctx.strokeStyle = colors[kind]; ctx.stroke();
    if (draft.length > 2) { shapePath(draft); ctx.fillStyle = colors[kind] + '30'; ctx.fill(); }
    if (tool !== 'freehand') draft.forEach((p, i) => handle(p, i === 0 ? colors[kind] : '#fff', scale));
  }
}
function handle(p, fill, scale) {
  ctx.beginPath(); ctx.arc(p[0] * canvas.width, p[1] * canvas.height, 4.5 * scale, 0, Math.PI * 2);
  ctx.fillStyle = fill; ctx.fill(); ctx.strokeStyle = '#17312a'; ctx.lineWidth = 1.5 * scale; ctx.stroke();
}
function setTool(value) {
  if (draft.length) { toast('Finish the current shape or press Escape to cancel it.'); return; }
  tool = value;
  for (const t of ['polygon','freehand','edit']) $(`${t}Tool`).classList.toggle('selected', t === value);
  $('toolHint').textContent = {polygon:'Click corners. Click the first point or press Enter to close.', freehand:'Drag around the path surface and release to close the shape.', edit:'Click an area, then drag its white points to refine the boundary.'}[value];
  canvas.style.cursor = value === 'edit' ? 'default' : 'crosshair'; draw();
}
function setKind(value) {
  if (draft.length) { toast('Finish the current shape or press Escape first.'); return; }
  kind = value; $('pedestrianType').classList.toggle('selected', value === 'pedestrian'); $('cyclingType').classList.toggle('selected', value === 'cycling');
  if (tool === 'edit') setTool('polygon');
}
function renderZones() {
  $('zoneList').replaceChildren();
  if (!zones.length) { const p = document.createElement('p'); p.className = 'hint'; p.textContent = 'No areas drawn yet.'; $('zoneList').append(p); }
  zones.forEach((zone, index) => {
    const button = document.createElement('button'); button.className = `zone-item${selected === index ? ' selected' : ''}`;
    const swatch = document.createElement('i'); swatch.className = 'swatch'; swatch.style.background = colors[zone.kind];
    const name = document.createElement('span'); name.textContent = `${zone.kind === 'pedestrian' ? 'Pedestrian' : 'Cycling'} area ${index + 1}`;
    const count = document.createElement('span'); count.textContent = `${zone.points.length} points`;
    button.append(swatch, name, count); button.onclick = () => { if (draft.length) return toast('Finish the current shape first.'); selected = index; setTool('edit'); renderZones(); draw(); };
    $('zoneList').append(button);
  });
  $('saveStatus').textContent = dirty ? 'Unsaved changes · save before monitoring.' : zones.length ? 'Paths saved for this camera.' : 'Draw at least one pedestrian area to monitor.';
}
function finishShape() {
  if (draft.length < 3) { toast('Place at least three points around an area.'); return; }
  remember(); zones.push({id: crypto.randomUUID(), kind, points: clone(draft)}); selected = zones.length - 1; draft = []; changed();
}
function simplify(points, epsilon) {
  if (points.length <= 2) return points;
  const a = points[0], b = points[points.length - 1], dx = b[0] - a[0], dy = b[1] - a[1];
  let index = 0, maximum = 0;
  points.slice(1, -1).forEach((p, i) => {
    const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy || 1)));
    const d = Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
    if (d > maximum) { maximum = d; index = i + 1; }
  });
  return maximum > epsilon ? [...simplify(points.slice(0, index + 1), epsilon).slice(0, -1), ...simplify(points.slice(index), epsilon)] : [a, b];
}
canvas.addEventListener('pointerdown', event => {
  if (!background || state?.mode !== 'setup' || state?.status !== 'connected' || event.button !== 0) return;
  const p = point(event); canvas.setPointerCapture(event.pointerId);
  if (tool === 'polygon') {
    if (draft.length >= 3 && distance(p, draft[0]) < 12) finishShape();
    else if (draft.length >= 256) toast('Maximum 256 points per area. Finish this shape.');
    else { draft.push(p); draw(); }
  } else if (tool === 'freehand') { tracing = true; draft = [p]; }
  else {
    if (selected >= 0) {
      const vertex = zones[selected].points.findIndex(v => distance(v, p) < 13);
      if (vertex >= 0) { remember(); dragging = {zone: selected, vertex}; return; }
    }
    selected = -1;
    for (let i = zones.length - 1; i >= 0; i--) { shapePath(zones[i].points); if (ctx.isPointInPath(p[0] * canvas.width, p[1] * canvas.height)) { selected = i; break; } }
    renderZones(); draw();
  }
});
canvas.addEventListener('pointermove', event => {
  const p = point(event);
  if (tracing && distance(p, draft[draft.length - 1]) > 3 && draft.length < 2500) { draft.push(p); draw(); }
  if (dragging) { zones[dragging.zone].points[dragging.vertex] = p; dirty = true; draw(); }
});
function pointerEnd() {
  if (tracing) {
    tracing = false;
    draft = simplify(draft, 0.003);
    if (draft.length > 3 && distance(draft[0], draft[draft.length - 1]) < 6) draft.pop();
    if (draft.length > 256) draft = draft.filter((_, i) => i % Math.ceil(draft.length / 256) === 0);
    if (draft.length >= 3) finishShape(); else { draft = []; draw(); toast('Trace a closed area with a longer drag.'); }
  }
  if (dragging) { dragging = null; changed(); }
}
canvas.addEventListener('pointerup', pointerEnd);
canvas.addEventListener('pointercancel', () => { tracing = false; dragging = null; draft = []; renderZones(); draw(); });
$('undoButton').onclick = () => {
  if (draft.length) draft.pop(); else if (history.length) { zones = history.pop(); selected = -1; dirty = true; }
  renderZones(); draw();
};
$('deleteShape').onclick = () => {
  if (draft.length) { draft = []; draw(); return; }
  if (selected < 0) return toast('Select an area from the list or use Edit first.');
  remember(); zones.splice(selected, 1); selected = -1; changed();
};
document.addEventListener('keydown', event => {
  if (['INPUT','SELECT','TEXTAREA'].includes(event.target.tagName) || $('evidenceDialog').open || state?.mode !== 'setup') return;
  if (event.key === 'Escape') { draft = []; tracing = false; dragging = null; draw(); }
  if (event.key === 'Enter' && draft.length) { event.preventDefault(); finishShape(); }
  if ((event.ctrlKey || event.metaKey) && event.key === 'z') { event.preventDefault(); $('undoButton').click(); }
  if (event.key === 'Delete') $('deleteShape').click();
});
window.addEventListener('beforeunload', event => { if (dirty || draft.length) { event.preventDefault(); event.returnValue = ''; } });
window.addEventListener('resize', draw);

$('cameraKind').onchange = () => { $('cameraSource').value = $('cameraKind').value === 'usb' ? '0' : ''; $('cameraBackend').value = 'auto'; cameraFields(); };
$('cameraForm').onsubmit = action(async event => {
  event.preventDefault();
  if (dirty || draft.length) throw new Error('Save your paths before changing cameras.');
  background?.close?.(); background = null; needsStill = true;
  stopBrowserCapture();
  const config = {kind:$('cameraKind').value, source:$('cameraSource').value, label:$('cameraLabel').value,
    backend:$('cameraBackend').value, width:Number($('cameraWidth').value), height:Number($('cameraHeight').value)};
  if (config.kind === 'browser') await startBrowserCapture(config);
  else state = {...state, ...await api('/api/camera', config)};
  updateState(state);
});
$('tryDemo').onclick = () => { $('cameraKind').value = 'demo'; $('cameraLabel').value = 'Sample cyclist'; cameraFields(); $('cameraForm').requestSubmit(); };
$('disconnectButton').onclick = action(async () => { stopBrowserCapture(); state = {...state, ...await api('/api/disconnect', {})}; updateState(state); });
$('findCameras').onclick = action(async () => {
  if (!navigator.mediaDevices?.getUserMedia) throw new Error('Use HTTPS or localhost to access your camera.');
  const permission = await navigator.mediaDevices.getUserMedia({video:true, audio:false});
  try { await listBrowserDevices($('browserDevice').value); } finally { permission.getTracks().forEach(track => track.stop()); }
});
$('logoutButton').onclick = action(async () => { stopBrowserCapture(); await api('/api/disconnect', {}); await api('/api/logout', {}); location.assign('/login'); });
window.addEventListener('pagehide', stopBrowserCapture);
$('refreshFrame').onclick = action(refreshStill);
$('pedestrianType').onclick = () => setKind('pedestrian'); $('cyclingType').onclick = () => setKind('cycling');
for (const t of ['polygon','freehand','edit']) $(`${t}Tool`).onclick = () => setTool(t);
$('finishShape').onclick = finishShape;
$('savePaths').onclick = action(async () => {
  if (draft.length) throw new Error('Finish the current shape or press Escape before saving.');
  const data = await api('/api/zones', {zones, source_key:loadedSource});
  state = {...state, ...data};
  zones = clone(data.zones); dirty = false; history = []; renderZones(); toast('Paths saved for this camera.');
});
$('revertPaths').onclick = () => {
  zones = clone(state?.zones || []); draft = []; history = []; selected = -1; dirty = false;
  renderZones(); draw(); toast('Restored the last saved paths.');
};
async function changeMode(mode) {
  if (mode === 'monitor' && (dirty || draft.length)) throw new Error('Finish drawing and save your paths before monitoring.');
  state = {...state, ...await api('/api/mode', {mode})};
  if (mode === 'setup') needsStill = true;
  updateState(state);
}
$('setupMode').onclick = action(() => changeMode('setup'));
$('monitorMode').onclick = $('startMonitor').onclick = action(() => changeMode('monitor'));
for (const id of ['overlap','dwell','confidence']) $(id).oninput = syncRanges;
$('saveRules').onclick = action(async () => {
  await api('/api/rules', {overlap:Number($('overlap').value)/100, dwell:Number($('dwell').value), confidence:Number($('confidence').value)/100, rearm:Number($('rearm').value), model:$('model').value});
  toast('Detection rules applied.');
});
$('saveCloud').onclick = action(async () => {
  await api('/api/cloud', {enabled:$('cloudEnabled').checked, bucket:$('cloudBucket').value, project:$('cloudProject').value, prefix:$('cloudPrefix').value, credentials_file:$('cloudCredentials').value});
  toast('Cloud settings saved. Local evidence saving remains enabled.');
});
$('retryCloud').onclick = action(async () => { await api('/api/cloud/retry', {}); toast('Failed uploads queued for retry.'); });

function renderEvidence(events) {
  const signature = events.map(e => e.id + e.cloud.status + (e.cloud.attempts || 0)).join('|');
  if (signature === evidenceSignature) return;
  evidenceSignature = signature; $('evidenceGrid').replaceChildren(); $('evidenceEmpty').hidden = events.length > 0;
  for (const event of events) {
    const card = document.createElement('button'); card.className = 'event-card';
    const image = document.createElement('img'); image.src = event.crop_url; image.alt = `Cyclist evidence at ${event.timestamp}`; image.loading = 'lazy';
    const body = document.createElement('div'); body.className = 'event-body';
    const title = document.createElement('strong'); title.textContent = new Date(event.timestamp).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'});
    const subtitle = document.createElement('p'); subtitle.textContent = `${event.camera} · ${new Date(event.timestamp).toLocaleDateString()}`;
    const info = document.createElement('div'); info.className = 'event-status';
    const overlap = document.createElement('span'); overlap.textContent = `${Math.round(event.pedestrian_contact_fraction * 100)}% contact overlap`;
    const cloud = document.createElement('span'); cloud.className = 'cloud-tag'; cloud.textContent = {local:'Local', pending:'Uploading', error:'Retry pending', uploaded:'GCS synced'}[event.cloud.status] || 'Local';
    info.append(overlap, cloud); body.append(title, subtitle, info); card.append(image, body); card.onclick = () => showEvidence(event); $('evidenceGrid').append(card);
  }
}
function showEvidence(event) {
  $('dialogTitle').textContent = new Date(event.timestamp).toLocaleString(); $('dialogImage').src = event.crop_url;
  $('dialogDescription').textContent = `${event.camera} · Cyclist #${event.track_id} · ${Math.round(event.confidence * 100)}% detection confidence · ${Math.round(event.pedestrian_contact_fraction * 100)}% contact overlap. ${event.event_type === 'crossed_from_cycling' ? 'Observed moving from cycling area into pedestrian area.' : 'First observed in pedestrian area; prior crossing was not observed.'}`;
  $('dialogCrop').href = event.crop_url; $('dialogFrame').href = event.frame_url; $('dialogMetadata').href = event.metadata_url;
  $('dialogFrame').hidden = !event.frame_url;
  $('dialogCloud').textContent = event.cloud.uri || (event.cloud.status === 'error' ? `Cloud upload pending: ${event.cloud.error}` : `Storage: ${event.cloud.status}`);
  $('evidenceDialog').showModal();
}
$('closeDialog').onclick = () => $('evidenceDialog').close();
$('evidenceDialog').addEventListener('click', event => { if (event.target === $('evidenceDialog')) $('evidenceDialog').close(); });
poll(); liveLoop();
