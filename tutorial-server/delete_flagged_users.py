#!/usr/bin/env python3
# Copyright (c) 2019 Steven R. Brandt, and Roland Haas
#
# Distributed under the LGPL
#
# Run manually, e.g.:
#   docker exec et-juphub /usr/local/bin/delete_flagged_users.py
# to actually remove the accounts flag_stale_users.py flagged. Deletes the
# Linux user + home directory, and strips the user from name_map.txt and
# user_registry.txt. This cannot touch the external tutorials-whitelist.txt
# (that's maintained outside this server), so it prints the whitelist hash
# for each deleted user's email for manual removal there.
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
    os.chmod(path, 0o0600)


def delete_account(username):
    home = "/home/" + username
    try:
        pwd.getpwnam(username)
    except KeyError:
        if os.path.isdir(home):
            shutil.rmtree(home)
        return
    subprocess.run(["userdel", "-r", username], check=True)


def main():
    if not os.path.exists(PENDING):
        print("Nothing pending")
        return

    with open(PENDING) as fd:
        lines = [l.strip() for l in fd if l.strip()]

    remaining = []
    report = []
    for line in lines:
        parts = line.split(":", 2)
        if len(parts) != 3:
            print(f"Skipping malformed line: {line}")
            continue
        username, email, last_login_iso = parts
        try:
            delete_account(username)
            strip_username(NAME_MAP, username)
            strip_username(REGISTRY, username)
            report.append((username, email, codeme(email) if email else None))
            print(f"Deleted {username} ({email or 'no email on file'})")
        except Exception as e:
            print(f"FAILED to delete {username}: {e}")
            remaining.append(line)

    with open(PENDING, "w") as fd:
        for line in remaining:
            fd.write(line + "\n")
    os.chmod(PENDING, 0o0600)

    if report:
        print("\nRemove these hashes from tutorials-whitelist.txt:")
        for username, email, h in report:
            if h:
                print(f"{h}  # {username} <{email}>")
            else:
                print(
                    f"(no email on file, can't compute hash)  # {username} "
                    "-- check tutorials-whitelist.txt manually if needed"
                )


if __name__ == "__main__":
    main()
