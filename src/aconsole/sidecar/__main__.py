"""`python -m aconsole.sidecar -- COMMAND [ARG...]`."""

import sys

from aconsole.sidecar.main import main

raise SystemExit(main(sys.argv[1:]))
