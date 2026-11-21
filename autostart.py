#!/usr/bin/env python3
"""Start the server instances through the web admin's API once the web admin is up.

The start is delayed on purpose: right after an unclean restart the front end still considers the
old ports taken and would hand out the next ones (60003 instead of 60000).

With RR_AUTOSTART=last the script keeps running, records every 30 seconds which instances are
running (/data/autostart-state.json) and starts exactly those after a restart. Without a state
file, "last" starts all instances.

Environment:
  RR_AUTOSTART        all (default) | last | none | comma-separated list of instance IDs
  RR_AUTOSTART_DELAY  seconds to wait after the web admin answers (default 45)
"""
import base64
import json
import os
import time
import urllib.request

BASE = "http://127.0.0.1:8088"
WANT = os.environ.get("RR_AUTOSTART", "all").strip()
DELAY = float(os.environ.get("RR_AUTOSTART_DELAY", "45"))
STATE_FILE = "/data/autostart-state.json"
POLL = 30
# Wine's HTTP service only answers requests whose Host header carries the port.
HEADERS = {"Host": "localhost:8088", "Accept": "application/json", "Content-Type": "application/json"}
try:
    with open("/data/server/access", encoding="utf-8-sig") as f:      # first line: user:password
        HEADERS["Authorization"] = "Basic " + base64.b64encode(f.readline().strip().encode()).decode()
except OSError:
    pass


def call(path, body=None, timeout=90):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8-sig") or "null")


def log(text):
    print("autostart: " + text + "\r", flush=True)      # \r: the container runs with a terminal


def load_state():
    """IDs that were running before the restart, or None if nothing has been recorded yet."""
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return [str(x) for x in json.load(f)["running"]]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_state(ids):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"running": ids}, f)
    os.replace(tmp, STATE_FILE)


def watch():
    """Record the running instances whenever the set changes. Never returns."""
    known = load_state()
    while True:
        time.sleep(POLL)
        try:
            running = sorted(str((i.get("GameSetting") or {}).get("Id")) for i in call("/dedi/", timeout=20) or []
                             if (i.get("ProcessState") or {}).get("Running"))
        except Exception:
            continue                                     # web admin busy or gone: keep the last state
        if running != known:
            try:
                save_state(running)
                known = running
            except OSError as exc:
                log("cannot write %s: %r" % (STATE_FILE, exc))


def wanted(instances):
    if WANT == "last":
        ids = load_state()
    else:
        ids = [x.strip() for x in WANT.split(",")]
    for inst in instances or []:
        setting = inst.get("GameSetting") or {}
        if WANT == "all" or ids is None or str(setting.get("Id")) in ids:
            yield setting.get("Id"), setting.get("Name"), inst.get("ProcessState") or {}


def main():
    if WANT == "none":
        return
    for _ in range(120):                                 # up to 10 minutes for the web admin
        try:
            call("/dedi/", timeout=10)
            break
        except Exception:
            time.sleep(5)
    else:
        log("the web admin does not answer, nothing started")
        return

    time.sleep(DELAY)
    try:
        for pid, name, state in wanted(call("/dedi/")):
            if state.get("Running"):
                continue
            try:
                call("/dedi/start", {"ProcessId": pid})
            except Exception as exc:
                log("instance %s (%s) not started: %r" % (pid, name, exc))
        time.sleep(20)
        for pid, name, state in wanted(call("/dedi/")):
            log("instance %s (%s): running=%s, port %s, time port %s"
                % (pid, name, state.get("Running"), state.get("Port"), state.get("TimePort")))
    except Exception as exc:
        log("gave up: %r" % exc)
    if WANT == "last":
        watch()


if __name__ == "__main__":
    main()
