"""Simulated responses for every outside service the app calls, in the formats their docs describe.

The weather is invented: windy Wyoming on Saturday evening, snow over the Cascades on
Monday, rain west of the Cascades. 'ncep_nbm_conus' is deliberately rejected so the
server's bad-model detection gets exercised. Used by run_simulated.py; never hits the network.
"""
import json
import math
import time
import urllib.parse
from datetime import datetime, timezone

CALLS = []
GOOGLE_WEATHER_429 = False  # set True to simulate Google's daily quota being used up
RATE_LIMIT_ONCE = set()     # hosts that answer the next request with a 429 (Retry-After: 1 s)


def enc_val(v):
    v = ~(v << 1) if v < 0 else (v << 1)
    out = []
    while v >= 0x20:
        out.append(chr((0x20 | (v & 0x1F)) + 63))
        v >>= 5
    out.append(chr(v + 63))
    return "".join(out)


def encode_polyline(pts, precision):
    f = 10 ** precision
    out, plat, plon = [], 0, 0
    for lat, lon in pts:
        a, b = int(round(lat * f)), int(round(lon * f))
        out.append(enc_val(a - plat))
        out.append(enc_val(b - plon))
        plat, plon = a, b
    return "".join(out)


def hav(a, b):
    R = 6371008.8
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def wiggly(a, b):
    d = hav(a, b)
    n = max(4, int(d / 300))
    pts = []
    for i in range(n):
        f = i / n
        off = 0.03 * math.sin(f * math.pi * 3) * math.sin(f * math.pi)
        pts.append((a[0] + (b[0] - a[0]) * f + off, a[1] + (b[1] - a[1]) * f - off))
    return pts


def build_route(wps):
    pts, legs = [], []
    for a, b in zip(wps[:-1], wps[1:]):
        seg = wiggly(a, b)
        d = sum(hav(seg[i], seg[i + 1]) for i in range(len(seg) - 1)) + hav(seg[-1], b)
        legs.append(d)
        pts.extend(seg)
    pts.append(wps[-1])
    return pts, legs


# ---- synthetic weather: windy Wyoming Saturday evening, snow on the Cascades Monday, rain west side
def wx(lat, lon, t, member=0):
    dt = datetime.fromtimestamp(t, timezone.utc)
    hour_local = (dt.hour - 7) % 24
    temp = 14 - (lat - 39) * 1.4 + 6 * math.sin((hour_local - 9) / 24 * 2 * math.pi) + member * 0.8
    wind = 12 + member * 2
    wdir = 250 + member * 8
    gust = wind * 1.5
    rain = 0.0
    snow_cm = 0.0
    day = dt.day + dt.hour / 24.0
    if -107.0 < lon < -105.4 and 41.0 < lat < 42.0 and 7.9 < day < 8.2:  # Sat evening MT = Nov 8 00-05Z
        wind = 42 + member * 4
        gust = 75 + member * 6
        wdir = 265
    if lon < -121.0 and lat > 47.2:  # Snoqualmie
        temp = -0.5 + member * 0.6
        if 9.6 < day < 10.0:
            snow_cm = 0.8 + 0.3 * member
    if lon < -121.5 and 9.5 < day < 10.2:  # west side rain Monday
        rain = 1.2 + 0.4 * member
    if -118.6 < lon < -118.2 and 45.3 < lat < 45.7:  # Blue Mountains near freezing
        temp = 0.8 + member * 0.4
        if 9.5 < day < 9.8:
            rain = 0.4
    precip = rain + snow_cm / 0.7
    return temp, wind, wdir, gust, precip, rain, snow_cm


MODELS_OK = {"gfs_seamless", "ecmwf_ifs025", "gem_seamless", "icon_seamless", "ukmo_seamless"}


def open_meteo(q):
    models = q["models"][0].split(",")
    if any(m not in MODELS_OK for m in models):
        bad = [m for m in models if m not in MODELS_OK]
        return 400, {"error": True, "reason": "Cannot initialize WeatherModel from invalid String value %s" % bad[0]}
    lats = [float(x) for x in q["latitude"][0].split(",")]
    lons = [float(x) for x in q["longitude"][0].split(",")]
    if "start_date" in q:
        t0 = datetime.strptime(q["start_date"][0], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
        t1 = datetime.strptime(q["end_date"][0], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() + 86400
    else:
        t0 = time.time() // 3600 * 3600
        t1 = t0 + 86400
    times = list(range(int(t0), int(t1), 3600))
    hourly_vars = q["hourly"][0].split(",")
    items = []
    for lat, lon in zip(lats, lons):
        hourly = {"time": times}
        for mi, m in enumerate(models):
            cols = {v: [] for v in hourly_vars}
            for t in times:
                temp, w, d, g, p, r, s = wx(lat, lon, t, mi)
                if m == "icon_seamless" and t > time.time() + 7.5 * 86400:
                    temp = w = d = g = p = r = s = None  # beyond ICON's range
                vals = {"temperature_2m": temp, "precipitation": p, "rain": r, "showers": 0.0 if r is not None else None,
                        "snowfall": s, "wind_speed_10m": w, "wind_direction_10m": d, "wind_gusts_10m": g}
                for v in hourly_vars:
                    cols[v].append(None if vals.get(v) is None else round(vals[v], 2))
            for v in hourly_vars:
                hourly[v + "_" + m if len(models) > 1 else v] = cols[v]
        items.append({"latitude": lat, "longitude": lon, "utc_offset_seconds": 0, "hourly": hourly})
    return 200, items if len(items) > 1 else items[0]


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def nws_grid(path):
    xy = path.rsplit("/", 1)[1]
    x, y = (int(v) for v in xy.split(","))
    lat, lon = y / 40.0, -x / 40.0
    start = int(time.time() // 3600 * 3600)
    hours = 7 * 24
    def layer(fn, uom, step):
        vals = []
        for h in range(0, hours, step):
            t = start + h * 3600
            vals.append({"validTime": "%s/PT%dH" % (iso(t), step), "value": fn(t)})
        return {"uom": uom, "values": vals}
    def sum6(idx):
        return lambda t: round(sum(wx(lat, lon, t + k * 3600, 1)[idx] for k in range(6)), 2)
    props = {
        "updateTime": iso(time.time()),
        "temperature": layer(lambda t: round(wx(lat, lon, t, 1)[0], 1), "wmoUnit:degC", 1),
        "windSpeed": layer(lambda t: round(wx(lat, lon, t, 1)[1], 1), "wmoUnit:km_h-1", 1),
        "windGust": layer(lambda t: round(wx(lat, lon, t, 1)[3], 1), "wmoUnit:km_h-1", 1),
        "windDirection": layer(lambda t: wx(lat, lon, t, 1)[2], "wmoUnit:degree_(angle)", 1),
        "quantitativePrecipitation": layer(sum6(4), "wmoUnit:mm", 6),
        "snowfallAmount": layer(lambda t: round(sum(wx(lat, lon, t + k * 3600, 1)[6] * 10 for k in range(6)), 1), "wmoUnit:mm", 6),
        "probabilityOfPrecipitation": layer(lambda t: 70 if wx(lat, lon, t, 1)[4] > 0 else 5, "wmoUnit:percent", 6),
    }
    props["temperature"]["values"].append({"validTime": "%s/PT1H" % iso(start + hours * 3600), "value": None})
    return 200, {"type": "Feature", "properties": props}


def met_no(q):
    lat, lon = float(q["lat"][0]), float(q["lon"][0])
    start = int(time.time() // 3600 * 3600)
    series = []
    for h in range(0, 9 * 24):
        if h > 60 and h % 6:
            continue
        t = start + h * 3600
        temp, w, d, g, p, r, s = wx(lat, lon, t, 2)
        data = {"instant": {"details": {"air_temperature": round(temp, 1), "wind_speed": round(w / 3.6, 1),
                                        "wind_from_direction": d, "wind_speed_of_gust": round(g / 3.6, 1)}}}
        if h <= 60:
            data["next_1_hours"] = {"details": {"precipitation_amount": round(p, 1), "probability_of_precipitation": 60 if p else 3}}
        data["next_6_hours"] = {"details": {"precipitation_amount": round(sum(wx(lat, lon, t + k * 3600, 2)[4] for k in range(6)), 1),
                                            "probability_of_precipitation": 50}}
        series.append({"time": datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "data": data})
    return 200, {"type": "Feature", "properties": {"timeseries": series}}


def google_weather(q):
    lat, lon = float(q["location.latitude"][0]), float(q["location.longitude"][0])
    hours = int(q.get("hours", ["240"])[0])
    size = int(q.get("pageSize", ["24"])[0])
    off = int(q.get("pageToken", ["0"])[0])
    start = int(time.time() // 3600 * 3600)
    out = []
    for h in range(off, min(hours, off + size)):
        t = start + h * 3600
        temp, w, d, g, p, r, s = wx(lat, lon, t, 3)
        ptype = "SNOW" if s > 0 and r == 0 else ("RAIN" if p > 0 else "RAIN")
        out.append({
            "interval": {"startTime": datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "endTime": datetime.fromtimestamp(t + 3600, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
            "temperature": {"degrees": round(temp, 1), "unit": "CELSIUS"},
            "precipitation": {"probability": {"percent": 70 if p else 5, "type": ptype},
                              "qpf": {"quantity": round(p, 2), "unit": "MILLIMETERS"}},
            "wind": {"direction": {"degrees": d, "cardinal": "WEST"}, "speed": {"value": round(w), "unit": "KILOMETERS_PER_HOUR"},
                     "gust": {"value": round(g), "unit": "KILOMETERS_PER_HOUR"}},
        })
    body = {"forecastHours": out, "timeZone": {"id": "America/Denver"}}
    if off + size < hours:
        body["nextPageToken"] = str(off + size)
    return 200, body


def make_fake(server_mod, route_mode="google"):
    from server import FetchError, PLACES
    by_addr = {p.get("address"): (p["lat"], p["lon"]) for p in PLACES.values() if p.get("address")}

    def fake(url, method="GET", headers=None, body=None, timeout=45):
        u = urllib.parse.urlsplit(url)
        q = urllib.parse.parse_qs(u.query)
        CALLS.append((u.netloc, u.path))
        if u.netloc in RATE_LIMIT_ONCE:
            RATE_LIMIT_ONCE.discard(u.netloc)
            raise FetchError("HTTP 429 from %s Minutely API request limit exceeded. Please try again in one minute."
                             % u.netloc, status=429, retry_after=1)
        status, data, hdrs = 200, None, {}
        if u.netloc == "routes.googleapis.com":
            if route_mode != "google":
                raise FetchError("HTTP 403 from routes.googleapis.com PERMISSION_DENIED", status=403)
            def ll(w):
                if "address" in w:
                    return by_addr[w["address"]]
                x = w["location"]["latLng"]
                return (x["latitude"], x["longitude"])
            wps = [ll(body["origin"])] + [ll(w) for w in body.get("intermediates", [])] + [ll(body["destination"])]
            pts, legs = build_route(wps)
            data = {"routes": [{"distanceMeters": int(sum(legs)), "polyline": {"encodedPolyline": encode_polyline(pts, 5)},
                                "legs": [{"distanceMeters": int(d), "startLocation": {"latLng": {"latitude": a[0], "longitude": a[1]}},
                                          "endLocation": {"latLng": {"latitude": b[0], "longitude": b[1]}}}
                                         for d, a, b in zip(legs, wps[:-1], wps[1:])]}]}
        elif "project-osrm" in u.netloc:
            coords = u.path.split("/driving/")[1].split(";")
            wps = [(float(c.split(",")[1]), float(c.split(",")[0])) for c in coords]
            pts, legs = build_route(wps)
            data = {"code": "Ok", "routes": [{"geometry": encode_polyline(pts, 6), "distance": sum(legs),
                                              "legs": [{"distance": d} for d in legs]}],
                    "waypoints": [{"location": [w[1], w[0]]} for w in wps]}
        elif u.netloc == "api.open-meteo.com":
            status, data = open_meteo(q)
        elif u.netloc == "api.weather.gov" and u.path.startswith("/points/"):
            lat, lon = (float(v) for v in u.path.split("/points/")[1].split(","))
            data = {"properties": {"forecastGridData": "https://api.weather.gov/gridpoints/TST/%d,%d" % (round(-lon * 40), round(lat * 40))}}
        elif u.netloc == "api.weather.gov" and "/gridpoints/" in u.path:
            status, data = nws_grid(u.path)
        elif u.netloc == "api.met.no":
            status, data = met_no(q)
            hdrs = {"Expires": "Tue, 03 Nov 2026 19:30:00 GMT"}
        elif u.netloc == "weather.googleapis.com" and GOOGLE_WEATHER_429:
            status, data = 429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message":
                                           "Quota exceeded for quota metric 'Weather API - Forecast Hours Usage' "
                                           "and limit 'Weather API - Forecast Hours Usage per day'"}}
        elif u.netloc == "weather.googleapis.com":
            status, data = google_weather(q)
        else:
            raise FetchError("could not reach %s (unmocked)" % u.netloc)
        if status != 200:
            raise FetchError("HTTP %d from %s %s" % (status, u.netloc, json.dumps(data)), status=status)
        # round-trip through JSON so the server sees exactly what a real response would give it
        return json.loads(json.dumps(data)), hdrs

    return fake
