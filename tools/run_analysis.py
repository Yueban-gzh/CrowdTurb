"""Run D's CLI from the repository without installing the package."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/analysis"))
from crowdturb_analysis.__main__ import main

if __name__ == "__main__":
    main()
