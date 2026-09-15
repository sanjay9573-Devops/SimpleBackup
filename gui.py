"""
gui.py

Tkinter desktop GUI for the backup app. Two tabs:
  - Backup: manage source folders + destination, run a backup now, optional
    auto-backup on a timer while the app is open.
  - Restore: pick a past snapshot and restore it to a folder of your choice.

Uses only the Python standard library (tkinter), so there is nothing to
install.
"""

import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from backup_engine import BackupEngine, human_size
import config as cfg


class BackupApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Simple Backup")
        self.geometry("720x560")
        self.minsize(640, 480)

        self.config_data = cfg.load_config()
        self.auto_backup_thread = None
        self.auto_backup_stop = threading.Event()
        self.backup_in_progress = False

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)

        self.backup_tab = ttk.Frame(notebook)
        self.restore_tab = ttk.Frame(notebook)
        notebook.add(self.backup_tab, text="Backup")
        notebook.add(self.restore_tab, text="Restore")

        self._build_backup_tab()
        self._build_restore_tab()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if self.config_data.get("auto_backup_enabled"):
            self._start_auto_backup_thread()

    # ---------------- Backup tab ----------------

    def _build_backup_tab(self):
        frame = self.backup_tab

        # Source folders
        ttk.Label(frame, text="Folders to back up:", font=("", 10, "bold")).pack(
            anchor="w", padx=10, pady=(10, 0)
        )
        list_frame = ttk.Frame(frame)
        list_frame.pack(fill="both", expand=False, padx=10, pady=4)

        self.source_listbox = tk.Listbox(list_frame, height=5)
        self.source_listbox.pack(side="left", fill="both", expand=True)
        for src in self.config_data.get("source_dirs", []):
            self.source_listbox.insert("end", src)

        scrollbar = ttk.Scrollbar(list_frame, command=self.source_listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.source_listbox.config(yscrollcommand=scrollbar.set)

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Button(btn_frame, text="Add Folder...", command=self._add_source).pack(side="left")
        ttk.Button(btn_frame, text="Remove Selected", command=self._remove_source).pack(
            side="left", padx=6
        )

        # Destination
        ttk.Label(frame, text="Backup destination:", font=("", 10, "bold")).pack(
            anchor="w", padx=10, pady=(6, 0)
        )
        dest_frame = ttk.Frame(frame)
        dest_frame.pack(fill="x", padx=10, pady=4)
        self.dest_var = tk.StringVar(value=self.config_data.get("dest_dir", ""))
        ttk.Entry(dest_frame, textvariable=self.dest_var).pack(side="left", fill="x", expand=True)
        ttk.Button(dest_frame, text="Choose...", command=self._choose_dest).pack(side="left", padx=6)

        # Auto backup
        auto_frame = ttk.Frame(frame)
        auto_frame.pack(fill="x", padx=10, pady=(6, 4))
        self.auto_var = tk.BooleanVar(value=self.config_data.get("auto_backup_enabled", False))
        ttk.Checkbutton(
            auto_frame,
            text="Auto-backup every",
            variable=self.auto_var,
            command=self._toggle_auto_backup,
        ).pack(side="left")
        self.interval_var = tk.StringVar(
            value=str(self.config_data.get("auto_backup_interval_minutes", 60))
        )
        ttk.Entry(auto_frame, textvariable=self.interval_var, width=6).pack(side="left", padx=4)
        ttk.Label(auto_frame, text="minutes (while this app is open)").pack(side="left")

        # Run button + progress
        run_frame = ttk.Frame(frame)
        run_frame.pack(fill="x", padx=10, pady=(10, 4))
        self.run_button = ttk.Button(run_frame, text="Back Up Now", command=self._run_backup_clicked)
        self.run_button.pack(side="left")
        self.progress = ttk.Progressbar(run_frame, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=10)

        # Log
        ttk.Label(frame, text="Log:", font=("", 10, "bold")).pack(anchor="w", padx=10, pady=(8, 0))
        self.log_text = tk.Text(frame, height=10, state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True, padx=10, pady=(2, 10))

    def _add_source(self):
        folder = filedialog.askdirectory(title="Choose a folder to back up")
        if folder:
            self.source_listbox.insert("end", folder)
            self._persist_config()

    def _remove_source(self):
        sel = list(self.source_listbox.curselection())
        for idx in reversed(sel):
            self.source_listbox.delete(idx)
        self._persist_config()

    def _choose_dest(self):
        folder = filedialog.askdirectory(title="Choose a backup destination folder")
        if folder:
            self.dest_var.set(folder)
            self._persist_config()
            self._refresh_versions()

    def _current_sources(self):
        return list(self.source_listbox.get(0, "end"))

    def _persist_config(self):
        self.config_data["source_dirs"] = self._current_sources()
        self.config_data["dest_dir"] = self.dest_var.get()
        self.config_data["auto_backup_enabled"] = self.auto_var.get()
        try:
            self.config_data["auto_backup_interval_minutes"] = int(self.interval_var.get())
        except ValueError:
            pass
        cfg.save_config(self.config_data)

    def _log(self, msg):
        def append():
            self.log_text.config(state="normal")
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
            self.log_text.config(state="disabled")
        self.after(0, append)

    def _run_backup_clicked(self):
        if self.backup_in_progress:
            return
        sources = self._current_sources()
        dest = self.dest_var.get().strip()
        if not sources:
            messagebox.showwarning("No folders selected", "Add at least one folder to back up.")
            return
        if not dest:
            messagebox.showwarning("No destination", "Choose a backup destination folder.")
            return
        self._persist_config()
        self.run_button.config(state="disabled")
        self.progress["value"] = 0
        self.backup_in_progress = True

        def worker():
            engine = BackupEngine(sources, dest, log_callback=self._log)

            def on_progress(done, total, current_file):
                pct = (done / total * 100) if total else 100
                self.after(0, lambda: self.progress.config(value=pct))

            try:
                engine.run_backup(progress_callback=on_progress)
            except Exception as e:
                self._log(f"ERROR: {e}")
            finally:
                self.backup_in_progress = False
                self.after(0, lambda: self.run_button.config(state="normal"))
                self.after(0, self._refresh_versions)

        threading.Thread(target=worker, daemon=True).start()

    def _toggle_auto_backup(self):
        self._persist_config()
        if self.auto_var.get():
            self._start_auto_backup_thread()
        else:
            self.auto_backup_stop.set()

    def _start_auto_backup_thread(self):
        self.auto_backup_stop.clear()

        def loop():
            while not self.auto_backup_stop.is_set():
                try:
                    minutes = int(self.interval_var.get())
                except ValueError:
                    minutes = 60
                waited = 0
                while waited < minutes * 60 and not self.auto_backup_stop.is_set():
                    time.sleep(1)
                    waited += 1
                if self.auto_backup_stop.is_set():
                    break
                if not self.backup_in_progress:
                    self.after(0, self._run_backup_clicked)

        self.auto_backup_thread = threading.Thread(target=loop, daemon=True)
        self.auto_backup_thread.start()

    # ---------------- Restore tab ----------------

    def _build_restore_tab(self):
        frame = self.restore_tab

        ttk.Label(frame, text="Available backup versions:", font=("", 10, "bold")).pack(
            anchor="w", padx=10, pady=(10, 0)
        )

        columns = ("date", "files", "size")
        self.versions_tree = ttk.Treeview(frame, columns=columns, show="headings", height=10)
        self.versions_tree.heading("date", text="Snapshot")
        self.versions_tree.heading("files", text="Files")
        self.versions_tree.heading("size", text="New data copied")
        self.versions_tree.column("date", width=220)
        self.versions_tree.column("files", width=100, anchor="center")
        self.versions_tree.column("size", width=140, anchor="center")
        self.versions_tree.pack(fill="both", expand=True, padx=10, pady=4)

        ttk.Button(frame, text="Refresh List", command=self._refresh_versions).pack(
            anchor="w", padx=10, pady=(0, 8)
        )

        ttk.Label(frame, text="Restore to folder:", font=("", 10, "bold")).pack(
            anchor="w", padx=10, pady=(4, 0)
        )
        restore_dest_frame = ttk.Frame(frame)
        restore_dest_frame.pack(fill="x", padx=10, pady=4)
        self.restore_dest_var = tk.StringVar()
        ttk.Entry(restore_dest_frame, textvariable=self.restore_dest_var).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(
            restore_dest_frame, text="Choose...", command=self._choose_restore_dest
        ).pack(side="left", padx=6)

        self.restore_button = ttk.Button(
            frame, text="Restore Selected Version", command=self._run_restore_clicked
        )
        self.restore_button.pack(anchor="w", padx=10, pady=(8, 4))

        self.restore_progress = ttk.Progressbar(frame, mode="determinate")
        self.restore_progress.pack(fill="x", padx=10, pady=(0, 10))

        self._refresh_versions()

    def _refresh_versions(self):
        for row in self.versions_tree.get_children():
            self.versions_tree.delete(row)
        dest = self.dest_var.get().strip()
        if not dest or not Path(dest).exists():
            return
        engine = BackupEngine([], dest)
        for snap in engine.list_versions():
            self.versions_tree.insert(
                "",
                "end",
                iid=snap["name"],
                values=(snap["name"], snap["file_count"], human_size(snap.get("bytes_copied", 0))),
            )

    def _choose_restore_dest(self):
        folder = filedialog.askdirectory(title="Choose where to restore files to")
        if folder:
            self.restore_dest_var.set(folder)

    def _run_restore_clicked(self):
        selection = self.versions_tree.selection()
        if not selection:
            messagebox.showwarning("No version selected", "Select a backup version to restore.")
            return
        snapshot_name = selection[0]
        restore_dest = self.restore_dest_var.get().strip()
        if not restore_dest:
            messagebox.showwarning("No destination", "Choose a folder to restore into.")
            return
        dest = self.dest_var.get().strip()
        if not dest:
            messagebox.showwarning("No backup destination set", "Set the backup destination on the Backup tab first.")
            return

        if not messagebox.askyesno(
            "Confirm restore",
            f"Restore snapshot '{snapshot_name}' into:\n{restore_dest}\n\n"
            "Existing files with the same names will be overwritten. Continue?",
        ):
            return

        self.restore_button.config(state="disabled")
        self.restore_progress["value"] = 0

        def worker():
            engine = BackupEngine([], dest, log_callback=self._log)

            def on_progress(done, total, current_file):
                pct = (done / total * 100) if total else 100
                self.after(0, lambda: self.restore_progress.config(value=pct))

            try:
                engine.restore(snapshot_name, restore_dest, progress_callback=on_progress)
                self.after(0, lambda: messagebox.showinfo("Restore complete", "Files restored successfully."))
            except Exception as e:
                self._log(f"ERROR: {e}")
                self.after(0, lambda: messagebox.showerror("Restore failed", str(e)))
            finally:
                self.after(0, lambda: self.restore_button.config(state="normal"))

        threading.Thread(target=worker, daemon=True).start()

    # ---------------- lifecycle ----------------

    def _on_close(self):
        self._persist_config()
        self.auto_backup_stop.set()
        self.destroy()
