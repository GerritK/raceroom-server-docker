#!/bin/bash
# run    - start RRRE_Dedicated.exe under Wine on a virtual display (default)
# update - download or update the server with SteamCMD (app 354060, anonymous login)
# setup  - create the Wine prefix: 64-bit, with the Microsoft .NET Framework 4.8 (5-20 minutes)
# shell  - debug shell
#
# Environment for "run":
#   RR_AUTOSTART         all (default) | last (what was running before) | none | comma-separated instance IDs
#   RR_AUTOSTART_DELAY   seconds to wait before starting them (default 45, see autostart.py)
#   RR_WINDOWS_VERSION   Windows version Wine reports to the server front end (default win10)
set -e
export WINEARCH=win64
export WINEPREFIX="${WINEPREFIX:-/data/prefix}"
export WINEDEBUG="${WINEDEBUG:-fixme-all}"
SERVER_DIR=/data/server
mkdir -p "$SERVER_DIR"

start_x() {
  rm -f /tmp/.X99-lock            # left behind when the container restarts; Xvfb would refuse to start
  Xvfb :99 -screen 0 1024x768x24 -nolisten tcp >/dev/null 2>&1 &
  sleep 2
  export DISPLAY=:99
}

# The front end has to run in Windows 10 mode; "winetricks dotnet48" leaves the prefix on Windows 7.
# See README, "Wine patch".
set_windows_version() {
  wine reg add 'HKCU\Software\Wine\AppDefaults\RRRE_Dedicated.exe' /v Version \
    /d "${RR_WINDOWS_VERSION:-win10}" /f >/dev/null \
    || echo "WARNING: could not set the Windows version for RRRE_Dedicated.exe."
}

case "${1:-run}" in
  run)
    if [ ! -f "$SERVER_DIR/RRRE_Dedicated.exe" ]; then
      echo "No server found in $SERVER_DIR. Run the 'update' command first (see README)."; exit 1
    fi
    if [ ! -d "$WINEPREFIX/drive_c/windows/Microsoft.NET/Framework64/v4.0.30319" ]; then
      echo "No Wine prefix with .NET 4.8 in $WINEPREFIX. Run the 'setup' command first (see README)."; exit 1
    fi
    start_x
    set_windows_version
    # Without winedbg a crash ends the process (and the container, which Docker then restarts)
    # instead of leaving it hanging in the debugger.
    export WINEDLLOVERRIDES="winedbg.exe=d"
    python3 /usr/local/bin/autostart.py &
    cd "$SERVER_DIR"
    exec wine RRRE_Dedicated.exe ;;
  update)
    # A fresh SteamCMD first updates itself and then often fails once with "Missing configuration".
    steamcmd +quit >/dev/null 2>&1 || true
    for attempt in 1 2 3; do
      steamcmd +@sSteamCmdForcePlatformType windows +force_install_dir "$SERVER_DIR" \
        +login anonymous +app_update 354060 validate +quit || true
      if [ -f "$SERVER_DIR/RRRE_Dedicated.exe" ]; then echo "Server files are in $SERVER_DIR."; exit 0; fi
      echo "Download failed (attempt $attempt of 3), retrying..."; sleep 5
    done
    echo "SteamCMD could not download the server."; exit 1 ;;
  setup)
    if [ -e "$WINEPREFIX/system.reg" ]; then
      echo "$WINEPREFIX already exists. Remove it first if you want to rebuild it."; exit 1
    fi
    start_x
    # Without these overrides wineboot waits forever on an invisible "install Wine Mono?" dialog.
    WINEDLLOVERRIDES="mscoree,mshtml=" wineboot -u
    wineserver -w
    winetricks -q dotnet48
    wineserver -w
    set_windows_version
    wineserver -w
    echo "Wine prefix with .NET Framework 4.8 created in $WINEPREFIX." ;;
  shell) exec bash ;;
  *) exec "$@" ;;
esac
