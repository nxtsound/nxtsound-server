#!/bin/sh
# Container entrypoint: make the data and plugins folders belong to the app user, then drop root privileges.
# Bind-mounted folders (./plugins, ./data) usually belong to root or to the host user, which would leave the server
# (user ID 1000) unable to install plugins or write its data. Doing this at every start makes a fresh setup work as is.
set -e
if [ "$(id -u)" = "0" ]; then
    as_app() { setpriv --reuid=1000 --regid=1000 --init-groups "$@"; }
    for dir in /data /plugins; do
        [ -d "$dir" ] && chown -R app:app "$dir" 2>/dev/null || echo "docker-entrypoint: could not change the owner of $dir (continuing)" >&2
    done
    if [ -d /backups ]; then  # the folder for backups to "a folder on the server": only the folder itself (an NFS share may not allow more)
        chown app:app /backups 2>/dev/null || echo "docker-entrypoint: could not change the owner of /backups (continuing)" >&2
    fi
    if [ "${MUSIC_ACCESS:-ro}" = "rw" ] && [ -d /music ]; then  # plugins keep songs in /music/<plugin>/: the folder itself must be writable (not recursive)
        chown app:app /music 2>/dev/null || echo "docker-entrypoint: could not change the owner of /music (continuing)" >&2
    fi
    export HOME=/home/app
else
    as_app() { "$@"; }  # already running as the app user: no root to drop
fi

# DJ_STEMS=1 (vocal separation, see stems.py) and BEAT_ML=1 (beat_this model, see bpm.py) both need PyTorch (CPU), which is not
# baked into the image (about 0.7 GB) so that people who want neither pay nothing for it. The first start with either one on
# installs what it needs into the data volume instead (needs internet access once; PyTorch is shared between the two so it is
# only fetched once); later starts find it there and skip straight past this. Turning both back off removes it again.
ML_DIR=/data/ml-packages
MODEL_DIR="${DJ_STEMS_DIR:-/data/models}"
if [ "${DJ_STEMS:-0}" = "1" ] || [ "${BEAT_ML:-0}" = "1" ]; then
    mkdir -p "$ML_DIR"
    [ "$(id -u)" = "0" ] && chown -R app:app "$ML_DIR"
    export PYTHONPATH="$ML_DIR${PYTHONPATH:+:$PYTHONPATH}"
    if ! as_app python3 -c "import torch" >/dev/null 2>&1; then
        echo "docker-entrypoint: installing PyTorch into $ML_DIR (first start only, about 0.7 GB, a few minutes)..."
        as_app pip install --no-cache-dir --target "$ML_DIR" torch torchaudio --index-url https://download.pytorch.org/whl/cpu
    fi
    if [ "${DJ_STEMS:-0}" = "1" ] && ! as_app python3 -c "import demucs" >/dev/null 2>&1; then
        echo "docker-entrypoint: DJ_STEMS=1, installing demucs into $ML_DIR..."
        as_app pip install --no-cache-dir --target "$ML_DIR" demucs
        as_app env TORCH_HOME="$MODEL_DIR" python3 -c "from demucs.pretrained import get_model; get_model('${DJ_STEMS_MODEL:-htdemucs}')"
    fi
    if [ "${BEAT_ML:-0}" = "1" ] && ! as_app python3 -c "import beat_this" >/dev/null 2>&1; then
        echo "docker-entrypoint: BEAT_ML=1, installing beat_this into $ML_DIR..."
        as_app pip install --no-cache-dir --target "$ML_DIR" beat_this
    fi
elif [ -d "$ML_DIR" ]; then
    echo "docker-entrypoint: DJ_STEMS and BEAT_ML are both off, removing $ML_DIR (frees the PyTorch install)"
    rm -rf "$ML_DIR"
fi
# The demucs model follows DJ_STEMS on its own: BEAT_ML does not need it, so it goes even if BEAT_ML keeps $ML_DIR around.
if [ "${DJ_STEMS:-0}" != "1" ] && [ -d "$MODEL_DIR" ]; then
    echo "docker-entrypoint: DJ_STEMS is off, removing the downloaded model at $MODEL_DIR"
    rm -rf "$MODEL_DIR"
fi

if [ "$(id -u)" = "0" ]; then
    exec setpriv --reuid=1000 --regid=1000 --init-groups "$@"
fi
exec "$@"
