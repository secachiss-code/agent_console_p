"""One-shot database preparation, before the daemon opens any connection pool."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from aconsole.core.infrastructure.persistence.startup import prepare_database


class _Arguments(argparse.Namespace):
    path: Path = Path()
    now: datetime = datetime.min.replace(tzinfo=UTC)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("path", type=Path)
    _ = parser.add_argument("now", type=datetime.fromisoformat)
    args = parser.parse_args(namespace=_Arguments())
    saved = prepare_database(args.path, now=args.now)
    print(json.dumps(None if saved is None else str(saved)))  # noqa: T201 -- private parent/worker protocol


if __name__ == "__main__":
    main()
