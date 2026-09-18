#!/usr/bin/env python3
"""Entry point for running the agent without installing the package.

    python run_server.py                 # stdio, for Claude Code / Claude Desktop
    python run_server.py --transport http --port 8765   # remote custom connector
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sourcing1688.mcp_server import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
