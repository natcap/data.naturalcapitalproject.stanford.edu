#!/usr/bin/env sh

# Port must match what's in the nginx config
/usr/sbin/gcsproxy -b 127.0.0.1:8989 &

exec nginx -g "daemon off;"
