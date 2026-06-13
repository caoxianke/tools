# -*- coding: utf-8 -*-
"""
stay_healthy.py — Unified Health Reminder & Keep-Awake Tool
============================================================
Author  : Cao Xianke (曹先科)
Platform: Windows (x64)
Created : 2026-06-11

Description:
    Combines stay_awake, stay_awake_popup, and screen_locker into one
    Windows desktop GUI application.

    Features:
    - Keeps Windows awake by simulating Scroll Lock key presses
      (prevents system from locking due to inactivity)
    - Periodically prompts you to take a rest and locks the screen
      at a configurable interval
    - Single configuration window with all options

    Four configuration options:
    1. [Checkbox] Terminate existing running instances (default: checked)
    2. [Input]    Lock screen interval in minutes (default: 50)
    3. [Input]    Response timeout in seconds (default: 60)
    4. [Checkbox] Silent mode — no console output (default: checked)

Compile to EXE (single file) with PyInstaller:
    pip install pyinstaller
    pyinstaller --onefile --noconsole stay_healthy.py
    # Output: dist/stay_healthy.exe
"""

import time
import ctypes
from ctypes import wintypes
import datetime
import sys
import os
import threading
import subprocess
import tempfile
import tkinter as tk
from tkinter import ttk

# Optional tray icon support (pystray + pillow)
try:
    import pystray
    from PIL import Image, ImageDraw
    HAVE_TRAY = True
except Exception:
    HAVE_TRAY = False

# ============================================================
# Windows API constants
# ============================================================
INPUT_KEYBOARD = 1
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002
SCROLL_LOCK_SCANCODE = 0x46

# MessageBox constants
MB_YESNO = 0x0004
MB_ICONQUESTION = 0x0020
MB_ICONINFORMATION = 0x0040
MB_ICONWARNING = 0x0030
MB_SYSTEMMODAL = 0x00001000
MB_TOPMOST = 0x00040000
MB_SETFOREGROUND = 0x00010000
MB_DEFBUTTON2 = 0x00000100
MB_ABORTRETRYIGNORE = 0x0002

IDYES = 6
IDNO = 7
IDABORT = 3
IDRETRY = 4
IDIGNORE = 5
IDTIMEOUT = 32000

# Prompt message box flags: system modal + topmost so it's always visible
PROMPT_FLAGS = (MB_ABORTRETRYIGNORE | MB_ICONQUESTION | MB_SYSTEMMODAL |
                MB_TOPMOST | MB_SETFOREGROUND | MB_DEFBUTTON2)

# Postpone snooze duration (minutes)
POSTPONE_MINUTES = 5
MIN_INTERVAL_MINUTES = 5

# Mutex name for single instance (session-local to avoid cross-session false positives)
MUTEX_NAME = "Local\\StayHealthy_SingleInstance_Mutex"
ERROR_ALREADY_EXISTS = 183

# Keep mutex handle alive for process lifetime
_mutex_handle = None

# ============================================================
# Scroll Lock key simulation (stay-awake mechanism)
# ============================================================

def press_scroll_lock():
    """Simulate pressing the Scroll Lock key to reset the system's idle timer."""
    ctypes.windll.user32.keybd_event(0x91, SCROLL_LOCK_SCANCODE, 0, 0)
    time.sleep(0.05)
    ctypes.windll.user32.keybd_event(0x91, SCROLL_LOCK_SCANCODE, KEYEVENTF_KEYUP, 0)


def keep_awake_loop(interval_seconds=60, stop_event=None):
    """
    Run in a background thread: press Scroll Lock every interval_seconds.
    Stops when stop_event is set.
    """
    trigger_count = 0
    while stop_event is None or not stop_event.is_set():
        # Check stop event with a short sleep so we can exit quickly
        for _ in range(interval_seconds):
            if stop_event and stop_event.is_set():
                return
            time.sleep(1)
        press_scroll_lock()
        trigger_count += 1


# ============================================================
# Lock prompt / screen lock functionality
# ============================================================

def lock_workstation():
    """Lock the Windows workstation immediately."""
    ctypes.windll.user32.LockWorkStation()


def show_lock_prompt(interval_minutes, prompt_timeout_seconds):
    """
    Show a Windows message box with three buttons asking what to do.

    Buttons:
      Abort  (A) -> Lock the screen now
      Retry  (R) -> Skip this cycle (wait for full interval)
      Ignore (I) -> Postpone / snooze for {POSTPONE_MINUTES} minutes

    Returns:
        'lock'     -> lock the screen now
        'skip'     -> skip this cycle
        'postpone' -> try again in {POSTPONE_MINUTES} minutes
    """
    message = (
        f"Time to take a rest!\n\n"
        f"You've been working for {interval_minutes} minute(s).\n"
        f"Your eyes and body need a break.\n\n"
        f"  [Abort]  = Lock the screen now\n"
        f"  [Retry]  = Skip this cycle (next prompt in {interval_minutes} min)\n"
        f"  [Ignore] = Postpone {POSTPONE_MINUTES} minutes (remind me later)\n\n"
        f"(Auto-lock in {prompt_timeout_seconds} seconds if no response)"
    )
    title = "Take a Rest - Screen Lock"

    result = [IDTIMEOUT]
    event = threading.Event()

    def show_box():
        result[0] = ctypes.windll.user32.MessageBoxW(
            None, message, title, PROMPT_FLAGS
        )
        event.set()

    box_thread = threading.Thread(target=show_box)
    box_thread.daemon = True
    box_thread.start()

    event.wait(timeout=prompt_timeout_seconds)

    if box_thread.is_alive():
        # Timeout — auto-lock
        return 'lock'

    choice = result[0]
    if choice == IDABORT:
        return 'lock'
    elif choice == IDRETRY:
        return 'skip'
    elif choice == IDIGNORE:
        return 'postpone'
    else:
        return 'lock'


def lock_loop(interval_minutes, prompt_timeout_seconds, stop_event=None):
    """
    Main lock loop: wait for interval, then prompt user.
    Stops when stop_event is set.
    """
    cycle_count = 0
    skip_count = 0
    lock_count = 0
    postpone_count = 0

    while stop_event is None or not stop_event.is_set():
        # Wait for the interval (check stop_event during sleep)
        for _ in range(interval_minutes * 60):
            if stop_event and stop_event.is_set():
                return
            time.sleep(1)

        if stop_event and stop_event.is_set():
            return

        cycle_count += 1

        # Show the prompt
        action = show_lock_prompt(interval_minutes, prompt_timeout_seconds)

        if action == 'lock':
            lock_count += 1
            lock_workstation()
        elif action == 'postpone':
            postpone_count += 1
            # Wait POSTPONE_MINUTES before prompting again
            for _ in range(POSTPONE_MINUTES * 60):
                if stop_event and stop_event.is_set():
                    return
                time.sleep(1)
            # After postpone, prompt again immediately (don't wait full interval)
            continue  # goes back to show_lock_prompt
        else:  # 'skip'
            skip_count += 1


# ============================================================
# Single-instance handling
# ============================================================

def check_single_instance():
    """
    Ensure only one instance runs. Uses a named mutex.
    Returns True if this instance should continue, False to exit.
    """
    global _mutex_handle

    ctypes.windll.kernel32.SetLastError(0)
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not mutex:
        ctypes.windll.user32.MessageBoxW(
            None,
            "Unable to create single-instance mutex.\nPlease restart the program.",
            "stay_healthy - Error",
            0x00000010 | 0x00001000  # MB_ICONERROR | MB_SYSTEMMODAL
        )
        sys.exit(1)

    last_error = ctypes.windll.kernel32.GetLastError()

    if last_error == ERROR_ALREADY_EXISTS:
        ctypes.windll.kernel32.CloseHandle(mutex)
        answer = ctypes.windll.user32.MessageBoxW(
            None,
            "Another instance of stay_healthy is already running.\n\n"
            "Do you want to terminate the existing instance and start a new one?\n\n"
            "Click 'Yes' to terminate and continue.\n"
            "Click 'No' to exit.",
            "stay_healthy - Already Running",
            MB_YESNO | MB_ICONQUESTION | MB_SYSTEMMODAL | MB_TOPMOST | MB_SETFOREGROUND | MB_DEFBUTTON2
        )

        if answer == IDYES:
            # Terminate existing instances
            terminate_other_instances()
            time.sleep(1)

            ctypes.windll.kernel32.SetLastError(0)
            _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
            if not _mutex_handle:
                ctypes.windll.user32.MessageBoxW(
                    None,
                    "Unable to create single-instance mutex after terminating old instance.\n"
                    "Please try again.",
                    "stay_healthy - Error",
                    0x00000010 | 0x00001000
                )
                sys.exit(1)

            last_error = ctypes.windll.kernel32.GetLastError()
            if last_error == ERROR_ALREADY_EXISTS:
                ctypes.windll.user32.MessageBoxW(
                    None,
                    "Failed to terminate the existing instance.\n"
                    "Please close it manually and try again.",
                    "stay_healthy - Error",
                    0x00000010 | 0x00001000
                )
                sys.exit(1)
            return True
        else:
            sys.exit(0)

    _mutex_handle = mutex
    return True


def terminate_other_instances():
    """
    Find and terminate other stay_healthy processes (compiled .exe or python
    script), excluding the current process. Uses Win32 API (no external deps).
    """
    PROCESS_TERMINATE = 0x0001
    TH32CS_SNAPPROCESS = 0x00000002

    kernel32 = ctypes.windll.kernel32

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize",              wintypes.DWORD),
            ("cntUsage",            wintypes.DWORD),
            ("th32ProcessID",       wintypes.DWORD),
            ("th32DefaultHeapID",   ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID",        wintypes.DWORD),
            ("cntThreads",          wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase",      ctypes.c_long),
            ("dwFlags",             wintypes.DWORD),
            ("szExeFile",           ctypes.c_wchar * 260),
        ]

    current_exe = os.path.basename(sys.executable).lower()
    current_pid = os.getpid()
    terminated = False

    hSnapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if hSnapshot == wintypes.HANDLE(-1).value:
        return False

    try:
        pe32 = PROCESSENTRY32W()
        pe32.dwSize = ctypes.sizeof(PROCESSENTRY32W)

        if not kernel32.Process32FirstW(hSnapshot, ctypes.byref(pe32)):
            return False

        while True:
            pid = pe32.th32ProcessID
            if pid == current_pid:
                if not kernel32.Process32NextW(hSnapshot, ctypes.byref(pe32)):
                    break
                continue

            exe_file = pe32.szExeFile.lower()

            if exe_file == current_exe or "stay_healthy" in exe_file:
                handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
                if handle:
                    kernel32.TerminateProcess(handle, 0)
                    kernel32.CloseHandle(handle)
                    terminated = True

            if not kernel32.Process32NextW(hSnapshot, ctypes.byref(pe32)):
                break
    finally:
        kernel32.CloseHandle(hSnapshot)

    return terminated


# ============================================================
# GUI — tkinter configuration window
# ============================================================

class StayHealthyApp:
    """Main application window."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("stay_healthy — Health Reminder")
        self.root.geometry("520x430")
        self.root.resizable(False, False)

        # Make window appear centered and on top
        self.root.attributes("-topmost", True)

        # Set icon if available (small program icon via window attributes)
        try:
            self.root.iconbitmap(default="")
        except Exception:
            pass

        # Variables
        self.terminate_var = tk.BooleanVar(value=True)   # Terminate existing: Yes
        self.interval_var = tk.StringVar(value="50")      # Lock interval: 50 min
        self.timeout_var = tk.StringVar(value="60")       # Response timeout: 60 sec
        self.silent_var = tk.BooleanVar(value=True)       # Silent mode: Yes

        # Control flags
        self.is_running = False
        self.stop_event = threading.Event()
        self.awake_thread = None
        self.lock_thread = None

        self.setup_ui()

        # Handle window close
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def setup_ui(self):
        """Build the tkinter UI."""
        main_frame = ttk.Frame(self.root, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # ---- Title ----
        title_label = ttk.Label(
            main_frame,
            text="stay_healthy — Health Reminder",
            font=("Segoe UI", 14, "bold")
        )
        title_label.pack(pady=(0, 5))

        subtitle_label = ttk.Label(
            main_frame,
            text="Keeps your PC awake and reminds you to take breaks",
            font=("Segoe UI", 9),
            foreground="#666666"
        )
        subtitle_label.pack(pady=(0, 20))

        # ---- Option 1: Terminate existing instances ----
        frame1 = ttk.Frame(main_frame)
        frame1.pack(fill=tk.X, pady=5)

        self.terminate_cb = ttk.Checkbutton(
            frame1,
            text="Terminate existing running instances",
            variable=self.terminate_var
        )
        self.terminate_cb.pack(anchor=tk.W)

        term_hint = ttk.Label(
            frame1,
            text="Kill other stay_healthy processes before starting",
            font=("Segoe UI", 8),
            foreground="#888888"
        )
        term_hint.pack(anchor=tk.W, padx=(25, 0))

        # Separator
        ttk.Separator(main_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=10)

        # ---- Option 2: Lock interval ----
        frame2 = ttk.Frame(main_frame)
        frame2.pack(fill=tk.X, pady=5)

        ttk.Label(frame2, text="Lock screen interval (minutes):",
                  font=("Segoe UI", 9)).pack(anchor=tk.W)

        input_frame2 = ttk.Frame(frame2)
        input_frame2.pack(fill=tk.X, pady=(3, 0))

        self.interval_entry = ttk.Entry(
            input_frame2,
            textvariable=self.interval_var,
            width=10,
            font=("Segoe UI", 11)
        )
        self.interval_entry.pack(side=tk.LEFT)

        ttk.Label(
            input_frame2,
            text="  How often to prompt you to rest (default: 50 min)",
            font=("Segoe UI", 8),
            foreground="#888888"
        ).pack(side=tk.LEFT, padx=(5, 0))

        # ---- Option 3: Response timeout ----
        frame3 = ttk.Frame(main_frame)
        frame3.pack(fill=tk.X, pady=5)

        ttk.Label(frame3, text="Response timeout (seconds):",
                  font=("Segoe UI", 9)).pack(anchor=tk.W)

        input_frame3 = ttk.Frame(frame3)
        input_frame3.pack(fill=tk.X, pady=(3, 0))

        self.timeout_entry = ttk.Entry(
            input_frame3,
            textvariable=self.timeout_var,
            width=10,
            font=("Segoe UI", 11)
        )
        self.timeout_entry.pack(side=tk.LEFT)

        ttk.Label(
            input_frame3,
            text="  Seconds to wait before auto-lock (default: 60 sec)",
            font=("Segoe UI", 8),
            foreground="#888888"
        ).pack(side=tk.LEFT, padx=(5, 0))

        # Separator
        ttk.Separator(main_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=10)

        # ---- Option 4: Silent mode ----
        frame4 = ttk.Frame(main_frame)
        frame4.pack(fill=tk.X, pady=5)

        self.silent_cb = ttk.Checkbutton(
            frame4,
            text="Silent mode (minimize to system tray after starting)",
            variable=self.silent_var
        )
        self.silent_cb.pack(anchor=tk.W)

        silent_hint = ttk.Label(
            frame4,
            text="No console output; runs in the background",
            font=("Segoe UI", 8),
            foreground="#888888"
        )
        silent_hint.pack(anchor=tk.W, padx=(25, 0))

        # ---- Buttons ----
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, padx=10, pady=(20, 15))

        self.start_button = ttk.Button(
            button_frame,
            text="OK",
            command=self.start_work,
            width=15
        )
        self.start_button.pack(side=tk.LEFT, padx=(0, 10))

        cancel_button = ttk.Button(
            button_frame,
            text="Exit",
            command=self.on_close,
            width=15
        )
        cancel_button.pack(side=tk.RIGHT)

        self.stop_button = ttk.Button(
            button_frame,
            text="■  Stop",
            command=self.stop_work,
            width=15,
            state=tk.DISABLED
        )
        self.stop_button.pack(side=tk.LEFT, padx=(0, 10))

        # ---- Status bar ----
        self.status_var = tk.StringVar(value="Ready — click OK to continue or Exit to quit")
        status_bar = ttk.Label(
            main_frame,
            textvariable=self.status_var,
            font=("Segoe UI", 8),
            foreground="#666666",
            anchor=tk.W
        )
        status_bar.pack(fill=tk.X, pady=(10, 0))

    # ---------------- Tray icon helpers ----------------
    def _make_tray_image(self, size=64, color="#1E90FF"):
        """Create a simple circular tray icon as a PIL Image."""
        if not HAVE_TRAY:
            return None
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        r = size // 2 - 4
        cx = cy = size // 2
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
        return img

    def show_from_tray(self):
        """Restore the main window from the tray."""
        try:
            self.root.deiconify()
            self.root.lift()
            self.stop_tray()
        except Exception:
            pass

    def start_tray(self):
        """Start the system tray icon in a background thread."""
        if not HAVE_TRAY:
            return
        if getattr(self, 'tray_icon', None):
            return

        image = self._make_tray_image(64)

        def on_show(icon, item):
            self.root.after(0, self.show_from_tray)

        def on_exit(icon, item):
            self.root.after(0, self.on_close)

        menu = pystray.Menu(
            pystray.MenuItem('Show', on_show),
            pystray.MenuItem('Exit', on_exit),
        )

        icon = pystray.Icon('stay_healthy', image, 'stay_healthy', menu)
        self.tray_icon = icon

        t = threading.Thread(target=icon.run, daemon=True)
        t.start()

    def stop_tray(self):
        """Stop and remove the tray icon if present."""
        icon = getattr(self, 'tray_icon', None)
        if icon:
            try:
                icon.stop()
            except Exception:
                pass
            self.tray_icon = None

    def validate_inputs(self):
        """Validate user inputs. Returns (interval_min, timeout_sec) or None."""
        try:
            interval = int(self.interval_var.get().strip())
            if interval < MIN_INTERVAL_MINUTES:
                self.show_error(
                    f"Interval must be at least {MIN_INTERVAL_MINUTES} minutes."
                )
                return None
            if interval > 999:
                interval = 999
        except ValueError:
            self.show_error("Invalid interval. Please enter a valid number (minutes).")
            return None

        try:
            timeout = int(self.timeout_var.get().strip())
            if timeout < 5:
                timeout = 5
            if timeout > 300:
                timeout = 300
        except ValueError:
            self.show_error("Invalid timeout. Please enter a valid number (seconds).")
            return None

        return interval, timeout

    def show_error(self, message):
        ctypes.windll.user32.MessageBoxW(
            None, message, "stay_healthy - Input Error",
            MB_ICONWARNING | MB_SYSTEMMODAL | MB_TOPMOST
        )

    def start_work(self):
        """Validate inputs and start the background threads."""
        values = self.validate_inputs()
        if values is None:
            return

        interval_min, timeout_sec = values
        terminate_existing = self.terminate_var.get()
        silent = self.silent_var.get()

        # Terminate existing instances if requested
        if terminate_existing:
            self.status_var.set("Terminating existing instances...")
            self.root.update()
            terminated = terminate_other_instances()
            if terminated:
                time.sleep(0.5)

        # Reset stop event
        self.stop_event.clear()

        # Start the keep-awake thread (Scroll Lock every ~60 seconds)
        self.awake_thread = threading.Thread(
            target=keep_awake_loop,
            args=(60, self.stop_event),
            daemon=True
        )
        self.awake_thread.start()

        # Start the lock-prompt thread
        self.lock_thread = threading.Thread(
            target=lock_loop,
            args=(interval_min, timeout_sec, self.stop_event),
            daemon=True
        )
        self.lock_thread.start()

        self.is_running = True
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)

        # Disable inputs while running
        self.terminate_cb.config(state=tk.DISABLED)
        self.interval_entry.config(state=tk.DISABLED)
        self.timeout_entry.config(state=tk.DISABLED)
        self.silent_cb.config(state=tk.DISABLED)

        self.status_var.set(
            f"▶ Running — Lock every {interval_min} min, "
            f"timeout {timeout_sec} sec"
        )

        if silent:
            # Minimize to system tray area: just hide the window
            self.root.withdraw()
            # start tray icon if available
            try:
                self.start_tray()
            except Exception:
                pass
            self.status_var.set(
                f"▶ Running (silent) — Lock every {interval_min} min, "
                f"timeout {timeout_sec} sec"
            )

    def stop_work(self):
        """Stop the background threads."""
        if self.is_running:
            self.stop_event.set()
            self.is_running = False

            # Wait a bit for threads to finish
            if self.awake_thread and self.awake_thread.is_alive():
                self.awake_thread.join(timeout=2)
            if self.lock_thread and self.lock_thread.is_alive():
                self.lock_thread.join(timeout=2)

        # Restore UI
        self.start_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        self.terminate_cb.config(state=tk.NORMAL)
        self.interval_entry.config(state=tk.NORMAL)
        self.timeout_entry.config(state=tk.NORMAL)
        self.silent_cb.config(state=tk.NORMAL)

        self.status_var.set("⏹  Stopped")

        # Show the window if it was hidden
        try:
            self.stop_tray()
        except Exception:
            pass
        self.root.deiconify()
        self.root.lift()

    def on_close(self):
        """Handle window close event."""
        if self.is_running:
            self.stop_work()
        try:
            self.stop_tray()
        except Exception:
            pass
        self.root.destroy()

    def run(self):
        """Start the tkinter main loop."""
        # Center the window on screen
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (width // 2)
        y = (self.root.winfo_screenheight() // 2) - (height // 2)
        self.root.geometry(f"+{x}+{y}")

        self.root.mainloop()


# ============================================================
# Entry point
# ============================================================

def main():
    # Check single instance
    check_single_instance()

    # Show GUI
    app = StayHealthyApp()
    app.run()

    # Mutex is released automatically by Windows when process exits


if __name__ == "__main__":
    main()