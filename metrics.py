#!/usr/bin/env python3
"""Prometheus exporter for the RaceRoom dedicated server.

Polls the web admin's API (GET /dedi/) every RR_METRICS_INTERVAL seconds and reads the session result
files the server writes (UserData/Log_Dedicated/Results/*.txt). /metrics only returns the last poll,
so scrapes never reach the server itself.

Environment:
  RR_METRICS_PORT      port for /metrics (the exporter only runs when this is set)
  RR_METRICS_INTERVAL  seconds between two API polls (default 15)
"""
import base64
import glob
import json
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = "http://127.0.0.1:8088"
PORT = int(os.environ.get("RR_METRICS_PORT", "9127"))
INTERVAL = max(5.0, float(os.environ.get("RR_METRICS_INTERVAL", "15")))
PREFIX = os.environ.get("WINEPREFIX", "/data/prefix")
RESULTS_GLOB = os.path.join(PREFIX, "drive_c/users/*/Documents/My Games/SimBin/RaceRoom Dedicated/"
                                    "UserData/Log_Dedicated/Results/*.txt")
CATALOGUE_REFRESH = 6 * 3600
# Wine's HTTP service only answers requests whose Host header carries the port.
HEADERS = {"Host": "localhost:8088", "Accept": "application/json"}


def log(text):
    print("metrics: " + text + "\r", flush=True)      # \r: the container runs with a terminal


def load_auth():
    try:
        with open("/data/server/access", encoding="utf-8-sig") as f:      # first line: user:password
            HEADERS["Authorization"] = "Basic " + base64.b64encode(f.readline().strip().encode()).decode()
    except OSError:
        pass


def call(path, timeout=10):
    req = urllib.request.Request(BASE + path, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8-sig") or "null")


def esc(value):
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


class Metrics:
    """Collects samples as {name: (type, help, {labels_tuple: value})}."""

    def __init__(self):
        self.families = {}

    def add(self, name, kind, text, value, **labels):
        family = self.families.setdefault(name, (kind, text, {}))
        family[2][tuple(sorted(labels.items()))] = value

    def render(self):
        out = []
        for name, (kind, text, samples) in self.families.items():
            out.append(f"# HELP {name} {text}")
            out.append(f"# TYPE {name} {kind}")
            for labels, value in samples.items():
                lab = ",".join(f'{k}="{esc(v)}"' for k, v in labels)
                out.append(f"{name}{{{lab}}} {value}" if lab else f"{name} {value}")
        return "\n".join(out) + "\n"


class Catalogue:
    """Names for track layouts and cars from /content/all."""

    def __init__(self):
        self.layouts, self.cars, self.loaded = {}, {}, 0.0

    def refresh(self):
        if time.time() - self.loaded < (CATALOGUE_REFRESH if self.layouts else 300):
            return
        self.loaded = time.time()
        try:
            data = call("/content/all", timeout=60) or {}
        except Exception as exc:                          # noqa: BLE001 - keep polling without names
            log(f"could not load /content/all: {exc}")
            return
        for track in data.get("Tracks") or []:
            for layout in track.get("Layouts") or []:
                self.layouts[layout.get("Id")] = (track.get("Name") or "", layout.get("Name") or "")
        for entry in data.get("Liveries") or []:
            car = entry.get("Car") or {}
            self.cars[car.get("Id")] = car.get("Name") or ""

    def layout(self, layout_id):
        return self.layouts.get(layout_id, (str(layout_id), ""))

    def car(self, car_id):
        return self.cars.get(car_id, str(car_id))


SESSION_ORDER = {"Practice": 0, "Qualify": 1, "Warmup": 2, "Race1": 3, "Race2": 4, "Race3": 5}


class Results:
    """Aggregates the result files; each file is parsed once (again only if it changes)."""

    def __init__(self):
        self.files = {}          # path -> (mtime, parsed summary)

    def scan(self):
        seen = set()
        for path in glob.glob(RESULTS_GLOB):
            seen.add(path)
            try:
                mtime = os.path.getmtime(path)
                if path in self.files and self.files[path][0] == mtime:
                    continue
                with open(path, encoding="utf-8-sig") as f:
                    self.files[path] = (mtime, self.parse(path, json.load(f)))
            except (OSError, ValueError):
                continue                                   # file still being written
        for path in set(self.files) - seen:
            del self.files[path]

    @staticmethod
    def parse(path, data):
        # Port_60000_2026_11_21_23_32_11_Qualify: game port, start of the event, session type
        name = os.path.basename(path)[:-4]
        parts = name.split("_")
        session = parts[-1] if len(parts) > 2 else "Unknown"
        port = parts[1] if len(parts) > 2 and parts[0] == "Port" else ""
        order = ("_".join(parts[2:-1]), SESSION_ORDER.get(session, 9))
        event = data.get("event") or {}
        drivers = []
        for d in data.get("drivers") or []:
            laps = [lap.get("lapTimeMs", -1) for lap in d.get("laps") or []]
            valid = [t for t in laps if isinstance(t, (int, float)) and t > 0]
            best = d.get("bestLapTimeMs", -1)
            if not (isinstance(best, (int, float)) and best > 0):
                best = min(valid) if valid else None
            drivers.append({"name": d.get("name") or str(d.get("userId")), "car": d.get("carId"),
                            "laps": int(d.get("totalLaps") or 0), "valid": len(valid), "best": best})
        return {"session": session, "port": port, "order": order, "layout": event.get("layoutId"),
                "drivers": drivers}

    def current_session(self, port):
        """The server creates the result file when a session starts: the newest one names the session."""
        newest = max((s for _, s in self.files.values() if s["port"] == str(port)),
                     key=lambda s: s["order"], default=None)
        return newest["session"] if newest else None


class Exporter:
    def __init__(self):
        self.catalogue, self.results = Catalogue(), Results()
        self.text = "# no data yet\n"
        self.lock = threading.Lock()

    def poll(self):
        m = Metrics()
        start = time.time()
        instances, up = [], 0
        try:
            instances, up = call("/dedi/") or [], 1
        except Exception as exc:                          # noqa: BLE001 - report as raceroom_up 0
            log(f"/dedi/ failed: {exc}")
        m.add("raceroom_up", "gauge", "1 if the web admin API answered the last poll.", up)
        m.add("raceroom_api_duration_seconds", "gauge", "Duration of the last API poll.",
              round(time.time() - start, 3))
        if up:
            self.catalogue.refresh()
        try:
            self.results.scan()
        except Exception as exc:                          # noqa: BLE001
            log(f"reading results failed: {exc}")
        for inst in instances:
            self.instance(m, inst)
        self.result_metrics(m)
        m.add("raceroom_last_poll_timestamp_seconds", "gauge", "Time of the last poll.", round(time.time()))
        with self.lock:
            self.text = m.render()

    def instance(self, m, inst):
        gs, ps = inst.get("GameSetting") or {}, inst.get("ProcessState") or {}
        lab = {"instance_id": gs.get("Id"), "instance_name": gs.get("Name") or ""}
        layouts = gs.get("TrackLayouts") or []
        layout_id = layouts[0].get("LayoutId") if layouts else None
        track, layout = self.catalogue.layout(layout_id)
        m.add("raceroom_instance_info", "gauge", "Configuration of a server instance (value is always 1).", 1,
              **lab, track=track, layout=layout, layout_id=layout_id, port=ps.get("Port") or "",
              cars=len(gs.get("Cars") or []))
        m.add("raceroom_instance_running", "gauge", "1 if the instance is running.",
              1 if ps.get("Running") else 0, **lab)
        m.add("raceroom_players", "gauge", "Players connected to the instance.",
              int(ps.get("PlayersOnServer") or 0), **lab)
        m.add("raceroom_max_players", "gauge", "Maximum number of players of the instance.",
              int(gs.get("MaxNumOfPlayers") or 0), **lab)
        # The API's CurrentSession, SessionTime, SessionTimeLeft and Players stay 0/empty; TimeLeft is the
        # remaining time of the running session in milliseconds (only meaningful with players on the server).
        m.add("raceroom_session_time_left_seconds", "gauge", "Remaining time of the running session.",
              round(float(ps.get("TimeLeft") or 0) / 1000.0, 1), **lab)
        session = self.results.current_session(ps.get("Port")) if ps.get("Running") else None
        if session:
            m.add("raceroom_current_session_info", "gauge",
                  "Running session (Practice, Qualify, Warmup, Race1-3; value is always 1).", 1, **lab,
                  session=session)

    def result_metrics(self, m):
        sessions, laps, valid, best = {}, {}, {}, {}
        for _, summary in self.results.files.values():
            track, layout = self.catalogue.layout(summary["layout"])
            key = summary["session"]
            sessions[key] = sessions.get(key, 0) + (1 if summary["drivers"] else 0)
            for d in summary["drivers"]:
                laps[d["name"]] = laps.get(d["name"], 0) + d["laps"]
                valid[d["name"]] = valid.get(d["name"], 0) + d["valid"]
                if d["best"]:
                    k = (d["name"], track, layout, self.catalogue.car(d["car"]))
                    best[k] = min(best.get(k, d["best"]), d["best"])
        m.add("raceroom_result_files", "gauge", "Result files in the Results directory.", len(self.results.files))
        for session, n in sessions.items():
            m.add("raceroom_sessions_with_drivers", "gauge", "Sessions with at least one driver, by type.", n,
                  session=session)
        for name in laps:
            m.add("raceroom_driver_laps", "gauge", "Laps driven (all result files).", laps[name], driver=name)
            m.add("raceroom_driver_valid_laps", "gauge", "Laps with a valid time (all result files).",
                  valid[name], driver=name)
        for (name, track, layout, car), ms in best.items():
            m.add("raceroom_best_lap_seconds", "gauge", "Best valid lap per driver, track layout and car.",
                  ms / 1000.0, driver=name, track=track, layout=layout, car=car)

    def loop(self):
        while True:
            try:
                self.poll()
            except Exception as exc:                      # noqa: BLE001 - never let the thread die
                log(f"poll failed: {exc}")
            time.sleep(INTERVAL)


def main():
    load_auth()
    exporter = Exporter()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?")[0] != "/metrics":
                self.send_error(404)
                return
            with exporter.lock:
                body = exporter.text.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    threading.Thread(target=exporter.loop, daemon=True).start()
    log(f"serving /metrics on port {PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
