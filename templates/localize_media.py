#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
"""
SiteGen Media Localizer
Downloads all remote media URLs from project.json to local assets/img/
and rewrites the paths to local references.
"""
import json
import os
import sys
import urllib.request
import ssl


def localize_media(project_json_path):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    with open(project_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    project_dir = os.path.dirname(project_json_path)
    img_dir = os.path.join(project_dir, "assets", "img")
    os.makedirs(img_dir, exist_ok=True)

    media = data.get("media", {})
    if not media:
        print("  No media keys found.")
        return

    for key, url in list(media.items()):
        if not isinstance(url, str) or not url.startswith("http"):
            continue

        ext = ".jpg"
        if ".png" in url.lower():
            ext = ".png"
        elif ".webp" in url.lower():
            ext = ".webp"

        dest = os.path.join(img_dir, f"{key}{ext}")
        try:
            urllib.request.urlretrieve(url, dest)
            media[key] = f"assets/img/{key}{ext}"
            size_kb = os.path.getsize(dest) / 1024
            print(f"  [OK] {key} ({size_kb:.0f} KB)")
        except Exception as e:
            print(f"  [WARN] {key}: {e}")

    with open(project_json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    print(f"  Media localized: {len(media)} keys processed")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 localize_media.py <path/to/project.json>")
        sys.exit(1)
    localize_media(sys.argv[1])
