#!/usr/bin/env python3
# Copyright (c) 2019 Steven R. Brandt, and Roland Haas
#
# Distributed under the LGPL
#
# Run manually, e.g.:
#   docker exec et-juphub /usr/local/bin/delete_flagged_users.py
# to actually remove ONE account from pending_deletion.txt -- the first
# entry, which is the most stale one (flag_stale_users.py writes them
# oldest-first). Run it again to work through the rest, one at a time,
# rather than bulk-deleting everything pending in one shot. Deletes the
# Linux user + home directory, and strips the user from name_map.txt and
# user_registry.txt. This cannot touch the external tutorials-whitelist.txt
# (that's maintained outside this server), so it prints the whitelist hash
# for the deleted user's email for manual removal there.
import base64
import hashlib
import os
import pwd
import re
import shutil
import subprocess

PENDING = "/home/pending_deletion.txt"
NAME_MAP = "/home/name_map.txt"
REGISTRY = "/home/user_registry.txt"


def codeme(m):
    m = m.lower()
    if isinstance(m, str):
        m = m.encode()
    h = hashlib.md5(m)
    v = base64.b64encode(h.digest())
    s = re.sub(r'[\+/]', '_', v.decode())
    return s[:-2]


def strip_username(path, username):
    if not os.path.exists(path):
        return
    with open(path) as fd:
        lines = fd.readlines()
    kept = [l for l in lines if not l.startswith(username + ":")]
    with open(path, "w") as fd:
        fd.writelines(kept)
    os.chmod(path, 0o0660)


def delete_account(username):
    home = "/home/" + username
    try:
        pwd.getpwnam(username)
    except KeyError:
        if os.path.isdir(home):
            shutil.rmtree(home)
        return
    # This script runs as notify (see startup.sh), not root -- userdel
    # needs the narrow sudo rule in notify.sudoers. Still works fine if
    # invoked as root directly (e.g. a manual `docker exec`): sudo as root
    # just runs it, no escalation needed.
    subprocess.run(["sudo", "userdel", "-r", username], check=True)


def main():
    if not os.path.exists(PENDING):
        print("Nothing pending")
        return

    with open(PENDING) as fd:
        lines = [l.strip() for l in fd if l.strip()]

    if not lines:
        print("Nothing pending")
        return

    # Process exactly one entry per run -- the first, which is the most
    # stale (flag_stale_users.py writes them oldest-first).
    line, remaining = lines[0], lines[1:]
    parts = line.split(":", 2)
    if len(parts) != 3:
        print(f"Skipping malformed line: {line}")
        with open(PENDING, "w") as fd:
            for l in remaining:
                fd.write(l + "\n")
        os.chmod(PENDING, 0o0660)
        return

    username, email, last_login_iso = parts
    try:
        delete_account(username)
        strip_username(NAME_MAP, username)
        strip_username(REGISTRY, username)
        print(f"Deleted {username} ({email or 'no email on file'})")
    except Exception as e:
        print(f"FAILED to delete {username}: {e}")
        return  # leave pending_deletion.txt untouched, retry next time

    with open(PENDING, "w") as fd:
        for l in remaining:
            fd.write(l + "\n")
    os.chmod(PENDING, 0o0660)

    if email:
        print("\nRemove this hash from tutorials-whitelist.txt:")
        print(f"{codeme(email)}  # {username} <{email}>")
    else:
        print(
            f"\nNo email on file for {username} -- check "
            "tutorials-whitelist.txt manually if needed"
        )


if __name__ == "__main__":
    main()
