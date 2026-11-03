'use strict';
/* Move Route Weather – browser side.
   Talks only to the local server (/api/...), which does all fetching and caching. */

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const state = {
  cfg: null,
  visible: new Set(),
  focus: null,
  portland: false,
  layers: { wind: true, rain: true, snow: true, temp: true },
  plans: new Map(),      // "r1|0" -> plan from /api/plan
  loading: new Set(),
  map: null, gm: null, overlay: null, info: null,
  lines: new Map(),      // variant key -> [Polyline]
  altLines: [],
  pollTimer: null,
  fitted: false,
};

// ---------------------------------------------------------------------------
// Colours and formatting
// ---------------------------------------------------------------------------
const WIND_BINS = [
  [10, '#15803d', 'under 10'], [20, '#ca8a04', '10–20'], [30, '#ea580c', '20–30'],
  [40, '#dc2626', '30–40'], [Infinity, '#7f1d1d', '40+'],
];
const RAIN = '#2563eb';
const SNOW = '#7c3aed';
const TEMP_BINS = [
  [10, '#1e3a8a', '#fff'], [20, '#1d4ed8', '#fff'], [32, '#3b82f6', '#fff'], [40, '#bfdbfe', '#0f172a'],
  [50, '#e2e8f0', '#0f172a'], [60, '#fde68a', '#0f172a'], [70, '#fdba74', '#0f172a'], [Infinity, '#f87171', '#0f172a'],
];
const windColor = (mph) => (WIND_BINS.find(([lim]) => mph < lim) || WIND_BINS[4])[1];
const tempStyle = (f) => { const b = TEMP_BINS.find(([lim]) => f < lim) || TEMP_BINS[7]; return { bg: b[1], fg: b[2] }; };
const arrowLen = (mph) => Math.min(66, 10 + mph * 1.35);
const rainH = (inch) => (inch > 0 ? Math.min(56, 4 + inch * 150) : 0);
const snowH = (inch) => (inch > 0 ? Math.min(56, 4 + inch * 14) : 0);

const TZ_ABBR = { 'America/Denver': 'MT', 'America/Los_Angeles': 'PT' };
const KIND_LABEL = { start: 'depart', overnight: 'overnight', fuel: 'fuel', break: 'break', meal: 'meal', end: 'arrive', pass: 'pass' };
const DIRS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
const compass = (d) => (d == null ? '' : DIRS[Math.round(((d % 360) + 360) % 360 / 22.5) % 16]);

const fmtCache = new Map();
function fmtTime(ts, tz, withDay = false) {
  if (ts == null) return '—';
  const k = tz + withDay;
  if (!fmtCache.has(k)) {
    const o = { hour: 'numeric', minute: '2-digit', timeZone: tz };
    if (withDay) o.weekday = 'short';
    fmtCache.set(k, new Intl.DateTimeFormat('en-US', o));
  }
  return `${fmtCache.get(k).format(new Date(ts * 1000))} ${TZ_ABBR[tz] || ''}`.trim();
}
const n0 = (v) => (v == null ? '–' : Math.round(v).toString());
const inch = (v, d = 2) => (v == null ? '–' : `${v.toFixed(d)}″`);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const $ = (sel) => document.querySelector(sel);

function minsAgo(ts) {
  if (!ts) return '';
  const m = Math.round((Date.now() / 1000 - ts) / 60);
  if (m < 1) return 'just now';
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  return `${h} h ${m % 60} min ago`;
}

function toast(msg) {
  const t = $('#toast');
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.hidden = true; }, 5000);
}

// ---------------------------------------------------------------------------
// Preferences (per browser; optional)
// ---------------------------------------------------------------------------
function loadPrefs() {
  try {
    const p = JSON.parse(localStorage.getItem('move-weather-prefs-v2') || 'null');
    if (!p) return false;
    if (Array.isArray(p.visible)) state.visible = new Set(p.visible.filter((id) => routeMeta(id)));
    if (p.focus && routeMeta(p.focus)) state.focus = p.focus;
    if (typeof p.portland === 'boolean') state.portland = p.portland;
    if (p.layers) Object.assign(state.layers, p.layers);
    return state.visible.size > 0;
  } catch (e) { return false; }
}
function savePrefs() {
  try {
    localStorage.setItem('move-weather-prefs-v2', JSON.stringify({
      visible: [...state.visible], focus: state.focus, portland: state.portland, layers: state.layers,
    }));
  } catch (e) { /* storage unavailable: fine */ }
}

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------
const routeMeta = (id) => state.cfg && state.cfg.routes.find((r) => r.id === id);
const portlandFor = (id) => { const r = routeMeta(id); return r.portland === 'always' || (r.portland === 'toggle' && state.portland); };
const vkey = (id, p) => `${id}|${(p ?? (routeMeta(id).portland === 'toggle' && state.portland)) ? 1 : 0}`;
const focusPlan = () => (state.focus ? state.plans.get(vkey(state.focus)) : null);
const visibleKeys = () => [...state.visible].map((id) => vkey(id));

async function api(path) {
  const res = await fetch(path);
  let body = null;
  try { body = await res.json(); } catch (e) { /* not json */ }
  if (!res.ok) throw new Error((body && body.error) || res.statusText);
  return body;
}

async function ensurePlan(id, alt = false) {
  const p = alt ? !(routeMeta(id).portland === 'toggle' && state.portland) : undefined;
  const k = vkey(id, p);
  const existing = state.plans.get(k);
  // a geometry-only copy (drawn as the dashed alternative) still needs its weather once it becomes the main route
  if ((existing && (alt || !existing._geomOnly)) || state.loading.has(k)) return;
  state.loading.add(k);
  renderDataLine();
  try {
    const flag = k.endsWith('|1') ? 1 : 0;
    const plan = await api(`/api/plan?route=${id}&portland=${flag}${alt ? '&weather=0' : ''}`);
    plan._geomOnly = alt;
    const prev = state.plans.get(k);
    if (!(prev && !prev._geomOnly && alt)) state.plans.set(k, plan);
  } catch (e) {
    if (alt) state.plans.set(k, { _geomOnly: true, _failed: true, days: [], paths: null });
    else toast(`Couldn't load ${routeMeta(id).name}: ${e.message}`);
  } finally {
    state.loading.delete(k);
  }
  if (!alt) {
    renderAll();
    schedulePoll();
  } else {
    renderMapRoutes();
  }
}

async function refreshWeather(keys) {
  await Promise.all(keys.map(async (k) => {
    const [id, flag] = k.split('|');
    try {
      const upd = await api(`/api/plan?route=${id}&portland=${flag}&geom=0`);
      const old = state.plans.get(k);
      if (!old) return;
      Object.assign(old, { weather: upd.weather, status: upd.status, samples: upd.samples, stops: upd.stops,
        days: upd.days, generated: upd.generated, _geomOnly: false });
    } catch (e) { /* keep showing what we have */ }
  }));
  renderWeatherViews();
}

function schedulePoll() {
  if (state.pollTimer) return;
  const busy = visibleKeys().filter((k) => state.plans.get(k)?.status?.queued > 0);
  if (!busy.length) { renderDataLine(); return; }
  state.pollTimer = setTimeout(async () => {
    state.pollTimer = null;
    await refreshWeather(visibleKeys().filter((k) => state.plans.get(k)?.status?.queued > 0));
    schedulePoll();
  }, 2500);
  renderDataLine();
}

// Pick up expired forecasts if the page is left open (the server decides whether to refetch).
setInterval(() => {
  const keys = visibleKeys().filter((k) => state.plans.get(k) && !state.plans.get(k)._geomOnly);
  if (keys.length) refreshWeather(keys).then(schedulePoll);
}, 10 * 60 * 1000);

// ---------------------------------------------------------------------------
// Google map
// ---------------------------------------------------------------------------
const MAP_STYLES = [
  { featureType: 'poi', stylers: [{ visibility: 'off' }] },
  { featureType: 'transit', stylers: [{ visibility: 'off' }] },
  { elementType: 'geometry', stylers: [{ saturation: -55 }] },
  { featureType: 'landscape.natural', elementType: 'geometry', stylers: [{ lightness: 8 }] },
  { featureType: 'road.highway', elementType: 'geometry', stylers: [{ saturation: -100 }, { lightness: 25 }] },
  { featureType: 'road.arterial', elementType: 'labels', stylers: [{ visibility: 'off' }] },
  { featureType: 'water', elementType: 'geometry', stylers: [{ saturation: -20 }, { lightness: 10 }] },
];

function mapNotice(msg) {
  const n = $('#map-notice');
  n.textContent = msg; n.hidden = !msg;
}

function loadGoogle(key) {
  if (window.google && window.google.maps && window.google.maps.importLibrary) return Promise.resolve();
  return new Promise((resolve, reject) => {
    window.__mwMapsReady = () => resolve();
    window.gm_authFailure = () => mapNotice('Google rejected the Maps key. Check google_maps_api_key in config.json — the demo key has to be enabled for the Maps JavaScript API.');
    const p = new URLSearchParams({ callback: '__mwMapsReady', v: 'weekly', loading: 'async' });
    if (key) p.set('key', key);
    const s = document.createElement('script');
    s.src = `https://maps.googleapis.com/maps/api/js?${p}`;
    s.async = true;
    s.onerror = () => reject(new Error('could not load Google Maps'));
    document.head.appendChild(s);
    setTimeout(() => reject(new Error('Google Maps took too long to load')), 25000);
  });
}

async function initMap() {
  const key = state.cfg.google_maps_api_key;
  if (!key) mapNotice('No Google Maps key yet — add your demo key to config.json. Until then Google shows a "for development purposes only" map.');
  try {
    await loadGoogle(key);
  } catch (e) {
    mapNotice(`Map unavailable (${e.message}). The chart, watch list and itinerary still work.`);
    return;
  }
  const { Map, Polyline, InfoWindow, OverlayView } = await google.maps.importLibrary('maps');
  const { LatLng, LatLngBounds } = await google.maps.importLibrary('core');
  state.gm = { Map, Polyline, InfoWindow, OverlayView, LatLng, LatLngBounds };
  state.map = new Map($('#map'), {
    center: { lat: 44.3, lng: -114.6 }, zoom: 5,
    mapTypeControl: true, mapTypeControlOptions: { mapTypeIds: ['roadmap', 'terrain', 'hybrid'] },
    streetViewControl: false, fullscreenControl: true, gestureHandling: 'greedy', clickableIcons: false,
    styles: MAP_STYLES,
  });
  state.info = new InfoWindow({ maxWidth: 400 });
  const WeatherOverlay = makeOverlayClass(OverlayView);
  state.overlay = new WeatherOverlay();
  state.overlay.setMap(state.map);
  renderMapRoutes();
  renderGlyphs();
}

function makeOverlayClass(OverlayView) {
  return class WeatherOverlay extends OverlayView {
    constructor() { super(); this.items = []; this.root = null; this.hl = null; this.hlPos = null; }
    onAdd() {
      this.root = document.createElement('div');
      this.root.className = 'wx-layer';
      this.getPanes().overlayMouseTarget.appendChild(this.root);
      this.hl = document.createElement('div');
      this.hl.className = 'wx-hl';
      this.hl.hidden = true;
      this.getPanes().floatPane.appendChild(this.hl);
      this.build();
    }
    onRemove() { if (this.root) this.root.remove(); if (this.hl) this.hl.remove(); this.root = this.hl = null; }
    setItems(items) { this.items = items; if (this.root) { this.build(); this.draw(); } }
    build() {
      const frag = document.createDocumentFragment();
      for (const it of this.items) {
        const el = document.createElement('div');
        el.className = `wx-glyph${it.focus ? ' is-focus' : ''}`;
        el.innerHTML = it.html;
        el.addEventListener('click', (e) => { e.stopPropagation(); it.onClick(); });
        it.el = el;
        frag.appendChild(el);
      }
      this.root.replaceChildren(frag);
    }
    draw() {
      const proj = this.getProjection();
      if (!proj || !this.root) return;
      const placed = [];
      const gap = 30;
      for (const it of this.items) {
        const p = proj.fromLatLngToDivPixel(it.pos);
        let show = true;
        for (const q of placed) {
          if (Math.abs(q.x - p.x) < gap && Math.abs(q.y - p.y) < gap) { show = false; break; }
        }
        it.el.style.display = show ? '' : 'none';
        if (show) { it.el.style.transform = `translate(${p.x}px,${p.y}px)`; placed.push(p); }
      }
      if (this.hl) {
        if (this.hlPos) {
          const p = proj.fromLatLngToDivPixel(this.hlPos);
          this.hl.style.transform = `translate(${p.x}px,${p.y}px)`;
          this.hl.hidden = false;
        } else this.hl.hidden = true;
      }
    }
    setHighlight(pos) { this.hlPos = pos; this.draw(); }
  };
}

function renderMapRoutes() {
  if (!state.map) return;
  const { Polyline, LatLngBounds } = state.gm;
  const wanted = new Set(visibleKeys());
  for (const [k, lines] of state.lines) {
    if (!wanted.has(k)) { lines.forEach((l) => l.setMap(null)); state.lines.delete(k); }
  }
  const bounds = new LatLngBounds();
  let any = false;
  for (const id of state.visible) {
    const k = vkey(id);
    const plan = state.plans.get(k);
    if (!plan || !plan.paths) continue;
    const focus = id === state.focus;
    let lines = state.lines.get(k);
    if (!lines) {
      lines = [];
      plan.paths.forEach((path) => {
        const pts = path.map(([a, b]) => ({ lat: a, lng: b }));
        const casing = new Polyline({ path: pts, strokeColor: '#ffffff', strokeOpacity: 0.9, strokeWeight: 9, clickable: false });
        const line = new Polyline({ path: pts, strokeColor: plan.color, strokeWeight: 5, clickable: true });
        line.addListener('click', () => setFocus(id));
        lines.push(casing, line);
      });
      state.lines.set(k, lines);
    }
    lines.forEach((l, i) => {
      const isCasing = i % 2 === 0;
      l.setOptions(isCasing
        ? { strokeOpacity: focus ? 0.95 : 0.6, zIndex: focus ? 20 : 10 }
        : { strokeOpacity: focus ? 0.95 : 0.55, strokeWeight: focus ? 5.5 : 4, zIndex: focus ? 21 : 11 });
      l.setMap(state.map);
    });
    plan.paths.forEach((path) => path.forEach(([a, b]) => { bounds.extend({ lat: a, lng: b }); any = true; }));
  }
  renderAltPath();
  if (any && !state.fitted) {
    state.map.fitBounds(bounds, { top: 30, right: 30, bottom: 30, left: 30 });
    state.fitted = true;
  }
}

// The finish not currently chosen (Snoqualmie vs Portland), drawn dashed for context.
function renderAltPath() {
  state.altLines.forEach((l) => l.setMap(null));
  state.altLines = [];
  if (!state.map || !state.focus) return;
  const meta = routeMeta(state.focus);
  if (meta.portland !== 'toggle' || !state.visible.has(state.focus)) return;
  const cur = state.plans.get(vkey(state.focus));
  const altKey = vkey(state.focus, !state.portland);
  const alt = state.plans.get(altKey);
  if (!alt) { ensurePlan(state.focus, true); return; }
  if (!cur || !alt.paths) return;
  const curT = new Set(cur.days.map((d) => d.template));
  alt.days.forEach((d, i) => {
    if (curT.has(d.template)) return;
    const line = new state.gm.Polyline({
      path: alt.paths[i].map(([a, b]) => ({ lat: a, lng: b })), strokeOpacity: 0, zIndex: 5, clickable: false,
      icons: [{ icon: { path: 'M 0,-1 0,1', strokeOpacity: 0.7, strokeColor: alt.color, scale: 3 }, offset: '0', repeat: '12px' }],
    });
    line.setMap(state.map);
    state.altLines.push(line);
  });
}

function glyphSVG(s, w, plan) {
  const L = state.layers;
  const parts = [];
  if (w) {
    if (L.wind && w.w != null && w.d != null) {
      const len = arrowLen(w.w);
      const gust = Math.max(w.ghi ?? w.g ?? w.w, w.w);
      const glen = arrowLen(gust);
      const col = windColor(w.w);
      const tip = -len / 2;
      const head = `M0 ${tip - 2} L-6 ${tip + 7} L6 ${tip + 7} Z`;
      parts.push(`<g class="hit" transform="rotate(${(w.d + 180) % 360})">`
        + (glen > len + 4 ? `<line x1="0" y1="${glen / 2}" x2="0" y2="${-glen / 2}" stroke="${col}" stroke-width="1.6" stroke-dasharray="3 3" opacity=".8"/>` : '')
        + `<line x1="0" y1="${len / 2}" x2="0" y2="${tip + 5}" stroke="#fff" stroke-width="6.5" stroke-linecap="round"/>`
        + `<path d="${head}" fill="#fff" stroke="#fff" stroke-width="3.5" stroke-linejoin="round"/>`
        + `<line x1="0" y1="${len / 2}" x2="0" y2="${tip + 5}" stroke="${col}" stroke-width="3.2" stroke-linecap="round"/>`
        + `<path d="${head}" fill="${col}"/></g>`);
    }
    let bx = 10;
    const base = 6;
    const bar = (value, max, hFn, color, minShow) => {
      if (!((value ?? 0) >= minShow || (max ?? 0) >= minShow * 2)) return;
      const h = hFn(value || 0);
      const hm = hFn(max || 0);
      if (hm > h) parts.push(`<rect x="${bx}" y="${base - hm}" width="7" height="${hm}" rx="1.5" fill="${color}" opacity=".22"/>`);
      if (h > 0) parts.push(`<rect class="hit" x="${bx}" y="${base - h}" width="7" height="${h}" rx="1.5" fill="${color}" stroke="#fff" stroke-width="1" opacity="${(0.45 + 0.55 * (w.agree ?? 1)).toFixed(2)}"/>`);
      bx += 9;
    };
    if (L.rain) bar(w.r, w.rhi, rainH, RAIN, 0.01);
    if (L.snow) bar(w.s, w.shi, snowH, SNOW, 0.1);
    if (L.temp && w.t != null) {
      const st = tempStyle(w.t);
      parts.push(`<g class="hit" transform="translate(-33,9)"><rect width="27" height="15" rx="7.5" fill="${st.bg}" stroke="#fff" stroke-width="1.3"/>`
        + `<text x="13.5" y="11" text-anchor="middle" font-size="10" font-weight="650" fill="${st.fg}">${Math.round(w.t)}°</text></g>`);
    }
  }
  const c = plan.color;
  let marker;
  switch (s.kind) {
    case 'start': case 'overnight': case 'end': {
      const ch = s.kind === 'start' ? 'S' : s.kind === 'end' ? '★' : 'H';
      marker = `<circle class="hit" r="8" fill="${c}" stroke="#fff" stroke-width="2"/><text y="3.6" text-anchor="middle" font-size="10" font-weight="700" fill="#fff" pointer-events="none">${ch}</text>`;
      break;
    }
    case 'fuel':
      marker = `<circle class="hit" r="7.5" fill="#fff" stroke="${c}" stroke-width="2.5"/><text y="3.4" text-anchor="middle" font-size="9" font-weight="750" fill="${c}" pointer-events="none">F</text>`;
      break;
    case 'break': case 'meal':
      marker = `<circle class="hit" r="5.5" fill="#fff" stroke="${c}" stroke-width="2.5"/>`;
      break;
    case 'pass':
      marker = `<path class="hit" d="M0 -7 L7 5 L-7 5 Z" fill="#1f2937" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/>`;
      break;
    default:
      marker = `<circle class="hit" r="3.4" fill="${c}" stroke="#fff" stroke-width="1.5"/>`;
  }
  const title = `${s.name} · ${fmtTime(s.eta, s.tz, true)}`;
  return `<svg class="wx-svg" width="160" height="160" viewBox="-80 -80 160 160"><title>${esc(title)}</title>${parts.join('')}${marker}</svg>`;
}

function samplePriority(s) {
  if (s.stop != null) return 0;
  if (s.kind === 'pass') return 1;
  if (s.kind === 'town') return 2;
  return 3;
}

function renderGlyphs() {
  if (!state.overlay) return;
  const items = [];
  const order = [state.focus, ...[...state.visible].filter((id) => id !== state.focus)];
  for (const id of order) {
    if (!id || !state.visible.has(id)) continue;
    const plan = state.plans.get(vkey(id));
    if (!plan || !plan.samples) continue;
    const idx = plan.samples.map((_, i) => i);
    idx.sort((a, b) => samplePriority(plan.samples[a]) - samplePriority(plan.samples[b]) || a - b);
    for (const i of idx) {
      const s = plan.samples[i];
      items.push({
        pos: new state.gm.LatLng(s.lat, s.lon),
        html: glyphSVG(s, plan.weather ? plan.weather[i] : null, plan),
        focus: id === state.focus,
        onClick: () => openInfo(plan, i),
      });
    }
  }
  state.overlay.setItems(items);
}

function stopOf(plan, s) {
  if (s.stop == null) return null;
  return plan.stops.filter((st) => st.day === s.day)[s.stop] || null;
}

function wxLine(w) {
  if (!w) return 'no forecast yet';
  const bits = [];
  if (w.t != null) bits.push(`${n0(w.t)}°F`);
  if (w.w != null) bits.push(`${compass(w.d)} ${n0(w.w)} mph${w.g != null ? `, gusts ${n0(w.ghi ?? w.g)}` : ''}`);
  if ((w.s ?? 0) >= 0.1) bits.push(`snow ${inch(w.s, 1)}`);
  else if ((w.r ?? 0) >= 0.01) bits.push(`rain ${inch(w.r)}`);
  else if (w.r != null) bits.push('dry');
  return bits.join(' · ');
}

function infoHTML(plan, i) {
  const s = plan.samples[i];
  const w = plan.weather ? plan.weather[i] : null;
  const st = stopOf(plan, s);
  const day = plan.days[s.day];
  const kindTxt = s.stop != null ? (s.kind === 'start' ? 'Departure' : 'Arrival') : 'Estimated pass-through';
  let html = `<div class="iw"><div class="iw-h"><span class="iw-sw" style="background:${plan.color}"></span>${esc(s.name)}</div>`
    + `<div class="iw-sub">${esc(plan.name)} · mile ${Math.round(s.mile)} · ${kindTxt} ${esc(fmtTime(s.eta, s.tz, true))}${day && day.estimated ? ' <span class="est">est.</span>' : ''}</div>`;
  if (st && st.note) html += `<div class="iw-note">${esc(st.note)}</div>`;
  if (!w) {
    html += `<div class="iw-note" style="margin-top:8px">No forecast for this time yet — either still loading or beyond the forecast range.</div></div>`;
    return html;
  }
  const kpi = (label, big, small) => `<div><label>${label}</label><b>${big}</b><small>${small}</small></div>`;
  html += '<div class="iw-kpis">'
    + kpi('Temp', w.t != null ? `${n0(w.t)}°F` : '–', w.tlo != null ? `${n0(w.tlo)}–${n0(w.thi)}° range` : '')
    + kpi('Wind', w.w != null ? `${compass(w.d)} ${n0(w.w)}` : '–', w.g != null ? `gusts ${n0(w.g)}, max ${n0(w.ghi)}` : 'mph')
    + kpi('Crosswind', w.xw != null ? n0(w.xw) : '–', w.xg != null ? `gusts ${n0(w.xg)} mph` : 'mph')
    + kpi('Rain', w.r != null ? inch(w.r) : '–', w.rhi != null ? `wettest ${inch(w.rhi)}` : '')
    + kpi('Snow', w.s != null ? inch(w.s, 1) : '–', w.shi != null ? `most ${inch(w.shi, 1)}` : '')
    + kpi('Agreement', w.np ? `${Math.round((w.agree || 0) * w.np)}/${w.np}` : '–', `sources show precip${w.pop != null ? ` · chance ${w.pop}%` : ''}`)
    + '</div>';
  html += '<table class="iw-t"><thead><tr><th>Source</th><th>Temp</th><th>Wind</th><th>Gust</th><th>Rain</th><th>Snow</th><th>Chance</th></tr></thead><tbody>';
  for (const m of w.m || []) {
    const [name, t, ws, d, g, r, sn, pop] = m;
    html += `<tr><td>${esc(name)}</td><td>${t == null ? '–' : `${n0(t)}°`}</td><td>${ws == null ? '–' : `${compass(d)} ${n0(ws)}`}</td>`
      + `<td>${n0(g)}</td><td>${r == null ? '–' : r.toFixed(2)}</td><td>${sn == null ? '–' : sn.toFixed(1)}</td><td>${pop == null ? '' : `${pop}%`}</td></tr>`;
  }
  html += `</tbody></table><div class="iw-foot">Wind in mph. Rain and snow are inches over the ${state.cfg.window_half_minutes * 2 / 60} hours centred on this time; the headline numbers are the average across sources.</div>`;
  if (s.place) {
    const others = plan.samples.map((x, j) => [x, j]).filter(([x, j]) => j !== i && x.place === s.place);
    for (const [x, j] of others) {
      html += `<div class="iw-also"><b>${x.kind === 'start' ? 'Departing' : 'Arriving'} ${esc(fmtTime(x.eta, x.tz, true))}:</b> ${esc(wxLine(plan.weather ? plan.weather[j] : null))}</div>`;
    }
  }
  return `${html}</div>`;
}

function openInfo(plan, i) {
  const s = plan.samples[i];
  if (state.info && state.map) {
    state.info.setContent(infoHTML(plan, i));
    state.info.setPosition({ lat: s.lat, lng: s.lon });
    state.info.open({ map: state.map });
  }
}

function focusSample(plan, i, open = true) {
  const s = plan.samples[i];
  if (state.map) {
    state.map.panTo({ lat: s.lat, lng: s.lon });
    if (state.map.getZoom() < 7) state.map.setZoom(7);
  }
  if (open) openInfo(plan, i);
}

// ---------------------------------------------------------------------------
// Legend
// ---------------------------------------------------------------------------
function renderLegend() {
  const el = $('#legend');
  const arrow = (mph, col) => {
    const len = arrowLen(mph) * 0.6;
    return `<svg width="${len + 8}" height="14" viewBox="${-len / 2 - 4} -7 ${len + 8} 14"><g transform="rotate(90)">`
      + `<line x1="0" y1="${len / 2}" x2="0" y2="${-len / 2 + 4}" stroke="${col}" stroke-width="2.6" stroke-linecap="round"/>`
      + `<path d="M0 ${-len / 2 - 2} L-4.5 ${-len / 2 + 5} L4.5 ${-len / 2 + 5} Z" fill="${col}"/></g></svg>`;
  };
  const winds = [[5, 'under 10'], [15, '10–20'], [25, '20–30'], [35, '30–40'], [45, '40+']]
    .map(([mph, lab]) => `<span title="${lab} mph">${arrow(mph, windColor(mph))}</span>`).join('');
  const chip = (f) => { const st = tempStyle(f); return `<svg width="28" height="15"><rect width="28" height="15" rx="7.5" fill="${st.bg}"/><text x="14" y="11" text-anchor="middle" font-size="10" font-weight="650" fill="${st.fg}">${f}°</text></svg>`; };
  el.innerHTML = `<div class="lg-head" id="lg-toggle"><span>Legend</span><button type="button">hide</button></div>`
    + '<div class="lg-body">'
    + `<div class="lg-row"><span class="lg-k">Wind</span>${winds}</div>`
    + '<div class="lg-row"><span class="lg-k"></span><span>under 10 · 20 · 30 · 40 · 40+ mph; dashes = gusts</span></div>'
    + `<div class="lg-row"><span class="lg-k">Rain</span><svg width="10" height="18"><rect x="1" y="2" width="7" height="16" rx="1.5" fill="${RAIN}"/></svg>`
    + `<span class="lg-k" style="min-width:auto;margin-left:10px">Snow</span><svg width="10" height="18"><rect x="1" y="2" width="7" height="16" rx="1.5" fill="${SNOW}"/></svg>`
    + '<span>faint bar = wettest source</span></div>'
    + `<div class="lg-row"><span class="lg-k">Temp</span>${[15, 28, 36, 45, 55, 65].map(chip).join('')}</div>`
    + '<div class="lg-row"><span class="lg-k">Stops</span>'
    + '<svg width="18" height="18" viewBox="-9 -9 18 18"><circle r="7.5" fill="#475569" stroke="#fff" stroke-width="1.5"/><text y="3.5" text-anchor="middle" font-size="9.5" font-weight="700" fill="#fff">H</text></svg> overnight '
    + '<svg width="18" height="18" viewBox="-9 -9 18 18"><circle r="7" fill="#fff" stroke="#475569" stroke-width="2.4"/><text y="3.3" text-anchor="middle" font-size="8.5" font-weight="750" fill="#475569">F</text></svg> fuel '
    + '<svg width="16" height="16" viewBox="-8 -8 16 16"><path d="M0 -6 L6 5 L-6 5 Z" fill="#1f2937"/></svg> pass</div>'
    + '<div class="lg-note">Each symbol shows the forecast for when you\'re expected to be there. Bars are 3-hour totals; more solid = more sources agree.</div>'
    + '</div>';
  const collapse = () => {
    el.classList.toggle('is-collapsed');
    el.querySelector('button').textContent = el.classList.contains('is-collapsed') ? 'show' : 'hide';
  };
  $('#lg-toggle').addEventListener('click', collapse);
  if (window.innerWidth < 900) collapse();
}

// ---------------------------------------------------------------------------
// Sidebar
// ---------------------------------------------------------------------------
const EYE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>';
const EYE_OFF = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 3l18 18"/><path d="M10.6 5.1A10 10 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3.2 4.1M6.6 6.6A17 17 0 0 0 2 12s3.5 7 10 7a9.6 9.6 0 0 0 5.4-1.6"/></svg>';

function planStats(plan) {
  if (!plan || !plan.weather) return null;
  let gust = null; let gustS = null; let snow = 0; let rain = 0; let lo = null; let have = 0;
  plan.weather.forEach((w) => {
    if (!w) return;
    have++;
    const g = w.g ?? w.w;
    if (g != null && (gust == null || g > gust)) gust = g;
    if (w.ghi != null && (gustS == null || w.ghi > gustS)) gustS = w.ghi;
    if (w.s != null && w.s > snow) snow = w.s;
    if (w.r != null && w.r > rain) rain = w.r;
    if (w.t != null && (lo == null || w.t < lo)) lo = w.t;
  });
  return { gust, gustMax: gustS, snow, rain, lo, have, total: plan.samples.length };
}

function renderRoutes() {
  const host = $('#routes');
  host.innerHTML = '';
  for (const r of state.cfg.routes) {
    const plan = state.plans.get(vkey(r.id));
    const st = planStats(plan);
    const shown = state.visible.has(r.id);
    const card = document.createElement('div');
    card.className = `route${r.id === state.focus ? ' is-focus' : ''}${shown ? '' : ' is-hidden'}`;
    card.style.setProperty('--rc', r.color);
    card.setAttribute('role', 'button');
    card.tabIndex = 0;
    const viaP = portlandFor(r.id);
    let stats = '';
    if (st && st.have) {
      const g = st.gust;
      stats = `<span class="stat ${g >= 40 ? 'bad' : g >= 30 ? 'warn' : ''}">gusts ${n0(g)} mph</span>`
        + `<span class="stat ${st.snow >= 0.1 ? 'snow' : ''}">snow ${st.snow >= 0.1 ? inch(st.snow, 1) : 'none'}</span>`
        + `<span class="stat ${st.rain >= 0.05 ? 'rain' : ''}">rain ${st.rain >= 0.01 ? inch(st.rain) : 'none'}</span>`
        + `<span class="stat">low ${n0(st.lo)}°</span>`;
    } else if (state.loading.has(vkey(r.id)) || (shown && !plan)) {
      stats = '<span class="stat">loading…</span>';
    }
    const badge = viaP && r.badge_portland ? r.badge_portland : r.badge;
    let extra = '';
    if (r.portland === 'toggle' && viaP) extra = '<span class="badge alt">via Portland</span>';
    else if (r.portland === 'never' && state.portland) extra = '<span class="badge muted">no Portland option</span>';
    card.innerHTML = `<div class="route-bar"></div><div class="route-body">`
      + `<div class="route-name"><b>${esc(r.name)}</b><span>${esc(r.short)}</span>${badge ? `<span class="badge${badge === r.badge_portland ? '' : ' plain'}">${esc(badge)}</span>` : ''}`
      + `${extra}</div>`
      + `<div class="route-meta">${plan ? `${plan.total_miles.toLocaleString()} mi · ` : ''}${esc(viaP && r.description_portland ? r.description_portland : r.description)}</div>`
      + `<div class="route-stats">${stats}</div></div>`
      + `<button class="eye" type="button" aria-pressed="${shown}" title="${shown ? 'Hide' : 'Show'} on map">${shown ? EYE : EYE_OFF}</button>`;
    card.addEventListener('click', (e) => {
      if (e.target.closest('.eye')) { toggleVisible(r.id); return; }
      setFocus(r.id);
    });
    card.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setFocus(r.id); } });
    host.appendChild(card);
  }
}

const LAYER_ICONS = {
  wind: `<svg viewBox="0 0 26 22"><g transform="translate(13 11) rotate(60)"><line x1="0" y1="8" x2="0" y2="-4" stroke="#ea580c" stroke-width="3" stroke-linecap="round"/><path d="M0 -10 L-5 -2 L5 -2 Z" fill="#ea580c"/></g></svg>`,
  rain: `<svg viewBox="0 0 26 22"><rect x="6" y="9" width="5" height="11" rx="1.2" fill="${RAIN}"/><rect x="14" y="3" width="5" height="17" rx="1.2" fill="${RAIN}"/></svg>`,
  snow: `<svg viewBox="0 0 26 22"><rect x="6" y="5" width="5" height="15" rx="1.2" fill="${SNOW}"/><rect x="14" y="11" width="5" height="9" rx="1.2" fill="${SNOW}"/></svg>`,
  temp: `<svg viewBox="0 0 26 22"><rect x="1" y="4" width="24" height="14" rx="7" fill="#3b82f6"/><text x="13" y="14.5" text-anchor="middle" font-size="9" font-weight="700" fill="#fff">31°</text></svg>`,
};

function renderLayers() {
  const host = $('#layers');
  host.innerHTML = '';
  for (const [k, label] of [['wind', 'Wind'], ['rain', 'Rain'], ['snow', 'Snow'], ['temp', 'Temp']]) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'layer';
    b.setAttribute('aria-pressed', String(state.layers[k]));
    b.innerHTML = `${LAYER_ICONS[k]}<span>${label}</span>`;
    b.addEventListener('click', () => {
      state.layers[k] = !state.layers[k];
      savePrefs();
      renderLayers();
      renderGlyphs();
      renderProfile();
    });
    host.appendChild(b);
  }
}

function hazards(plan) {
  const items = [];
  if (!plan || !plan.weather) return items;
  const S = plan.samples;
  const W = plan.weather;
  const where = (i) => `${S[i].name} · ${fmtTime(S[i].eta, S[i].tz, true)}`;
  let missing = 0;
  let gi = -1; let g = -1;
  let si = -1; let sv = 0;
  let ri = -1; let rv = 0;
  let ti = -1; let tv = 999;
  const icy = [];
  W.forEach((w, i) => {
    if (!w) { missing++; return; }
    const gg = w.g ?? w.w;
    if (gg != null && gg > g) { g = gg; gi = i; }
    if ((w.s ?? 0) > sv) { sv = w.s; si = i; }
    if ((w.r ?? 0) > rv) { rv = w.r; ri = i; }
    if (w.t != null && w.t < tv) { tv = w.t; ti = i; }
    if (w.t != null && w.t <= 34 && ((w.r ?? 0) >= 0.01)) icy.push(i);
  });
  if (gi >= 0 && g >= 25) {
    const w = W[gi];
    items.push({ sev: g >= 45 ? 'high' : g >= 35 ? 'med' : 'low', i: gi,
      text: `Gusts to ${n0(g)} mph${w.ghi != null && w.ghi > g + 3 ? ` (one source ${n0(w.ghi)})` : ''}`,
      sub: `${where(gi)}${w.xg != null ? ` · crosswind gusts ~${n0(w.xg)} mph` : ''}` });
  }
  if (si >= 0 && sv >= 0.1) {
    const w = W[si];
    items.push({ sev: sv >= 1 ? 'high' : 'med', i: si, text: `Snow ${inch(sv, 1)} in 3 h (up to ${inch(w.shi, 1)})`,
      sub: `${where(si)} · ${Math.round((w.agree || 0) * w.np)}/${w.np} sources show precip` });
  }
  if (icy.length) {
    const i = icy.reduce((a, b) => ((W[b].r || 0) > (W[a].r || 0) ? b : a));
    items.push({ sev: W[i].t <= 32 ? 'high' : 'med', i, text: `Rain near freezing (${n0(W[i].t)}°F)`, sub: `${where(i)} · watch for ice` });
  }
  if (ri >= 0 && rv >= 0.03) {
    items.push({ sev: rv >= 0.25 ? 'med' : 'low', i: ri, text: `Wettest stretch: ${inch(rv)} rain in 3 h`, sub: where(ri) });
  }
  if (ti >= 0 && tv <= 25) items.push({ sev: 'low', i: ti, text: `Coldest: ${n0(tv)}°F`, sub: where(ti) });
  if (!items.length && plan.weather.some(Boolean)) items.push({ sev: 'ok', i: null, text: 'Nothing notable in the current forecasts', sub: 'Gusts under 25 mph, no snow, little rain' });
  if (missing) {
    const queued = plan.status && plan.status.queued;
    items.push({ sev: 'info', i: null, text: `${missing} of ${S.length} points have no forecast yet`, sub: queued ? 'Still fetching…' : 'Probably beyond the forecast range; forecasts reach about 7–10 days out.' });
  }
  return items;
}

function renderWatch() {
  const plan = focusPlan();
  const meta = routeMeta(state.focus);
  $('#watch-route').textContent = meta ? `· ${meta.name}` : '';
  const host = $('#watch');
  host.innerHTML = '';
  if (!plan) { host.innerHTML = '<li class="info"><span class="dot"></span><span>Loading…</span></li>'; return; }
  for (const it of hazards(plan)) {
    const li = document.createElement('li');
    li.className = it.sev;
    li.innerHTML = `<span class="dot"></span><span>${esc(it.text)}<small>${esc(it.sub)}</small></span>`;
    if (it.i != null) li.addEventListener('click', () => focusSample(plan, it.i));
    host.appendChild(li);
  }
}

function renderItinerary() {
  const plan = focusPlan();
  const meta = routeMeta(state.focus);
  $('#itin-route').textContent = meta ? `· ${meta.name}${plan && plan.portland ? ' via Portland' : ''}` : '';
  const host = $('#itin');
  host.innerHTML = '';
  if (!plan) return;
  plan.days.forEach((d, di) => {
    const box = document.createElement('div');
    box.className = 'itin-day';
    box.innerHTML = `<h3><b>${esc(d.label)} · ${esc(d.title)}${d.estimated ? '<span class="est">times est.</span>' : ''}</b><span>${d.miles} mi</span></h3>`
      + (d.note ? `<div class="day-note">${esc(d.note)}</div>` : '')
      + (d.approx ? '<div class="day-note" style="color:#9a3412">Route line approximate (routing service unreachable).</div>' : '');
    plan.stops.filter((st) => st.day === di).forEach((st) => {
      const t = st.kind === 'start' ? st.depart : (st.arrive ?? st.depart);
      const si = plan.samples.findIndex((s) => s.day === di && s.stop != null && stopOf(plan, s) === st);
      const w = si >= 0 && plan.weather ? plan.weather[si] : null;
      let wx = '';
      if (w) {
        wx = `<div class="stop-wx">${w.t != null ? `<span>${n0(w.t)}°F</span>` : ''}`
          + `${w.w != null ? `<span>${compass(w.d)} ${n0(w.w)}${w.g != null ? `–${n0(w.ghi ?? w.g)}` : ''} mph</span>` : ''}`
          + `${(w.s ?? 0) >= 0.1 ? `<span class="s">snow ${inch(w.s, 1)}</span>` : ''}`
          + `${(w.r ?? 0) >= 0.01 ? `<span class="r">rain ${inch(w.r)}</span>` : ''}</div>`;
      }
      const row = document.createElement('div');
      row.className = 'stop';
      row.innerHTML = `<div class="stop-time">${esc(fmtTime(t, st.tz))}<small>${KIND_LABEL[st.kind] || st.kind}</small></div>`
        + `<div><div class="stop-name">${esc(st.name)}</div>${st.note ? `<div class="stop-note">${esc(st.note)}</div>` : ''}${wx}</div>`;
      if (si >= 0) row.addEventListener('click', () => focusSample(plan, si));
      box.appendChild(row);
    });
    host.appendChild(box);
  });
}

function renderSources() {
  const plan = focusPlan();
  const host = $('#sources');
  const status = plan && plan.status ? plan.status.sources : {};
  let html = '';
  for (const src of state.cfg.sources) {
    const st = status[src.id];
    let cls = 'src';
    let detail = '';
    if (!src.enabled) {
      cls += ' off';
      detail = src.id === 'google_weather' ? 'needs the Google key in config.json' : 'turned off in config.json';
    } else if (!st) {
      cls += ' loading';
      detail = 'waiting';
    } else {
      const got = st.fresh + st.stale;
      if (st.queued) { cls += ' loading'; detail = `fetching ${st.queued} of ${st.points} points…`; }
      else if (st.error && !got) { cls += ' error'; detail = `unavailable: ${st.error_msg || 'error'}`; }
      else {
        detail = `${got}/${st.points} points · fetched ${minsAgo(st.newest)}`;
        if (st.error) { cls += ' error'; detail += ` · ${st.error} failed (${st.error_msg || 'error'})`; }
      }
      if (src.id === 'open_meteo') detail = `${state.cfg.models.join(', ')} — ${detail}`;
      if (src.id === 'google_weather') detail += ' · stops and passes only';
      if (st.note) detail += ` · ${st.note}`;
    }
    html += `<div class="${cls}"><span class="dot"></span><div>${esc(src.label)}<small>${esc(detail)}</small></div></div>`;
  }
  html += '<div class="src-foot">Forecasts are cached on this computer and only re-fetched once they\'re a couple of hours old.</div>';
  host.innerHTML = html;
}

function renderDataLine() {
  const el = $('#data-line');
  const plans = visibleKeys().map((k) => state.plans.get(k)).filter(Boolean);
  const queued = plans.reduce((a, p) => a + ((p.status && p.status.queued) || 0), 0);
  if (state.loading.size) {
    el.innerHTML = '<span class="spin"></span>Loading routes…';
  } else if (queued) {
    el.innerHTML = `<span class="spin"></span>Fetching forecasts… ${queued} left`;
  } else if (plans.length) {
    const newest = Math.max(...plans.flatMap((p) => Object.values((p.status && p.status.sources) || {}).map((s) => s.newest || 0)));
    el.textContent = newest > 0 ? `Forecasts updated ${minsAgo(newest)}` : 'No forecasts loaded';
  } else el.textContent = '';
  renderSources();
}

// ---------------------------------------------------------------------------
// Profile chart: weather along the focused route
// ---------------------------------------------------------------------------
function renderProfile() {
  const host = $('#profile');
  const plan = focusPlan();
  const meta = routeMeta(state.focus);
  $('#pf-swatch').style.background = meta ? meta.color : '';
  $('#pf-title').textContent = meta ? `${meta.name}${plan && plan.portland ? ' via Portland' : ''} — weather when you get there` : 'Weather along the route';
  $('#pf-sub').textContent = plan ? `${plan.total_miles.toLocaleString()} mi · hover for details, click to find it on the map` : '';
  if (!plan || !plan.samples) { host.innerHTML = '<div class="pf-empty">Loading route…</div>'; return; }

  const L = state.layers;
  const S = plan.samples;
  const WX = plan.weather || [];
  const Wd = Math.max(340, host.clientWidth || 800);
  const ml = 46; const mr = 46;
  const xmax = Math.max(...plan.days.map((d) => d.x1));
  const X = (x) => ml + (x / xmax) * (Wd - ml - mr);

  // vertical layout depends on which layers are on
  let y = 18;
  const P = {};
  if (L.wind) { P.arrows = [y, y + 22]; y += 26; }
  if (L.temp) { P.temp = [y + 4, y + 58]; y += 66; }
  if (L.rain || L.snow) { P.precip = [y + 4, y + 44]; y += 52; }
  if (L.wind) { P.wind = [y + 4, y + 44]; y += 52; }
  const axisY = y + 4;
  const H = axisY + 30;
  const out = [];
  out.push(`<svg viewBox="0 0 ${Wd} ${H}" height="${H}" role="img" aria-label="Weather along ${esc(plan.name)}">`);

  // day bands and nights
  plan.days.forEach((d, i) => {
    const x0 = X(d.x0); const x1 = X(d.x1);
    out.push(`<rect x="${x0}" y="0" width="${x1 - x0}" height="${axisY}" fill="${i % 2 ? '#fafbf9' : '#ffffff'}"/>`);
    const label = `${d.label} · ${d.title}${d.estimated ? ' (est.)' : ''}`;
    const maxChars = Math.max(4, Math.floor((x1 - x0 - 8) / 6.1));
    out.push(`<text x="${x0 + 4}" y="12" font-size="11" font-weight="600" fill="#4a5662">${esc(label.length > maxChars ? `${label.slice(0, maxChars - 1)}…` : label)}</text>`);
    if (i < plan.days.length - 1) {
      const nx0 = x1; const nx1 = X(plan.days[i + 1].x0);
      out.push(`<rect x="${nx0}" y="0" width="${nx1 - nx0}" height="${axisY}" fill="#e9ece8"/>`);
      if (nx1 - nx0 > 10) out.push(`<text transform="translate(${(nx0 + nx1) / 2 + 3},${axisY / 2}) rotate(-90)" text-anchor="middle" font-size="9.5" fill="#7b8692" letter-spacing=".08em">NIGHT</text>`);
    }
  });

  // stop guide lines
  plan.stops.forEach((st) => {
    out.push(`<line x1="${X(st.x)}" x2="${X(st.x)}" y1="18" y2="${axisY}" stroke="#d5dad4" stroke-dasharray="2 3"/>`);
  });

  // segments of consecutive samples within a day, for lines and bands
  const segs = [];
  let cur = [];
  S.forEach((s, i) => {
    if (cur.length && S[cur[cur.length - 1]].day !== s.day) { segs.push(cur); cur = []; }
    cur.push(i);
  });
  if (cur.length) segs.push(cur);

  const spacing = (Wd - ml - mr) / Math.max(1, S.length);

  if (P.arrows) {
    const [t, b] = P.arrows; const cy = (t + b) / 2;
    let lastX = -1e9;
    S.forEach((s, i) => {
      const w = WX[i]; if (!w || w.w == null || w.d == null) return;
      const x = X(s.x); if (x - lastX < 13) return; lastX = x;
      const len = Math.min(20, 8 + w.w * 0.45); const col = windColor(w.w);
      out.push(`<g transform="translate(${x},${cy}) rotate(${(w.d + 180) % 360})"><line x1="0" y1="${len / 2}" x2="0" y2="${-len / 2 + 4}" stroke="${col}" stroke-width="2.4" stroke-linecap="round"/><path d="M0 ${-len / 2 - 1.5} L-4 ${-len / 2 + 5} L4 ${-len / 2 + 5} Z" fill="${col}"/></g>`);
    });
    out.push(`<text x="${ml - 6}" y="${cy + 4}" text-anchor="end" font-size="10" fill="#7b8692">wind</text>`);
  }

  if (P.temp) {
    const [t, b] = P.temp;
    const vals = [32];
    WX.forEach((w) => { if (w && w.tlo != null) vals.push(w.tlo, w.thi); });
    const lo = Math.floor(Math.min(...vals) - 2); const hi = Math.ceil(Math.max(...vals) + 2);
    const Y = (v) => t + ((hi - v) / (hi - lo)) * (b - t);
    out.push(`<line x1="${ml}" x2="${Wd - mr}" y1="${Y(32)}" y2="${Y(32)}" stroke="#3b82f6" stroke-dasharray="4 3" stroke-width="1"/>`);
    out.push(`<text x="${Wd - mr + 4}" y="${Y(32) + 3.5}" font-size="10" fill="#3b82f6">32°</text>`);
    out.push(`<text x="${ml - 6}" y="${t + 8}" text-anchor="end" font-size="10" fill="#7b8692">${hi}°</text>`);
    out.push(`<text x="${ml - 6}" y="${b}" text-anchor="end" font-size="10" fill="#7b8692">${lo}°</text>`);
    out.push(`<text x="${ml - 6}" y="${(t + b) / 2 + 4}" text-anchor="end" font-size="10" fill="#4a5662" font-weight="600">temp</text>`);
    for (const seg of segs) {
      const pts = seg.filter((i) => WX[i] && WX[i].t != null);
      if (pts.length < 1) continue;
      const top = pts.map((i) => `${X(S[i].x)},${Y(WX[i].thi)}`);
      const bot = pts.slice().reverse().map((i) => `${X(S[i].x)},${Y(WX[i].tlo)}`);
      out.push(`<polygon points="${top.concat(bot).join(' ')}" fill="#94a3b8" opacity=".22"/>`);
      out.push(`<polyline points="${pts.map((i) => `${X(S[i].x)},${Y(WX[i].t)}`).join(' ')}" fill="none" stroke="#334155" stroke-width="1.8" stroke-linejoin="round"/>`);
      pts.forEach((i) => { const st = tempStyle(WX[i].t); out.push(`<circle cx="${X(S[i].x)}" cy="${Y(WX[i].t)}" r="2.6" fill="${st.bg}" stroke="#334155" stroke-width=".8"/>`); });
    }
  }

  if (P.precip) {
    const [t, b] = P.precip; const h = b - t;
    const rs = Math.max(0.25, ...WX.map((w) => (w && w.rhi) || 0));
    const ss = Math.max(2, ...WX.map((w) => (w && w.shi) || 0));
    out.push(`<line x1="${ml}" x2="${Wd - mr}" y1="${b}" y2="${b}" stroke="#cfd5cf"/>`);
    if (L.rain) out.push(`<text x="${ml - 6}" y="${t + 8}" text-anchor="end" font-size="10" fill="${RAIN}">${rs.toFixed(2)}″</text><text x="${ml - 6}" y="${b - 2}" text-anchor="end" font-size="10" fill="${RAIN}" font-weight="600">rain</text>`);
    if (L.snow) out.push(`<text x="${Wd - mr + 4}" y="${t + 8}" font-size="10" fill="${SNOW}">${ss.toFixed(1)}″</text><text x="${Wd - mr + 4}" y="${b - 2}" font-size="10" fill="${SNOW}" font-weight="600">snow</text>`);
    const bw = Math.max(2, Math.min(6, spacing * 0.38));
    S.forEach((s, i) => {
      const w = WX[i]; if (!w) return;
      const x = X(s.x);
      const op = (0.45 + 0.55 * (w.agree ?? 1)).toFixed(2);
      if (L.rain && w.rhi != null) {
        const hm = (Math.min(w.rhi, rs) / rs) * h; const hv = (Math.min(w.r || 0, rs) / rs) * h;
        if (hm > 0.5) out.push(`<rect x="${x - bw - 0.5}" y="${b - hm}" width="${bw}" height="${hm}" fill="${RAIN}" opacity=".2"/>`);
        if (hv > 0.5) out.push(`<rect x="${x - bw - 0.5}" y="${b - hv}" width="${bw}" height="${hv}" fill="${RAIN}" opacity="${op}"/>`);
      }
      if (L.snow && w.shi != null) {
        const hm = (Math.min(w.shi, ss) / ss) * h; const hv = (Math.min(w.s || 0, ss) / ss) * h;
        if (hm > 0.5) out.push(`<rect x="${x + 0.5}" y="${b - hm}" width="${bw}" height="${hm}" fill="${SNOW}" opacity=".2"/>`);
        if (hv > 0.5) out.push(`<rect x="${x + 0.5}" y="${b - hv}" width="${bw}" height="${hv}" fill="${SNOW}" opacity="${op}"/>`);
      }
    });
  }

  if (P.wind) {
    const [t, b] = P.wind; const h = b - t;
    const wmax = Math.max(50, ...WX.map((w) => ((w && (w.ghi ?? w.g ?? w.w)) || 0) + 5));
    const Y = (v) => b - (v / wmax) * h;
    out.push(`<line x1="${ml}" x2="${Wd - mr}" y1="${b}" y2="${b}" stroke="#cfd5cf"/>`);
    out.push(`<line x1="${ml}" x2="${Wd - mr}" y1="${Y(40)}" y2="${Y(40)}" stroke="#dc2626" stroke-dasharray="4 3" opacity=".6"/>`);
    out.push(`<text x="${Wd - mr + 4}" y="${Y(40) + 3.5}" font-size="10" fill="#dc2626">40</text>`);
    out.push(`<text x="${ml - 6}" y="${t + 8}" text-anchor="end" font-size="10" fill="#7b8692">${Math.round(wmax)}</text>`);
    out.push(`<text x="${ml - 6}" y="${b - 2}" text-anchor="end" font-size="10" fill="#4a5662" font-weight="600">mph</text>`);
    const bw = Math.max(2, Math.min(6, spacing * 0.5));
    S.forEach((s, i) => {
      const w = WX[i]; if (!w || w.w == null) return;
      const x = X(s.x);
      out.push(`<rect x="${x - bw / 2}" y="${Y(w.w)}" width="${bw}" height="${b - Y(w.w)}" fill="${windColor(w.w)}" opacity=".85"/>`);
      const g = w.g ?? null; const gh = w.ghi ?? null;
      if (gh != null && gh > w.w) out.push(`<line x1="${x}" x2="${x}" y1="${Y(w.w)}" y2="${Y(gh)}" stroke="#334155" stroke-width="1" opacity=".5"/>`);
      if (g != null) out.push(`<circle cx="${x}" cy="${Y(g)}" r="2.3" fill="#111827"/>`);
    });
  }

  // stops axis
  const big = (st) => ['start', 'overnight', 'end'].includes(st.kind);
  plan.stops.forEach((st) => {
    const x = X(st.x);
    const col = st.kind === 'pass' ? '#1f2937' : meta.color;
    if (st.kind === 'pass') out.push(`<path d="M${x} ${axisY + 1} l5 8 l-10 0 Z" fill="${col}"/>`);
    else out.push(`<circle cx="${x}" cy="${axisY + 5}" r="${big(st) ? 4.5 : 3}" fill="${big(st) ? col : '#fff'}" stroke="${col}" stroke-width="1.6"/>`);
  });
  // labels for start, nights and arrival; drop the time, then the label, when they'd collide
  const taken = [];
  const labelled = plan.stops.map((st, k) => [st, k]).filter(([st, k]) => big(st) && !(st.kind === 'start' && k > 0));
  const order = [labelled[labelled.length - 1], labelled[0], ...labelled.slice(1, -1)].filter(Boolean);
  for (const [st, k] of order) {
    const x = X(st.x);
    const t = st.kind === 'start' ? st.depart : st.arrive;
    const anchor = k === 0 ? 'start' : k === plan.stops.length - 1 ? 'end' : 'middle';
    for (const withTime of [true, false]) {
      const txt = withTime ? `${st.short} ${fmtTime(t, st.tz)}` : st.short;
      const w = txt.length * 5.9 + 8;
      const x0 = anchor === 'start' ? x : anchor === 'end' ? x - w : x - w / 2;
      if (taken.some(([a, b]) => x0 < b && x0 + w > a)) continue;
      taken.push([x0, x0 + w]);
      out.push(`<text x="${x}" y="${axisY + 22}" text-anchor="${anchor}" font-size="10.5" fill="#4a5662"><tspan font-weight="600">${esc(st.short)}</tspan>${withTime ? ` ${esc(fmtTime(t, st.tz))}` : ''}</text>`);
      break;
    }
  }

  out.push(`<line id="pf-cursor" x1="0" x2="0" y1="18" y2="${axisY}" stroke="#111827" stroke-width="1" visibility="hidden"/>`);
  out.push(`<rect id="pf-hit" x="${ml}" y="0" width="${Wd - ml - mr}" height="${H}" fill="transparent"/>`);
  out.push('</svg>');
  host.innerHTML = out.join('');

  // interaction
  const svg = host.querySelector('svg');
  const hit = host.querySelector('#pf-hit');
  const cursor = host.querySelector('#pf-cursor');
  const tip = $('#pf-tip');
  const xs = S.map((s) => X(s.x));
  const nearest = (px) => {
    let best = 0; let bd = Infinity;
    xs.forEach((x, i) => { const d = Math.abs(x - px); if (d < bd) { bd = d; best = i; } });
    return best;
  };
  const toLocal = (e) => {
    const r = svg.getBoundingClientRect();
    return (e.clientX - r.left) * (Wd / r.width);
  };
  hit.addEventListener('mousemove', (e) => {
    const i = nearest(toLocal(e));
    const s = S[i]; const w = WX[i];
    cursor.setAttribute('x1', xs[i]); cursor.setAttribute('x2', xs[i]); cursor.setAttribute('visibility', 'visible');
    let html = `<b>${esc(s.name)}</b><span class="muted">${esc(fmtTime(s.eta, s.tz, true))} · mile ${Math.round(s.mile)}</span><br>`;
    if (w) {
      html += `${w.t != null ? `${n0(w.t)}°F <span class="muted">(${n0(w.tlo)}–${n0(w.thi)})</span>` : ''}<br>`
        + `${w.w != null ? `Wind ${compass(w.d)} ${n0(w.w)} mph${w.g != null ? `, gusts ${n0(w.g)}` : ''}${w.xg != null ? ` <span class="muted">· cross ${n0(w.xg)}</span>` : ''}` : ''}<br>`
        + `<span class="r">Rain ${inch(w.r)}</span> · <span class="s">Snow ${inch(w.s, 1)}</span> <span class="muted">· ${w.n} sources</span>`;
    } else html += '<span class="muted">No forecast yet</span>';
    tip.innerHTML = html;
    tip.hidden = false;
    const wrap = host.parentElement.getBoundingClientRect();
    const left = e.clientX - wrap.left;
    tip.style.left = `${Math.min(left + 14, wrap.width - tip.offsetWidth - 8)}px`;
    tip.style.top = `${Math.max(4, e.clientY - wrap.top - tip.offsetHeight - 10)}px`;
    if (state.overlay) state.overlay.setHighlight(new state.gm.LatLng(s.lat, s.lon));
  });
  hit.addEventListener('mouseleave', () => {
    cursor.setAttribute('visibility', 'hidden');
    tip.hidden = true;
    if (state.overlay) state.overlay.setHighlight(null);
  });
  hit.addEventListener('click', (e) => focusSample(plan, nearest(toLocal(e))));
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------
function setFocus(id) {
  state.focus = id;
  if (!state.visible.has(id)) state.visible.add(id);
  savePrefs();
  ensurePlan(id);
  renderAll();
}

function toggleVisible(id) {
  if (state.visible.has(id)) {
    if (state.visible.size === 1) { toast('Keep at least one route on the map.'); return; }
    state.visible.delete(id);
    if (state.focus === id) state.focus = [...state.visible][0];
  } else {
    state.visible.add(id);
    ensurePlan(id);
  }
  savePrefs();
  renderAll();
}

function setPortland(on) {
  state.portland = on;
  savePrefs();
  if (state.info) state.info.close();
  for (const id of state.visible) ensurePlan(id);
  renderAll();
}

function renderWeatherViews() {
  renderRoutes();
  renderGlyphs();
  renderWatch();
  renderItinerary();
  renderProfile();
  renderDataLine();
}

function renderAll() {
  renderMapRoutes();
  renderWeatherViews();
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
async function boot() {
  try {
    state.cfg = await api('/api/config');
  } catch (e) {
    $('#data-line').textContent = `Can't reach the local server (${e.message}). Is server.py running?`;
    return;
  }
  document.title = `${state.cfg.title} · route weather`;
  $('#title').textContent = state.cfg.title;
  $('#subtitle').textContent = state.cfg.subtitle;
  if (!loadPrefs()) {
    state.visible = new Set(state.cfg.routes.map((r) => r.id));
    state.focus = state.cfg.default_focus;
    state.portland = !!state.cfg.default_portland;
  }
  if (!state.focus || !state.visible.has(state.focus)) state.focus = [...state.visible][0];
  const pc = $('#portland');
  pc.checked = state.portland;
  pc.addEventListener('change', () => setPortland(pc.checked));
  renderLayers();
  renderLegend();
  renderAll();
  initMap();
  await ensurePlan(state.focus);
  for (const id of state.visible) if (id !== state.focus) ensurePlan(id);
  let rt;
  window.addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(renderProfile, 150); });
}

boot();
