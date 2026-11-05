"""``python -m server`` entry point."""

from __future__ import annotations

import sys


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    host = port = None
    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg == "--host" and args:
            host = args.pop(0)
        elif arg == "--port" and args:
            port = int(args.pop(0))
        elif arg.startswith("--host="):
            host = arg.split("=", 1)[1]
        elif arg.startswith("--port="):
            port = int(arg.split("=", 1)[1])

    from .app import run_server

    run_server(host=host, port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
