"""Offline end-to-end check: runs server.py against simulated APIs (tests/fakeapis.py).

    python tests/run_simulated.py            # Google key path (Routes + Google Weather)
    python tests/run_simulated.py --no-key   # OSRM routing, Google Weather off
    python tests/run_simulated.py --serve    # keep serving on http://127.0.0.1:8765 afterwards

Uses a throwaway cache folder, so the real cache/ is untouched. Exits non-zero on failure.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if "--quiet" not in sys.argv:
    sys.argv.append("--quiet")
if hasattr(sys.stdout, "reconfigure"):  # Windows consoles can't print the → in day titles
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import server  # noqa: E402
import fakeapis  # noqa: E402

route_mode = "osrm" if "--no-key" in sys.argv else "google"
cache_dir = tempfile.mkdtemp(prefix="move-weather-test-")
server.CACHE = server.DiskCache(cache_dir)
server.CACHE_DIR = cache_dir
server.METER = server.UsageMeter(os.path.join(cache_dir, "google_usage.json"))
server.OM_BUDGET = server.CallBudget("Open-Meteo", [(60, 1e9)])  # no pacing here; CallBudget is checked below
server.TRIP_LOCAL_PATH = os.path.join(cache_dir, "trip.local.json")  # never touch the real edits
server.TRIP_LOCAL.clear()
server.CONFIG["google_maps_api_key"] = "TEST-KEY" if route_mode == "google" else ""
server.CONFIG["sources"]["google_weather"] = True  # off in config.json, but the code path is still tested
for t in server.THROTTLE.values():
    t.min_interval = 0.0
server.http_request = fakeapis.make_fake(server, route_mode)

PORT = 8765
srv = server.make_server(host="127.0.0.1", port=PORT)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % PORT
failures = []


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=120) as r:
        return json.loads(r.read())


def post(path, body, ctype="application/json"):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def wait_weather(path):
    plan = get(path)
    for _ in range(400):
        if not plan["status"]["queued"]:
            break
        time.sleep(0.25)
        plan = get(path + "&geom=0")
    return plan


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


def local(ts, tz):
    off = -8 if tz == "America/Los_Angeles" else -7
    return datetime.fromtimestamp(ts + off * 3600, timezone.utc).strftime("%a %H:%M") + (" PT" if off == -8 else " MT")


cfg = get("/api/config")
print("routes:", [(r["id"], r["short"], r["portland"]) for r in cfg["routes"]])
variants = []
for r in cfg["routes"]:
    variants.append((r["id"], 0))
    if r["portland"] == "toggle":
        variants.append((r["id"], 1))

plans = {}
for rid, pl in variants:
    plan = get("/api/plan?route=%s&portland=%d" % (rid, pl))
    for _ in range(400):
        if not plan["status"]["queued"]:
            break
        time.sleep(0.25)
        plan = get("/api/plan?route=%s&portland=%d&geom=0" % (rid, pl))
    plans[(rid, pl)] = plan
    with_wx = sum(1 for w in plan["weather"] if w)
    print("\n%s portland=%d: %d mi, %d samples, %d with weather" % (rid, pl, plan["total_miles"], len(plan["samples"]), with_wx))
    for d in plan["days"]:
        print("   %s %-42s %4d mi  routed by %s%s" % (d["label"], d["title"], d["miles"], d["provider"], "  (APPROX)" if d["approx"] else ""))
    check(with_wx == len(plan["samples"]), "every sample has weather")
    xs = [s["x"] for s in plan["samples"]]
    etas = [s["eta"] for s in plan["samples"]]
    check(xs == sorted(xs) and etas == sorted(etas), "samples ordered by distance and time")
    for st in plan["stops"]:
        t = st["depart"] if st["kind"] == "start" else st["arrive"]
        smp = [s for s in plan["samples"] if s["day"] == st["day"] and s["stop"] is not None and s["place"] == st["place"]]
        if smp:
            check(abs(smp[0]["eta"] - t) < 1, "%s at %s" % (st["short"], local(t, st["tz"])))
    for src, v in plan["status"]["sources"].items():
        check(v["error"] == 0, "%s: %d points fresh, %d errors" % (src, v["fresh"], v["error"]))

print("\nskipped Open-Meteo models:", sorted(server.BAD_MODELS))
check("ncep_nbm_conus" in server.BAD_MODELS, "rejected model detected and skipped")

before = len(fakeapis.CALLS)
for rid, pl in variants:
    get("/api/plan?route=%s&portland=%d" % (rid, pl))
check(len(fakeapis.CALLS) == before, "repeat requests served from cache (no outside calls)")

# ---- schedule: speed and edits ----------------------------------------------------
print("\nSchedule editing")
base = plans[("r1", 1)]
d0 = [s for s in base["stops"] if s["day"] == 0]
leg_h = (d0[1]["arrive"] - d0[0]["depart"]) / 3600.0
leg_mi = d0[1]["mile"] - d0[0]["mile"]
check(abs(leg_h - leg_mi / 60.0) < 0.02, "a leg takes miles / 60 mph (%.0f mi in %.2f h)" % (leg_mi, leg_h))
check(abs((d0[1]["depart"] - d0[1]["arrive"]) / 60 - 15) < 0.5, "planned 15-minute break kept")

code, r = post("/api/speed", {"speed": 50})
slow = get("/api/plan?route=r1&portland=1&geom=0")
rs_fast = d0[-1]["arrive"]
rs_slow = [s for s in slow["stops"] if s["day"] == 0][-1]["arrive"]
check(code == 200 and rs_slow - rs_fast > 1800, "50 mph gets to Rock Springs later (%+.0f min)" % ((rs_slow - rs_fast) / 60))
post("/api/speed", {"speed": 60})

code, _ = post("/api/speed", {"speed": 50}, ctype="text/plain")
check(code == 415, "a non-JSON POST (another web page) is refused")

ed = get("/api/edit?route=r1&portland=1")
check(not ed["edited"] and len(ed["model"]["days"]) == 3, "unedited route comes back as the plan")
m = ed["model"]
day1, day2 = m["days"][0]["stops"], m["days"][1]["stops"]
sinclair = [s for s in day1 if s.get("place") == "sinclair_stinker"][0]
# drive less on day 1: sleep at the Sinclair fuel stop instead of Rock Springs
m["days"][0]["stops"] = [s for s in day1 if s.get("place") != "rs_spark"]
m["days"][0]["stops"][-1] = {"place": "sinclair_stinker", "kind": "overnight"}
m["days"][1]["stops"] = [{"place": "sinclair_stinker", "kind": "start"}] + day2[1:]
code, r = post("/api/edit", {"route": "r1", "portland": 1, "model": m})
check(code == 200, "edited schedule saved")
check(os.path.exists(server.TRIP_LOCAL_PATH), "edits written to trip.local.json")
ep = wait_weather("/api/plan?route=r1&portland=1")
e0 = [s for s in ep["stops"] if s["day"] == 0]
e1 = [s for s in ep["stops"] if s["day"] == 1]
check(ep["edited"] and e0[-1]["place"] == "sinclair_stinker" and e1[0]["place"] == "sinclair_stinker",
      "night 1 is now at Sinclair (%s)" % ep["days"][0]["title"])
check(abs(ep["total_miles"] - base["total_miles"]) < 40, "total distance about the same (%d vs %d mi)" % (ep["total_miles"], base["total_miles"]))
check(all(w for w in ep["weather"]), "every sample of the edited route has weather")
check(e0[-1]["arrive"] < rs_fast, "day 1 ends earlier")

bad = json.loads(json.dumps(m))
bad["days"][0]["stops"] = bad["days"][0]["stops"][:1]
code, r = post("/api/edit", {"route": "r1", "portland": 1, "model": bad})
check(code == 400, "a day without an end is rejected (%s)" % r.get("error"))

code, _ = post("/api/edit/reset", {"route": "r1", "portland": 1})
back = get("/api/plan?route=r1&portland=1&geom=0")
check(code == 200 and not back["edited"] and back["days"][0]["title"] == base["days"][0]["title"], "reset puts the plan back")

# ---- rate limits ----------------------------------------------------------------
print("\nRate limits")
fakeapis.RATE_LIMIT_ONCE.update({"api.met.no", "api.open-meteo.com"})
probe = {"lat": 46.2, "lon": -119.1, "lk": "rl-probe"}
server.WORKERS["met_no"].want("rl-probe", probe)
server.WORKERS["open_meteo"].want(server.om_key("rl-probe"), dict(probe, models=server.active_models()))
t0 = time.time()
while time.time() - t0 < 20 and not (server.CACHE.get("met_no", "rl-probe") and server.CACHE.get("open_meteo", server.om_key("rl-probe"))):
    time.sleep(0.2)
check(not fakeapis.RATE_LIMIT_ONCE, "both services answered 429 once")
check(bool(server.CACHE.get("met_no", "rl-probe")), "MET Norway waited out its 429 and fetched the point")
check(bool(server.CACHE.get("open_meteo", server.om_key("rl-probe"))), "Open-Meteo waited out its 429 and fetched the point")
check(server.get_backoff("met_no", "rl-probe") is None, "a short 429 doesn't mark the point failed")
check(server.rate_limit_wait(server.FetchError("Daily API request limit exceeded", status=429)) > 3600,
      "a daily-limit 429 waits for the next day")

b = server.CallBudget("test", [(1.5, 10)])
t0 = time.time(); b.take(6); b.take(6)
check(1.0 < time.time() - t0 < 4, "budget waits for the window when a call would go over")
b = server.CallBudget("test", [(3600, 10)])
b.take(6)
try:
    b.take(6)
    check(False, "budget raises when the wait is longer than RATE_WAIT_MAX")
except server.FetchError as e:
    check(e.status == 429 and e.retry_after > 3000, "budget raises when the wait is longer than RATE_WAIT_MAX")
check(abs(server.om_weight(40, ["a"] * 6) - 192) < 1, "Open-Meteo weight: 40 places x 6 models = 192 calls")

# ---- Google usage meter -------------------------------------------------------
print("\nGoogle usage meter")
P = server.pacific
check(P(1793736000).strftime("%Y-%m-%d %H:%M") == "2026-11-03 12:00", "Pacific clock in November (PST)")
check(P(1783886400).strftime("%H:%M") == "13:00", "Pacific clock in July (PDT)")
check(server.next_pacific_midnight(1793736000) == 1793779200, "next midnight Pacific")
check(server.next_pacific_month(1793736000) == 1796112000, "next billing month starts Dec 1, 00:00 Pacific")

M = server.METER
n_weather = sum(1 for c in fakeapis.CALLS if c[0] == "weather.googleapis.com")
n_routes = sum(1 for c in fakeapis.CALLS if c[0] == "routes.googleapis.com")
if route_mode == "google":
    check(M.counts.get("weather", 0) == n_weather > 0, "every Google Weather page counted (%d)" % n_weather)
    check(M.counts.get("routes", 0) == n_routes > 0, "every Google Routes call counted (%d)" % n_routes)
    check(M.counts.get("maps_js", 0) == 1, "one map load counted per page load")
    snap = get("/api/plan?route=r1&portland=1&geom=0")["status"]["google"]
    check(snap["est_cost"] == 0 and len(snap["skus"]) == 3, "usage snapshot in /api/plan, est. cost $0")

    probe = {"lat": 45.0, "lon": -120.0, "lk": "probe1"}
    server.CONFIG["google_monthly_caps"]["weather"] = M.counts["weather"]  # at the cap
    before = len(fakeapis.CALLS)
    server.fetch_google_weather([("probe1", probe)])
    b = server.get_backoff("google_weather", "probe1")
    check(len(fakeapis.CALLS) == before and b and "monthly limit" in b[1], "no Google call once the monthly cap is reached")
    check(b and b[0] - time.time() > 3600, "capped source waits for the next month, not 15 min")

    server.CONFIG["google_monthly_caps"]["weather"] = 9000
    fakeapis.GOOGLE_WEATHER_429 = True
    used = M.counts["weather"]
    server.fetch_google_weather([("probe2", dict(probe, lk="probe2")), ("probe3", dict(probe, lk="probe3"))])
    fakeapis.GOOGLE_WEATHER_429 = False
    n429 = sum(1 for c in fakeapis.CALLS[before:] if c[0] == "weather.googleapis.com")
    check(n429 == 1, "daily-quota 429 stops further Google calls (%d made)" % n429)
    check(M.counts["weather"] == used, "a 429 is not counted as billable")
    b = server.get_backoff("google_weather", "probe3")
    check(b and abs(b[0] - server.next_pacific_midnight(time.time())) < 5, "daily-quota 429 waits until midnight Pacific")
    M.pauses.clear()

    server.CONFIG["google_monthly_caps"]["maps_js"] = 1
    c2 = get("/api/config")
    check(c2["google_maps_api_key"] == "" and c2["maps_paused"], "page gets no map key once map loads are capped")
else:
    check(not M.counts, "no Google calls counted without a key")

print("\n%d failure(s)" % len(failures))
if "--serve" in sys.argv:
    print("serving %s (Ctrl+C to stop)" % BASE)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
shutil.rmtree(cache_dir, ignore_errors=True)
sys.exit(1 if failures else 0)
