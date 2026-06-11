# -*- coding: utf-8 -*-
"""
stay_healthy_cli.py — CLI-only version of stay_healthy
=======================================================
Author  : Cao Xianke (曹先科)
Platform: Windows (x64)
Created : 2026-06-11

Description:
    CLI-only version of stay_healthy with no GUI, no pop-up windows.
    All configuration is done via command-line arguments.

    Combines:
    - stay_awake: Scroll Lock simulation to prevent system lock
    - screen_locker: Periodic lock prompts with skip option

    Four CLI options:
    1. --terminate / -t  : Terminate existing instances (default: yes)
    2. --interval / -i   : Lock screen interval in minutes (default: 50)
    3. --timeout / -w    : Response timeout in seconds (default: 60)
    4. --silent / -s     : Supress all console output (default: no)

Compile to EXE (single file) with PyInstaller:
    pip install pyinstaller
    pyinstaller --onefile --console stay_healthy_cli.py
    # Output: dist/stay_healthy_cli.exe

    For silent background mode (no console window):
    pyinstaller --onefile --noconsole stay_healthy_cli.py
    # Output: dist/stay_healthy_cli.exe
"""

import time
import ctypes
from ctypes import wintypes
import datetime
import sys
import os
import threading
import argparse

# Windows API constants
INPUT_KEYBOARD = 1
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002
SCROLL_LOCK_SCANCODE = 0x46

# MessageBox constants
MB_YESNO = 0x0004
MB_ICONQUESTION = 0x0020
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

# Prompt message box flags
PROMPT_FLAGS = (MB_ABORTRETRYIGNORE | MB_ICONQUESTION | MB_SYSTEMMODAL |
                MB_TOPMOST | MB_SETFOREGROUND | MB_DEFBUTTON2)

# Postpone snooze duration (minutes)
POSTPONE_MINUTES = 5

# Mutex name
MUTEX_NAME = "Global\\StayHealthy_SingleInstance_Mutex"

_mutex_handle = None


# ============================================================
# Logging helper
# ============================================================

def log(msg, silent=False):
    """Print a timestamped log message unless silent mode is on."""
    if not silent:
        timestamp = datetime.datetime.now().strftime('%H:%M:%S')
        print(f"[{timestamp}] {msg}", flush=True)


def log_info(msg, silent=False):
    if not silent:
        print(f"[i] {msg}", flush=True)


def log_ok(msg, silent=False):
    if not silent:
        print(f"[+] {msg}", flush=True)


def log_warn(msg, silent=False):
    if not silent:
        print(f"[!] {msg}", flush=True)


# ============================================================
# Scroll Lock key simulation
# ============================================================

def press_scroll_lock():
    """Simulate pressing the Scroll Lock key."""
    ctypes.windll.user32.keybd_event(0x91, SCROLL_LOCK_SCANCODE, 0, 0)
    time.sleep(0.05)
    ctypes.windll.user32.keybd_event(0x91, SCROLL_LOCK_SCANCODE, KEYEVENTF_KEYUP, 0)


def keep_awake_loop(interval_seconds, stop_event, silent=False):
    """
    Press Scroll Lock every interval_seconds in a background thread.
    Prints a log message on each trigger.
    """
    trigger_count = 0
    while not stop_event.is_set():
        for _ in range(interval_seconds):
            if stop_event.is_set():
                return
            time.sleep(1)

        press_scroll_lock()
        trigger_count += 1
        log(f"Triggered Scroll Lock #{trigger_count}", silent)

    log(f"[stop] Keep-awake loop terminated ({trigger_count} triggers)", silent)


# ============================================================
# Lock prompt / screen lock
# ============================================================

def lock_workstation():
    """Lock the Windows workstation."""
    ctypes.windll.user32.LockWorkStation()


def show_lock_prompt(interval_minutes, prompt_timeout_seconds, silent=False):
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

    result = [IDTIMEOUT]
    event = threading.Event()

    def show_box():
        result[0] = ctypes.windll.user32.MessageBoxW(
            None, message, "Take a Rest - Screen Lock", PROMPT_FLAGS
        )
        event.set()

    box_thread = threading.Thread(target=show_box)
    box_thread.daemon = True
    box_thread.start()

    event.wait(timeout=prompt_timeout_seconds)

    if box_thread.is_alive():
        log_warn(f"No response within {prompt_timeout_seconds}s — auto-locking", silent)
        return 'lock'

    choice = result[0]
    if choice == IDABORT:
        log("User clicked 'Abort' — locking now", silent)
        return 'lock'
    elif choice == IDRETRY:
        log("User clicked 'Retry' — skipping this cycle", silent)
        return 'skip'
    elif choice == IDIGNORE:
        log(f"User clicked 'Ignore' — postponing {POSTPONE_MINUTES} minutes", silent)
        return 'postpone'
    else:
        log_warn(f"Unexpected response ({choice}) — locking", silent)
        return 'lock'


def lock_loop(interval_minutes, prompt_timeout_seconds, stop_event, silent=False):
    """
    Main lock loop: wait for interval, prompt, lock, skip, or postpone.
    Runs in a background thread.
    """
    cycle_count = 0
    skip_count = 0
    lock_count = 0
    postpone_count = 0

    log_ok(f"Lock cycle started: every {interval_minutes} min, timeout {prompt_timeout_seconds}s", silent)

    while not stop_event.is_set():
        # Wait for the interval
        for _ in range(interval_minutes * 60):
            if stop_event.is_set():
                log("[stop] Lock loop terminated by stop event", silent)
                return
            time.sleep(1)

        if stop_event.is_set():
            return

        cycle_count += 1
        log(f"[LOCK CYCLE #{cycle_count}] Prompting user...", silent)

        action = show_lock_prompt(interval_minutes, prompt_timeout_seconds, silent)

        if action == 'lock':
            lock_count += 1
            log(f"[LOCK] Locking workstation...", silent)
            lock_workstation()
            log(f"[OK] Workstation unlocked, resuming...", silent)
        elif action == 'postpone':
            postpone_count += 1
            log(f"[POSTPONE] Postponing {POSTPONE_MINUTES} minutes ({postpone_count} total postpones)", silent)
            # Wait POSTPONE_MINUTES before prompting again
            for _ in range(POSTPONE_MINUTES * 60):
                if stop_event.is_set():
                    log("[stop] Lock loop terminated during postpone", silent)
                    return
                time.sleep(1)
            # After postpone, prompt again immediately
            continue
        else:  # 'skip'
            skip_count += 1
            log(f"[SKIP] Skipped cycle #{cycle_count} ({skip_count} total skips)", silent)

        start = datetime.datetime.now()
        log(f"Next prompt at: {(start + datetime.timedelta(minutes=interval_minutes)).strftime('%H:%M:%S')}", silent)

    log(f"[stop] Lock loop terminated — cycles: {cycle_count}, locks: {lock_count}, skips: {skip_count}, postpones: {postpone_count}", silent)


# ============================================================
# Single-instance handling
# ============================================================

def check_single_instance(silent=False):
    """
    Check if another instance is running.
    If found, ask (via MessageBox) whether to terminate it.
    Returns True to continue, False to exit.
    """
    global _mutex_handle

    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    last_error = ctypes.windll.kernel32.GetLastError()

    if last_error == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.kernel32.CloseHandle(mutex)

        if silent:
            # In silent mode, auto-terminate without prompt
            log_warn("Another instance detected — auto-terminating (silent mode)", silent)
            if terminate_other_instances(silent):
                time.sleep(1)
                _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
                last_error = ctypes.windll.kernel32.GetLastError()
                if last_error == 183:
                    log_warn("Failed to terminate existing instance. Exiting.", silent)
                    sys.exit(1)
                return True
            else:
                log_warn("Could not terminate existing instance. Exiting.", silent)
                sys.exit(1)
        else:
            answer = ctypes.windll.user32.MessageBoxW(
                None,
                "Another instance of stay_healthy_cli is already running.\n\n"
                "Click 'Yes' to terminate it and continue.\n"
                "Click 'No' to exit.",
                "stay_healthy_cli - Already Running",
                MB_YESNO | MB_ICONQUESTION | MB_SYSTEMMODAL | MB_TOPMOST | MB_SETFOREGROUND | MB_DEFBUTTON2
            )

            if answer == IDYES:
                log_info("Terminating existing instance...", silent)
                terminate_other_instances(silent)
                time.sleep(1)
                _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
                last_error = ctypes.windll.kernel32.GetLastError()
                if last_error == 183:
                    log_warn("Failed to terminate existing instance. Exiting.", silent)
                    sys.exit(1)
                return True
            else:
                log_info("User chose to exit.", silent)
                sys.exit(0)

    _mutex_handle = mutex
    return True


def terminate_other_instances(silent=False):
    """
    Find and terminate other stay_healthy_cli processes.
    Uses Win32 API — no external dependencies.
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
    found_pids = []

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
                found_pids.append(pid)

            if not kernel32.Process32NextW(hSnapshot, ctypes.byref(pe32)):
                break
    finally:
        kernel32.CloseHandle(hSnapshot)

    for pid in found_pids:
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if handle:
            kernel32.TerminateProcess(handle, 0)
            kernel32.CloseHandle(handle)
            log_ok(f"Terminated PID {pid}", silent)
            terminated = True

    if terminated:
        log_ok("Existing instance(s) terminated", silent)
    else:
        log_info("No other instances found", silent)

    return terminated


# ============================================================
# Wait thread — keeps the main thread alive
# ============================================================

def wait_loop(stop_event, silent=False):
    """
    Main thread waits here until KeyboardInterrupt.
    """
    try:
        while not stop_event.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        log("\n[!] Stopped by user (Ctrl+C)", silent)
        stop_event.set()


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        prog='stay_healthy_cli',
        description='Health reminder & keep-awake tool — CLI version.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  stay_healthy_cli.exe
  stay_healthy_cli.exe --interval 30 --timeout 15
  stay_healthy_cli.exe -i 60 -w 30 --no-terminate --silent
  stay_healthy_cli.exe --help

Compile to EXE:
  pip install pyinstaller
  pyinstaller --onefile --console stay_healthy_cli.py       # with console
  pyinstaller --onefile --noconsole stay_healthy_cli.py      # background only
        """
    )

    parser.add_argument(
        '-t', '--terminate', action='store_true', default=True,
        help='Terminate existing instances before starting (default: yes)'
    )
    parser.add_argument(
        '--no-terminate', action='store_false', dest='terminate',
        help='Do NOT terminate existing instances'
    )
    parser.add_argument(
        '-i', '--interval', type=int, default=50,
        help='Lock screen interval in minutes (default: 50, min: 1, max: 999)'
    )
    parser.add_argument(
        '-w', '--timeout', type=int, default=60,
        help='Response timeout in seconds (default: 60, min: 5, max: 300)'
    )
    parser.add_argument(
        '-s', '--silent', action='store_true',
        help='Silent mode: suppress all console output'
    )

    args = parser.parse_args()

    # Validate
    interval = max(1, min(999, args.interval))
    timeout = max(5, min(300, args.timeout))
    silent = args.silent
    do_terminate = args.terminate

    # Print banner (even in silent mode, since it goes to console once)
    if not silent:
        print("=" * 60)
        print("       stay_healthy_cli — by Cao Xianke")
        print("=" * 60)
        print(f" Lock interval:  every {interval} minute(s)")
        print(f" Prompt timeout: {timeout} second(s)")
        print(f" Terminate existing: {'yes' if do_terminate else 'no'}")
        print(f" Silent mode:    {'yes' if silent else 'no'}")
        print("-" * 60)
        print(" Stay-awake: Scroll Lock simulated every 60s to prevent lock")
        print(" Lock cycle:  A Windows lock prompt will appear periodically")
        print("              [Abort]  = Lock screen now")
        print("              [Retry]  = Skip this cycle")
        print("              [Ignore] = Postpone 5 minutes (continue working)")
        print("              No response = auto-lock after timeout")
        print("-" * 60)
        print(" Press Ctrl+C to stop.")
        print("=" * 60)

    # Single-instance check
    check_single_instance(silent=silent)

    # Terminate existing if requested
    if do_terminate:
        terminate_other_instances(silent=silent)

    # Create stop event
    stop_event = threading.Event()

    # Start keep-awake thread
    awake_thread = threading.Thread(
        target=keep_awake_loop,
        args=(60, stop_event, silent),
        daemon=True
    )
    awake_thread.start()
    if not silent:
        log_ok("Keep-awake thread started (Scroll Lock every 60s)", silent)

    # Start lock loop thread
    lock_thread = threading.Thread(
        target=lock_loop,
        args=(interval, timeout, stop_event, silent),
        daemon=True
    )
    lock_thread.start()
    if not silent:
        log_ok(f"Lock thread started (every {interval} min, timeout {timeout}s)", silent)

    # Wait for Ctrl+C in main thread
    wait_loop(stop_event, silent)

    # Cleanup (threads are daemon, but wait a moment)
    stop_event.set()
    time.sleep(0.5)

    if not silent:
        print("\n" + "=" * 60)
        print(" stay_healthy_cli stopped cleanly.")
        print("=" * 60)


if __name__ == "__main__":
    main()