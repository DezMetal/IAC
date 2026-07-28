# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
from PIL import Image
import imagehash
from io import BytesIO

def get_dhash(image_bytes):
    img = Image.open(BytesIO(image_bytes))
    return imagehash.dhash(img)   # returns an ImageHash object

# Usage
seen = set()   # stores the hashes

def is_duplicate(image_bytes, seen_hashes, threshold=5):
    current = get_dhash(image_bytes)
    
    # Quick exact check first
    if current in seen_hashes:
        return True, current
    
    # Then check near-matches
    for stored in list(seen_hashes):
        if current - stored < threshold:
            return True, current
    
    seen_hashes.add(current)
    return False, current

