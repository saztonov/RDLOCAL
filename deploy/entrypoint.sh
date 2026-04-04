#!/bin/bash
set -e

echo "=== Starting LM Studio daemon ==="
lms daemon up || echo "WARNING: lms daemon failed to start"

sleep 2

echo "=== Starting LM Studio server on port 1234 ==="
lms server start --port 1234 || echo "WARNING: lms server start failed"

echo "=== Starting supervisord (backend) ==="
exec supervisord -n -c /etc/supervisor/conf.d/supervisord.conf
