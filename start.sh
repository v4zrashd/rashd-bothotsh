#!/bin/bash

# Bind to the PORT environment variable provided by Render/Railway, defaulting to 8080
PORT=${PORT:-8080}

# Prevent bytecode (.pyc) files from being generated to close security leaks
export PYTHONDONTWRITEBYTECODE=1

echo "Applying filesystem security hardening..."
# Clean up any existing pycache files
find /app -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find /app -name "*.pyc" -exec rm -f {} + 2>/dev/null || true

# Create data directories if they do not exist
mkdir -p /app/data /app/data/bots /app/data/logs

# Set permissions: /app, /app/data, /app/data/bots are traversable but not listable by others
chmod 711 /app
chmod 711 /app/data
chmod 711 /app/data/bots
chmod 777 /app/data/logs

# Make manager files private (only readable by root owner)
chmod 600 /app/*.py
chmod 600 /app/requirements.txt
chmod 600 /app/Dockerfile
chmod 600 /app/start.sh

# Make templates and static folders private
chmod 700 /app/templates
chmod 700 /app/static

# Make database private if it exists
if [ -f /app/data/database.json ]; then
    chmod 600 /app/data/database.json
fi

echo "Starting Telegram Bot Host Manager on port $PORT..."
exec uvicorn main:app --host 0.0.0.0 --port "$PORT"
