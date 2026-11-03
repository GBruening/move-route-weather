# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

One person, the trip owner, planning a household move at a desk on a Windows desktop/laptop browser (http://localhost:8000). Used mostly before the trip, with planned re-checks on Thu Nov 5 and Fri Nov 6, 2026. Not a multi-user or on-the-road tool.

## Product Purpose

A local web app for one specific trip: Golden, CO to Bellingham, WA, Sat Nov 7 – Mon Nov 9, 2026, in a Penske 26-ft diesel box truck. It shows candidate routes on a Google map with the forecast for the time the truck is expected at each point, aggregated from several weather sources. Success: the owner first chooses a route (I-80, I-70 wind alternate, Montana I-90), then makes a daily go / hold / reroute call with confidence.

## Positioning

Forecasts are evaluated at the truck's estimated arrival time at each point along the route, not at a place on a generic timeline. Several sources are aggregated and their disagreement stays visible. The app is built around this truck's risks: high-profile-vehicle wind closures and chain law.

## Operating Context

- Routes: R1 I-80 (Portland detour on by default = current plan), R2 I-70 wind alternate, R3 Montana I-90 (never planned in detail; times and overnight towns are estimates, no Portland option).
- Day 1 departs Golden ~1:30 PM after loading. Truck ~10 mpg, ~50 gal tank.
- Concerns: WYDOT wind closures of I-80 and I-25 to light, high-profile vehicles (Laramie–Elk Mountain–Arlington–Rawlins), Washington chain law (over 10,000 lb, Snoqualmie, Nov 1 – Apr 1), Oregon Blue Mountains / Columbia Gorge snow and ice, rain on Day 3.
- Diesel strategy: buy in Wyoming and Idaho, top off in Oregon, avoid Washington except the Penske return fill.
- Road-condition links: wyoroad.info, cotrip.org, udottraffic.utah.gov, 511.idaho.gov, tripcheck.com, WSDOT mountain passes.
- The full plan lives in a separate claude.ai doc ("Golden → Bellingham Move Plan (Route 1)"); this repo mirrors only the stops and times it needs.

## Capabilities and Constraints

- Python standard library only (3.8+); plain HTML/CSS/JS in `static/`, no build step or frameworks.
- Polite to free APIs: disk cache, per-host throttles, error back-off; forecasts fetched lazily for routes being viewed, no polling on page load.
- Google key lives only in git-ignored `config.local.json`.
- Stop times are ISO 8601 with UTC offsets (MST -07:00, PST -08:00).
- Weather sources: Open-Meteo (multi-model), NWS, MET Norway, Google Weather (stops/passes only). Aggregation uses median temp/wind/gust and mean rain/snow so a minority of snowy models still shows.
- Several live-service behaviors are unverified (see CLAUDE.md).

## Evidence on Hand

Real trip plan in `trip.json`; offline simulated API tests in `tests/`. Simulated weather is invented. No user testimonials or external proof exist; none should be fabricated.

## Product Principles

1. Time-of-arrival truth: every weather value is tied to when the truck is actually there.
2. Disagreement is information: show source spread and agreement, never hide it behind a single number.
3. Decision-first: surface what changes the call (gusts vs the 40 mph line, snow, rain near freezing, closures) ahead of raw data.
4. Honest uncertainty: estimated times, approximate routing, and stale or missing sources are labeled, never implied precise.
5. Respect the free APIs: lazy, cached, throttled.

## Accessibility & Inclusion

No product-specific requirements beyond standard accessibility practice.
