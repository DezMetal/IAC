#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
"""
SiteGen Media Injector
Reads scraped image data from the IAC results payload and injects
validated, deduplicated URLs into the project.json media map.

The AI never touches raw URLs — this script handles URL integrity.
Performs HEAD validation to remove dead/expired links.
Deduplicates by URL so repeat scrapes of the same photo don't bloat keys.
"""
import json
import os
import sys
import re
import urllib.request
import ssl


def validate_url(url, timeout=5):
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        return False
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, method="HEAD")
        req.add_header("User-Agent", "Mozilla/5.0")
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.status < 400
    except Exception:
        try:
            req = urllib.request.Request(url, method="GET")
            req.add_header("User-Agent", "Mozilla/5.0")
            req.add_header("Range", "bytes=0-0")
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return resp.status < 400
        except Exception:
            return "fbcdn.net" in url


def has_word(text, keywords):
    for w in keywords:
        pattern = r'\b' + re.escape(w) + r'\b'
        if re.search(pattern, text):
            return True
    return False


def classify(desc_lower):
    if has_word(desc_lower, ["kitchen", "sink", "cabinet", "stove", "counter", "fridge", "refrigerator", "dining"]):
        return "kitchen"
    if has_word(desc_lower, ["bath", "shower", "tub", "toilet", "bathroom", "restroom"]):
        return "bath"
    if has_word(desc_lower, ["door", "entry", "entrance"]):
        return "door"
    if has_word(desc_lower, ["window", "glass"]):
        return "window"
    if has_word(desc_lower, ["deck", "porch", "exterior", "house", "yard", "garden", "roof", "balcony", "patio"]):
        return "exterior"
    if has_word(desc_lower, ["car", "vehicle", "paint", "polish", "detail", "ceramic", "wheel", "rim", "wax", "buff", "coat", "interior", "seat", "dashboard"]):
        return "vehicle"
    return "general"


def inject_media(results_path, project_json_path, max_images=60):
    if not os.path.isfile(results_path):
        print(f"  [ERROR] Results file not found: {os.path.abspath(results_path)}")
        return {}

    if not os.path.isfile(project_json_path):
        print(f"  [ERROR] Project JSON not found: {os.path.abspath(project_json_path)}")
        return {}

    with open(results_path, "r", encoding="utf-8") as f:
        results = json.load(f)

    payload = results.get("final_payload", results.get("payload", results))
    print(f"  Payload keys: {len(payload)} total, searching for image_* entries...")

    with open(project_json_path, "r", encoding="utf-8") as f:
        project = json.load(f)

    # Collect image keys — handle both dict format {"src": "..."} and string format "http://..."
    raw_image_keys = []
    for k in payload:
        if not k.startswith("image_"):
            continue
        val = payload[k]
        if isinstance(val, dict) and "src" in val:
            raw_image_keys.append(k)
        elif isinstance(val, str) and (val.startswith("http://") or val.startswith("https://")):
            # Normalize string-only entries to dict format
            payload[k] = {"src": val, "desc": ""}
            raw_image_keys.append(k)

    image_keys = sorted(
        raw_image_keys,
        key=lambda x: int(x.split("_")[1]) if x.split("_")[1].isdigit() else 0
    )

    print(f"  Found {len(image_keys)} scraped images in payload")

    valid_media = {}
    descriptions = {}

    # Semantic categories — extensible
    all_cats = ["vehicle", "kitchen", "bath", "door", "window", "exterior", "general"]
    categorized = {cat: [] for cat in all_cats}

    seen_urls = set()
    validated_items = []

    for img_key in image_keys:
        src = payload[img_key]["src"]

        # Skip duplicate URLs — same photo appearing multiple times in FB grid
        if src in seen_urls:
            print(f"  [DUP]  {img_key} — duplicate URL, skipping")
            continue

        # Prefer AI caption (raw_output), fall back to FB alt text (desc)
        desc = payload[img_key].get("raw_output") or payload[img_key].get("desc", "")
        first_sentence = desc.split(".")[0] + "." if "." in desc else desc[:120]

        if validate_url(src):
            seen_urls.add(src)
            cat = classify(desc.lower())
            item = {"src": src, "desc": first_sentence.strip(), "orig_key": img_key, "cat": cat}
            categorized[cat].append(item)
            validated_items.append(item)
            print(f"  [OK]   {img_key} -> {cat.upper()} - {first_sentence[:60]}")
        else:
            print(f"  [DEAD] {img_key} - URL not accessible")

    print(f"  {len(validated_items)} unique, valid images after deduplication")

    if not validated_items:
        print("  [WARN] No valid images found. project.json media left empty.")
        project["media"] = {}
        with open(project_json_path, "w", encoding="utf-8") as f:
            json.dump(project, f, indent=2)
        return {}

    # --- Hero and Promo Banner ---
    # Pull from the best semantic category available
    hero_pref = ["vehicle", "exterior", "general", "window", "door", "kitchen", "bath"]
    hero_item = None
    for cat in hero_pref:
        if categorized[cat]:
            hero_item = categorized[cat].pop(0)
            break
    if hero_item:
        valid_media["hero_poster"] = hero_item["src"]
        descriptions["hero_poster"] = hero_item["desc"]
        print(f"  Mapped {hero_item['orig_key']} -> hero_poster")

    promo_item = None
    for cat in hero_pref:
        if categorized[cat]:
            promo_item = categorized[cat].pop(0)
            break
    if promo_item:
        valid_media["promo_banner"] = promo_item["src"]
        descriptions["promo_banner"] = promo_item["desc"]
        print(f"  Mapped {promo_item['orig_key']} -> promo_banner")

    # --- Semantic category keys (kitchen_N, bath_N, etc.) ---
    # Only assign a key if we ACTUALLY have that category of image
    for cat in all_cats:
        for idx, item in enumerate(categorized[cat], start=1):
            key_name = f"{cat}_{idx}"
            valid_media[key_name] = item["src"]
            descriptions[key_name] = item["desc"]
            print(f"  Mapped {item['orig_key']} -> {key_name} (semantic)")

    # --- Sequential photo_N keys for ALL validated images ---
    # These give the AI a clean, flat list of every unique image regardless of category.
    # This is the key mechanism ensuring ALL images end up used in the site.
    used_in_semantic = [hero_item, promo_item]
    photo_idx = 1
    for item in validated_items:
        if item is hero_item or item is promo_item:
            continue
        key_name = f"photo_{photo_idx}"
        valid_media[key_name] = item["src"]
        descriptions[key_name] = item["desc"]
        print(f"  Mapped {item['orig_key']} -> {key_name} (sequential)")
        photo_idx += 1

    project["media"] = valid_media

    if "data" not in project:
        project["data"] = {}
    project["data"]["descriptions"] = descriptions

    with open(project_json_path, "w", encoding="utf-8") as f:
        json.dump(project, f, indent=2)

    print(f"\n  [SUCCESS] Injected {len(valid_media)} media keys into project.json")
    print(f"    hero_poster + promo_banner + {photo_idx - 1} photo_N keys + semantic category keys")
    return valid_media


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 inject_media.py <results.json> <project.json> [max_images]")
        sys.exit(1)

    results_path = sys.argv[1]
    project_path = sys.argv[2]
    max_img = int(sys.argv[3]) if len(sys.argv) > 3 else 60

    try:
        result = inject_media(results_path, project_path, max_img)
        if not result:
            print("  [WARN] inject_media returned no media keys")
    except Exception as e:
        import traceback
        print(f"  [FATAL] inject_media.py crashed: {e}")
        traceback.print_exc()
        sys.exit(1)
