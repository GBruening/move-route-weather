# Move Route Weather

Local web app for one specific trip: a household move from **Golden, CO to Bellingham, WA,
Sat Nov 7 – Mon Nov 9, 2026**, in a **Penske 26-ft diesel box truck**. It shows the
candidate routes on a Google map with the forecast **for the time the truck is expected at
each point**, aggregated from several sources. The owner runs it on **Windows**
(`start-windows.bat` → `py server.py` → http://localhost:8000).

## Ground rules

- **Python standard library only** (3.8+). No pip installs, no build step, no frameworks.
  Front end is plain HTML/CSS/JS in `static/`, no bundler.
- **Be polite to the free APIs.** Everything goes through the disk cache, per-host throttles
  and error back-off in `server.py`. Don't add code paths that fetch on every page load or
  poll. Forecasts are fetched lazily, only for routes being viewed.
- **The Google key lives in `config.local.json`** (git-ignored). Never put it in `config.json`
  or commit it.
- All stop times in `trip.json` are ISO 8601 **with UTC offsets** (MST `-07:00`, PST `-08:00`;
  DST ended Nov 1, 2026).
- Changes to `static/` need only a browser refresh; changes to `server.py`, `trip.json` or
  config need a server restart.
- The app must keep running as a plain script with no build. The packaged apps
  (`packaging/`) are an optional extra; PyInstaller is only installed on the machine that
  builds them, never needed to run the code.

## Files

| File | What it is |
|---|---|
| `trip.local.json` | Edits made in the app (per-variant schedules, cruise speed). Git-ignored; delete it to go back to `trip.json`. |
| `trip.json` | The plan: `places` (stops with lat/lon and, where known, street address), `days` (day templates with timed stops), `routes` (which days make each route, with or without the Portland detour), `landmarks` (passes and towns used as extra sample points). |
| `server.py` | Routing, sampling, arrival-time estimation, weather fetching, caching, aggregation, HTTP API, static file server. |
| `static/app.js` | Map (Google Maps JS API), weather glyph overlay, sidebar (route cards, toggles, watch list, itinerary, source status), profile chart. |
| `static/style.css`, `static/index.html` | Layout and styling. |
| `config.json` / `config.local.json` | Settings; local file overrides shared file (sources on/off, TTLs, Open-Meteo model list, port). |
| `tests/run_simulated.py`, `tests/fakeapis.py` | Offline end-to-end test against simulated API responses. |
| `packaging/MoveRouteWeather.spec`, `packaging/build.py` | Builds the double-click app (Windows `.exe`, macOS binary) with PyInstaller. Smoke-tests it (`--online` also checks HTTPS) and zips it with `config.local.example.json` and "How to run.txt" into `release/`. |
| `.github/workflows/build-apps.yml` | On a `v*` tag, or run by hand: tests, then builds on Windows and macOS (Apple Silicon) and attaches the zips to a GitHub Release. |

## Routes (as the owner wants them)

1. **Route 1, I-80.** Portland detour **off** = original plan (nights Rock Springs and Ontario,
   I-84 → I-82 → I-90 over Snoqualmie). Detour **on** = the **current plan** (nights Rock Springs
   and Baker City, then Columbia Gorge → I-205 → I-5). The detour is on by default.
2. **Route 2, I-70** wind alternate (nights Green River UT and Ontario). Detour allowed (Gorge
   finish from Ontario). Stop times are estimates.
3. **Route 3, Montana I-90** (I-25 → Buffalo WY → I-90 via Billings, Bozeman, Butte, Missoula,
   Spokane → Snoqualmie). **No Portland option.** Never planned in detail: overnight towns
   (Buffalo, Missoula) and all times are estimates; town-level stops have no businesses chosen.

Route-card badges come from `badge` / `badge_portland`; `portland` is `toggle` (has
`portland_days`), `always`, or `never`.

## How it works (server.py)

- **Files and folders**: `RES_DIR` holds the bundled files (`static/`, `trip.json`,
  `config.json`). `DATA_DIR` holds your files (`config.local.json`, `trip.local.json`,
  `cache/`). Run as a script, both are the repo folder. In a packaged app (`FROZEN`),
  `RES_DIR` is PyInstaller's temporary unpack folder and `DATA_DIR` is the folder next to the
  app, or the user's app-data folder if that one isn't writable. `app_file()` lets a
  `trip.json` or `config.json` next to the app override the bundled copy.
- **HTTPS**: `SSL_CONTEXT` falls back to the OS certificate bundle (`/etc/ssl/cert.pem` etc.)
  when Python's default store is empty, which is the case in a packaged Mac app.

- **Routing per day template**: Google Routes API (address waypoints, then lat/lon) → OSRM
  demo server → straight lines (flagged `approx`). Stops are routing waypoints; leg distances
  give each stop's mileage; snapped leg endpoints replace the approximate stop coordinates.
  Stops with `"via": false` (e.g. Snoqualmie Pass) are projected onto the line instead. Route
  results are cached permanently, keyed by waypoints.
- **Samples**: every stop, every `landmarks` entry within 6 km of the route (passes kept ≥3 mi
  from others, towns ≥7 mi), plus fill points so gaps stay ≲25 mi.
- **Schedule** (`template_model`, `variant_model`, `timed_day`): each route variant is a model,
  a list of days. Each day has a date, a departure time (local wall clock), and stops with how
  many minutes are spent at each. Stops are a `place` id, a `custom` {name, lat, lon}, or a
  routing-only `via`. Times are always computed: departure + leg distance ÷ cruise speed +
  time at each stop. The speed is `trip_speed()`, set from the page; default `truck_mph` 60.
  An unedited variant's model comes from `trip.json`: its stop lengths are the planned
  depart − arrive, and the planned arrival is kept as `plan` so the itinerary can show drift
  over 15 min. Edited models are saved per variant (`"r1|1"`) in **`trip.local.json`**
  (git-ignored), along with `speed`. `timed_day` registers each computed day in `DAYS` under a
  content-hash id, so `build_day` and its caches work unchanged.
- **Arrival time** at a sample = linear interpolation by distance between the previous stop's
  departure and the next stop's arrival. Passes and other untimed markers get an
  interpolated time.
- **Forecast points** are keyed (`lk`) on a 0.05° grid (about 5 km), so moved stops and the
  other routes reuse cached forecasts.
- **Time zone labels**: `tz_for()` — Pacific for Oregon west of Ontario, Washington, and the
  Idaho Panhandle west of Lookout Pass; Mountain otherwise.
- **Sources** (each normalized to "members": `inst` [[t, temp_c, wind_kmh, dir, gust_kmh]],
  `precip` [[t0, t1, total_mm, rain_mm|None, snow_cm|None]], `pop` [[t0, t1, %]]):
  - Open-Meteo, one batched call for up to 40 locations, several models
    (`config.json` → `open_meteo_models`). A 400 triggers per-model probing; invalid model
    names go into `BAD_MODELS` and are skipped.
  - NWS: `/points` lookup (cached forever) → `forecastGridData` layers, expanded hourly; QPF and
    snowfall are 6-hour blocks.
  - MET Norway `locationforecast/2.0/complete`; rain/snow split inferred from temperature;
    respects `Expires`.
  - Google Weather `forecast/hours:lookup` (paged, 24 h per page), **stops and passes only**
    to limit calls; needs the key. **Off by default** (`sources.google_weather: false`): it's
    billable past the free tier and the three free sources already cover the trip. The code
    stays so it can be switched back on.
- **Evaluation at arrival time**: temp interpolated; wind from nearest hour; gust = max within
  ±1 h; rain/snow = totals over the **3 hours centered on arrival**.
- **Aggregation**: median temp/wind/gust; vector-mean wind direction; **mean** rain/snow (so a
  minority of snowy models still shows); `agree` = share of sources with precip; crosswind =
  speed × |sin(wind dir − route heading)|. Per-source values are returned for the info window.
- **Caching**: `cache/` on disk + memory. TTL per source (`ttl_minutes`). Stale data is still
  shown while a refresh is queued. Errors back off 15 min (NWS 404s for a day). One background
  worker per source with throttles: Open-Meteo 1 s, NWS 0.4 s, MET 0.3 s, Google 0.15 s,
  Google Routes 0.5 s, OSRM 1.2 s.
- **Rate limits**: on an HTTP 429 the source's worker waits (from `Retry-After`, or from the
  message: one minute ≈ 65 s, hourly/daily until the reset) and retries the same points.
  Waits over `RATE_WAIT_MAX` (5 min) back the points off instead, so they show the last
  forecast. Open-Meteo is also paced by `CallBudget` against `open_meteo_budget`
  (450/min, 4,000/h, 8,000/day of its weighted calls). A request is counted as
  locations × max(1, variables × models / 10), and recent calls are saved to
  `cache/open_meteo_calls.json`.
- **Google usage meter** (`UsageMeter`, `google_call()`): every billable Google call (each
  Weather page, each Routes call, each page load that gets the map key from `/api/config`) is
  counted per calendar month in Pacific time, which is Google's billing month, in
  `cache/google_usage.json`. At `google_monthly_caps` (kept under Google's free 10,000 per SKU;
  the $200 credit no longer exists) the app stops calling Google until the 1st. A quota 429
  that mentions "per day" pauses that SKU until midnight Pacific; other 429s pause it for 60 s.
  429s aren't counted. Usage and an estimated charge show in the sources panel.
- **API**: `GET /api/config`; `GET /api/plan?route=r1&portland=1[&geom=0][&weather=0]` returns
  days, stops, samples (with `eta`, `x` chart position, `tz`), per-day paths, aggregated
  weather and per-source status including `queued`. It also returns `speed` and `edited`.
  The page polls every 2.5 s while anything is queued. `GET /api/edit?route=&portland=`
  returns the editable model, each stop annotated with `info` (mile, times). `POST /api/edit`
  {route, portland, model}, `POST /api/edit/reset` and `POST /api/speed` {speed} require
  `Content-Type: application/json`, so other web pages can't post to them cross-site.

## Front end (static/app.js)

- Map: Google Maps JS API loaded with the key (no key → Google's watermarked dev map).
  Weather is drawn by a custom `OverlayView` (`makeOverlayClass`) as SVG glyphs: wind arrow
  (starts at the point and points downwind; length and color = speed, dashed = gusts), blue rain bar, purple snow bar (faint bar = wettest
  source, opacity = agreement), temperature chip, stop markers (S/H/F/★, ▲ for passes).
  Glyphs closer than 30 px are hidden, focused route first.
- Sidebar: route cards (eye = show/hide, click = focus), Portland toggle, layer toggles
  (wind/rain/snow/temp), watch list (worst gust, snow, rain near freezing, wettest, coldest),
  itinerary with weather at each stop, source status.
- Profile chart under the map: day bands, wind arrows, temperature with source spread, rain
  and snow bars, wind/gust with a 40 mph line; hover links to the map.
- Overview strip above the chart (`renderNav`, `initNav`, `state.span`): click a day (click
  it again, press Esc, or press All to go back), or drag across the strip to choose any
  stretch. The window can be moved and its edges dragged. The chart then shows only that
  stretch, with its scales rescaled and fuel stops, breaks, passes and towns named; the map
  fits to it. The selection is cleared when the focused route changes.
- Route cards show miles and driving time (miles ÷ cruise speed). The Cruise speed slider
  (45–70) updates those live and, when released, saves the speed and re-times every route.
- Itinerary **Edit schedule** (`state.edit`, `moveNight`, `addStop`, `stopEditor`): set each
  day's departure, and use the Night N picker to move a night to any planned stop or town
  between the neighbouring days. The old hotel is dropped, and stops moved to the other day
  lose their `plan` time. "Edit stops" per day changes a stop's kind and minutes, removes
  stops, or adds a town along the route or a spot clicked on the map. Every change is saved
  at once, and "Reset to plan" removes the edits.
- Panes: drag splitters resize the sidebar and the chart (double-click resets). The sidebar,
  the chart and each sidebar section can also be collapsed. The state is saved in prefs `ui`.
- Preferences are kept in `localStorage` key `move-weather-prefs-v2` (bump the key when
  defaults change).

## Testing

- `python tests/run_simulated.py` (and `--no-key`) runs the whole server offline against
  simulated responses and checks stop times, ordering, every sample having weather, and that
  repeat requests make **zero** outside calls. Run it after server changes.
- The simulated weather is invented; only the formats are meant to match.

## Not yet verified against the live services

The app was built without network access to these services, so check these on the first
real run and fix anything that differs:

- Open-Meteo multi-model responses suffix variables as `temperature_2m_<model>`, and
  multi-location requests return a JSON list.
- `ecmwf_ifs025` is a valid Open-Meteo model name (bad ones are skipped automatically; see
  "skipped models" in the sources panel). `ncep_nbm_conus` was checked live on Nov 3, 2026 and
  is valid. On the same check, `ukmo_seamless` had gusts and snow for the whole trip;
  `jma_seamless` and `ecmwf_aifs025_single` had no gusts; `kma_seamless` returned no data.
- Google Routes accepts `address` waypoints and the field mask
  `routes.legs.startLocation,routes.legs.endLocation`; the demo key is allowed for Routes and
  Weather calls made from a server.
- Google Weather: the code ignores `snowQpf` (its units weren't documented) and splits
  precipitation by `precipitation.probability.type` (or temperature).
- NWS layer units (`wmoUnit:degC`, `wmoUnit:km_h-1`, `wmoUnit:mm`) are handled, with
  conversions for other units.
- Stop coordinates in `trip.json` are approximate. Google routes by address where one is
  given; the Green River UT stops and the Montana towns have no address.

## Planning context (from the earlier conversation)

- Day 1 departs Golden ~1:30 PM after loading. Truck ~10 mpg, ~50 gal tank.
- Concerns the app should help with: **Wyoming wind closures** (WYDOT closes I-80 and I-25 to
  light, high-profile vehicles, which includes rental box trucks; Laramie–Elk Mountain–Arlington–Rawlins),
  **Washington chain law** (vehicles over 10,000 lb must carry chains on Snoqualmie
  Nov 1 – Apr 1), Oregon Blue Mountains / Columbia Gorge snow and ice, and rain on Day 3.
- Diesel strategy: buy in Wyoming and Idaho, top off in Oregon, avoid Washington (priciest)
  except the Penske return fill.
- Re-check planned for Thu Nov 5 / Fri Nov 6. Road links: wyoroad.info, cotrip.org,
  udottraffic.utah.gov, 511.idaho.gov, tripcheck.com, WSDOT mountain passes.
- The full plan lives in a separate claude.ai doc ("Golden → Bellingham Move Plan (Route 1)");
  this repo only mirrors the stops and times it needs.
