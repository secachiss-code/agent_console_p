#!/bin/sh
# Spike statusline: forward stdin to the recorder and print a visible line.
input=$(cat)
printf '%s' "$input" | curl -sS -m 2 -o /dev/null -X POST \
  -H 'Content-Type: application/json' \
  --data-binary @- \
  http://127.0.0.1:8765/v1/tabs/tab1/claude/statusline || true
printf 'AConsole\n'
