#!/bin/sh
set -e

# Get port and host from environment variables, default to 8000 and 0.0.0.0
PORT=${SERVER_PORT:-8000}
HOST=${SERVER_HOST:-0.0.0.0}

# If arguments are passed, execute them (allows command override)
if [ $# -gt 0 ]; then
    exec "$@"
fi

echo "Starting Moyuren API on ${HOST}:${PORT}..."

# Execute the main command
exec uvicorn app.main:app --host "${HOST}" --port "${PORT}"
