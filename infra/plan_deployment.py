#!/usr/bin/env python3
"""Print a blocked deployment review. No network, installation or file writes."""

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from infra.catalog import COMPONENTS, deployment_review

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component", choices=tuple(COMPONENTS), required=True)
    args = parser.parse_args()
    print(json.dumps(deployment_review(args.component), indent=2))
