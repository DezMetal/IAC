# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
import os
import re
import datetime

# Common project markers for structural DNA extraction
CORE_MARKERS = [
    r'class\s+(\w+)', 
    r'def\s+(\w+)',
    r'import\s+([\w\.]+)',
    r'from\s+([\w\.]+)\s+import',
    r'ADDON_MANIFEST\s*=',
    r'Blueprint\(',
    r'@\w+\.route\(',
    r'mcp\.tool\(',
    r'mcp\.resource\('
]

def register_prism_operations(registry):
    """Registers prism structural scanner operations as IAC native operations."""

    def make_prism_handler(action):
        def handler(args, context):
            if action != "scan":
                return {"status": "error", "error": f"Unknown action '{action}'"}

            raw_path = args.get('path', '.')
            workspace_dir = (context or {}).get("workspace_dir", None)
            
            if workspace_dir and not os.path.isabs(raw_path):
                root_path = os.path.abspath(os.path.join(workspace_dir, raw_path))
            else:
                root_path = os.path.abspath(raw_path)
            
            exclude_dirs = args.get('exclude', [])
            exclude_dirs += ['.git', 'node_modules', '__pycache__', 'venv', '.env', '.idea', '.vscode']
            custom_patterns = args.get('patterns', [])
            
            all_patterns = CORE_MARKERS + custom_patterns
            compiled_patterns = [re.compile(p) for p in all_patterns]

            prism_map = {
                "timestamp": datetime.datetime.now().isoformat(),
                "root": os.path.abspath(root_path),
                "structure": {},
                "dna": {
                    "logic_anchors": [],
                    "architectural_nuances": [],
                    "entry_points": []
                }
            }

            try:
                # Recursive walk
                for root, dirs, files in os.walk(root_path):
                    # Prune excluded directories
                    dirs[:] = [d for d in dirs if d not in exclude_dirs and not d.startswith('.')]
                    
                    rel_root = os.path.relpath(root, root_path)
                    if rel_root == '.':
                        rel_root = ''
                    
                    # Clean root key for JSON mapping
                    clean_rel_root = rel_root.replace('\\', '/')
                    
                    prism_map["structure"][clean_rel_root] = {
                        "dirs": dirs,
                        "files": []
                    }

                    for f in files:
                        if f.startswith('.'): continue
                        
                        f_path = os.path.join(root, f)
                        rel_f_path = os.path.relpath(f_path, root_path).replace('\\', '/')
                        
                        file_info = {
                            "name": f,
                            "size": os.path.getsize(f_path),
                            "ext": os.path.splitext(f)[1],
                            "symbols": []
                        }

                        if f.endswith(('.py', '.js', '.ts', '.html', '.md', '.json')):
                            try:
                                with open(f_path, 'r', encoding='utf-8', errors='ignore') as file_obj:
                                    content = file_obj.read(2000000) 
                                    
                                    if f in ['manifest.py', 'routes.py', 'models.py', 'app.py', 'run.py', 'main.py', 'mcp.py', 'mcp_core.py', 'GEMINI.md']:
                                        prism_map["dna"]["logic_anchors"].append(rel_f_path)
                                    
                                    if 'if __name__ == "__main__":' in content or 'app.run(' in content or 'FastMCP(' in content:
                                        prism_map["dna"]["entry_points"].append(rel_f_path)

                                    for pattern in compiled_patterns:
                                        matches = pattern.findall(content)
                                        if matches:
                                            for m in matches:
                                                if isinstance(m, tuple): m = m[0]
                                                if m and m not in file_info["symbols"]:
                                                    file_info["symbols"].append(m)
                                                    
                                                    if 'bifurcated' in m.lower() or 'speech_parameter' in m.lower():
                                                        if "Bifurcated output protocols" not in prism_map["dna"]["architectural_nuances"]:
                                                            prism_map["dna"]["architectural_nuances"].append("Bifurcated output protocols")

                            except Exception as e:
                                pass # Skip file error

                        prism_map["structure"][clean_rel_root]["files"].append(file_info)

                return {"status": "success", "data": prism_map, "message": f"Completed structural scan of {root_path}"}

            except Exception as e:
                return {"status": "error", "error": str(e)}

        return handler

    prism_schemas = {
        "scan": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Root path to scan."},
                "exclude": {"type": "array", "items": {"type": "string"}, "description": "List of dirs to exclude."},
                "patterns": {"type": "array", "items": {"type": "string"}, "description": "Custom regex patterns to extract."}
            },
            "required": ["path"]
        }
    }

    registry.register(
        name="scan",
        domain="prism",
        description="Recursive structural scan of a workspace dir.",
        parameters=prism_schemas["scan"],
        handler=make_prism_handler("scan")
    )
