#!/bin/bash
# Make sure cron is running
randpass MND | grep pass: | cut -f2 -d: | sed 's/\s//g' > /usr/enable_mkuser
echo "STARTUP CODE:$(cat /usr/enable_mkuser)"

/etc/init.d/cron start >/dev/null 2>&1
# `cron` is the daemon binary and takes no file argument at all -- it was
# silently ignoring /root/crontab.txt here, so nothing in that file (this
# job included) has actually been running. `crontab` is the command that
# installs a crontab for the already-running daemon to pick up. Installed
# under notify (not root) so the nightly jobs run with minimal privilege;
# the couple of things that genuinely need root go through the narrow sudo
# rules in notify.sudoers instead.
crontab -u notify /root/crontab.txt
cd /

python3 /usr/local/bin/make_users.py
if [ -r /home/shadow ]
then
    cp /home/shadow /etc/shadow
fi

# home_fs already has real content by now, so useradd -m in cilogon.docker
# won't have populated this on its own -- ensure it every boot instead.
mkdir -p /home/notify
chown notify:notify /home/notify

# /home itself has whatever ownership/mode it accumulated over the years
# (root:root, historically). notify (running the nightly cleanup jobs) needs
# to create/remove entries directly under it -- pending_deletion.txt on
# first flag, or rmtree'ing an orphaned home dir with no Linux account left.
# The setgid bit makes new entries inherit the notify group automatically.
chgrp notify /home
chmod g+ws /home

jupyterhub --ip 0.0.0.0 --port 443 -f jup-config.py 2>&1 | tee /var/log/jup-log.txt
