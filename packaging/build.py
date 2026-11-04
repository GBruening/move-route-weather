"""Build the double-click app for this computer's system and zip it for a release.

    python -m pip install pyinstaller      (build machine only)
    python packaging/build.py [--online]   (--online also checks HTTPS from the built app)

Windows makes MoveRouteWeather.exe, a Mac makes the MoveRouteWeather app. The script
starts the built app once to check it serves the page, then writes
release/MoveRouteWeather-<system>.zip with the app, config.local.example.json and
"How to run.txt". GitHub Actions runs this on Windows and macOS for each release
(.github/workflows/build-apps.yml).
"""
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
RELEASE = os.path.join(ROOT, "release")
SMOKE_PORT = 8799

HOW_TO = {
    "windows": """Move Route Weather for Windows
==============================

1. Unzip this folder somewhere you'll find it again, like Documents.
2. Google key (optional, for the full map): copy config.local.example.json to
   config.local.json in this folder, open it in Notepad and paste your key.
   Without a key the map has Google's "for development purposes only" watermark.
3. Double-click MoveRouteWeather.exe. The first time, Windows may say it protected
   your PC: click "More info", then "Run anyway" (the app isn't signed).

Your browser opens http://localhost:8000. Keep the black window open while you use it;
close it to stop. Your schedule edits (trip.local.json) and downloaded forecasts
(cache folder) are saved in this folder.
""",
    "mac": """Move Route Weather for Mac (Apple Silicon: M1 or newer)
=======================================================

1. Unzip this folder somewhere you'll find it again, like Documents.
2. Google key (optional, for the full map): copy config.local.example.json to
   config.local.json in this folder, open it in TextEdit and paste your key.
   Without a key the map has Google's "for development purposes only" watermark.
3. Double-click MoveRouteWeather. It isn't signed by Apple, so the first time macOS
   blocks it: open System Settings > Privacy & Security, scroll down and click
   "Open Anyway" (on older macOS: right-click the app, choose Open, then Open).
   If macOS says the app is damaged, run this once in Terminal, with the folder path:
       xattr -dr com.apple.quarantine ~/Documents/MoveRouteWeather-mac

It opens a Terminal window and your browser at http://localhost:8000. Keep Terminal
open while you use it; press Ctrl+C or close it to stop. Your schedule edits
(trip.local.json) and downloaded forecasts (cache folder) are saved in this folder.
""",
}


def system():
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "mac"
    return "linux"


def build():
    for d in (DIST, os.path.join(ROOT, "build")):
        shutil.rmtree(d, ignore_errors=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", DIST, "--workpath", os.path.join(ROOT, "build"),
                    os.path.join(ROOT, "packaging", "MoveRouteWeather.spec")], check=True, cwd=ROOT)
    app = os.path.join(DIST, "MoveRouteWeather.exe" if system() == "windows" else "MoveRouteWeather")
    if not os.path.exists(app):
        sys.exit("build produced no %s" % app)
    return app


def smoke_test(app, online=False):
    """Start the built app on a spare port and check it serves the config and the page."""
    proc = subprocess.Popen([app, "--no-browser", "--port", str(SMOKE_PORT)], cwd=DIST,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        deadline = time.time() + 90
        while time.time() < deadline:
            if proc.poll() is not None:
                sys.exit("the app exited early:\n" + proc.stdout.read().decode("utf-8", "replace"))
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/api/config" % SMOKE_PORT, timeout=5) as r:
                    cfg = json.loads(r.read())
                with urllib.request.urlopen("http://127.0.0.1:%d/" % SMOKE_PORT, timeout=5) as r:
                    page = r.read().decode("utf-8")
                assert cfg["routes"] and "app.js" in page
                print("smoke test ok: %d routes, page served" % len(cfg["routes"]))
                if online:  # HTTPS from inside the packaged app: route day lines through OSRM (no weather)
                    url = "http://127.0.0.1:%d/api/plan?route=r1&portland=1&weather=0" % SMOKE_PORT
                    with urllib.request.urlopen(url, timeout=120) as r:
                        plan = json.loads(r.read())
                    bad = [d["route_errors"] for d in plan["days"] if d["approx"]]
                    if bad:
                        sys.exit("HTTPS from the app failed; routing fell back to straight lines: %s" % bad[0])
                    print("online check ok: %d days routed by %s" % (len(plan["days"]), plan["days"][0]["provider"]))
                return
            except (OSError, ValueError):
                time.sleep(1)
        sys.exit("the app didn't answer on port %d within 90 s" % SMOKE_PORT)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        # the run leaves a cache folder next to the app; it isn't part of the release
        shutil.rmtree(os.path.join(DIST, "cache"), ignore_errors=True)


def package(app):
    sysname = system()
    arch = platform.machine().lower().replace("x86_64", "x64").replace("amd64", "x64")
    folder = "MoveRouteWeather-%s" % sysname
    os.makedirs(RELEASE, exist_ok=True)
    out = os.path.join(RELEASE, "%s-%s.zip" % (folder, arch))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        info = zipfile.ZipInfo.from_file(app, "%s/%s" % (folder, os.path.basename(app)))
        info.external_attr = (0o755 << 16) if sysname != "windows" else info.external_attr  # keep it runnable
        info.compress_type = zipfile.ZIP_DEFLATED
        with open(app, "rb") as f:
            z.writestr(info, f.read())
        z.write(os.path.join(ROOT, "config.local.example.json"), "%s/config.local.example.json" % folder)
        z.writestr("%s/How to run.txt" % folder, HOW_TO.get(sysname, HOW_TO["mac"]).replace("\n", "\r\n" if sysname == "windows" else "\n"))
    print("wrote %s (%.1f MB)" % (out, os.path.getsize(out) / 1e6))
    return out


if __name__ == "__main__":
    built = build()
    if "--no-smoke" not in sys.argv:
        smoke_test(built, online="--online" in sys.argv)
    package(built)
