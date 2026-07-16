#!/usr/bin/env python3
# Copyright (c) 2019 Steven R. Brandt, and Roland Haas
#
# Distributed under the LGPL
#
# Run periodically (see crontab.txt) to flag accounts that haven't logged in
# for STALE_DAYS. This never deletes anything itself: it only appends
# candidates to pending_deletion.txt and sends a Telegram message about
# accounts newly flagged in this run. Run delete_flagged_users.py separately
# to actually remove them.
#
# Also flags a second, riskier category: home directories that predate
# user_registry.txt entirely (so we have no login history at all for them)
# but whose files haven't been touched in LEGACY_STALE_DAYS. These are
# recorded with an empty email field, since we never captured one for them
# -- delete_flagged_users.py can't produce a whitelist hash for those.
import datetime
import os
import shlex
import subprocess

REGISTRY = "/home/user_registry.txt"
EXEMPT = "/home/cleanup_exempt.txt"
PENDING = "/home/pending_deletion.txt"
HOME_DIR = "/home"
STALE_DAYS = int(os.environ.get("STALE_DAYS", "90"))
LEGACY_STALE_DAYS = int(os.environ.get("LEGACY_STALE_DAYS", "365"))


def read_registry():
    entries = {}
    if os.path.exists(REGISTRY):
        with open(REGISTRY) as fd:
            for line in fd:
                line = line.strip()
                if not line:
                    continue
                parts = line.split(":", 2)
                if len(parts) == 3:
                    entries[parts[0]] = (parts[1], parts[2])
    return entries


def read_names(path):
    names = set()
    if os.path.exists(path):
        with open(path) as fd:
            for line in fd:
                line = line.strip()
                if line and not line.startswith("#"):
                    names.add(line)
    return names


def already_pending_usernames():
    names = set()
    if os.path.exists(PENDING):
        with open(PENDING) as fd:
            for line in fd:
                line = line.strip()
                if line:
                    names.add(line.split(":", 1)[0])
    return names


def is_user_home(name):
    path = os.path.join(HOME_DIR, name)
    return os.path.isdir(path) and os.path.exists(os.path.join(path, ".bashrc"))


def most_recent_mtime(path):
    # Only real file mtimes count as "activity" -- directory mtimes reflect
    # entry add/remove/rename, not content changes, and would dominate here
    # (e.g. right after creating the home dir) even if every file inside is
    # actually old.
    latest = None
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                m = os.path.getmtime(os.path.join(root, f))
            except OSError:
                continue
            if latest is None or m > latest:
                latest = m
    if latest is None:
        # No files at all -- fall back to the directory's own mtime.
        latest = os.path.getmtime(path)
    return latest


def find_legacy_stale_users(registry, exempt, already_flagged):
    threshold = datetime.datetime.utcnow() - datetime.timedelta(
        days=LEGACY_STALE_DAYS
    )
    candidates = []
    try:
        names = os.listdir(HOME_DIR)
    except OSError:
        return candidates
    for name in names:
        if name in registry or name in exempt or name in already_flagged:
            continue
        if not is_user_home(name):
            continue
        try:
            latest = most_recent_mtime(os.path.join(HOME_DIR, name))
        except OSError:
            continue
        last_activity = datetime.datetime.utcfromtimestamp(latest)
        if last_activity < threshold:
            candidates.append((name, "", last_activity.isoformat()))
    return candidates


def main():
    registry = read_registry()
    exempt = read_names(EXEMPT)
    already_flagged = already_pending_usernames()

    threshold = datetime.datetime.utcnow() - datetime.timedelta(days=STALE_DAYS)
    newly_flagged = []
    for username, (email, last_login_iso) in registry.items():
        if username in exempt or username in already_flagged:
            continue
        try:
            last_login = datetime.datetime.fromisoformat(last_login_iso)
        except ValueError:
            continue
        if last_login < threshold:
            newly_flagged.append((username, email, last_login_iso))

    legacy_flagged = find_legacy_stale_users(registry, exempt, already_flagged)
    newly_flagged.extend(legacy_flagged)

    if not newly_flagged:
        print("No newly-stale accounts found")
        return

    with open(PENDING, "a") as fd:
        for username, email, last_login_iso in newly_flagged:
            fd.write(f"{username}:{email}:{last_login_iso}\n")
    os.chmod(PENDING, 0o0600)

    lines = [
        f"{u} (no login record, files untouched since {t})"
        if not e
        else f"{u} ({e}) last login {t}"
        for u, e, t in newly_flagged
    ]
    message = (
        f"{len(newly_flagged)} account(s) flagged for deletion "
        f"(inactive {STALE_DAYS}+ days, or no login record and files "
        f"untouched {LEGACY_STALE_DAYS}+ days):\n" + "\n".join(lines)
    )
    print(message)
    # "su -" (login shell) sets HOME from notify's passwd entry, so
    # telegram-send resolves its usual ~/.config/telegram-send.conf on its
    # own -- no --config flag needed. -s overrides notify's nologin shell
    # just for running this one command.
    cmd = f"telegram-send {shlex.quote(message)}"
    result = subprocess.run(["su", "-s", "/bin/sh", "-", "notify", "-c", cmd])
    if result.returncode != 0:
        print(f"telegram-send failed (rc={result.returncode})")


if __name__ == "__main__":
    main()
