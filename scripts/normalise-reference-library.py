#!/usr/bin/env python3
"""
normalise-reference-library.py -- Normalise raw reference library corpus.

Takes the raw downloaded reference library and produces a curated, normalised
version with standard frontmatter, consistent naming, and deduplication.

Usage:
    python3 scripts/normalise-reference-library.py \
        --input /tmp/reference-library \
        --output /tmp/reference-library-normalised

    python3 scripts/normalise-reference-library.py \
        --input /tmp/reference-library \
        --output /tmp/reference-library-normalised \
        --dry-run
"""

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from kairix.reflib.normalise import NormaliseConfig, normalise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Normalise the kairix reference library corpus.",
    )
    parser.add_argument(
        "--input", required=True, type=Path,
        help="Path to raw reference library (e.g. /tmp/reference-library)",
    )
    parser.add_argument(
        "--output", required=True, type=Path,
        help="Path for normalised output (e.g. /tmp/reference-library-normalised)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would happen without writing files",
    )
    parser.add_argument(
        "--max-tier", type=int, default=3,
        help="Maximum licence tier to include (default: 3 = CC0/MIT/Apache/CC-BY)",
    )
    parser.add_argument(
        "--skip-dedup", action="store_true",
        help="Skip cross-collection deduplication",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if not args.input.is_dir():
        logging.error("Input directory does not exist: %s", args.input)
        return 1

    config = NormaliseConfig(
        input_dir=args.input,
        output_dir=args.output,
        max_tier=args.max_tier,
        dry_run=args.dry_run,
        dedup=not args.skip_dedup,
    )

    report = normalise(config)

    # Print report
    print()
    print("=" * 60)
    print("NORMALISATION REPORT")
    print("=" * 60)
    print(f"  Input files:          {report.total_input:>6}")
    print(f"  Output files:         {report.total_output:>6}")
    print(f"  Filtered boilerplate: {report.filtered_boilerplate:>6}")
    print(f"  Filtered licence:     {report.filtered_licence:>6}")
    print(f"  Filtered too small:   {report.filtered_too_small:>6}")
    print(f"  Split (large files):  {report.split_count:>6}")
    print(f"  Exact duplicates:     {report.exact_duplicates:>6}")
    print(f"  HTML cleaned:         {report.html_cleaned:>6}")
    print(f"  Frontmatter added:    {report.frontmatter_added:>6}")
    print(f"  Renamed:              {report.renamed:>6}")

    if report.unregistered_sources:
        print()
        print("  Unregistered sources (skipped):")
        for s in report.unregistered_sources:
            print(f"    - {s}")

    print()
    print("  Per-source output:")
    for source, count in sorted(report.collections.items()):
        print(f"    {source:50s} {count:>5} files")

    if config.dry_run:
        print()
        print("  [DRY RUN — no files written]")

    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
