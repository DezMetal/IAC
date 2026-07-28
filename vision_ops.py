# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
import os
import time
import math
import time

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

def register_vision_operations(registry):
    """Registers vision/image processing commands as IAC operations."""
    if not HAS_PIL:
        print("[WARN] 'Pillow' is not installed. Vision operations (vision.*) are disabled.")
        return

    def make_vision_handler(op_name):
        def handler(args, context):
            payload = context.get("payload", {}) if context else {}
            
            try:
                if op_name == "overlay":
                    key = args.get("payload_key")
                    if not key or key not in payload or not isinstance(payload[key], dict):
                        return {"status": "error", "error": f"Invalid or missing payload_key: {key}"}
                        
                    src_path = payload[key].get("src")
                    if not src_path or not os.path.exists(src_path):
                        return {"status": "error", "error": f"Image file not found at src: {src_path}"}
                        
                    img = Image.open(src_path).convert("RGBA")
                    overlay = Image.new("RGBA", img.size, (255, 255, 255, 0))
                    draw = ImageDraw.Draw(overlay)
                    
                    overlay_type = args.get("type", "grid")
                    
                    if overlay_type == "grid":
                        width, height = img.size
                        raw_grid = args.get("grid_size")
                        if not raw_grid or raw_grid < 50:
                            grid_size = max(180, min(width, height) // 6)
                        else:
                            grid_size = raw_grid

                        color = args.get("color", "red")
                        
                        try:
                            # Try to get a default font, fallback to default internal PIL font
                            font = ImageFont.truetype("arial.ttf", max(14, grid_size // 5))
                        except Exception:
                            font = ImageFont.load_default()
                            
                        cell_num = 1
                        for y in range(0, height, grid_size):
                            for x in range(0, width, grid_size):
                                # Draw borders
                                draw.rectangle([x, y, x + grid_size, y + grid_size], outline=color, width=2)
                                
                                # Draw number with semi-transparent background box for legibility
                                text = str(cell_num)
                                draw.rectangle([x + 2, y + 2, x + 35, y + 22], fill=(0, 0, 0, 160))
                                draw.text((x + 5, y + 4), text, fill=(255, 255, 255, 255), font=font)
                                cell_num += 1
                                
                    elif overlay_type == "boxes":
                        # Draw arbitrary bounding boxes
                        boxes = args.get("boxes", [])
                        color = args.get("color", "red")
                        for box in boxes:
                            if isinstance(box, dict) and "x" in box and "y" in box and "w" in box and "h" in box:
                                x, y, w, h = box["x"], box["y"], box["w"], box["h"]
                                draw.rectangle([x, y, x + w, y + h], outline=color, width=2)
                                if "label" in box:
                                    draw.text((x, max(0, y - 15)), str(box["label"]), fill=color)
                                    
                    elif overlay_type == "points":
                        # Draw annotated points
                        points = args.get("points", [])
                        color = args.get("color", "red")
                        r = args.get("radius", 5)
                        for pt in points:
                            if isinstance(pt, dict) and "x" in pt and "y" in pt:
                                x, y = pt["x"], pt["y"]
                                draw.ellipse([x-r, y-r, x+r, y+r], fill=color)
                                if "label" in pt:
                                    draw.text((x + r + 2, y - r), str(pt["label"]), fill=color)

                    # Composite and save
                    final_img = Image.alpha_composite(img, overlay).convert("RGB")
                    
                    out_path = args.get("output_path")
                    if not out_path:
                        base, ext = os.path.splitext(src_path)
                        out_path = f"{base}_overlay_{int(time.time())}{ext}"
                        
                    final_img.save(out_path)
                    
                    # Store back to payload
                    payload[key]["src"] = out_path
                    # Also keep original just in case
                    payload[key]["original_src"] = src_path
                    
                    return {"status": "ok", "path": out_path}
                    
                elif op_name == "resolve_cells":
                    key = args.get("payload_key")
                    if not key or key not in payload or not isinstance(payload[key], dict):
                        return {"status": "error", "error": f"Invalid or missing payload_key: {key}"}
                        
                    src_path = payload[key].get("original_src") or payload[key].get("src")
                    if not src_path or not os.path.exists(src_path):
                        return {"status": "error", "error": f"Image file not found at src: {src_path}"}
                        
                    img = Image.open(src_path)
                    width, height = img.size
                    
                    grid_size = args.get("grid_size", 100)
                    cols = math.ceil(width / grid_size)
                    
                    cells = args.get("cells", [])
                    if args.get("start_cell") and args.get("end_cell"):
                        cells.extend([args["start_cell"], args["end_cell"]])
                        
                    if not cells:
                        return {"status": "error", "error": "No cells provided to resolve"}
                        
                    min_x, min_y = width, height
                    max_x, max_y = 0, 0
                    
                    for c in cells:
                        try:
                            idx = int(c) - 1
                        except (ValueError, TypeError):
                            continue
                        if idx < 0: continue
                        col = idx % cols
                        row = idx // cols
                        x1 = col * grid_size
                        y1 = row * grid_size
                        x2 = x1 + grid_size
                        y2 = y1 + grid_size
                        
                        min_x = min(min_x, x1)
                        min_y = min(min_y, y1)
                        max_x = max(max_x, min(x2, width))
                        max_y = max(max_y, min(y2, height))
                        
                    if min_x >= max_x or min_y >= max_y:
                        return {"status": "error", "error": "Invalid cell numbers or dimensions"}
                        
                    box = {
                        "x": min_x,
                        "y": min_y,
                        "w": max_x - min_x,
                        "h": max_y - min_y,
                        "center_x": min_x + (max_x - min_x) // 2,
                        "center_y": min_y + (max_y - min_y) // 2
                    }
                    
                    out_key = args.get("output_key", "resolved_box")
                    payload[out_key] = box
                    return {"status": "ok", "box": box, "key": out_key}
                
                return {"status": "error", "error": f"Unknown vision operation: {op_name}"}
                
            except Exception as e:
                return {"status": "error", "error": f"Vision op '{op_name}' failed: {str(e)}"}
                
        return handler

    vision_schemas = {
        "overlay": {
            "type": "object",
            "properties": {
                "payload_key": {"type": "string", "description": "Key of the image in payload to overlay on"},
                "type": {"type": "string", "description": "Type of overlay: grid, boxes, points", "default": "grid"},
                "grid_size": {"type": "integer", "description": "Size of each grid cell in pixels (for type=grid). Recommended: 200", "default": 200},
                "color": {"type": "string", "description": "Color of the overlay elements", "default": "red"},
                "output_path": {"type": "string", "description": "Optional custom path to save overlaid image"},
                "boxes": {"type": "array", "description": "List of box objects {x, y, w, h, label} for type=boxes"},
                "points": {"type": "array", "description": "List of point objects {x, y, label} for type=points"}
            },
            "required": ["payload_key"]
        },
        "resolve_cells": {
            "type": "object",
            "properties": {
                "payload_key": {"type": "string", "description": "Key of the original image in payload to get dimensions"},
                "grid_size": {"type": "integer", "description": "Size of the grid cells used", "default": 100},
                "cells": {"type": "array", "items": {"type": "integer"}, "description": "List of cell numbers to bound"},
                "start_cell": {"type": "integer", "description": "Alternative to 'cells': top-left cell number"},
                "end_cell": {"type": "integer", "description": "Alternative to 'cells': bottom-right cell number"},
                "output_key": {"type": "string", "description": "Payload key to store the resulting box object", "default": "resolved_box"}
            },
            "required": ["payload_key"]
        }
    }

    vision_ops = [
        ("overlay", "Apply visual overlays (numbered grids, boxes, points) to an image"),
        ("resolve_cells", "Convert grid cell numbers into a pixel bounding box")
    ]

    for op_name, desc in vision_ops:
        registry.register(
            name=op_name,
            domain="vision",
            description=desc,
            parameters=vision_schemas.get(op_name, {}),
            handler=make_vision_handler(op_name)
        )
