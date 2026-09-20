"""Compatibility launcher; maintained desktop source is in apps/desktop."""
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_root / "tools" / "src"))
sys.path.insert(0, str(_root / "apps" / "desktop" / "src"))

if __name__ == "__main__":
    from gomoku_desktop.app import main
    main()
