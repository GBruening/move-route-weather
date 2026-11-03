# Move Route Weather — Golden → Bellingham, Nov 7–9 2026

A small web app that shows the three planned routes on a Google map, with the
forecast for **when you'll actually be at each point** (Golden Saturday 1:30 PM,
Rock Springs Saturday evening, Snoqualmie Monday ~1:15 PM, and so on).

## Run it

You need Python 3.8 or newer (free from python.org or the Microsoft Store). No packages
to install.

- **Windows:** double-click `start-windows.bat`, or in a terminal run `py server.py`.
- **Mac/Linux:** `python3 server.py`

It opens http://localhost:8000 in your browser. Leave the window open while you use it;
press Ctrl+C (or close the window) to stop.

## Add your Google Maps demo key

1. Get it at https://developers.google.com/maps/documentation/javascript/demo-key
   (sign in with a personal @gmail.com account).
2. Copy `config.local.example.json` to `config.local.json`, paste the key in, and restart
   the server. `config.local.json` is git-ignored, so the key never lands in the repository.

The key is used for three things, each with a fallback:

| Use                   | Without the key / if Google refuses               |
|-----------------------|---------------------------------------------------|
| The map               | Google's darkened "for development purposes only" map |
| Route lines (Routes API) | Free OSRM demo router; straight lines if that fails too |
| Google Weather (4th source) | Skipped; the other three sources still work |

Optional: put an email address in `"contact"` in `config.local.json`. weather.gov and MET
Norway ask apps to identify themselves.

## What you're looking at

- **Routes**: click a card to focus it (chart, watch list, itinerary follow it);
  the eye button shows or hides it on the map.
  - Route 1: I-80. Without the detour it's the original plan (nights in Rock Springs and
    Ontario, over Snoqualmie). With the detour it's the current plan (nights in Rock
    Springs and Baker City, then the Columbia Gorge and I-5).
  - Route 2: I-70 wind alternate (nights in Green River UT and Ontario; times estimated).
  - Route 3: Montana I-90 via Buffalo WY and Missoula (never planned in detail, so its
    overnight towns and times are estimates; no Portland option).
- **Portland detour** switches Routes 1 and 2 to the Gorge/I-5 finish. The finish you
  didn't pick is drawn dashed. It's on by default because that's the current plan.
- **Layers**: wind arrows (length and color = speed; dashes = gusts), rain as blue
  bars, snow as purple bars, temperature chips. Bars are 3-hour totals around your
  arrival time. A faint bar behind shows the wettest source; a more solid bar means
  more sources agree.
- Click any symbol for the per-source breakdown. The chart under the map shows the
  same thing along the whole route; hover it to find the spot on the map.

## Where the weather comes from

All fetching happens in `server.py` and is combined per point:

- **Open-Meteo**: NOAA GFS/HRRR, ECMWF, Canada GEM, DWD ICON (and NOAA NBM if available)
- **National Weather Service** gridded forecast (weather.gov)
- **MET Norway** (rain/snow split estimated from temperature)
- **Google Weather** (stops and passes only, needs the key)

Temperature and wind use the middle value of the sources; rain and snow use the average.

## Caching

Nothing is fetched until a route is viewed. Then:

- Route lines and NWS grid lookups are saved permanently in `cache/`.
- Forecasts are reused for 2–6 hours (see `ttl_minutes` in `config.json`).
- Each service gets one request at a time with a pause between them; failures back
  off for 15 minutes.

Delete the `cache/` folder to start fresh.

## Changing the plan

Stops, times (with UTC offsets: MST -07:00, PST -08:00) and the three routes live in
`trip.json`. Edit and restart. Forecasts only reach about 7–10 days ahead, so points
past that show "no forecast yet".
