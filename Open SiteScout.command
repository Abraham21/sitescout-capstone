#!/bin/sh
# Double-click in Finder to open the prepared operator desk.
cd "$(dirname "$0")" || exit 1
url=http://127.0.0.1:8501
if curl --fail --silent --max-time 2 "$url/_stcore/health" >/dev/null; then
    open "$url"
    exit
fi
if [ ! -x .venv/bin/streamlit ]; then
    open -t README.md
    exit 1
fi
(
    for attempt in 1 2 3 4 5 6 7 8 9 10; do
        if curl --fail --silent --max-time 1 "$url/_stcore/health" >/dev/null; then
            open "$url"
            exit
        fi
        sleep 1
    done
) &
exec .venv/bin/streamlit run app.py --server.port 8501
