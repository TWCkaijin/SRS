#!/usr/bin/env python3
"""Compare fresh-process checkpoint continuation with an uninterrupted run."""

import argparse
import json
from pathlib import Path


def compare(reference: Path, resumed: Path, ranks: int = 1) -> dict:
    import torch

    results = []
    for rank in range(ranks):
        left = json.loads((reference / f"rank-{rank}.json").read_text())
        right = json.loads((resumed / f"rank-{rank}.json").read_text())
        if left["completed_step"] != right["completed_step"] or right["start_step"] <= 0:
            raise ValueError("Compare a real resumed run at the same final optimizer step")
        for field in ("profile", "world_size", "torch_version", "device"):
            if left[field] != right[field]:
                raise ValueError(f"Mismatched {field}")
        for key in ("global_batch", "sequence_length", "width", "layers", "heads", "vocabulary", "seed"):
            if left["spec"][key] != right["spec"][key]:
                raise ValueError(f"Mismatched workload input: {key}")
        expected = torch.load(reference / f"rank-{rank}-probe.pt", weights_only=True, map_location="cpu")
        actual = torch.load(resumed / f"rank-{rank}-probe.pt", weights_only=True, map_location="cpu")
        torch.testing.assert_close(actual, expected, rtol=1e-4, atol=1e-5)
        results.append({"rank": rank, "max_absolute_difference": float((actual - expected).abs().max())})
    return {"passed": True, "comparison": "post-resume evaluation logits", "ranks": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("resumed", type=Path)
    parser.add_argument("--ranks", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    print(json.dumps(compare(args.reference, args.resumed, args.ranks)))
