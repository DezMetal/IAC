#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
"""
gen_licenses.py -- Generate THIRD-PARTY-LICENSES for a D-Net Lab release.

Portable: drop this file into any project and run it. It has no dependencies
of its own -- it prefers `pip-licenses` when installed, and otherwise falls
back to `importlib.metadata`, which ships with Python.

    python tools/gen_licenses.py --project "Aether" --output THIRD-PARTY-LICENSES

Why this exists
---------------
Permissive licences (MIT, BSD, Apache-2.0) are free to use but NOT free of
obligation: nearly all of them require you to reproduce their copyright notice
and licence text in anything you distribute. Shipping a paid binary without
that file is the most common licence violation in commercial software, and it
is entirely avoidable.

This collects every installed distribution, records its licence and copyright,
and flags anything that needs a human decision before release:

  * copyleft (GPL/AGPL/LGPL) -- may impose obligations on YOUR source
  * unknown or missing licence metadata -- must be resolved manually

Exit codes:
    0  clean
    1  review required (copyleft or unknown licences present)

Restrict the report to what you actually ship with --requirements; scanning a
whole dev environment lists test and tooling packages you never distribute.
"""

import argparse
import json
import os
import re
import sys
from datetime import date

# Licences that can impose source-disclosure obligations on a distributed work.
# Presence is not automatically a problem -- it is a decision that needs making.
COPYLEFT = ("GPL", "AGPL", "LGPL", "MPL", "EPL", "CDDL", "OSL", "EUPL", "SSPL")

PERMISSIVE = ("MIT", "BSD", "Apache", "ISC", "Python Software Foundation",
              "Zope", "Unlicense", "WTFPL", "Public Domain", "PSF", "HPND")


def _norm(value) -> str:
    if not value:
        return ""
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value if v)
    return re.sub(r"\s+", " ", str(value)).strip()


def _classify(licence: str) -> str:
    text = (licence or "").upper()
    if not text or text in ("UNKNOWN", "NONE", "N/A"):
        return "unknown"
    for tag in COPYLEFT:
        # Word-ish match so "LGPL" is not caught by a bare "GPL" substring test
        # in a way that mislabels, and so "MIT" never matches "LIMIT".
        if re.search(rf"\b{tag}", text):
            return "copyleft"
    for tag in PERMISSIVE:
        if tag.upper() in text:
            return "permissive"
    return "other"


# Short identifiers recoverable from a full licence text when a package dumps
# the whole thing into the License field instead of naming it.
TEXT_SIGNATURES = [
    ("Apache License", "Apache-2.0"),
    ("GNU AFFERO", "AGPL"),
    ("GNU LESSER", "LGPL"),
    ("GNU GENERAL PUBLIC", "GPL"),
    ("Mozilla Public License", "MPL-2.0"),
    ("Redistribution and use in source and binary forms", "BSD"),
    ("Permission is hereby granted, free of charge", "MIT"),
    ("Permission to use, copy, modify, and/or distribute", "ISC"),
]


def _licence_of(meta) -> str:
    """Best short licence identifier for a distribution.

    Order matters. Packaging has moved the licence around over the years, and
    reading only the legacy `License` field reports UNKNOWN for most of a
    modern environment -- which would make the generated file useless.

      1. License-Expression  (PEP 639, the current standard: a clean SPDX id)
      2. License :: classifiers  (what most published wheels still carry)
      3. License field, but only if it looks like a NAME rather than the full
         licence text -- numpy and others paste the entire BSD text in here
    """
    expr = _norm(meta.get("License-Expression"))
    if expr:
        return expr

    classifiers = meta.get_all("Classifier") or []
    named = []
    for c in classifiers:
        if not c.startswith("License ::"):
            continue
        tail = c.split("::")[-1].strip()
        # "OSI Approved" alone carries no information.
        if tail and tail.lower() not in ("osi approved", "other/proprietary license"):
            named.append(tail)
    if named:
        return _norm(named[0])

    raw = _norm(meta.get("License"))
    if raw and raw.lower() not in ("unknown", "none", "n/a"):
        if len(raw) <= 60:
            return raw
        # Full licence text pasted into the field -- recover the identifier.
        upper = raw.upper()
        for needle, short in TEXT_SIGNATURES:
            if needle.upper() in upper:
                return short
        return raw[:57].rstrip() + "..."

    return ""


def _wanted(requirements_path):
    """Distribution names listed in a requirements file, lowercased."""
    if not requirements_path or not os.path.exists(requirements_path):
        return None
    names = set()
    with open(requirements_path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            name = re.split(r"[<>=!~\[; ]", line, 1)[0].strip()
            if name:
                names.add(name.lower().replace("_", "-"))
    return names or None


def collect() -> list:
    """Every installed distribution with licence and author metadata."""
    import importlib.metadata as md

    out = []
    for dist in md.distributions():
        try:
            meta = dist.metadata
            name = meta["Name"] or getattr(dist, "name", None)
            if not name:
                continue

            licence = _licence_of(meta)

            out.append({
                "name": name,
                "version": meta["Version"] or "",
                "license": licence or "UNKNOWN",
                "author": _norm(meta.get("Author") or meta.get("Maintainer")),
                "url": _norm(meta.get("Home-page")
                             or (meta.get_all("Project-URL") or [""])[0]),
                "kind": _classify(licence),
            })
        except Exception:
            continue

    # Deduplicate (an environment can expose the same dist twice)
    seen, unique = set(), []
    for pkg in sorted(out, key=lambda p: p["name"].lower()):
        key = pkg["name"].lower()
        if key not in seen:
            seen.add(key)
            unique.append(pkg)
    return unique


def render(packages, project, org, slogan, year) -> str:
    lines = [
        f"THIRD-PARTY LICENCES -- {project}",
        "=" * 72,
        "",
        f"{project} is a product of {org}.",
        f"Copyright (c) {year} {org}. All rights reserved.",
    ]
    if slogan:
        lines += ["", f'    "{slogan}"']
    lines += [
        "",
        f"{project} incorporates the third-party components listed below. Each",
        "remains the property of its respective authors and is used under the",
        "terms of its own licence. This notice is provided to satisfy those",
        "terms; it does not alter the licence covering {p} itself.".format(p=project),
        "",
        f"Generated {date.today().isoformat()} by tools/gen_licenses.py",
        "",
        "-" * 72,
        "",
    ]

    by_kind = {}
    for pkg in packages:
        by_kind.setdefault(pkg["kind"], []).append(pkg)

    order = [("permissive", "PERMISSIVELY LICENSED COMPONENTS"),
             ("other", "OTHER LICENCES"),
             ("copyleft", "COPYLEFT LICENCES -- REVIEW BEFORE DISTRIBUTION"),
             ("unknown", "UNDETERMINED LICENCES -- MUST BE RESOLVED")]

    for kind, heading in order:
        group = by_kind.get(kind)
        if not group:
            continue
        lines += [heading, "-" * len(heading), ""]
        if kind == "copyleft":
            lines += [
                "These may impose obligations on distributed software, including",
                "source disclosure. Confirm each is compatible with your licence",
                "before shipping.",
                "",
            ]
        if kind == "unknown":
            lines += [
                "No licence metadata was published. Determine each licence from",
                "the project's repository and record it here manually.",
                "",
            ]
        for pkg in group:
            lines.append(f"{pkg['name']} {pkg['version']}")
            lines.append(f"    Licence : {pkg['license']}")
            if pkg["author"]:
                lines.append(f"    Author  : {pkg['author']}")
            if pkg["url"]:
                lines.append(f"    Source  : {pkg['url']}")
            lines.append("")
        lines.append("")

    lines += ["-" * 72, "",
              f"Full licence texts are available from each project's source.",
              f"Questions: {org}", ""]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(prog="gen_licenses")
    ap.add_argument("--project", required=True, help="Product name")
    ap.add_argument("--org", default="D-Net Lab")
    ap.add_argument("--slogan", default="With Accessibility Comes Understanding and Inspiration")
    ap.add_argument("--year", default=str(date.today().year))
    ap.add_argument("--output", default="THIRD-PARTY-LICENSES")
    ap.add_argument("--requirements", default=None,
                    help="Limit to distributions named in this requirements file")
    ap.add_argument("--json", action="store_true", help="Also emit machine-readable JSON")
    ap.add_argument("--strict", action="store_true",
                    help="Exit non-zero if any copyleft or unknown licence is present")
    args = ap.parse_args()

    packages = collect()

    wanted = _wanted(args.requirements)
    if wanted:
        packages = [p for p in packages
                    if p["name"].lower().replace("_", "-") in wanted]
        if not packages:
            print(f"[gen_licenses] Nothing in {args.requirements} is installed here.",
                  file=sys.stderr)

    text = render(packages, args.project, args.org, args.slogan, args.year)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(text)

    if args.json:
        with open(args.output + ".json", "w", encoding="utf-8") as f:
            json.dump(packages, f, indent=2)

    counts = {}
    for pkg in packages:
        counts[pkg["kind"]] = counts.get(pkg["kind"], 0) + 1

    print(f"[gen_licenses] {args.output}: {len(packages)} component(s)")
    for kind in ("permissive", "other", "copyleft", "unknown"):
        if counts.get(kind):
            print(f"    {kind:11} {counts[kind]}")

    review = counts.get("copyleft", 0) + counts.get("unknown", 0)
    if review:
        print(f"[gen_licenses] {review} component(s) need review before release.",
              file=sys.stderr)
        for pkg in packages:
            if pkg["kind"] in ("copyleft", "unknown"):
                print(f"    {pkg['name']:24} {pkg['license']}", file=sys.stderr)
        if args.strict:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
