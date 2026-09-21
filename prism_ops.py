# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
import os
import re
import json
import time
import subprocess
import sys
import datetime

def _no_window():
    """On Windows a console child of a windowless parent gets a console window
    of its own. Nothing run here is meant to be seen; its output is captured."""
    if sys.platform != "win32":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": si}


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

# ---------------------------------------------------------------------------
# Survey -- the altitude above `scan`.
#
# `scan` answers "what is inside this project". `survey` answers "what projects
# are in this root, and which of them are still alive". Same walk, wider lens.
# ---------------------------------------------------------------------------

# Never the project itself: dependencies, caches, build output.
SURVEY_SKIP_DIRS = {
    'node_modules', '.git', '__pycache__', '.venv', 'venv', 'env',
    'dist', 'build', '.next', '.nuxt', '.pytest_cache', '.mypy_cache',
    '.ruff_cache', '.idea', '.vscode', 'site-packages', '.ipynb_checkpoints',
    '.netlify', 'rotation_cache', '.cache', 'coverage', 'htmlcov', '.tox',
    'dist-release', '.gradle', 'target', 'vendor',
    # Vendored upstream source. Counting it makes a 300-line tool look like a
    # 2M-line one, which is how Choir came to outrank the entire rest of the lab.
    'third_party', 'thirdparty', 'external', 'deps', 'llama.cpp', 'ggml',
    'Lib', 'Scripts', 'site-packages', 'wheels', 'models',
}

# House standards. If a project matches one of these, it is wired into the
# ecosystem rather than reinventing it -- this is the adoption evidence.
HOUSE_STANDARDS = {
    'IAC': (
        'The structured payload protocol every node speaks.',
        ('from IAC', 'import IAC', 'iac.py', 'IACPayload', 'data_payload',
         'registry.register(', 'from .iac', 'IAC/runner'),
    ),
    'DSS': (
        'The signature stylesheet.',
        ('dss.css', 'dss-card', 'dss-bg-grid', 'data-dss-preset'),
    ),
    'DNLAAS': (
        'Agent autonomy: boundary, ledger, loop, AIM.',
        ('DNLAAS', 'dnlaas', 'boundary.check', 'from boundary import'),
    ),
    'StreamAudio': (
        'Dynamic TTS and voice conversion.',
        ('StreamAudio', 'stream_audio', 'from streamaudio'),
    ),
    'PRISM': (
        'Structural scan and lab survey.',
        ('prism.scan', 'prism.survey', 'prism_ops', 'prism_wrapper'),
    ),
    # Ghost ships as an addon inside DNet rather than as its own top-level
    # project, which is exactly why the first survey missed it. A standard is
    # not defined by where it sits in the tree.
    'Ghost': (
        'Persistent identity and memory across cyberspace.',
        ('ghost_bridge', 'GhostBridge', 'ghost/api/', 'ghost_profile',
         'GhostProfile', 'ghost.imprint', 'ghost:chat', 'ghost_jobs'),
    ),
    'Aether': (
        'Pipelines, triggers, the voice workspace.',
        ('Aether', 'aether_', '.aether/', 'AETHER_'),
    ),
    'SiteGen': (
        'The SPA site builder.',
        ('SiteGen', 'sitegen_auto', 'gallery-loader', 'content-rotator'),
    ),
}

# Top-level entries that are tooling around the lab, not projects in it.
SURVEY_SKIP_TOP = {
    '.git', '.claude', '.gemini', '.vscode', '.idea', '.agent',
    '__pycache__', '.ipynb_checkpoints', 'instance', 'rotation_cache',
}

LANG_BY_EXT = {
    '.py': 'Python', '.js': 'JavaScript', '.mjs': 'JavaScript',
    '.cjs': 'JavaScript', '.jsx': 'JavaScript', '.ts': 'TypeScript',
    '.tsx': 'TypeScript', '.html': 'HTML', '.htm': 'HTML', '.css': 'CSS',
    '.scss': 'CSS', '.md': 'Markdown', '.json': 'JSON', '.sh': 'Shell',
    '.ps1': 'PowerShell', '.bat': 'Batch', '.cs': 'C#', '.java': 'Java',
    '.go': 'Go', '.rs': 'Rust', '.c': 'C', '.h': 'C', '.cpp': 'C++',
    '.sql': 'SQL', '.yml': 'YAML', '.yaml': 'YAML', '.toml': 'TOML',
    '.ipynb': 'Notebook', '.vue': 'Vue', '.svelte': 'Svelte',
}

# Data and prose never win "primary language" -- a Python tool with a long
# README is still a Python tool.
NOT_PRIMARY = {'Markdown', 'JSON', 'YAML', 'TOML'}

COUNTABLE_EXTS = set(LANG_BY_EXT) | {'.txt', '.cfg', '.ini'}

# Standards are detected in code only. A name in a README or a JSON blob is a
# mention, not a dependency -- counting those had Atlas "using" five systems
# because its own output file listed their names.
CODE_EXTS = {
    '.py', '.js', '.mjs', '.cjs', '.jsx', '.ts', '.tsx',
    '.html', '.htm', '.css', '.scss', '.vue', '.svelte',
}
SURVEY_MAX_FILE_BYTES = 2000000

# Superseded copies, by naming convention. Safe to generalise: a directory
# named `foo_old` is not the project, in any root.
#
# Deliberately NOT here: names like 'docs', 'scratch' or 'stuff'. Those are
# judgements about one particular root, and baking them into a shared operation
# hides real content -- `docs/` in this lab held the commercial doctrine.
# Callers pass their own via the `ignore` argument.
IGNORED_SUFFIXES = ('_old', '_backup', '_bak', '-old', '.old')


def _git(repo, *args):
    """Run git in repo. Returns stripped stdout, or None if it did not work."""
    try:
        out = subprocess.run(
            ['git', '-C', repo] + list(args),
            capture_output=True, text=True, timeout=20, **_no_window()
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def _git_info(path):
    """Branch, remote, dirty count and last commit for a repo, else None."""
    if not os.path.exists(os.path.join(path, '.git')):
        return None

    status = _git(path, 'status', '--porcelain')
    last = _git(path, 'log', '-1', '--format=%cI%x00%s')

    commit_iso, subject = None, None
    if last and '\x00' in last:
        commit_iso, subject = last.split('\x00', 1)
        subject = subject[:120]

    return {
        'repo': True,
        'branch': _git(path, 'rev-parse', '--abbrev-ref', 'HEAD'),
        'remote': _git(path, 'remote', 'get-url', 'origin'),
        'dirty': len([l for l in status.splitlines() if l.strip()]) if status else 0,
        'last_commit': commit_iso,
        'last_subject': subject,
        'submodules': os.path.exists(os.path.join(path, '.gitmodules')),
    }


def _readme_blurb(path):
    """(title, first prose line) from a project's README or DESIGN doc."""
    for name in ('README.md', 'readme.md', 'README.txt', 'DESIGN.md'):
        f = os.path.join(path, name)
        if not os.path.isfile(f):
            continue
        try:
            with open(f, 'r', encoding='utf-8', errors='replace') as fh:
                text = fh.read(200000)
        except OSError:
            continue

        title, blurb = None, None
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith(('---', '```', '|', '<!--', '![')):
                continue
            if line.startswith('#'):
                if title is None:
                    title = line.lstrip('#').strip()
                continue
            blurb = line.lstrip('> ').strip().strip('*_')
            break
        return title, (blurb[:220] if blurb else None)
    return None, None


def _is_ignored(name, extra):
    low = name.lower()
    return low in extra or low.endswith(IGNORED_SUFFIXES)


def _classify(newest_mtime):
    """Bucket by how alive it looks, from the real last-touch date."""
    if not newest_mtime:
        return 'empty'
    days = (time.time() - newest_mtime) / 86400.0
    if days <= 30:
        return 'active'
    if days <= 120:
        return 'warm'
    if days <= 365:
        return 'cooling'
    return 'cold'


def _walk_project(path, exclude_dirs):
    """Count files, lines and languages; find the real last-touched file."""
    counts = {}
    loc = 0
    files = 0
    newest = 0.0
    newest_file = None
    standards = set()
    has_tests = False
    base_depth = len(path.rstrip(os.sep).split(os.sep))

    for root, dirs, filenames in os.walk(path):
        dirs[:] = [d for d in dirs if d not in exclude_dirs]

        tail = root.split(os.sep)[base_depth:]
        if any(p.lower() in ('test', 'tests', '__tests__') for p in tail):
            has_tests = True

        for fname in filenames:
            fpath = os.path.join(root, fname)
            ext = os.path.splitext(fname)[1].lower()

            try:
                st = os.stat(fpath)
            except OSError:
                continue

            files += 1
            if st.st_mtime > newest:
                newest = st.st_mtime
                newest_file = os.path.relpath(fpath, path).replace('\\', '/')

            lang = LANG_BY_EXT.get(ext)
            if lang:
                counts[lang] = counts.get(lang, 0) + 1

            if ext not in COUNTABLE_EXTS or st.st_size > SURVEY_MAX_FILE_BYTES:
                continue

            try:
                with open(fpath, 'r', encoding='utf-8', errors='replace') as fh:
                    body = fh.read()
            except OSError:
                continue

            loc += body.count('\n')
            if body and not body.endswith('\n'):
                loc += 1

            if ext in CODE_EXTS:
                for std, (_desc, needles) in HOUSE_STANDARDS.items():
                    if std in standards:
                        continue
                    if any(n in body for n in needles):
                        standards.add(std)

    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    primary = None
    for lang, _n in ranked:
        if lang not in NOT_PRIMARY:
            primary = lang
            break
    if primary is None and ranked:
        primary = ranked[0][0]

    return {
        'files': files,
        'loc': loc,
        'languages': dict(ranked[:6]),
        'primary': primary,
        'newest_mtime': newest,
        'newest_file': newest_file,
        'standards': sorted(standards),
        'has_tests': has_tests,
    }


def _iso(ts):
    if not ts:
        return None
    return datetime.datetime.fromtimestamp(ts).astimezone().isoformat()


def survey_root(root_path, exclude=None, include_git=True, ignore=None):
    """Inventory every top-level project under root_path.

    `exclude` prunes directory names anywhere in the walk (dependencies,
    caches). `ignore` drops whole top-level entries from the survey -- scratch
    space and third-party checkouts that are not projects in this root.

    Returns the plain dict that `prism.survey` wraps -- importable directly so
    tooling (the Atlas renderer) does not need a running registry.
    """
    exclude_dirs = set(SURVEY_SKIP_DIRS) | set(exclude or [])
    ignore_names = {n.lower() for n in (ignore or [])}
    projects = []
    loose_files = []

    ignored = []

    for name in sorted(os.listdir(root_path), key=lambda s: s.lower()):
        if name in SURVEY_SKIP_TOP:
            continue
        full = os.path.join(root_path, name)

        if os.path.isdir(full) and _is_ignored(name, ignore_names):
            ignored.append(name)
            continue

        if os.path.isfile(full):
            try:
                st = os.stat(full)
            except OSError:
                continue
            loose_files.append({
                'name': name,
                'bytes': st.st_size,
                'modified': _iso(st.st_mtime),
            })
            continue

        if not os.path.isdir(full):
            continue

        tree = _walk_project(full, exclude_dirs)
        title, blurb = _readme_blurb(full)

        entry = dict(tree)
        # A project is not a consumer of itself.
        entry['standards'] = [s for s in tree['standards'] if s.lower() != name.lower()]
        entry['name'] = name
        entry['title'] = title
        entry['blurb'] = blurb
        entry['git'] = _git_info(full) if include_git else None
        entry['modified'] = _iso(tree['newest_mtime'])
        entry['state'] = _classify(tree['newest_mtime'])
        projects.append(entry)

    projects.sort(key=lambda e: -(e['newest_mtime'] or 0))

    # Adoption: who actually uses each house standard, and who is live.
    adoption = {}
    for std, (desc, _needles) in HOUSE_STANDARDS.items():
        users = [p['name'] for p in projects if std in p['standards']]
        live = [p['name'] for p in projects
                if std in p['standards'] and p['state'] in ('active', 'warm')]
        adoption[std] = {
            'description': desc,
            'users': users,
            'count': len(users),
            'live_count': len(live),
        }

    return {
        'generated': datetime.datetime.now().astimezone().isoformat(),
        'root': os.path.abspath(root_path),
        'project_count': len(projects),
        'total_loc': sum(p['loc'] for p in projects),
        'total_files': sum(p['files'] for p in projects),
        'adoption': adoption,
        'projects': projects,
        'ignored': ignored,
        'loose_files': loose_files,
    }


def register_prism_operations(registry):
    """Registers prism structural scanner operations as IAC native operations."""

    def resolve_path(raw_path, context):
        """Resolve against the workspace when the path is relative."""
        workspace_dir = (context or {}).get("workspace_dir", None)
        if workspace_dir and not os.path.isabs(raw_path):
            return os.path.abspath(os.path.join(workspace_dir, raw_path))
        return os.path.abspath(raw_path)

    def make_survey_handler():
        def handler(args, context):
            root_path = resolve_path(args.get('path', '.'), context)
            if not os.path.isdir(root_path):
                return {"status": "error",
                        "error": f"Not a directory: {root_path}"}
            try:
                data = survey_root(
                    root_path,
                    exclude=args.get('exclude', []),
                    include_git=args.get('git', True),
                    ignore=args.get('ignore', []),
                )
            except Exception as e:
                return {"status": "error", "error": str(e)}

            return {
                "status": "success",
                "data": data,
                "message": (f"Surveyed {data['project_count']} projects "
                            f"({data['total_loc']:,} lines) under {root_path}"),
            }
        return handler

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
        },
        "survey": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Root holding the projects to inventory."},
                "exclude": {"type": "array", "items": {"type": "string"}, "description": "Extra directory names to prune anywhere in the walk."},
                "ignore": {"type": "array", "items": {"type": "string"}, "description": "Top-level entries to drop from the survey entirely (scratch, vendored checkouts)."},
                "git": {"type": "boolean", "description": "Collect git branch/remote/dirty state. Default true."}
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

    registry.register(
        name="survey",
        domain="prism",
        description=("Inventory every project under a root: size, languages, "
                     "git state, real last-touch date and liveness."),
        parameters=prism_schemas["survey"],
        handler=make_survey_handler()
    )
