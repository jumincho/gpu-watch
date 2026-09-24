#!/bin/sh
if [ -n "$GPU_WATCH_SSH_PASSWORD" ]; then
  printf '%s\n' "$GPU_WATCH_SSH_PASSWORD"
  exit 0
fi
exit 1
