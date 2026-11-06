#!/bin/sh
# Copy stdin to aconsole in the background, then print the user's statusline.
copy=$(mktemp) || exit 0
tee "$copy" >/dev/null || { rm -f "$copy"; exit 0; }
printf "Authorization: Bearer %s\n" "$ACONSOLE_TAB_TOKEN" | curl -sS -m 2 -H @- --data-binary @"$copy" "http://127.0.0.1:${ACONSOLE_PORT}/v1/tabs/${ACONSOLE_TAB}/claude/statusline" >/dev/null 2>&1 &
curl_pid=$!
if [ -n "${ACONSOLE_USER_STATUSLINE:-}" ] && [ -x "${ACONSOLE_USER_STATUSLINE}" ]; then
  "${ACONSOLE_USER_STATUSLINE}" < "$copy"
fi
wait "$curl_pid" || true
rm -f "$copy"
exit 0
