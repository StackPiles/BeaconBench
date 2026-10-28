"""Export a preparation template without registering or executing anything."""
import argparse
from pathlib import Path

from beaconbench.adapters.babel import recipe
from beaconbench.util import write_once


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--family', required=True)
    parser.add_argument('--domain', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    write_once(args.output, recipe(args.family, args.domain))
    print('Prepared native recipe template; execution_ready=false; no models or commands started')


if __name__ == '__main__': main()
