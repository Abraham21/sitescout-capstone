#!/bin/sh
# Open the dashboard for this Mac login session.
set -eu
cd "$(dirname "$0")" || exit 1
url=http://127.0.0.1:8501
healthy() { [ "$(curl -fs --max-time 2 "$url/_stcore/health")" = ok ]; }

if ! healthy; then
    if [ ! -x .venv/bin/streamlit ]; then
        open -t README.md
        exit 1
    fi
    mkdir -p .runtime
    label=local.sitescout.capstone.dashboard
    job="gui/$(id -u)/$label"
    log="$PWD/.runtime/dashboard.log"
    if launchctl print "$job" >/dev/null 2>&1; then
        launchctl kickstart "$job"
    else
        launchctl submit -l "$label" -o "$log" -e "$log" -- /usr/bin/env "PATH=$PATH" \
            /bin/sh -c 'cd "$1" && exec .venv/bin/streamlit run app.py --server.port 8501' sitescout "$PWD" \
            || launchctl print "$job" >/dev/null
    fi
    for attempt in $(seq 1 20); do
        if healthy; then break; fi
        sleep 1
    done
    if ! healthy; then
        echo "SiteScout did not start. Check $log for the startup error." >&2
        exit 1
    fi
fi
exec open "$url"
