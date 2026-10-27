#!/bin/sh
# Fixture diff driver. A correct reader never executes this.
if [ -n "${ACONSOLE_DIFF_DRIVER_RAN:-}" ]; then
  printf ran >> "$ACONSOLE_DIFF_DRIVER_RAN"
fi
exit 0
