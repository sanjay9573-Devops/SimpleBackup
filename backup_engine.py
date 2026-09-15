"""
backup_engine.py

Core backup logic for the desktop backup app.

Design (classic "snapshot" style, similar in spirit to rsync --link-dest / Time Machine):
  - Each backup run creates a new snapshot folder named by timestamp under the
    destination directory, e.g.  <dest>/snapshots/2026-09-15_14-30-00/
  - For every source file:
      * If it is new or has changed since the last snapshot (compared by size +
        mtime, falling back to a SHA-256 hash if needed), it is COPIED into the
        new snapshot, preserving its relative path.
      * If it is unchanged, a HARD LINK is created in the new snapshot pointing
        at the same file in the previous snapshot. This means unchanged files
        cost almost no extra disk space, but every snapshot folder still looks
        like a complete, independent copy of your data -- so restoring is just
        "copy this folder back".
  - A manifest.json file at the destination root tracks all snapshots and,
    within each snapshot, the size/mtime/hash of every file, so future runs
    know what changed without re-hashing everything every time.

Notes / limitations of this first version:
  - Hard links require the snapshot destination to be on the same filesystem
    as the previous snapshot (always true here, since all snapshots live under
    the same destination directory). If hard-linking fails for any reason,
    the engine transparently falls back to a full copy for that file.
  - Deleted files: if a file existed in the previous snapshot but no longer
    exists in the source, it simply won't appear in the new snapshot (this
    matches how Time Machine / most snapshot tools behave -- each snapshot is
    a faithful copy of the source at that point in time).
  - This uses Python's standard library only, so it runs anywhere Python runs
    with no extra installs.
"""

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from datetime import datetime


MANIFEST_NAME = "manifest.json"
SNAPSHOTS_DIR = "snapshots"


def human_size(num_bytes: int) -> str:
    """Format a byte count as a human-readable string."""
    size = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def file_hash(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Compute a SHA-256 hash of a file's contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


class BackupEngine:
    def __init__(self, source_dirs, dest_dir, log_callback=None):
        """
        source_dirs: list of str/Path folders to back up
        dest_dir: str/Path folder where snapshots + manifest are stored
        log_callback: optional function(str) called with progress messages
        """
        self.source_dirs = [Path(p) for p in source_dirs]
        self.dest_dir = Path(dest_dir)
        self.log_callback = log_callback or (lambda msg: None)
        self._cancel_requested = False

    def cancel(self):
        self._cancel_requested = True

    def _log(self, msg):
        self.log_callback(msg)

    # ---------- manifest handling ----------

    def _manifest_path(self):
        return self.dest_dir / MANIFEST_NAME

    def load_manifest(self):
        path = self._manifest_path()
        if not path.exists():
            return {"snapshots": []}
        with open(path, "r") as f:
            return json.load(f)

    def save_manifest(self, manifest):
        self.dest_dir.mkdir(parents=True, exist_ok=True)
        with open(self._manifest_path(), "w") as f:
            json.dump(manifest, f, indent=2)

    def list_versions(self):
        """Return list of snapshot records, most recent first."""
        manifest = self.load_manifest()
        return list(reversed(manifest.get("snapshots", [])))

    # ---------- scanning ----------

    def _scan_source(self, source_dir: Path):
        """Yield (relative_path_str, absolute_path) for every file under source_dir."""
        for root, dirs, files in os.walk(source_dir):
            for name in files:
                abs_path = Path(root) / name
                try:
                    rel_path = abs_path.relative_to(source_dir.parent)
                except ValueError:
                    rel_path = abs_path.name
                yield str(rel_path), abs_path

    def estimate_file_count(self):
        count = 0
        for src in self.source_dirs:
            if src.exists():
                for _ in self._scan_source(src):
                    count += 1
        return count

    # ---------- backup ----------

    def run_backup(self, progress_callback=None):
        """
        Run one backup pass. progress_callback(done, total, current_file) is
        called periodically if provided. Returns the new snapshot record dict.
        """
        self._cancel_requested = False
        manifest = self.load_manifest()
        prev_snapshots = manifest.get("snapshots", [])
        prev_files = prev_snapshots[-1]["files"] if prev_snapshots else {}
        prev_snapshot_name = prev_snapshots[-1]["name"] if prev_snapshots else None

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        candidate = timestamp
        suffix = 1
        # Guard against two backups starting within the same second, which
        # would otherwise collide on the same snapshot folder name.
        while (self.dest_dir / SNAPSHOTS_DIR / candidate).exists():
            suffix += 1
            candidate = f"{timestamp}_{suffix}"
        timestamp = candidate
        snapshot_root = self.dest_dir / SNAPSHOTS_DIR / timestamp
        snapshot_root.mkdir(parents=True, exist_ok=True)

        all_files = []
        for src in self.source_dirs:
            if not src.exists():
                self._log(f"WARNING: source folder not found, skipping: {src}")
                continue
            all_files.extend(list(self._scan_source(src)))

        total = len(all_files)
        self._log(f"Starting backup: {total} files found across {len(self.source_dirs)} source folder(s).")

        new_manifest_files = {}
        copied_count = 0
        linked_count = 0
        skipped_count = 0
        bytes_copied = 0

        for i, (rel_path, abs_path) in enumerate(all_files):
            if self._cancel_requested:
                self._log("Backup cancelled by user.")
                break
            try:
                stat = abs_path.stat()
            except OSError as e:
                self._log(f"WARNING: could not read {abs_path}: {e}")
                continue

            size = stat.st_size
            mtime = stat.st_mtime
            prev_record = prev_files.get(rel_path)

            unchanged = (
                prev_record is not None
                and prev_record["size"] == size
                and abs(prev_record["mtime"] - mtime) < 1e-6
            )

            dest_path = snapshot_root / rel_path
            dest_path.parent.mkdir(parents=True, exist_ok=True)

            if unchanged and prev_snapshot_name:
                prev_file_path = self.dest_dir / SNAPSHOTS_DIR / prev_snapshot_name / rel_path
                linked_ok = False
                if prev_file_path.exists():
                    try:
                        os.link(prev_file_path, dest_path)
                        linked_ok = True
                    except OSError:
                        linked_ok = False
                if linked_ok:
                    linked_count += 1
                    new_manifest_files[rel_path] = prev_record
                else:
                    # fall back to a fresh copy
                    shutil.copy2(abs_path, dest_path)
                    copied_count += 1
                    bytes_copied += size
                    new_manifest_files[rel_path] = {"size": size, "mtime": mtime}
            else:
                shutil.copy2(abs_path, dest_path)
                copied_count += 1
                bytes_copied += size
                new_manifest_files[rel_path] = {"size": size, "mtime": mtime}

            if progress_callback:
                progress_callback(i + 1, total, rel_path)

        skipped_count = total - copied_count - linked_count

        record = {
            "name": timestamp,
            "created_at": timestamp,
            "file_count": len(new_manifest_files),
            "copied": copied_count,
            "linked": linked_count,
            "bytes_copied": bytes_copied,
            "files": new_manifest_files,
        }

        manifest.setdefault("snapshots", []).append(record)
        self.save_manifest(manifest)

        self._log(
            f"Backup complete: {copied_count} file(s) copied "
            f"({human_size(bytes_copied)}), {linked_count} unchanged (linked)."
        )
        return record

    # ---------- restore ----------

    def restore(self, snapshot_name, restore_to_dir, progress_callback=None):
        """Copy every file from the given snapshot into restore_to_dir."""
        snapshot_root = self.dest_dir / SNAPSHOTS_DIR / snapshot_name
        if not snapshot_root.exists():
            raise FileNotFoundError(f"Snapshot not found: {snapshot_name}")

        restore_to_dir = Path(restore_to_dir)
        restore_to_dir.mkdir(parents=True, exist_ok=True)

        all_files = list(self._scan_source(snapshot_root))
        # _scan_source uses parent-relative paths; here we want paths relative
        # to the snapshot root itself.
        all_files = []
        for root, dirs, files in os.walk(snapshot_root):
            for name in files:
                abs_path = Path(root) / name
                rel_path = abs_path.relative_to(snapshot_root)
                all_files.append((rel_path, abs_path))

        total = len(all_files)
        self._log(f"Restoring {total} file(s) from snapshot {snapshot_name} to {restore_to_dir}")

        for i, (rel_path, abs_path) in enumerate(all_files):
            if self._cancel_requested:
                self._log("Restore cancelled by user.")
                break
            dest_path = restore_to_dir / rel_path
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(abs_path, dest_path)
            if progress_callback:
                progress_callback(i + 1, total, str(rel_path))

        self._log("Restore complete.")
