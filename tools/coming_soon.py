from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "This app"
    suite_root = Path(__file__).resolve().parents[1]
    print(f"{name} is not implemented yet in {suite_root}.")
    print("Use the suite launcher or another available app profile for now.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
