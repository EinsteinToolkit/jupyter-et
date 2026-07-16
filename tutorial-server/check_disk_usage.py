#!/usr/bin/env python3
# Copyright (c) 2019 Steven R. Brandt, and Roland Haas
#
# Distributed under the LGPL
#
# Run periodically (see crontab.txt) to catch the filesystem-fill problem
# early, plus general resource pressure: alerts via Telegram (as the notify
# user) when a watched mount is nearly full, a watched log file grows
# unusually fast between checks, memory usage is too high, or system load
# is too high relative to CPU count. Only notifies on OK<->ALERT state
# transitions, not on every tick while a condition remains active, so it
# doesn't spam the same alert forever if nobody's free to fix it right away.
import json
import os
import shlex
import shutil
import subprocess

STATE_FILE = "/var/lib/tutorial-server/disk-monitor-state.json"
MOUNTS = [m for m in os.environ.get("DISK_MOUNTS", "/,/home").split(",") if m]
DISK_ALERT_PERCENT = float(os.environ.get("DISK_ALERT_PERCENT", "85"))
# Keep this list in sync with the logrotate stanza these same files are in.
LOG_FILES = [
    p
    for p in os.environ.get(
        "LOG_FILES",
        "/var/log/jup-log.txt,/var/log/flag-stale-users.log,"
        "/var/log/check-disk-usage.log,/tmp/git-refresh.log",
    ).split(",")
    if p
]
LOG_GROWTH_ALERT_MB = float(os.environ.get("LOG_GROWTH_ALERT_MB", "100"))
MEM_ALERT_PERCENT = float(os.environ.get("MEM_ALERT_PERCENT", "90"))
# Alert when the 5-minute load average exceeds this multiple of CPU count --
# a load of 8 means "overloaded" on a 2-core box but is fine on 8 cores.
LOAD_ALERT_MULTIPLIER = float(os.environ.get("LOAD_ALERT_MULTIPLIER", "2.0"))


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as fd:
            return json.load(fd)
    return {
        "disk_alert": {},
        "log_sizes": {},
        "log_alert": {},
        "mem_alert": False,
        "load_alert": False,
    }


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as fd:
        json.dump(state, fd)


def notify(message):
    print(message, flush=True)
    cmd = "telegram-send " + shlex.quote(message)
    subprocess.run(["su", "-s", "/bin/sh", "-", "notify", "-c", cmd])


def check_disk(state):
    for mount in MOUNTS:
        try:
            usage = shutil.disk_usage(mount)
        except OSError:
            continue
        percent = usage.used / usage.total * 100
        was_alerting = state["disk_alert"].get(mount, False)
        is_alerting = percent >= DISK_ALERT_PERCENT
        if is_alerting and not was_alerting:
            notify(
                f"Disk usage alert: {mount} is {percent:.1f}% full "
                f"({usage.free // (1024**2)} MB free)"
            )
        elif was_alerting and not is_alerting:
            notify(f"Disk usage OK again: {mount} is {percent:.1f}% full")
        state["disk_alert"][mount] = is_alerting


def check_logs(state):
    for path in LOG_FILES:
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        prev = state["log_sizes"].get(path, size)
        growth_mb = (size - prev) / (1024**2)
        was_alerting = state["log_alert"].get(path, False)
        is_alerting = growth_mb >= LOG_GROWTH_ALERT_MB
        if is_alerting and not was_alerting:
            notify(
                f"Log growth alert: {path} grew {growth_mb:.1f} MB since "
                f"last check (now {size // (1024**2)} MB)"
            )
        elif was_alerting and not is_alerting:
            notify(f"Log growth OK again: {path}")
        state["log_alert"][path] = is_alerting
        state["log_sizes"][path] = size


def read_meminfo():
    info = {}
    with open("/proc/meminfo") as fd:
        for line in fd:
            key, _, rest = line.partition(":")
            info[key] = int(rest.strip().split()[0])  # kB
    return info


def check_memory(state):
    try:
        info = read_meminfo()
        total = info["MemTotal"]
        available = info.get("MemAvailable", info.get("MemFree", 0))
    except (OSError, KeyError, IndexError, ValueError):
        return
    if total <= 0:
        return
    percent = (total - available) / total * 100
    was_alerting = state.get("mem_alert", False)
    is_alerting = percent >= MEM_ALERT_PERCENT
    if is_alerting and not was_alerting:
        notify(f"Memory usage alert: {percent:.1f}% used")
    elif was_alerting and not is_alerting:
        notify(f"Memory usage OK again: {percent:.1f}% used")
    state["mem_alert"] = is_alerting


def check_load(state):
    try:
        with open("/proc/loadavg") as fd:
            load5 = float(fd.read().split()[1])
    except (OSError, IndexError, ValueError):
        return
    cpus = os.cpu_count() or 1
    ratio = load5 / cpus
    was_alerting = state.get("load_alert", False)
    is_alerting = ratio >= LOAD_ALERT_MULTIPLIER
    if is_alerting and not was_alerting:
        notify(
            f"Load average alert: 5m load {load5:.2f} on {cpus} cpu(s) "
            f"({ratio:.2f}x)"
        )
    elif was_alerting and not is_alerting:
        notify(f"Load average OK again: 5m load {load5:.2f} on {cpus} cpu(s)")
    state["load_alert"] = is_alerting


def main():
    state = load_state()
    check_disk(state)
    check_logs(state)
    check_memory(state)
    check_load(state)
    save_state(state)


if __name__ == "__main__":
    main()
