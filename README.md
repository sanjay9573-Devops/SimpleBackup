# Simple Backup

A lightweight desktop backup app for individuals. Pick folders to protect, pick
a backup destination (an external drive, a second disk, a NAS mount, etc.),
and it keeps versioned, space-efficient snapshots you can browse and restore
from.

No accounts, no cloud, no installs beyond Python itself — everything runs
locally on your machine.

## Requirements

- Python 3.8+ (uses only the standard library — no `pip install` needed)
- Windows, macOS, or Linux

## Running it

```bash
python3 main.py
```

This opens the app window. You do not need to "install" anything else.

## How it works

- **Backup tab**: add one or more source folders, choose a destination
  folder, and click **Back Up Now**. Each run creates a new timestamped
  snapshot under `<destination>/snapshots/`.
- **Unchanged files are not duplicated.** If a file hasn't changed since the
  last backup, the new snapshot links to the same data on disk instead of
  copying it again — so snapshots stay cheap on space even though each one
  looks like a full, independent copy of your data.
- **Restore tab**: pick any past snapshot from the list and restore it to any
  folder you choose. The original files are left untouched — restoring
  writes into the folder you pick.
- **Auto-backup**: check "Auto-backup every N minutes" to have it run
  automatically at an interval while the app is open. This is a simple
  in-app timer, not a background service — see "Scheduling" below if you
  want backups to run even when the app isn't open.

## Scheduling backups when the app is closed

The built-in auto-backup only runs while the window is open. For true
background scheduling, you can call the backup engine directly from your
OS's scheduler:

```bash
python3 -c "
from backup_engine import BackupEngine
BackupEngine(['/path/to/folder1', '/path/to/folder2'], '/path/to/destination').run_backup()
"
```

- **macOS/Linux**: add that command to a `cron` job or a `launchd`/`systemd`
  timer.
- **Windows**: add it as a script in Task Scheduler.

## Notes and limitations (read before relying on this for important data)

- Hard-linking for unchanged files works because all snapshots live on the
  same destination drive. If your destination is a different filesystem type
  that doesn't support hard links, the app automatically falls back to a
  full copy for that file — it will still work, just use more space.
- Deleted files: if you delete a file from a source folder, it simply won't
  appear in the *next* snapshot — it remains recoverable from any earlier
  snapshot that still has it.
- This is a first version focused on local, single-user backups. It does not
  currently include encryption or cloud upload — let me know if you'd like
  either added.
- Test your restore process on unimportant files first, and keep your backup
  destination on a *different* physical disk than your source data (a backup
  on the same drive won't protect you if that drive fails).

## File overview

| File | Purpose |
|---|---|
| `backup_engine.py` | Core backup/restore/snapshot logic (no UI dependency) |
| `gui.py` | Tkinter desktop window (Backup + Restore tabs) |
| `config.py` | Saves your folder/destination settings between launches |
| `main.py` | Launches the app |
