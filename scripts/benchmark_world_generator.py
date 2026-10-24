"""Reproduce measurements of the unchanged, trusted 34d2300 generator."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scene_rsi.world.generator_benchmark import run

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--samples', type=int, default=32)
    args = parser.parse_args()
    run(args.out, args.samples)
