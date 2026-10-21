# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
"""
`.cellapp` bundles -- a Cell App you can carry.

A bundle is a gzipped tar of one app's directory: its manifest, its code, and
nothing else. Export on one machine, import on another, run `setup`.

WHAT A BUNDLE DELIBERATELY DOES NOT CARRY

* **Configs.** A config names hosts, ports, paths and keys, every one of which
  is wrong on another machine -- and some of which are secrets. Carrying one
  is how a credential ends up in a file someone emails around.
* **Models.** Hundreds of megabytes the receiving host may already have, and
  which Cell is responsible for anyway.
* **Caches, logs, recordings.** Not part of the app, and in the case of
  recordings, actively none of the recipient's business.

So an import gives you a runnable app that still needs configuring, and says
so. That is the honest division: a bundle is the program, not the deployment.

IMPORT IS NOT INSTALL

`import_bundle` unpacks and validates. It does not install dependencies,
because `apps.check` can report them with the exact command and a launcher that
silently pip-installs is how a machine becomes unexplainable. See DESIGN.md §4.

Bundles are untrusted input, so paths are checked before extraction -- a tar
entry may name `../../etc/anything` and Python will happily write it.
"""

import io
import json
import os
import tarfile
import time

from .manifest import MANIFEST_NAME, Manifest, load

__all__ = ["export_app", "import_bundle", "inspect_bundle", "SUFFIX"]

SUFFIX = ".cellapp"

#: Never travels. Configs and models are excluded for the reasons above;
#: the rest is noise.
_EXCLUDE_DIRS = {
    "__pycache__", ".git", ".pytest_cache", "node_modules", "venv", ".venv",
    "site-packages", "dist", "build", "models", "logs", "recordings",
}
_EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".log", ".wav", ".mp3", ".bak", ".tmp",
                     ".onnx", ".gguf", ".pt", ".bin", ".safetensors")
_EXCLUDE_NAMES = {"config.local.json", "config.json.bak", ".env",
                  ".env.local", "secrets.json", "credentials.json"}

#: Config files, excluded by NAME rather than only by what the manifest
#: declares. The first version keyed solely off `manifest.config`, so an app
#: whose manifest did not name its config shipped it anyway -- and a config is
#: exactly the file that holds an api key. A rule that only protects the
#: declared case does not protect the dangerous one.
_CONFIG_NAMES = {"config.json", "config.local.json", "config.yaml",
                 "config.yml", "config.toml", "settings.json"}


def _excluded(name):
    if name in _EXCLUDE_NAMES or name in _EXCLUDE_DIRS:
        return True
    return name.endswith(_EXCLUDE_SUFFIXES)


def export_app(manifest, out_path=None, include_config=False):
    """Write a `.cellapp` for this app. Returns the path.

    `include_config` exists for moving an app between two of your own machines
    and defaults off. It is reported in the bundle's metadata, so an importer
    can see that one came with a config rather than discovering it.
    """
    if not manifest.root or not os.path.isdir(manifest.root):
        raise ValueError("app %r has no directory to export" % manifest.name)

    out_path = out_path or os.path.join(
        os.getcwd(), "%s-%s%s" % (manifest.name, manifest.version, SUFFIX))

    skipped = []
    # Every config name, plus whatever this manifest calls its own. Not only
    # the declared one -- see the note on `_CONFIG_NAMES`.
    configs = set(_CONFIG_NAMES)
    declared = os.path.basename(manifest.config_path or "")
    if declared:
        configs.add(declared)

    def filter_entry(info):
        parts = info.name.split("/")
        base = parts[-1]
        for part in parts[1:]:
            if _excluded(part):
                skipped.append(info.name)
                return None
        if base in configs and not include_config:
            skipped.append(info.name + " (config, not carried)")
            return None
        # Ownership and permissions are the exporting machine's, not the
        # importing one's.
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        return info

    root_name = manifest.name
    with tarfile.open(out_path, "w:gz") as tar:
        tar.add(manifest.root, arcname=root_name, filter=filter_entry)
        meta = {
            "bundle": "1.0",
            "app": manifest.name,
            "version": manifest.version,
            "title": manifest.title,
            "exported_at": time.time(),
            "includes_config": bool(include_config),
            "requires": manifest.requires,
            "note": ("Configs and models are not carried. Run the app's setup "
                     "after importing." if not include_config else
                     "This bundle carries a config from the exporting "
                     "machine; check its hosts and keys."),
        }
        blob = json.dumps(meta, indent=2).encode("utf-8")
        info = tarfile.TarInfo("%s/.bundle.json" % root_name)
        info.size = len(blob)
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(blob))

    return {"path": out_path,
            "bytes": os.path.getsize(out_path),
            "skipped": len(skipped),
            "includes_config": bool(include_config)}


def _safe_members(tar, destination):
    """Yield members that stay inside `destination`.

    A bundle is untrusted input. A tar entry may be named `../../.bashrc` or
    be an absolute path or a symlink pointing out of the tree, and Python will
    extract all three without complaint.
    """
    destination = os.path.abspath(destination)
    for member in tar.getmembers():
        if member.name.startswith(("/", "\\")) or ".." in member.name.split("/"):
            continue
        if member.issym() or member.islnk():
            # A link in an app bundle has no legitimate use and is the
            # easiest way to escape the destination.
            continue
        target = os.path.abspath(os.path.join(destination, member.name))
        if not target.startswith(destination + os.sep) and target != destination:
            continue
        yield member


def inspect_bundle(path):
    """What is in a bundle, without unpacking it."""
    if not os.path.isfile(path):
        raise ValueError("no bundle at %s" % path)
    try:
        with tarfile.open(path, "r:gz") as tar:
            names = [m.name for m in tar.getmembers()]
            meta, manifest_data = None, None
            for member in tar.getmembers():
                base = os.path.basename(member.name)
                if base == ".bundle.json" and meta is None:
                    meta = json.loads(
                        tar.extractfile(member).read().decode("utf-8"))
                elif base == MANIFEST_NAME and manifest_data is None:
                    manifest_data = json.loads(
                        tar.extractfile(member).read().decode("utf-8"))
    except tarfile.TarError as exc:
        raise ValueError("%s is not a readable bundle: %s"
                         % (os.path.basename(path), exc))

    if manifest_data is None:
        raise ValueError("%s contains no %s, so it is not a Cell App bundle"
                         % (os.path.basename(path), MANIFEST_NAME))

    manifest = Manifest(manifest_data)
    return {"meta": meta, "manifest": manifest.to_dict(),
            "files": len(names), "bytes": os.path.getsize(path),
            "problems": manifest.problems()}


def import_bundle(path, destination, overwrite=False):
    """Unpack a bundle into `destination`. Returns what landed.

    Validates the manifest before writing anything: a bundle that is not a
    Cell App should not leave a half-extracted directory behind.
    """
    info = inspect_bundle(path)
    name = info["manifest"]["name"]
    if not name:
        raise ValueError("the bundle's manifest has no 'name'")

    target = os.path.join(os.path.abspath(destination), name)
    if os.path.exists(target) and not overwrite:
        raise ValueError("%s already exists. Pass overwrite to replace it -- "
                         "and note that its config would survive, since "
                         "bundles do not carry one." % target)

    os.makedirs(destination, exist_ok=True)
    with tarfile.open(path, "r:gz") as tar:
        members = list(_safe_members(tar, destination))
        refused = len(tar.getmembers()) - len(members)
        # The bundle's top-level directory is the app name, so extracting
        # into `destination` produces `destination/<name>`.
        for member in members:
            tar.extract(member, destination)

    manifest_path = os.path.join(target, MANIFEST_NAME)
    if not os.path.isfile(manifest_path):
        raise ValueError("the bundle unpacked but there is no %s at %s"
                         % (MANIFEST_NAME, target))
    manifest = load(manifest_path)

    return {"app": manifest.name, "path": target,
            "version": manifest.version,
            "refused_members": refused,
            "problems": manifest.problems(),
            "next": ("Nothing is configured yet: bundles do not carry configs "
                     "or models. Run `apps.check %s` to see what this host is "
                     "missing." % manifest.name)}
