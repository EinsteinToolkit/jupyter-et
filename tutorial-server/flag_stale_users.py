#!/usr/bin/env python3
# Copyright (c) 2019 Steven R. Brandt, and Roland Haas
#
# Distributed under the LGPL
#
# Run periodically (see crontab.txt, as the notify user) to flag accounts
# that haven't logged in for STALE_DAYS. This never deletes anything
# itself: it maintains pending_deletion.txt (capped at MAX_PENDING entries,
# the most-stale overall) and sends a Telegram message about accounts newly
# added to that list in this run. Run delete_flagged_users.py separately to
# actually remove them, one at a time.
#
# Also flags a second, riskier category: home directories that predate
# user_registry.txt entirely (so we have no login history at all for them)
# but whose files haven't been touched in LEGACY_STALE_DAYS. These are
# recorded with an empty email field, since we never captured one for them
# -- delete_flagged_users.py can't produce a whitelist hash for those.
#
# Safety floor: does nothing at all if the system has MIN_TOTAL_USERS or
# fewer real accounts, so a small pilot/test deployment never gets its
# handful of users auto-flagged.
import datetime
import os
import subprocess

REGISTRY = "/home/user_registry.txt"
EXEMPT = "/home/cleanup_exempt.txt"
PENDING = "/home/pending_deletion.txt"
HOME_DIR = "/home"
STALE_DAYS = int(os.environ.get("STALE_DAYS", "90"))
LEGACY_STALE_DAYS = int(os.environ.get("LEGACY_STALE_DAYS", "365"))
MAX_PENDING = int(os.environ.get("MAX_PENDING", "3"))
MIN_TOTAL_USERS = int(os.environ.get("MIN_TOTAL_USERS", "20"))


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


def read_pending():
    entries = []
    if os.path.exists(PENDING):
        with open(PENDING) as fd:
            for line in fd:
                line = line.strip()
                if not line:
                    continue
                parts = line.split(":", 2)
                if len(parts) == 3:
                    entries.append(tuple(parts))
    return entries


def is_user_home(name):
    path = os.path.join(HOME_DIR, name)
    return os.path.isdir(path) and os.path.exists(os.path.join(path, ".bashrc"))


def count_total_users():
    try:
        return sum(1 for name in os.listdir(HOME_DIR) if is_user_home(name))
    except OSError:
        return 0


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


def find_legacy_stale_users(registry, exempt):
    threshold = datetime.datetime.utcnow() - datetime.timedelta(
        days=LEGACY_STALE_DAYS
    )
    candidates = []
    try:
        names = os.listdir(HOME_DIR)
    except OSError:
        return candidates
    for name in names:
        if name in registry or name in exempt:
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


def describe(username, email, last_login_iso):
    if email:
        return f"{username} ({email}) last login {last_login_iso}"
    return f"{username} (no login record, files untouched since {last_login_iso})"


def notify(message):
    print(message, flush=True)
    # This whole script runs as notify via cron now (see startup.sh), so
    # ~/.config/telegram-send.conf resolves on its own -- no su/--config
    # hop needed.
    result = subprocess.run(["telegram-send", message])
    if result.returncode != 0:
        print(f"telegram-send failed (rc={result.returncode})")


def main():
    total_users = count_total_users()
    if total_users <= MIN_TOTAL_USERS:
        print(
            f"Only {total_users} user(s) on the system (<= {MIN_TOTAL_USERS}), "
            "skipping stale-account flagging entirely"
        )
        return

    registry = read_registry()
    exempt = read_names(EXEMPT)

    threshold = datetime.datetime.utcnow() - datetime.timedelta(days=STALE_DAYS)
    candidates = []
    for username, (email, last_login_iso) in registry.items():
        if username in exempt:
            continue
        try:
            last_login = datetime.datetime.fromisoformat(last_login_iso)
        except ValueError:
            continue
        if last_login < threshold:
            candidates.append((username, email, last_login_iso))

    candidates.extend(find_legacy_stale_users(registry, exempt))

    # Oldest (most stale) first, keep only the MAX_PENDING worst offenders
    # overall -- this replaces pending_deletion.txt's content rather than
    # appending to it, so the file is always this bounded "worst of" list,
    # not an ever-growing backlog.
    candidates.sort(key=lambda c: c[2])
    kept = candidates[:MAX_PENDING]

    previous = read_pending()
    previous_usernames = {p[0] for p in previous}
    newly_added = [c for c in kept if c[0] not in previous_usernames]

    with open(PENDING, "w") as fd:
        for username, email, last_login_iso in kept:
            fd.write(f"{username}:{email}:{last_login_iso}\n")
    os.chmod(PENDING, 0o0660)

    if not kept:
        print("No stale accounts found")
        return

    print(f"{len(kept)} account(s) currently pending deletion (of {len(candidates)} eligible):")
    for c in kept:
        print("  " + describe(*c))

    if newly_added:
        message = (
            f"{len(newly_added)} account(s) newly flagged for deletion "
            f"(inactive {STALE_DAYS}+ days, or no login record and files "
            f"untouched {LEGACY_STALE_DAYS}+ days):\n"
            + "\n".join(describe(*c) for c in newly_added)
        )
        notify(message)


if __name__ == "__main__":
    main()
