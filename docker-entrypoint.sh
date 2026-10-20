#!/bin/sh
# Container entrypoint: make the data and plugins folders belong to the app user, then drop root privileges.
# Bind-mounted folders (./plugins, ./data) usually belong to root or to the host user, which would leave the server
# (user ID 1000) unable to install plugins or write its data. Doing this at every start makes a fresh setup work as is.
set -e
if [ "$(id -u)" = "0" ]; then
    for dir in /data /plugins; do
        [ -d "$dir" ] && chown -R app:app "$dir" 2>/dev/null || echo "docker-entrypoint: could not change the owner of $dir (continuing)" >&2
    done
    if [ "${MUSIC_ACCESS:-ro}" = "rw" ] && [ -d /music ]; then  # plugins keep songs in /music/<plugin>/: the folder itself must be writable (not recursive)
        chown app:app /music 2>/dev/null || echo "docker-entrypoint: could not change the owner of /music (continuing)" >&2
    fi
    export HOME=/home/app
    exec setpriv --reuid=1000 --regid=1000 --init-groups "$@"
fi
exec "$@"
