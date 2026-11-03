#!/usr/bin/env python3
"""Move Route Weather: a local web app for the Golden -> Bellingham move.

    python3 server.py            then open http://localhost:8000

Standard library only (Python 3.8+). Forecasts and routes are cached on disk in
./cache so the outside services are called as little as possible:

  * routes (Google Routes, or the OSRM demo server)  - cached until trip.json changes
  * NWS point -> grid lookups                        - cached permanently
  * forecasts                                        - reused for ttl_minutes (config.json)

Forecasts are fetched lazily (only for routes someone is looking at), in the
background, one request at a time per service with polite spacing.
"""

from __future__ import annotations

import bisect
import email.utils
import gzip
import hashlib
import json
import math
import os
import re
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")
CACHE_DIR = os.path.join(HERE, "cache")

DEFAULT_CONFIG = {
    "google_maps_api_key": "",
    "contact": "",
    "host": "127.0.0.1",
    "port": 8000,
    "open_browser": True,
    "sources": {"open_meteo": True, "nws": True, "met_no": True, "google_weather": True},
    "use_google_routes": True,
    "ttl_minutes": {"open_meteo": 180, "nws": 120, "met_no": 120, "google_weather": 360},
    "error_retry_minutes": 15,
    "sample_spacing_miles": 25,
    "open_meteo_models": ["gfs_seamless", "ecmwf_ifs025", "gem_seamless", "icon_seamless", "ncep_nbm_conus"],
    "osrm_url": "https://router.project-osrm.org",
}


def _load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_config():
    """config.json holds shared settings; config.local.json (git-ignored) overrides it, e.g. for the API key."""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    for name in ("config.json", "config.local.json"):
        path = os.path.join(HERE, name)
        if not os.path.exists(path):
            continue
        for k, v in _load_json(path).items():
            if k.startswith("_"):
                continue
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    if os.environ.get("GOOGLE_MAPS_API_KEY"):
        cfg["google_maps_api_key"] = os.environ["GOOGLE_MAPS_API_KEY"]
    return cfg


CONFIG = load_config()
TRIP = _load_json(os.path.join(HERE, "trip.json"))
PLACES = TRIP["places"]
DAYS = TRIP["days"]
ROUTES = OrderedDict((r["id"], r) for r in TRIP["routes"])
LANDMARKS = TRIP.get("landmarks", [])

VERBOSE = "--quiet" not in sys.argv


def log(msg):
    if VERBOSE:
        try:
            print(time.strftime("%H:%M:%S"), msg, flush=True)
        except (UnicodeEncodeError, OSError):  # e.g. a Windows console that can't print a character
            print(time.strftime("%H:%M:%S"), msg.encode("ascii", "replace").decode("ascii"), flush=True)


def google_key():
    return (CONFIG.get("google_maps_api_key") or "").strip()


# --------------------------------------------------------------------------
# Small helpers: time, units, geometry
# --------------------------------------------------------------------------

_FRAC_RE = re.compile(r"\.\d+")
_DUR_RE = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def parse_iso(s):
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    s = _FRAC_RE.sub("", s, count=1)
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def parse_duration(s):
    m = _DUR_RE.match(s.strip())
    if not m:
        raise ValueError("bad duration " + s)
    d, h, mi, se = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def c_to_f(c):
    return None if c is None else c * 9.0 / 5.0 + 32.0


def kmh_to_mph(v):
    return None if v is None else v / 1.609344


def snow_fraction(temp_c):
    """Share of precipitation falling as snow, for sources that don't say."""
    if temp_c is None:
        return 0.0
    if temp_c <= 0.5:
        return 1.0
    if temp_c >= 2.5:
        return 0.0
    return (2.5 - temp_c) / 2.0


R_EARTH = 6371008.8
M_PER_MI = 1609.344


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_EARTH * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def decode_polyline(s, precision=5):
    coords, idx, lat, lon = [], 0, 0, 0
    factor = 10 ** precision
    n = len(s)
    while idx < n:
        vals = []
        for _ in range(2):
            shift = result = 0
            while True:
                b = ord(s[idx]) - 63
                idx += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            vals.append(~(result >> 1) if result & 1 else (result >> 1))
        lat += vals[0]
        lon += vals[1]
        coords.append((lat / factor, lon / factor))
    return coords


def tz_for(lat, lon):
    # Pacific on this trip: Oregon west of Ontario (Malheur County), Washington,
    # and the Idaho Panhandle west of Lookout Pass. Everything else is Mountain.
    if lon < -117.2 and lat > 42.0:
        return "America/Los_Angeles"
    if lat > 46.0 and lon < -115.72:
        return "America/Los_Angeles"
    return "America/Denver"


def r1(x):
    return None if x is None else round(x, 1)


def r2(x):
    return None if x is None else round(x, 2)


# --------------------------------------------------------------------------
# Disk cache
# --------------------------------------------------------------------------

class DiskCache:
    def __init__(self, root):
        self.root = root
        self.lock = threading.Lock()
        self.mem = {}

    def _path(self, ns, key):
        h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:24]
        return os.path.join(self.root, ns, h + ".json")

    def get(self, ns, key):
        with self.lock:
            if (ns, key) in self.mem:
                return self.mem[(ns, key)]
        entry = None
        try:
            with open(self._path(ns, key), "r", encoding="utf-8") as f:
                entry = json.load(f)
            if entry.get("key") != key:
                entry = None
        except (OSError, ValueError):
            entry = None
        with self.lock:
            self.mem[(ns, key)] = entry
        return entry

    def put(self, ns, key, entry):
        entry = dict(entry)
        entry["key"] = key
        path = self._path(ns, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = "%s.%d.tmp" % (path, threading.get_ident())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(entry, f, separators=(",", ":"))
        for attempt in range(5):  # Windows refuses to replace a file another thread is reading
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                time.sleep(0.05 * (attempt + 1))
        else:
            try:
                os.remove(tmp)
            except OSError:
                pass
        with self.lock:
            self.mem[(ns, key)] = entry
        return entry


CACHE = DiskCache(CACHE_DIR)

# In-memory back-off after errors so a failing service isn't retried on every poll.
BACKOFF = {}  # (source, key) -> (until_ts, message)
LAST_ERROR = {}  # source -> message
BACKOFF_LOCK = threading.Lock()


def set_backoff(src, key, msg, seconds=None):
    seconds = seconds if seconds is not None else CONFIG["error_retry_minutes"] * 60
    with BACKOFF_LOCK:
        BACKOFF[(src, key)] = (time.time() + seconds, msg)
        LAST_ERROR[src] = msg
    log("  ! %s: %s" % (src, msg))


def get_backoff(src, key):
    with BACKOFF_LOCK:
        b = BACKOFF.get((src, key))
    if b and b[0] > time.time():
        return b
    return None


# --------------------------------------------------------------------------
# HTTP with polite spacing
# --------------------------------------------------------------------------

class FetchError(Exception):
    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status = status


def user_agent():
    contact = (CONFIG.get("contact") or "").strip()
    return "MoveRouteWeather/1.0 (personal trip planner%s)" % ("; " + contact if contact else "")


def http_request(url, method="GET", headers=None, body=None, timeout=45):
    """Return (parsed_json, response_headers). Raises FetchError."""
    h = {"User-Agent": user_agent(), "Accept-Encoding": "gzip", "Accept": "application/json"}
    h.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        h["Content-Type"] = "application/json"
    host = urllib.parse.urlparse(url).netloc
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if "gzip" in (resp.headers.get("Content-Encoding") or ""):
                raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8")), resp.headers
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            raw = e.read()
            if "gzip" in (e.headers.get("Content-Encoding") or ""):
                raw = gzip.decompress(raw)
            detail = raw.decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise FetchError("HTTP %s from %s %s" % (e.code, host, detail.strip()), status=e.code)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise FetchError("could not reach %s (%s)" % (host, e))


class Throttle:
    def __init__(self, min_interval):
        self.min_interval = min_interval
        self.lock = threading.Lock()
        self.last = 0.0

    def wait(self):
        with self.lock:
            delay = self.last + self.min_interval - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.last = time.monotonic()


THROTTLE = {
    "open_meteo": Throttle(1.0),
    "nws": Throttle(0.4),
    "met_no": Throttle(0.3),
    "google_weather": Throttle(0.15),
    "google_routes": Throttle(0.5),
    "osrm": Throttle(1.2),
}


# --------------------------------------------------------------------------
# Routing (Google Routes with the demo key, else OSRM, else straight lines)
# --------------------------------------------------------------------------

def _route_from_points(pts, legs_m, provider, via_locs=None, approx=False):
    """Cumulative distances, via-point mileage, and a lighter copy for the map."""
    cum = [0.0]
    for i in range(1, len(pts)):
        cum.append(cum[-1] + haversine_m(pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1]))
    total = cum[-1]
    leg_total = sum(legs_m) or total or 1.0
    scale = total / leg_total if leg_total else 1.0
    via_m = [0.0]
    for d in legs_m:
        via_m.append(via_m[-1] + d * scale)
    via_m[-1] = total
    # keep a point every ~100 m for projection, and one every ~400 m for drawing
    keep_pts, keep_cum = [pts[0]], [0.0]
    for p, c in zip(pts[1:-1], cum[1:-1]):
        if c - keep_cum[-1] >= 100:
            keep_pts.append(p)
            keep_cum.append(c)
    keep_pts.append(pts[-1])
    keep_cum.append(total)
    draw, last = [keep_pts[0]], 0.0
    for p, c in zip(keep_pts[1:-1], keep_cum[1:-1]):
        if c - last >= 400:
            draw.append(p)
            last = c
    draw.append(keep_pts[-1])
    return {
        "points": [[round(a, 5), round(b, 5)] for a, b in keep_pts],
        "cum_m": [round(c, 1) for c in keep_cum],
        "draw": [[round(a, 5), round(b, 5)] for a, b in draw],
        "via_m": via_m,
        "via_locs": via_locs,
        "total_m": total,
        "provider": provider,
        "approx": approx,
    }


def _route_google(wps, use_address):
    def waypoint(w):
        if use_address and w.get("address"):
            return {"address": w["address"]}
        return {"location": {"latLng": {"latitude": w["lat"], "longitude": w["lon"]}}}

    body = {
        "origin": waypoint(wps[0]),
        "destination": waypoint(wps[-1]),
        "intermediates": [waypoint(w) for w in wps[1:-1]],
        "travelMode": "DRIVE",
        "polylineQuality": "HIGH_QUALITY",
        "polylineEncoding": "ENCODED_POLYLINE",
    }
    headers = {
        "X-Goog-Api-Key": google_key(),
        "X-Goog-FieldMask": "routes.distanceMeters,routes.polyline.encodedPolyline,"
                            "routes.legs.distanceMeters,routes.legs.startLocation,routes.legs.endLocation",
    }
    THROTTLE["google_routes"].wait()
    data, _ = http_request("https://routes.googleapis.com/directions/v2:computeRoutes", "POST", headers, body)
    routes = data.get("routes") or []
    if not routes:
        raise FetchError("Google Routes returned no route")
    r = routes[0]
    pts = decode_polyline(r["polyline"]["encodedPolyline"], 5)
    legs = r.get("legs") or []
    legs_m = [float(l.get("distanceMeters") or 0) for l in legs]

    def ll(loc):
        try:
            x = loc["latLng"]
            return [x["latitude"], x["longitude"]]
        except (KeyError, TypeError):
            return None

    via_locs = None
    if len(legs) == len(wps) - 1:
        via_locs = [ll(legs[0].get("startLocation"))] + [ll(l.get("endLocation")) for l in legs]
    return _route_from_points(pts, legs_m, "google", via_locs)


def _route_osrm(wps):
    coord = ";".join("%.5f,%.5f" % (w["lon"], w["lat"]) for w in wps)
    url = "%s/route/v1/driving/%s?overview=full&geometries=polyline6&steps=false" % (
        CONFIG["osrm_url"].rstrip("/"), coord)
    THROTTLE["osrm"].wait()
    data, _ = http_request(url)
    if data.get("code") != "Ok" or not data.get("routes"):
        raise FetchError("OSRM: %s" % (data.get("message") or data.get("code")))
    r = data["routes"][0]
    pts = decode_polyline(r["geometry"], 6)
    legs_m = [float(l.get("distance") or 0) for l in r.get("legs") or []]
    via_locs = None
    wp = data.get("waypoints") or []
    if len(wp) == len(wps):
        via_locs = [[w["location"][1], w["location"][0]] for w in wp]
    return _route_from_points(pts, legs_m, "osrm", via_locs)


def _route_straight(wps):
    pts, legs = [], []
    for a, b in zip(wps[:-1], wps[1:]):
        d = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
        legs.append(d * 1.2)  # roads aren't straight
        n = max(2, int(d / 2000))
        for i in range(n):
            f = i / n
            pts.append((a["lat"] + (b["lat"] - a["lat"]) * f, a["lon"] + (b["lon"] - a["lon"]) * f))
    pts.append((wps[-1]["lat"], wps[-1]["lon"]))
    return _route_from_points(pts, legs, "straight", None, approx=True)


def compute_route(wps):
    """wps: [{lat, lon, address?}]. Tries Google (address, then lat/lon), OSRM, straight lines."""
    base = "|".join("%.5f,%.5f,%s" % (w["lat"], w["lon"], w.get("address") or "") for w in wps)
    attempts = []
    if google_key() and CONFIG.get("use_google_routes", True):
        attempts.append(("route_google_addr", lambda: _route_google(wps, True)))
        attempts.append(("route_google_ll", lambda: _route_google(wps, False)))
    attempts.append(("route_osrm", lambda: _route_osrm(wps)))
    errors = []
    for ns, fn in attempts:
        e = CACHE.get(ns, base)
        if e and e.get("ok"):
            return e["data"], errors
        b = get_backoff(ns, base)
        if b:
            errors.append(b[1])
            continue
        try:
            data = fn()
            CACHE.put(ns, base, {"ok": True, "fetched": time.time(), "data": data})
            log("route via %s (%d pts, %.0f mi)" % (ns, len(data["points"]), data["total_m"] / M_PER_MI))
            return data, errors
        except FetchError as ex:
            set_backoff(ns, base, str(ex), 600)
            errors.append(str(ex))
        except (KeyError, IndexError, TypeError, ValueError) as ex:
            set_backoff(ns, base, "unexpected routing response: %r" % ex, 600)
            errors.append(str(ex))
    return _route_straight(wps), errors


# --------------------------------------------------------------------------
# Trip plan: days, stops, sample points and arrival times
# --------------------------------------------------------------------------

DWELL_MIN = {"break": 15, "fuel": 30, "meal": 40}
OVERNIGHT_GAP = 35.0  # profile-axis spacing for a night, in miles-equivalent
DAY_CACHE = {}
DAY_LOCKS = {}
DAY_LOCKS_GUARD = threading.Lock()


def _stop_place(s):
    if isinstance(s.get("via"), list):
        return {"name": s.get("name", "via"), "short": s.get("name", "via"), "lat": s["via"][0], "lon": s["via"][1]}
    return PLACES[s["place"]]


def _is_routed(s):
    if isinstance(s.get("via"), list):
        return True
    return s.get("via", s.get("kind") != "pass")


def _idx_at_m(cum, m):
    i = bisect.bisect_left(cum, m)
    return min(max(i, 0), len(cum) - 1)


def _project(points, lat, lon, start=0):
    coslat = math.cos(math.radians(lat))
    best_i, best_d2 = start, float("inf")
    for i in range(start, len(points)):
        p = points[i]
        dy = p[0] - lat
        dx = (p[1] - lon) * coslat
        d2 = dx * dx + dy * dy
        if d2 < best_d2:
            best_d2, best_i = d2, i
    return best_i, math.sqrt(best_d2) * 111195.0


def _heading(points, cum, m):
    i0 = _idx_at_m(cum, max(0.0, m - 1600))
    i1 = _idx_at_m(cum, min(cum[-1], m + 1600))
    if i1 == i0:
        i1 = min(len(points) - 1, i0 + 1)
        i0 = max(0, i1 - 1)
    a, b = points[i0], points[i1]
    return bearing_deg(a[0], a[1], b[0], b[1])


def build_day(day_id):
    with DAY_LOCKS_GUARD:
        lock = DAY_LOCKS.setdefault(day_id, threading.Lock())
    with lock:
        cached = DAY_CACHE.get(day_id)
        if cached and not (cached["approx"] and time.time() - cached["built"] > 600):
            return cached
        day = DAYS[day_id]
        stops = day["stops"]
        routed = [i for i, s in enumerate(stops) if _is_routed(s)]
        wps = []
        for i in routed:
            p = _stop_place(stops[i])
            wps.append({"lat": p["lat"], "lon": p["lon"], "address": p.get("address")})
        route, route_errors = compute_route(wps)
        pts, cum = route["points"], route["cum_m"]
        total_m = cum[-1]

        # Stops: position, mileage, times
        out_stops = []
        last_idx = 0
        legs_ok = len(route.get("via_m") or []) == len(wps)
        for i, s in enumerate(stops):
            if isinstance(s.get("via"), list):
                continue  # routing-only point
            p = _stop_place(s)
            lat, lon = p["lat"], p["lon"]
            if i in routed and legs_ok:
                k = routed.index(i)
                m = route["via_m"][k]
                locs = route.get("via_locs")
                if locs and k < len(locs) and locs[k]:
                    lat, lon = locs[k]
                gi = _idx_at_m(cum, m)
            else:
                gi, _ = _project(pts, lat, lon, last_idx)
                m = cum[gi]
            last_idx = gi
            arrive = parse_iso(s["arrive"]) if s.get("arrive") else None
            depart = parse_iso(s["depart"]) if s.get("depart") else None
            out_stops.append({
                "place": s.get("place"), "name": p["name"], "short": p.get("short", p["name"]),
                "kind": s.get("kind", "break"), "note": s.get("note", ""),
                "lat": round(lat, 5), "lon": round(lon, 5), "m": m,
                "arrive": arrive, "depart": depart, "tz": tz_for(lat, lon),
            })

        # Timed events for interpolation: (m, arrive_ts, depart_ts)
        events = []
        for j, st in enumerate(out_stops):
            a, d = st["arrive"], st["depart"]
            if a is None and d is None:
                continue
            if a is None:
                a = d
            if d is None:
                last = j == len(out_stops) - 1
                d = a if (last or st["kind"] in ("overnight", "end")) else a + DWELL_MIN.get(st["kind"], 0) * 60
            events.append((st["m"], a, d))
        events.sort(key=lambda e: e[0])
        ev_m = [e[0] for e in events]

        def eta(m):
            if not events:
                return None
            i = bisect.bisect_right(ev_m, m) - 1
            if i < 0:
                return events[0][1]
            if i >= len(events) - 1:
                return events[-1][1]
            m0, _, d0 = events[i]
            m1, a1, _ = events[i + 1]
            if m1 <= m0:
                return a1
            return d0 + (m - m0) / (m1 - m0) * (a1 - d0)

        # Named sample points: stops, then passes, then towns (spaced out)
        lat_lo = min(p[0] for p in pts) - 0.1
        lat_hi = max(p[0] for p in pts) + 0.1
        lon_lo = min(p[1] for p in pts) - 0.1
        lon_hi = max(p[1] for p in pts) + 0.1
        named = []
        for j, st in enumerate(out_stops):
            if j == 0:
                t = st["depart"] or st["arrive"]
            else:
                t = st["arrive"] or st["depart"]
            named.append({"m": st["m"], "lat": st["lat"], "lon": st["lon"], "name": st["name"],
                          "short": st["short"], "kind": st["kind"], "stop": j, "place": st["place"],
                          "eta": t, "key": True})
        for want_type, min_gap_mi in (("pass", 3.0), ("town", 7.0)):
            for lm in LANDMARKS:
                if lm.get("type", "town") != want_type:
                    continue
                if not (lat_lo <= lm["lat"] <= lat_hi and lon_lo <= lm["lon"] <= lon_hi):
                    continue
                gi, dist = _project(pts, lm["lat"], lm["lon"])
                if dist > 6000:
                    continue
                m = cum[gi]
                if any(abs(m - n["m"]) < min_gap_mi * M_PER_MI for n in named):
                    continue
                named.append({"m": m, "lat": pts[gi][0], "lon": pts[gi][1], "name": lm["name"],
                              "short": lm["name"].split(" (")[0], "kind": want_type, "stop": None,
                              "place": None, "eta": None, "key": want_type == "pass"})
        named.sort(key=lambda n: (n["m"], 0 if n["stop"] is not None else 1))

        # Fill long gaps so samples are never much more than ~25 miles apart
        spacing = float(CONFIG.get("sample_spacing_miles", 25)) * M_PER_MI
        samples = []
        for a, b in zip(named, named[1:] + [None]):
            samples.append(a)
            if b is None:
                break
            gap = b["m"] - a["m"]
            if gap > spacing * 1.25:
                n = int(math.ceil(gap / spacing)) - 1
                for k in range(1, n + 1):
                    m = a["m"] + gap * k / (n + 1)
                    gi = _idx_at_m(cum, m)
                    samples.append({"m": m, "lat": pts[gi][0], "lon": pts[gi][1],
                                    "name": "Between %s and %s" % (a["short"], b["short"]),
                                    "short": "", "kind": "fill", "stop": None, "place": None,
                                    "eta": None, "key": False})

        out_samples = []
        for k, s in enumerate(samples):
            t = s["eta"] if s["eta"] is not None else eta(s["m"])
            out_samples.append({
                "id": "%s:%d" % (day_id, k), "lat": round(s["lat"], 5), "lon": round(s["lon"], 5),
                "m": s["m"], "eta": t, "heading": round(_heading(pts, cum, s["m"])),
                "name": s["name"], "kind": s["kind"], "stop": s["stop"], "place": s["place"],
                "key": s["key"], "tz": tz_for(s["lat"], s["lon"]),
                "lk": "%.3f,%.3f" % (s["lat"], s["lon"]),
            })

        built = {
            "id": day_id, "label": day.get("label", ""), "title": day.get("title", ""),
            "note": day.get("note", ""), "estimated": bool(day.get("estimated")),
            "total_m": total_m, "draw": route["draw"], "stops": out_stops, "samples": out_samples,
            "provider": route["provider"], "approx": route["approx"], "route_errors": route_errors,
            "built": time.time(),
        }
        DAY_CACHE[day_id] = built
        return built


def route_days(route_id, portland):
    r = ROUTES[route_id]
    if portland and r.get("portland_days"):
        return r["portland_days"]
    return r["days"]


def build_plan(route_id, portland):
    days = [build_day(d) for d in route_days(route_id, portland)]
    out_days, stops, samples, paths = [], [], [], []
    offset_m = 0.0
    for di, d in enumerate(days):
        x0 = offset_m / M_PER_MI + di * OVERNIGHT_GAP
        for st in d["stops"]:
            s2 = dict(st)
            s2["mile"] = round((offset_m + st["m"]) / M_PER_MI, 1)
            s2["x"] = round(x0 + st["m"] / M_PER_MI, 2)
            s2["day"] = di
            s2.pop("m", None)
            stops.append(s2)
        for sm in d["samples"]:
            s2 = dict(sm)
            s2["mile"] = round((offset_m + sm["m"]) / M_PER_MI, 1)
            s2["x"] = round(x0 + sm["m"] / M_PER_MI, 2)
            s2["day"] = di
            s2.pop("m", None)
            samples.append(s2)
        out_days.append({
            "template": d["id"], "label": d["label"], "title": d["title"], "note": d["note"],
            "estimated": d["estimated"], "miles": round(d["total_m"] / M_PER_MI),
            "x0": round(x0, 2), "x1": round(x0 + d["total_m"] / M_PER_MI, 2),
            "provider": d["provider"], "approx": d["approx"], "route_errors": d["route_errors"],
        })
        paths.append(d["draw"])
        offset_m += d["total_m"]
    r = ROUTES[route_id]
    return {
        "route": route_id, "name": r["name"], "short": r.get("short", ""), "color": r.get("color", "#475569"),
        "portland": bool(portland and r.get("portland_days")),
        "total_miles": round(offset_m / M_PER_MI),
        "days": out_days, "stops": stops, "samples": samples, "paths": paths,
    }


def _trip_window():
    ts = []
    for d in DAYS.values():
        for s in d["stops"]:
            for k in ("arrive", "depart"):
                if s.get(k):
                    ts.append(parse_iso(s[k]))
    return min(ts) - 6 * 3600, max(ts) + 6 * 3600


WINDOW_START, WINDOW_END = _trip_window()
OM_START = datetime.fromtimestamp(WINDOW_START, timezone.utc).strftime("%Y-%m-%d")
OM_END = datetime.fromtimestamp(WINDOW_END, timezone.utc).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------
# Weather sources. Every source is normalised to "members":
#   inst:   [[t, temp_c, wind_kmh, wind_from_deg, gust_kmh], ...]
#   precip: [[t0, t1, total_liquid_mm, rain_mm|None, snow_cm|None], ...]
#   pop:    [[t0, t1, percent], ...]
# --------------------------------------------------------------------------

SOURCE_LABELS = OrderedDict([
    ("open_meteo", "Open-Meteo models"),
    ("nws", "National Weather Service"),
    ("met_no", "MET Norway"),
    ("google_weather", "Google Weather"),
])
MODEL_LABELS = {
    "gfs_seamless": "NOAA GFS/HRRR", "ecmwf_ifs025": "ECMWF IFS", "ecmwf_ifs": "ECMWF IFS",
    "gem_seamless": "Canada GEM", "icon_seamless": "DWD ICON", "ncep_nbm_conus": "NOAA NBM",
    "ecmwf_aifs025_single": "ECMWF AIFS", "gfs_graphcast025": "GraphCast",
}
BAD_MODELS = set()
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OM_HOURLY = ["temperature_2m", "precipitation", "rain", "showers", "snowfall",
             "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m"]


def active_models():
    return [m for m in CONFIG["open_meteo_models"] if m not in BAD_MODELS]


def om_key(lk):
    return "%s|%s|%s|%s" % (lk, OM_START, OM_END, ",".join(active_models()))


def source_enabled(src):
    if not CONFIG["sources"].get(src, False):
        return False
    if src == "google_weather" and not google_key():
        return False
    if src == "open_meteo" and not active_models():
        return False
    return True


def ttl(src):
    return float(CONFIG["ttl_minutes"].get(src, 120)) * 60


# ---- Open-Meteo -----------------------------------------------------------

def parse_open_meteo(item, models):
    hourly = item.get("hourly") or {}
    times = hourly.get("time") or []
    n = len(times)
    members = []
    for model in models:
        def col(name):
            v = hourly.get("%s_%s" % (name, model))
            if v is None and len(models) == 1:
                v = hourly.get(name)
            return v if v is not None else [None] * n

        T, P, RA, SH, SN = col("temperature_2m"), col("precipitation"), col("rain"), col("showers"), col("snowfall")
        WS, WD, WG = col("wind_speed_10m"), col("wind_direction_10m"), col("wind_gusts_10m")
        inst, precip = [], []
        for i, t in enumerate(times):
            if T[i] is None and WS[i] is None:
                continue
            inst.append([t, T[i], WS[i], WD[i], WG[i]])
            if P[i] is not None:
                rain = None
                if RA[i] is not None or SH[i] is not None:
                    rain = (RA[i] or 0.0) + (SH[i] or 0.0)
                precip.append([t - 3600, t, P[i], rain, SN[i]])
        if inst:
            members.append({"src": "open_meteo", "name": MODEL_LABELS.get(model, model), "inst": inst,
                            "precip": precip, "pop": [], "snow_ratio": 0.7})
    return members


def _probe_bad_models(models, payload):
    """A 400 for a multi-model call usually means one model name isn't valid. Find it."""
    bad = []
    for m in models:
        params = {"latitude": "%.3f" % payload["lat"], "longitude": "%.3f" % payload["lon"],
                  "hourly": "temperature_2m", "models": m, "forecast_days": 1}
        THROTTLE["open_meteo"].wait()
        try:
            http_request(OPEN_METEO_URL + "?" + urllib.parse.urlencode(params))
        except FetchError as e:
            if e.status == 400:
                bad.append(m)
    for m in bad:
        BAD_MODELS.add(m)
        log("Open-Meteo rejected model '%s'; continuing without it" % m)
    return bool(bad)


def fetch_open_meteo(batch):
    models = batch[0][1]["models"]
    params = {
        "latitude": ",".join("%.3f" % p["lat"] for _, p in batch),
        "longitude": ",".join("%.3f" % p["lon"] for _, p in batch),
        "hourly": ",".join(OM_HOURLY), "models": ",".join(models),
        "start_date": OM_START, "end_date": OM_END, "timezone": "GMT", "timeformat": "unixtime",
    }
    THROTTLE["open_meteo"].wait()
    log("Open-Meteo: %d locations" % len(batch))
    try:
        data, _ = http_request(OPEN_METEO_URL + "?" + urllib.parse.urlencode(params, safe=","))
    except FetchError as e:
        if e.status == 400 and len(models) > 1 and _probe_bad_models(models, batch[0][1]):
            return  # requests are re-made with the remaining models on the next poll
        raise
    items = data if isinstance(data, list) else [data]
    if len(items) != len(batch):
        raise FetchError("Open-Meteo returned %d locations for %d requested" % (len(items), len(batch)))
    now = time.time()
    for (key, p), item in zip(batch, items):
        CACHE.put("open_meteo", key, {"ok": True, "fetched": now, "expires": now + ttl("open_meteo"),
                                      "data": {"members": parse_open_meteo(item, models)}})


# ---- National Weather Service --------------------------------------------

def _nws_layer(props, name):
    layer = props.get(name) or {}
    uom = layer.get("uom") or ""
    out = []
    for v in layer.get("values") or []:
        val = v.get("value")
        if val is None:
            continue
        try:
            start_s, dur_s = v["validTime"].split("/")
            t0 = parse_iso(start_s)
            out.append((t0, t0 + parse_duration(dur_s), float(val)))
        except (KeyError, ValueError):
            continue
    return uom, out


def _nws_temp_c(v, uom):
    return (v - 32) * 5 / 9 if uom.endswith("degF") else v


def _nws_speed_kmh(v, uom):
    if "m_s" in uom:
        return v * 3.6
    if "mi_h" in uom or uom.endswith("mph"):
        return v * 1.609344
    if uom.endswith("kt") or "knot" in uom:
        return v * 1.852
    return v


def _nws_len_mm(v, uom):
    if uom.endswith(":cm"):
        return v * 10
    if uom.endswith(":m"):
        return v * 1000
    if uom.endswith(":in"):
        return v * 25.4
    return v


def parse_nws(data):
    props = data.get("properties") or {}
    hours = {}

    def expand(name, slot, conv):
        uom, rows = _nws_layer(props, name)
        for t0, t1, val in rows:
            t = t0
            n = 0
            while t < t1 and n < 240:
                hours.setdefault(int(t), [None, None, None, None])[slot] = conv(val, uom)
                t += 3600
                n += 1

    expand("temperature", 0, _nws_temp_c)
    expand("windSpeed", 1, _nws_speed_kmh)
    expand("windDirection", 2, lambda v, u: v)
    expand("windGust", 3, _nws_speed_kmh)
    inst = [[t] + v for t, v in sorted(hours.items()) if v[0] is not None or v[1] is not None]

    q_uom, qpf = _nws_layer(props, "quantitativePrecipitation")
    s_uom, snow = _nws_layer(props, "snowfallAmount")
    precip = []
    for t0, t1, val in qpf:
        mm = _nws_len_mm(val, q_uom)
        snow_cm = 0.0
        for s0, s1, sv in snow:
            ov = min(t1, s1) - max(t0, s0)
            if ov > 0 and s1 > s0:
                snow_cm += _nws_len_mm(sv, s_uom) / 10.0 * ov / (s1 - s0)
        precip.append([t0, t1, mm, max(0.0, mm - snow_cm), snow_cm])
    _, pop_rows = _nws_layer(props, "probabilityOfPrecipitation")
    pop = [[t0, t1, v] for t0, t1, v in pop_rows]
    if not inst:
        return []
    return [{"src": "nws", "name": "NWS forecast", "inst": inst, "precip": precip, "pop": pop, "snow_ratio": 1.0}]


def fetch_nws(batch):
    for lk, p in batch:
        now = time.time()
        pe = CACHE.get("nws_points", lk)
        if not (pe and pe.get("ok")):
            THROTTLE["nws"].wait()
            try:
                data, _ = http_request("https://api.weather.gov/points/%.4f,%.4f" % (p["lat"], p["lon"]),
                                       headers={"Accept": "application/geo+json"})
            except FetchError as e:
                set_backoff("nws", lk, str(e), 86400 if e.status == 404 else None)
                continue
            grid = (data.get("properties") or {}).get("forecastGridData")
            if not grid:
                set_backoff("nws", lk, "weather.gov has no forecast grid for this point", 86400)
                continue
            pe = CACHE.put("nws_points", lk, {"ok": True, "fetched": now, "data": {"grid": grid}})
        grid = pe["data"]["grid"]
        ge = CACHE.get("nws_grid", grid)
        if ge and ge.get("ok") and ge.get("expires", 0) > now:
            continue  # another sample point shares this grid cell and already refreshed it
        THROTTLE["nws"].wait()
        try:
            data, _ = http_request(grid, headers={"Accept": "application/geo+json"})
        except FetchError as e:
            set_backoff("nws", lk, str(e))
            continue
        CACHE.put("nws_grid", grid, {"ok": True, "fetched": now, "expires": now + ttl("nws"),
                                     "data": {"members": parse_nws(data)}})


# ---- MET Norway -----------------------------------------------------------

def parse_met_no(data):
    series = (data.get("properties") or {}).get("timeseries") or []
    inst, precip, pop = [], [], []
    last_end = -1e18
    for e in series:
        try:
            t = parse_iso(e["time"])
        except (KeyError, ValueError):
            continue
        d = e.get("data") or {}
        det = (d.get("instant") or {}).get("details") or {}
        ws, wg = det.get("wind_speed"), det.get("wind_speed_of_gust")
        inst.append([t, det.get("air_temperature"), None if ws is None else ws * 3.6,
                     det.get("wind_from_direction"), None if wg is None else wg * 3.6])
        for blk_name, hrs in (("next_1_hours", 1), ("next_6_hours", 6)):
            blk = d.get(blk_name)
            if not blk:
                continue
            bd = blk.get("details") or {}
            amt = bd.get("precipitation_amount")
            if amt is None:
                continue
            if t >= last_end:
                t1 = t + hrs * 3600
                precip.append([t, t1, amt, None, None])
                if bd.get("probability_of_precipitation") is not None:
                    pop.append([t, t1, bd["probability_of_precipitation"]])
                last_end = t1
            break
    if not inst:
        return []
    return [{"src": "met_no", "name": "MET Norway", "inst": inst, "precip": precip, "pop": pop, "snow_ratio": 1.0}]


def fetch_met_no(batch):
    for lk, p in batch:
        THROTTLE["met_no"].wait()
        url = "https://api.met.no/weatherapi/locationforecast/2.0/complete?lat=%.4f&lon=%.4f" % (p["lat"], p["lon"])
        try:
            data, headers = http_request(url)
        except FetchError as e:
            set_backoff("met_no", lk, str(e))
            continue
        now = time.time()
        expires = now + ttl("met_no")
        try:  # never refetch before the service's own Expires time
            exp_hdr = headers.get("Expires")
            if exp_hdr:
                expires = max(expires, email.utils.parsedate_to_datetime(exp_hdr).timestamp())
        except (TypeError, ValueError):
            pass
        CACHE.put("met_no", lk, {"ok": True, "fetched": now, "expires": expires,
                                 "data": {"members": parse_met_no(data)}})


# ---- Google Weather (demo key) --------------------------------------------

def _g_temp_c(o):
    if not o or o.get("degrees") is None:
        return None
    v = float(o["degrees"])
    return (v - 32) * 5 / 9 if (o.get("unit") or "").upper().startswith("FAHR") else v


def _g_speed_kmh(o):
    if not o or o.get("value") is None:
        return None
    v = float(o["value"])
    return v * 1.609344 if "MILES" in (o.get("unit") or "").upper() else v


def _g_mm(o):
    if not o or o.get("quantity") is None:
        return None
    v = float(o["quantity"])
    return v * 25.4 if "INCH" in (o.get("unit") or "").upper() else v


def parse_google_weather(hours):
    inst, precip, pop = [], [], []
    for e in hours:
        iv = e.get("interval") or {}
        if not iv.get("startTime"):
            continue
        t0 = parse_iso(iv["startTime"])
        t1 = parse_iso(iv["endTime"]) if iv.get("endTime") else t0 + 3600
        temp = _g_temp_c(e.get("temperature"))
        wind = e.get("wind") or {}
        inst.append([t0, temp, _g_speed_kmh(wind.get("speed")), (wind.get("direction") or {}).get("degrees"),
                     _g_speed_kmh(wind.get("gust"))])
        pr = e.get("precipitation") or {}
        prob = pr.get("probability") or {}
        q = _g_mm(pr.get("qpf"))
        if q is not None:
            ptype = (prob.get("type") or "").upper()
            if "SNOW" in ptype and "RAIN" in ptype:
                f = 0.5
            elif "SNOW" in ptype:
                f = 1.0
            elif "RAIN" in ptype:
                f = 0.0
            else:
                f = snow_fraction(temp)
            precip.append([t0, t1, q, q * (1 - f), q * f])
        if prob.get("percent") is not None:
            pop.append([t0, t1, prob["percent"]])
    if not inst:
        return []
    return [{"src": "google_weather", "name": "Google Weather", "inst": inst, "precip": precip, "pop": pop,
             "snow_ratio": 1.0}]


def fetch_google_weather(batch):
    for lk, p in batch:
        now = time.time()
        hours_needed = int(math.ceil((WINDOW_END - now) / 3600.0)) + 1
        hours_needed = max(1, min(240, hours_needed))
        entries, token, pages = [], None, 0
        try:
            while True:
                params = {"key": google_key(), "location.latitude": "%.4f" % p["lat"],
                          "location.longitude": "%.4f" % p["lon"], "hours": hours_needed, "pageSize": 24}
                if token:
                    params["pageToken"] = token
                THROTTLE["google_weather"].wait()
                data, _ = http_request("https://weather.googleapis.com/v1/forecast/hours:lookup?" +
                                       urllib.parse.urlencode(params))
                fh = data.get("forecastHours") or []
                entries.extend(fh)
                token = data.get("nextPageToken")
                pages += 1
                if not token or pages >= 11 or not fh:
                    break
                last = (fh[-1].get("interval") or {}).get("startTime")
                if last and parse_iso(last) > WINDOW_END:
                    break
        except FetchError as e:
            set_backoff("google_weather", lk, str(e))
            continue
        CACHE.put("google_weather", lk, {"ok": True, "fetched": now, "expires": now + ttl("google_weather"),
                                         "data": {"members": parse_google_weather(entries)}})


# --------------------------------------------------------------------------
# Background workers (one per source, so a slow service doesn't hold up others)
# --------------------------------------------------------------------------

class SourceWorker(threading.Thread):
    def __init__(self, name, fn, batch_size=1):
        super().__init__(daemon=True, name="worker-" + name)
        self.src, self.fn, self.batch_size = name, fn, batch_size
        self.pending = OrderedDict()
        self.inflight = set()
        self.cv = threading.Condition()

    def want(self, key, payload):
        with self.cv:
            if key in self.pending or key in self.inflight:
                return
            self.pending[key] = payload
            self.cv.notify()

    def busy(self, key):
        with self.cv:
            return key in self.pending or key in self.inflight

    def queued_count(self):
        with self.cv:
            return len(self.pending) + len(self.inflight)

    def run(self):
        while True:
            with self.cv:
                while not self.pending:
                    self.cv.wait()
                batch = []
                while self.pending and len(batch) < self.batch_size:
                    k, p = self.pending.popitem(last=False)
                    self.inflight.add(k)
                    batch.append((k, p))
            try:
                self.fn(batch)
            except FetchError as e:
                for k, _ in batch:
                    set_backoff(self.src, k, str(e))
            except Exception as e:  # a parsing surprise shouldn't kill the worker
                traceback.print_exc()
                for k, _ in batch:
                    set_backoff(self.src, k, "unexpected response: %r" % e)
            finally:
                with self.cv:
                    for k, _ in batch:
                        self.inflight.discard(k)


WORKERS = {
    "open_meteo": SourceWorker("open_meteo", fetch_open_meteo, batch_size=40),
    "nws": SourceWorker("nws", fetch_nws),
    "met_no": SourceWorker("met_no", fetch_met_no),
    "google_weather": SourceWorker("google_weather", fetch_google_weather),
}


def source_lookup(src, sample, enqueue=True):
    """-> (members, state, queued, fetched_ts, error_msg). state: fresh|stale|missing|error."""
    lk = sample["lk"]
    now = time.time()
    if src == "open_meteo":
        key = om_key(lk)
        entry = CACHE.get("open_meteo", key)
    elif src == "nws":
        key = lk
        pe = CACHE.get("nws_points", lk)
        entry = CACHE.get("nws_grid", pe["data"]["grid"]) if pe and pe.get("ok") else None
    else:
        key = lk
        entry = CACHE.get(src, lk)
    has = bool(entry and entry.get("ok"))
    fresh = has and entry.get("expires", 0) > now
    worker = WORKERS[src]
    err = None
    if fresh:
        state = "fresh"
    else:
        b = get_backoff(src, key)
        if b:
            err = b[1]
            state = "stale" if has else "error"
        else:
            if enqueue:
                payload = {"lat": sample["lat"], "lon": sample["lon"], "lk": lk}
                if src == "open_meteo":
                    payload["models"] = active_models()
                worker.want(key, payload)
            state = "stale" if has else "missing"
    members = entry["data"]["members"] if has else []
    return members, state, worker.busy(key), (entry.get("fetched") if has else None), err


# --------------------------------------------------------------------------
# Evaluating a forecast at an arrival time, and combining sources
# --------------------------------------------------------------------------

WINDOW_HALF = 90 * 60  # rain/snow totals cover 3 hours centred on the arrival time


def evaluate_member(m, t):
    inst = m["inst"]
    times = m.get("_times")
    if times is None or len(times) != len(inst):
        times = [row[0] for row in inst]
        m["_times"] = times
    n = len(times)
    if n == 0:
        return None
    i = bisect.bisect_left(times, t)
    cands = [j for j in (i - 1, i) if 0 <= j < n]
    jn = min(cands, key=lambda j: abs(times[j] - t))
    if abs(times[jn] - t) > 3 * 3600:
        return None  # arrival is outside this source's forecast range

    def temp_at(tt):
        k = bisect.bisect_left(times, tt)
        if k < n and times[k] == tt:
            return inst[k][1]
        if 0 < k < n and inst[k - 1][1] is not None and inst[k][1] is not None and times[k] - times[k - 1] <= 7 * 3600:
            f = (tt - times[k - 1]) / float(times[k] - times[k - 1])
            return inst[k - 1][1] + f * (inst[k][1] - inst[k - 1][1])
        cs = [j for j in (k - 1, k) if 0 <= j < n and inst[j][1] is not None]
        if not cs:
            return None
        j = min(cs, key=lambda j: abs(times[j] - tt))
        return inst[j][1] if abs(times[j] - tt) <= 4 * 3600 else None

    temp = temp_at(t)
    ws, wd = inst[jn][2], inst[jn][3]
    lo = bisect.bisect_left(times, t - 3600)
    hi = bisect.bisect_right(times, t + 3600)
    gusts = [row[4] for row in inst[lo:hi] if row[4] is not None]
    gust = max(gusts) if gusts else inst[jn][4]

    w0, w1 = t - WINDOW_HALF, t + WINDOW_HALF
    rain = snow = 0.0
    cover = 0.0
    ratio = m.get("snow_ratio", 1.0) or 1.0
    for p0, p1, amt, r, s in m["precip"]:
        if p1 <= w0 or p0 >= w1 or amt is None or p1 <= p0:
            continue
        ov = min(p1, w1) - max(p0, w0)
        frac = ov / (p1 - p0)
        cover += ov
        if r is not None or s is not None:
            sn = s or 0.0
            rn = r if r is not None else max(0.0, amt - sn / ratio)
        else:
            f = snow_fraction(temp_at((p0 + p1) / 2.0))
            rn, sn = amt * (1 - f), amt * f  # 10:1 snow ratio => 1 mm water ~ 1 cm snow
        rain += rn * frac
        snow += sn * frac
    have_precip = cover >= 0.5 * (w1 - w0)
    pops = [pc for p0, p1, pc in m.get("pop", []) if p1 > w0 and p0 < w1 and pc is not None]
    if temp is None and ws is None:
        return None
    return {
        "name": m["name"], "src": m["src"],
        "t": c_to_f(temp), "w": kmh_to_mph(ws), "d": wd, "g": kmh_to_mph(gust),
        "r": rain / 25.4 if have_precip else None, "s": snow / 2.54 if have_precip else None,
        "pop": max(pops) if pops else None,
    }


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def aggregate(vals, heading):
    if not vals:
        return None
    temps = [v["t"] for v in vals if v["t"] is not None]
    winds = [v for v in vals if v["w"] is not None]
    gusts = [v["g"] for v in vals if v["g"] is not None]
    pv = [v for v in vals if v["r"] is not None]
    pops = [v["pop"] for v in vals if v["pop"] is not None]

    wdir = None
    if winds:
        u = sum(v["w"] * math.sin(math.radians(v["d"])) for v in winds if v["d"] is not None)
        w = sum(v["w"] * math.cos(math.radians(v["d"])) for v in winds if v["d"] is not None)
        if abs(u) + abs(w) > 1e-6:
            wdir = (math.degrees(math.atan2(u, w)) + 360) % 360
        else:
            ds = [v["d"] for v in winds if v["d"] is not None]
            wdir = ds[0] if ds else None
    wmed = _median([v["w"] for v in winds])
    gmed = _median(gusts)
    xw = xg = None
    if wdir is not None and heading is not None:
        sf = abs(math.sin(math.radians(wdir - heading)))
        xw = wmed * sf if wmed is not None else None
        xg = (gmed if gmed is not None else wmed) * sf if (gmed is not None or wmed is not None) else None

    res = {
        "t": r1(_median(temps)), "tlo": r1(min(temps)) if temps else None, "thi": r1(max(temps)) if temps else None,
        "w": r1(wmed), "wlo": r1(min(v["w"] for v in winds)) if winds else None,
        "whi": r1(max(v["w"] for v in winds)) if winds else None,
        "d": None if wdir is None else int(round(wdir)) % 360,
        "g": r1(gmed), "ghi": r1(max(gusts)) if gusts else None,
        "xw": r1(xw), "xg": r1(xg),
        "r": r2(sum(v["r"] for v in pv) / len(pv)) if pv else None,
        "rhi": r2(max(v["r"] for v in pv)) if pv else None,
        "s": r1(sum(v["s"] for v in pv) / len(pv)) if pv else None,
        "shi": r1(max(v["s"] for v in pv)) if pv else None,
        "agree": round(sum(1 for v in pv if v["r"] >= 0.01 or v["s"] >= 0.1) / len(pv), 2) if pv else None,
        "np": len(pv), "n": len(vals),
        "pop": int(round(max(pops))) if pops else None,
        "m": [[v["name"], r1(v["t"]), r1(v["w"]), None if v["d"] is None else int(round(v["d"])),
               r1(v["g"]), r2(v["r"]), r1(v["s"]), None if v["pop"] is None else int(round(v["pop"]))]
              for v in vals],
    }
    return res


def weather_for_plan(plan, enqueue=True):
    sources = [s for s in SOURCE_LABELS if source_enabled(s)]
    status = {s: {"label": SOURCE_LABELS[s], "fresh": 0, "stale": 0, "missing": 0, "error": 0,
                  "queued": 0, "oldest": None, "newest": None, "error_msg": None, "points": 0} for s in sources}
    weather = []
    queued_total = 0
    for sm in plan["samples"]:
        vals = []
        for src in sources:
            if src == "google_weather" and not sm.get("key"):
                continue  # Google is paged per location, so only stops and passes use it
            members, state, queued, fetched, err = source_lookup(src, sm, enqueue)
            st = status[src]
            st["points"] += 1
            st[state] += 1
            if queued:
                st["queued"] += 1
                queued_total += 1
            if err:
                st["error_msg"] = err
            if fetched:
                st["oldest"] = fetched if st["oldest"] is None else min(st["oldest"], fetched)
                st["newest"] = fetched if st["newest"] is None else max(st["newest"], fetched)
            if sm["eta"] is None:
                continue
            for mem in members:
                v = evaluate_member(mem, sm["eta"])
                if v:
                    vals.append(v)
        weather.append(aggregate(vals, sm.get("heading")))
    for src in sources:
        if status[src]["error_msg"] is None and status[src]["error"]:
            status[src]["error_msg"] = LAST_ERROR.get(src)
    if "open_meteo" in status and BAD_MODELS:
        status["open_meteo"]["note"] = "skipped models: " + ", ".join(sorted(BAD_MODELS))
    return weather, {"queued": queued_total, "sources": status}


# --------------------------------------------------------------------------
# API + static files
# --------------------------------------------------------------------------

class ApiError(Exception):
    pass


def api_config():
    routes = []
    for r in ROUTES.values():
        if r.get("portland") == "always":
            mode = "always"
        elif r.get("portland_days") and r.get("portland") != "never":
            mode = "toggle"
        else:
            mode = "never"
        routes.append({
            "id": r["id"], "name": r["name"], "short": r.get("short", ""), "color": r.get("color", "#475569"),
            "badge": r.get("badge"), "badge_portland": r.get("badge_portland"),
            "description": r.get("description", ""), "description_portland": r.get("description_portland"),
            "portland": mode,
        })
    default_focus = TRIP.get("default_focus")
    if default_focus not in ROUTES:
        default_focus = next(iter(ROUTES))
    return {
        "title": TRIP.get("title", "Route weather"), "subtitle": TRIP.get("subtitle", ""),
        "google_maps_api_key": google_key(),
        "routes": routes,
        "sources": [{"id": s, "label": SOURCE_LABELS[s], "enabled": source_enabled(s)} for s in SOURCE_LABELS],
        "models": [MODEL_LABELS.get(m, m) for m in active_models()],
        "default_focus": default_focus,
        "default_portland": bool(TRIP.get("default_portland", False)),
        "window_half_minutes": WINDOW_HALF // 60,
    }


def api_plan(q):
    route = (q.get("route") or [""])[0]
    if route not in ROUTES:
        raise ApiError("unknown route")
    portland = (q.get("portland") or ["0"])[0] in ("1", "true", "yes")
    want_geom = (q.get("geom") or ["1"])[0] != "0"
    want_weather = (q.get("weather") or ["1"])[0] != "0"
    plan = build_plan(route, portland)
    if want_weather:
        plan["weather"], plan["status"] = weather_for_plan(plan)
    if not want_geom:
        plan.pop("paths", None)
    plan["generated"] = time.time()
    return plan


class Handler(SimpleHTTPRequestHandler):
    # Explicit types: on Windows the registry can map .js to text/plain.
    extensions_map = dict(SimpleHTTPRequestHandler.extensions_map, **{
        ".js": "application/javascript", ".css": "text/css", ".html": "text/html; charset=utf-8",
        ".json": "application/json", ".svg": "image/svg+xml", "": "application/octet-stream",
    })

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def send_json(self, obj, status=200):
        body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        gz = "gzip" in (self.headers.get("Accept-Encoding") or "") and len(body) > 2048
        if gz:
            body = gzip.compress(body, 5)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if gz:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path.startswith("/api/"):
            q = urllib.parse.parse_qs(u.query)
            try:
                if u.path == "/api/config":
                    return self.send_json(api_config())
                if u.path == "/api/plan":
                    return self.send_json(api_plan(q))
                return self.send_json({"error": "not found"}, 404)
            except ApiError as e:
                return self.send_json({"error": str(e)}, 400)
            except Exception as e:
                traceback.print_exc()
                return self.send_json({"error": "server error: %s" % e}, 500)
        return super().do_GET()


_workers_started = False


def start_workers():
    global _workers_started
    if not _workers_started:
        for w in WORKERS.values():
            w.start()
        _workers_started = True


def make_server(host=None, port=None):
    os.makedirs(CACHE_DIR, exist_ok=True)
    start_workers()
    return ThreadingHTTPServer((host or CONFIG["host"], int(port or CONFIG["port"])), Handler)


def main():
    port = CONFIG["port"]
    for i, a in enumerate(sys.argv):
        if a == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])
    srv = make_server(port=port)
    url = "http://localhost:%d" % port
    print("\n  Move Route Weather is running at %s  (Ctrl+C to stop)\n" % url)
    print("  Google key: %s" % ("set" if google_key() else "not set (map shows Google's development watermark)"))
    print("  Weather sources: %s" % ", ".join(SOURCE_LABELS[s] for s in SOURCE_LABELS if source_enabled(s)))
    print("  Cache folder: %s\n" % CACHE_DIR)
    if CONFIG.get("open_browser", True) and "--no-browser" not in sys.argv:
        try:
            import webbrowser
            threading.Timer(0.8, lambda: webbrowser.open(url)).start()
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
