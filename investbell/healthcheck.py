"""Container health probe: exit 0 only when the dashboard answers /api/health.

A module rather than an inline command, so the Quadlet HealthCmd needs no
nested shell quoting.
"""

import sys
from urllib.request import urlopen


def main(url="http://127.0.0.1:8765/api/health"):
    try:
        with urlopen(url, timeout=3) as response:
            return 0 if response.status == 200 else 1
    except OSError:
        return 1


if __name__ == "__main__":
    sys.exit(main())
