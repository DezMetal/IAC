#!/usr/bin/env python3
"""Atlas -- PRISM's map of a lab, rendered from `prism.survey`.

The walking lives in `prism_ops.survey_root`; this only renders it. The tool
ships with PRISM, but its output is documentation, so it is written next to the
docs rather than committed back into this repo.

    python atlas.py                     # survey ../.. , write to <root>/DNetLab
    python atlas.py --root D:/code      # survey somewhere else
    python atlas.py --out ./build       # put the artifacts elsewhere
    python atlas.py --json-only         # skip the HTML render
    python atlas.py --no-git            # skip git calls (much faster)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

try:
    from prism_ops import survey_root
except ImportError as exc:  # pragma: no cover - depends on checkout layout
    sys.exit(f"Cannot import prism_ops from {HERE.parent}: {exc}")

# Lab-specific, and deliberately not baked into prism.survey: scratch space and
# third-party checkouts parked in the lab root. `docs/` is NOT here -- burying a
# generic name like that in the shared op once hid real documentation.
# `docs` and `DNetLab` are documentation, `challenges` is practice work -- real
# content, but not projects on a map of what is being built. This is the right
# place for that call; burying it in the shared op is what hid guidance.txt.
NOT_PROJECTS = [
    "_archive", "scratch", "stuff", "recordings",
    "docs", "challenges", "DNetLab",
]


def parse_args(argv):
    p = argparse.ArgumentParser(description="Render a PRISM survey as a map.")
    p.add_argument("--root", type=Path, default=HERE.parent.parent,
                   help="Root holding the projects (default: two levels up).")
    p.add_argument("--out", type=Path, default=None,
                   help="Output directory (default: <root>/DNetLab).")
    p.add_argument("--json-only", action="store_true", help="Skip the HTML render.")
    p.add_argument("--no-git", action="store_true", help="Skip git state collection.")
    p.add_argument("--ignore", nargs="*", default=NOT_PROJECTS,
                   help="Top-level entries that are not projects.")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    if not root.is_dir():
        sys.exit(f"Not a directory: {root}")

    out_dir = (args.out or root / "DNetLab").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Surveying {root} ...", flush=True)
    atlas = survey_root(str(root), include_git=not args.no_git, ignore=args.ignore)

    (out_dir / "atlas.json").write_text(json.dumps(atlas, indent=2), encoding="utf-8")

    states: dict[str, int] = {}
    for p in atlas["projects"]:
        states[p["state"]] = states.get(p["state"], 0) + 1

    print(f"atlas.json -- {atlas['project_count']} projects, "
          f"{atlas['total_loc']:,} lines, {len(atlas['loose_files'])} loose files")
    print("             " + "  ".join(f"{k}:{v}" for k, v in sorted(states.items())))
    print(f"             -> {out_dir}")

    if args.json_only:
        return 0

    template = HERE / "atlas.template.html"
    if not template.is_file():
        print("atlas.template.html missing -- skipping render", file=sys.stderr)
        return 1

    html = template.read_text(encoding="utf-8").replace(
        "/*__ATLAS_DATA__*/null", json.dumps(atlas)
    )
    (out_dir / "atlas.html").write_text(html, encoding="utf-8")
    print("atlas.html rendered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
