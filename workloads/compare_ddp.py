#!/usr/bin/env python3
"""Check that two-rank training matches a single-device fixed-global-batch run."""

import argparse
import json
from pathlib import Path


def compare(reference: Path, distributed: Path) -> dict:
    import torch

    baseline = json.loads((reference / "rank-0.json").read_text())
    ranks = [json.loads((distributed / f"rank-{rank}.json").read_text()) for rank in range(2)]
    if baseline["world_size"] != 1 or baseline["profile"] not in {"cpu", "single"}:
        raise ValueError("Reference must be a single-device training run")
    for result in ranks:
        if result["profile"] != "ddp" or result["world_size"] != 2:
            raise ValueError("Comparison requires a real two-rank DDP run")
        if result["completed_step"] != baseline["completed_step"]:
            raise ValueError("Optimizer step counts must match")
        for key in ("global_batch", "sequence_length", "width", "layers", "heads", "vocabulary", "seed"):
            if baseline["spec"][key] != result["spec"][key]:
                raise ValueError(f"Mismatched workload input: {key}")
    expected = torch.load(reference / "rank-0-probe.pt", weights_only=True, map_location="cpu")
    actual = torch.cat([torch.load(distributed / f"rank-{rank}-probe.pt", weights_only=True, map_location="cpu")
                        for rank in range(2)])
    torch.testing.assert_close(actual, expected, rtol=1e-4, atol=1e-5)
    return {"passed": True, "comparison": "single-device vs two-rank fixed-global-batch training",
            "max_absolute_difference": float((actual - expected).abs().max()),
            "speedup": baseline["median_step_seconds"] / ranks[0]["median_step_seconds"],
            "note": "This one baseline is correctness evidence; a speedup claim also requires the faster single-GPU baseline and repeated measurements."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("distributed", type=Path)
    args = parser.parse_args()
    print(json.dumps(compare(args.reference, args.distributed)))
