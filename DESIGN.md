---
name: Move Route Weather
description: A quiet planning console that puts arrival-time weather for one truck move on a map and a chart.
colors:
  accent-teal: "#0f766e"
  rain-blue: "#2563eb"
  snow-violet: "#7c3aed"
  warn-ember: "#c2410c"
  bad-red: "#b91c1c"
  ok-green: "#15803d"
  ink: "#18212b"
  ink-2: "#4a5662"
  ink-3: "#65707c"
  line: "#e2e5e1"
  line-2: "#eef0ec"
  bg-sage-paper: "#f4f5f2"
  panel: "#ffffff"
  map-ground: "#e8ebe6"
  night-ink: "#111827"
  route-1-slate: "#475569"
  route-2-ochre: "#a16207"
  route-3-teal: "#0f766e"
  wind-calm: "#15803d"
  wind-breezy: "#ca8a04"
  wind-windy: "#ea580c"
  wind-strong: "#dc2626"
  wind-closure: "#7f1d1d"
typography:
  headline:
    fontFamily: "ui-sans-serif, -apple-system, Segoe UI, Roboto, Helvetica Neue, Arial, sans-serif"
    fontSize: "19px"
    fontWeight: 700
    letterSpacing: "-0.01em"
  title:
    fontFamily: "ui-sans-serif, -apple-system, Segoe UI, Roboto, Helvetica Neue, Arial, sans-serif"
    fontSize: "14px"
    fontWeight: 650
    lineHeight: 1.4
  body:
    fontFamily: "ui-sans-serif, -apple-system, Segoe UI, Roboto, Helvetica Neue, Arial, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.45
  small:
    fontFamily: "ui-sans-serif, -apple-system, Segoe UI, Roboto, Helvetica Neue, Arial, sans-serif"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.35
  label:
    fontFamily: "ui-sans-serif, -apple-system, Segoe UI, Roboto, Helvetica Neue, Arial, sans-serif"
    fontSize: "11px"
    fontWeight: 650
    letterSpacing: "0.08em"
  micro:
    fontFamily: "ui-sans-serif, -apple-system, Segoe UI, Roboto, Helvetica Neue, Arial, sans-serif"
    fontSize: "10.5px"
    fontWeight: 650
rounded:
  xs: "4px"
  sm: "6px"
  md: "8px"
  lg: "10px"
  pill: "99px"
spacing:
  xs: "6px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  gutter: "20px"
components:
  route-card:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: "10px 10px 10px 0"
  badge:
    backgroundColor: "#e6f4f1"
    textColor: "#0f5f58"
    typography: "{typography.micro}"
    rounded: "{rounded.pill}"
    padding: "1px 6px"
  stat-chip:
    backgroundColor: "{colors.line-2}"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.sm}"
    padding: "1px 7px"
  stat-chip-bad:
    backgroundColor: "#fde2e2"
    textColor: "#991b1b"
  stat-chip-snow:
    backgroundColor: "#efe7fe"
    textColor: "#5b21b6"
  icon-button:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.md}"
    size: "30px"
  switch-on:
    backgroundColor: "{colors.accent-teal}"
    rounded: "{rounded.pill}"
    width: "34px"
    height: "20px"
  watch-item:
    backgroundColor: "#f7f8f6"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "8px 10px"
  tooltip:
    backgroundColor: "{colors.night-ink}"
    textColor: "#f8fafc"
    rounded: "{rounded.md}"
    padding: "7px 9px"
---

# Design System: Move Route Weather

## Overview

**Creative North Star: "The Dispatcher's Desk"**

A calm, precise planning console for one person deciding where and when a 26-ft truck should drive. The chrome is deliberately quiet: sage-tinted paper, white panels, hairline borders, small system type, tabular numbers. All the loudness is saved for the weather and the risk. When something on screen is red, violet or orange, it carries meaning: gusts near the closure line, snow at a pass, rain near freezing.

The map and the profile chart under it do the work. The 372px sidebar is a dispatcher's clipboard. It holds the route cards, the toggles, a ranked watch list, the itinerary with weather at each stop, and an honest account of which forecast sources answered. Density is high but never cramped: 12–14px text, 6–8px gaps, 20px gutters.

Uncertainty is shown, not hidden. Estimated stop times get an amber "est" tag. Approximate route lines get a warning note. The faint bars, the opacity that tracks how many sources agree, and the shaded temperature spread show where the sources disagree.

**Key Characteristics:**
- Quiet neutral chrome; color reserved for weather encodings, risk and route identity.
- System sans throughout, tabular numerals for every time, temperature and speed.
- Flat surfaces with hairline borders; soft shadows only on things floating over the map.
- Small, uppercase, letter-spaced section labels; no large headings.
- Every hazard encoding is paired with a number or label, never color alone.

## Colors

A cool sage-gray neutral field with a single teal accent. The real color comes from the fixed weather and risk scales.

### Primary
- **Dispatch Teal** (accent-teal): the only interactive accent. Used for the on state of switches, focus outlines, and the loading spinner. It is also Route 3's identity color, so keep it off anything that could be mistaken for a route line.

### Weather encodings (fixed meaning, never decorative)
- **Rain Blue** (rain-blue): rain bars on the map and chart, rain figures in the itinerary.
- **Snow Violet** (snow-violet): snow bars, snow figures, snow stat chips.
- **Wind scale** (wind-calm → wind-breezy → wind-windy → wind-strong → wind-closure): wind arrow color by sustained speed in mph: under 10, 10–20, 20–30, 30–40, 40+. The deepest red marks closure territory for high-profile vehicles.
- **Temperature chips**: a cold-to-warm ramp from navy (≤10°F) through blue, ice (#bfdbfe at 32–40°F), slate and pale yellow to coral (70°F+), with white or near-black text chosen per step for contrast. The 32°F line is drawn in #3b82f6.

### Risk / status
- **Ember** (warn-ember) and **Alarm Red** (bad-red): warning and bad states in stat chips, notices and watch-list dots. Watch-list severity runs red (high), #ea580c (medium), #ca8a04 (low), green (ok), #94a3b8 (info).
- **Go Green** (ok-green): healthy source status, OK watch items.

### Route identity
- **Route 1 Slate** (route-1-slate), **Route 2 Ochre** (route-2-ochre), **Route 3 Teal** (route-3-teal). Set in `trip.json`. Each appears as the route card's left bar, the focus ring, the map polyline (drawn over a 9px white casing), and the profile chart swatch.

### Neutral
- **Ink** (ink): primary text. **Ink 2** (ink-2): secondary text and values. **Ink 3** (ink-3): labels, captions, metadata.
- **Hairline** (line) and **Faint Hairline** (line-2): borders, dividers, quiet chip fills.
- **Sage Paper** (bg-sage-paper): app background. **Panel** (panel): sidebar, cards, chart band. **Map Ground** (map-ground): map placeholder before tiles load.
- **Night Ink** (night-ink): dark tooltip, toast, chart cursor, and the pulsing map highlight ring.

### Named Rules
**The Weather Owns Color Rule.** Hue on screen means weather, risk, or which route. Chrome stays neutral. Blue never decorates, because blue means rain.

**The Paired Encoding Rule.** Every color-coded value also shows a number, label or shape (mph value, "est" tag, S/H/F/★/▲ marker), so nothing depends on hue alone.

## Typography

**Body Font:** the system UI sans stack (Segoe UI on the owner's Windows machine).
**Mono Font:** the system mono stack. It is defined but rarely used; numbers use `font-variant-numeric: tabular-nums` on the sans instead.

**Character:** native and invisible. The type should feel like part of the operating system, so the data reads as data.

### Hierarchy
- **Headline** (700, 19px, -0.01em): the single app title at the top of the sidebar. Nothing larger exists.
- **Title** (650, 13–14.5px): route names, day headings, info-window headings, chart title.
- **Body** (400, 14px / 1.45): the base. Sidebar content mostly runs 12.5–13.5px.
- **Small** (400, 11.5–12px / 1.35): notes, metadata, source details, chart axis text (10–11px in SVG).
- **Label** (650, 11px, uppercase, 0.08em): section headings ("Routes", "Watch list"). Info-window KPI labels use a 10.5px, 0.05em variant.
- **Micro** (650, 10.5px): badges, the "est" tag, stop-kind tags (uppercase, 0.04em).

### Named Rules
**The Tabular Numbers Rule.** Times, temperatures, speeds, mileages and tables always use tabular numerals, so columns line up when values change.

**The No Big Type Rule.** The largest type is 19px. Hierarchy comes from weight, case and ink level, not size.

## Layout

A two-column app shell filling the viewport: a fixed 372px scrolling sidebar on the left, and the main area on the right split into the map (fills the remaining height) and a profile chart strip under it (minimum 120px). The legend floats bottom-left over the map. Notices float top-center.

Sidebar rhythm: 20px horizontal gutter, sections padded 16px top / 6px bottom, 10px under each section label, 6–8px gaps between cards and list items. Itinerary stops are a 82px time column plus a flexible detail column, separated by faint hairlines.

Below 900px the shell stacks: map and chart first (map at 62vh), sidebar below at full width. The legend narrows to 220px and drops its long notes. This is a fallback; the primary use is a desktop browser.

## Elevation & Depth

Flat by default. Panels, cards, chips and toggles sit on the page with 1px hairline borders and no shadow. Depth appears only on elements floating over the map or the page, and only as soft, diffuse shadows.

### Shadow Vocabulary
- **Map overlay** (`box-shadow: 0 2px 10px rgba(0,0,0,.07)`; the notice uses `.08`): the legend and the map notice.
- **Floating dark** (`box-shadow: 0 4px 14px rgba(0,0,0,.2)`): the chart tooltip.
- **Toast** (`box-shadow: 0 6px 20px rgba(0,0,0,.25)`): the transient toast at the bottom.
- **Switch knob** (`box-shadow: 0 1px 2px rgba(0,0,0,.25)`): the only shadow inside a control.

### Named Rules
**The Float-Only Shadow Rule.** A shadow means "this is above the map or page." Panels and cards that sit in the layout never get one. The focused route card uses a 1px inset ring in its route color instead of lift.

## Shapes

Gently rounded and consistent: 10px for cards and the legend, 9px for layer toggles, notices and the toast, 8px for icon buttons, watch items and the tooltip, 6–7px for stat chips and KPI tiles, 4px for the tiny "est" tag, and full pills for badges and switches. Route cards carry a 6px colored bar on the left edge, squared on the outside and rounded 4px on the inside.

Map glyphs follow a fixed vocabulary: wind arrow (length and color = speed, dashed = gusts), vertical rain and snow bars, a rounded temperature chip, circle markers with S/H/F/★ letters for stops, and a dark triangle (▲) for passes.

## Components

Quiet and precise: small type, hairlines, tabular numbers, and color reserved for weather and risk.

### Route Card
- **Shape:** 10px radius, 1px hairline border, a 6px route-colored bar on the left.
- **Content:** bold route name and short name, badges, metadata line, then a row of stat chips.
- **Hover:** border darkens to #cfd4cf. **Focus (selected):** border and a 1px inset ring in the route color. **Hidden:** body fades to 50%.
- **Eye button:** 30px square, 8px radius, hairline border, faint fill on hover.

### Badges and Stat Chips
- **Badges:** pill, micro type. Variants: default teal (#e6f4f1 / #0f5f58), alt ochre (#f3f0e6 / #7a5a12), plain neutral, muted outline.
- **Stat chips:** 6px radius, 11.5px tabular. Neutral by default. Tinted warn (ember), bad (red), snow (violet) or rain (blue) only when the value crosses a threshold.
- **Est tag:** amber (#fef3c7 / #92400e), 4px radius. Marks estimated times.

### Switch
- 34×20 pill track (#cfd5d0 off, teal on), 16px white knob with a small shadow, 0.15s transitions. Focus-visible shows a 2px teal outline offset 2px. Paired with a bold label and a small explanatory line.

### Layer Toggles
- Four equal tiles in a row, 9px radius, with an icon above the label. Pressed: border #9fb3ad, faint green-gray fill, full-ink text. Unpressed: icon at 30% opacity, ink-3 text. State uses `aria-pressed`.

### Watch List
- Rows on #f7f8f6, 8px radius, an 8px severity dot, title text and a small caption. Hover darkens the fill slightly. Clicking a row focuses that point on the map.

### Itinerary
- Day heading with right-aligned meta, an optional day note, then stops in a time | detail grid with hairline separators. Stop names are 550 weight, kind tags are uppercase micro, and weather figures are tabular with rain and snow in their encoding colors.

### Resizable and Collapsible Panes
- **Splitters:** an invisible 8px drag strip on the sidebar's right edge (col-resize) and on the chart's top edge (row-resize). A 2px Dispatch Teal line shows on hover, drag or keyboard focus. Arrow keys move 16px (Shift: 64px), and double-click resets the default size. The sidebar is clamped to 280–720px, and the map is kept at least 160px tall. When the chart is taller than its natural height, its temp/precip/wind rows stretch to fill it.
- **Side panel:** a 22×52 edge tab on the map's left edge (white, hairline border, map-overlay shadow, chevron). Hides the whole sidebar; the map takes the width. Hidden under 900px, where the sidebar stacks below.
- **Chart:** a quiet "Hide chart / Show chart" text button at the right of the chart header; the header stays as a slim bar when collapsed.
- **Sidebar sections:** each uppercase section label is a full-width toggle with a chevron (rotates -90° when closed).
- Open/closed state persists in the prefs `ui` field. All chevrons are the same authored 16px SVG, stroke 2.

### Map Legend and Info Window
- **Legend:** white at 95% opacity, 10px radius, overlay shadow, collapsible header.
- **Info window:** 380px max. A heading with a route swatch, a three-up KPI tile row (#f6f7f5, 7px radius), and a compact right-aligned per-source table.

### Overview Strip
- A 22px strip between the chart header and the chart, aligned to the chart's 46px margins. It has an "All" button (filled Ink when the whole route shows) and one quiet button per day (Faint Hairline fill, 5px radius, bold day label plus Ink 3 title, truncated with an ellipsis).
- The selection is a 2px Dispatch Teal window (6px radius) with small teal grips on both edges, and white 68% shades over everything outside it. Click a day, or drag to create, move or resize the window.

### Profile Chart (signature)
- SVG strip under the map. Days are alternating white / #fafbf9 bands, with grey NIGHT bands labeled vertically. Rows: wind arrows; temperature line (#334155) over a shaded source-spread band, with a dashed 32° line; rain and snow bars; wind with gust dots and a dashed red 40 mph line. A dark vertical cursor and dark tooltip link the hover position to the map.

## Do's and Don'ts

### Do:
- **Do** keep chrome neutral (ink, hairline, sage paper) and let weather, risk and route colors carry all hue.
- **Do** use tabular numerals for every number that can change.
- **Do** pair every color encoding with a value, label or glyph.
- **Do** mark estimates and approximations visibly (amber "est" tag, warning note), never silently.
- **Do** reserve shadows for elements floating over the map or page.
- **Do** keep the 40 mph and 32°F reference lines; they are the decision thresholds.

### Don't:
- **Don't** use rain blue, snow violet or the wind reds for decoration, buttons or branding.
- **Don't** add type larger than the 19px title, or display fonts.
- **Don't** add shadows to in-layout cards or panels; use the inset route-color ring for selection.
- **Don't** reuse Dispatch Teal on anything that could read as Route 3's line.
