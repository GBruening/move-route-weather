# PyInstaller recipe: one self-contained app (Python included) built from server.py.
# Run through packaging/build.py, which also smoke-tests the result and zips it.
# PyInstaller is only needed on the machine that builds; the app itself uses no packages.
import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821 (SPECPATH is set by PyInstaller)

a = Analysis(  # noqa: F821
    [os.path.join(ROOT, "server.py")],
    pathex=[ROOT],
    datas=[
        (os.path.join(ROOT, "static"), "static"),
        (os.path.join(ROOT, "trip.json"), "."),
        (os.path.join(ROOT, "config.json"), "."),
    ],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="MoveRouteWeather",
    console=True,  # the window shows the log; closing it stops the server
    upx=False,
    target_arch=None,
    codesign_identity=None,  # unsigned; macOS builds get PyInstaller's ad-hoc signature
    entitlements_file=None,
)
