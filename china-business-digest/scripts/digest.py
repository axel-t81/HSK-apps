#!/usr/bin/env python3
"""Run the China Business Digest collector, preview, or email delivery."""
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from digestlib.cli import main


if __name__ == "__main__":
    main()
