# -*- coding: utf-8 -*-
"""
screen_locker.py — Periodically Lock Screen with Skip Option
==============================================================
Author  : Cao Xianke (曹先科)
Platform: Windows (x64)
Created : 2026-06-10

Description:
    Periodically locks the workstation at a user-specified interval.
    Before locking, a Windows message box appears asking the user if
    they want to skip (postpone) the lock. If the user clicks "Yes"
    or does not respond within a timeout, the screen is locked.
    If the user clicks "No", the timer resets and the next lock
    cycle begins.

    On startup, a tkinter input dialog asks for the lock interval
    (in minutes) and the prompt timeout (in seconds) with defaults
    of 50 minutes and 30 seconds.

    Useful for enforcing regular breaks, security timeouts, or
    simply reminding yourself to take a rest from the computer.

Compile to EXE (single file) with PyInstaller:
    pip install pyinstaller
    pyinstaller --onefile --console screen_locker.py
    # Output: dist/screen_locker.exe

    For silent background mode (no console window):
    pyinstaller --onefile --noconsole screen_locker.py
    # Output: dist/screen_locker.exe
"""

import time
import ctypes
import sys
import argparse
import threading
import datetime
import os

# Windows API constants for MessageBox
MB_YESNO = 0x0004
MB_ICONQUESTION = 0x0020
MB_SYSTEMMODAL = 0x00001000
MB_TOPMOST = 0x00040000
MB_SETFOREGROUND = 0x00010000

# Combine flags: question icon + Yes/No buttons + system modal + topmost
MB_FLAGS = MB_YESNO | MB_ICONQUESTION | MB_SYSTEMMODAL | MB_TOPMOST | MB_SETFOREGROUND

# MessageBox return values
IDYES = 6      # User clicked "Yes"
IDNO = 7       # User clicked "No"
IDTIMEOUT = 32000  # Custom: we treat timeout as "Yes" (lock)


# Windows API constants for kernel32
ERROR_ALREADY_EXISTS = 183
MUTEX_NAME = "Global\\ScreenLocker_SingleInstance_Mutex"


def terminate_existing_instances(current_pid=None):
    """
    Find and terminate any existing screen_locker processes 
    (compiled .exe or running as python script), excluding the 
    current process.

    Uses Win32 API directly (CreateToolhelp32Snapshot / OpenProcess /
    TerminateProcess) — this works reliably WITHOUT a console window,
    unlike subprocess-based wmic/taskkill alternatives.

    Args:
        current_pid: PID of the current process to exclude from
                     termination (important: don't kill ourselves).
    """
    from ctypes import wintypes

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

    # Base name of the current executable (e.g. "screen_locker.exe")
    current_exe = os.path.basename(sys.executable).lower()
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

            # Skip the current process — don't kill ourselves
            if current_pid is not None and pid == current_pid:
                if not kernel32.Process32NextW(hSnapshot, ctypes.byref(pe32)):
                    break
                continue

            exe_file = pe32.szExeFile.lower()

            # Match: same executable name (compiled binary), 
            # or anything containing "screen_locker" (script run via python).
            if exe_file == current_exe or "screen_locker" in exe_file:
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


def check_single_instance():
    """
    Ensure only one instance of this program runs on the same machine.
    Uses a Windows named mutex. If the mutex already exists, the program
    asks the user whether to terminate the existing instance and proceed,
    or exit.
    """
    mutex = ctypes.windll.kernel32.CreateMutexW(
        None, False, MUTEX_NAME
    )
    last_error = ctypes.windll.kernel32.GetLastError()
    if last_error == ERROR_ALREADY_EXISTS:
        ctypes.windll.kernel32.CloseHandle(mutex)

        # Ask the user: terminate existing instance or exit?
        MB_YESNO = 0x0004
        MB_ICONQUESTION = 0x0020
        MB_SYSTEMMODAL = 0x00001000
        answer = ctypes.windll.user32.MessageBoxW(
            None,
            "Another instance of screen_locker is already running.\n\n"
            "Do you want to terminate the existing instance and start a new one?\n\n"
            "Click 'Yes' to terminate it and continue.\n"
            "Click 'No' to exit.",
            "screen_locker - Already Running",
            MB_YESNO | MB_ICONQUESTION | MB_SYSTEMMODAL | MB_TOPMOST | MB_SETFOREGROUND
        )

        if answer == IDYES:
            # Terminate existing instances
            print("[INFO] Terminating existing screen_locker instance...")
            terminate_existing_instances(current_pid=os.getpid())
            # Give the OS a moment to release the mutex, then retry
            time.sleep(1)
            mutex = ctypes.windll.kernel32.CreateMutexW(
                None, False, MUTEX_NAME
            )
            last_error = ctypes.windll.kernel32.GetLastError()
            if last_error == ERROR_ALREADY_EXISTS:
                ctypes.windll.user32.MessageBoxW(
                    None,
                    "Failed to terminate the existing instance.\n"
                    "Please close it manually and try again.",
                    "screen_locker - Error",
                    0x00000010 | 0x00001000  # MB_ICONERROR | MB_SYSTEMMODAL
                )
                sys.exit(1)
            return mutex
        else:
            # User chose not to terminate -> exit
            sys.exit(0)

    return mutex


def lock_workstation():
    """Lock the Windows workstation immediately."""
    ctypes.windll.user32.LockWorkStation()


def show_lock_prompt(interval_minutes, prompt_timeout_seconds):
    """
    Show a Windows message box asking if the user wants to skip the lock.

    Returns:
        True  -> lock the screen (user clicked Yes, or timed out)
        False -> skip this lock cycle (user clicked No)
    """
    message = (
        f"Time to take a rest!\n\n"
        f"You've been working for {interval_minutes} minute(s).\n"
        f"Your eyes and body need a break.\n\n"
        f"The screen will be locked now.\n"
        f"Click 'Yes' to lock it now, or 'No' to skip this time.\n\n"
        f"(Auto-lock in {prompt_timeout_seconds} seconds if no response)"
    )
    title = "Take a Rest - Screen Lock"

    # We'll use a threaded approach with a timeout
    result = [IDTIMEOUT]  # Default to timeout (lock)
    event = threading.Event()

    def show_box():
        # MessageBox returns the button clicked
        result[0] = ctypes.windll.user32.MessageBoxW(
            None, message, title, MB_FLAGS
        )
        event.set()

    box_thread = threading.Thread(target=show_box)
    box_thread.daemon = True
    box_thread.start()

    # Wait for the timeout or the user's response
    event.wait(timeout=prompt_timeout_seconds)

    if box_thread.is_alive():
        # Timeout occurred, auto-lock
        print(f"[!] No response within {prompt_timeout_seconds} seconds. Auto-locking...")
        # We can't easily close the MessageBox from another thread in Windows,
        # so we leave it open. The user will see it after the lock screen
        # is dismissed (since the lock screen suspends the message box).
        return True  # Lock

    return result[0] == IDYES or result[0] == IDTIMEOUT


def run_lock_loop(interval_minutes, prompt_timeout=30, skip_first=False):
    """
    Main loop: wait for the interval, then prompt the user.
    
    Args:
        interval_minutes: Minutes between lock prompts.
        prompt_timeout: Seconds to wait for user response before auto-locking.
        skip_first: If True, skip the first lock cycle (start with waiting).
    """
    interval_seconds = interval_minutes * 60

    print("=" * 60)
    print("       screen_locker - by Cao Xianke")
    print("=" * 60)
    print(f" Lock interval: every {interval_minutes} minute(s)")
    print(f" Prompt timeout: {prompt_timeout} seconds")
    print(f" Skip first cycle: {skip_first}")
    print("-" * 60)
    print(" This script forces you to take regular breaks.")
    print(" A message box will remind you to rest before locking.")
    print(" Click 'Yes' to lock now, 'No' to skip this once.")
    print(" If you don't respond, the screen will lock automatically.")
    print("-" * 60)
    print(" Press Ctrl+C to exit.")
    print("=" * 60)

    cycle_count = 0
    skip_count = 0

    if skip_first:
        print("\n[*] Skipping first cycle...")
        next_prompt = datetime.datetime.now() + datetime.timedelta(seconds=interval_seconds)
        print(f" Next prompt at: {next_prompt.strftime('%H:%M:%S')}")
    else:
        # First prompt fires immediately
        next_prompt = datetime.datetime.now()

    try:
        while True:
            # Wait until the next prompt time
            now = datetime.datetime.now()
            if now < next_prompt:
                sleep_seconds = (next_prompt - now).total_seconds()
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)

            cycle_count += 1
            now_str = datetime.datetime.now().strftime('%H:%M:%S')
            print(f"\n[{now_str}] [LOCK] Lock cycle #{cycle_count}")

            # Show the prompt
            should_lock = show_lock_prompt(interval_minutes, prompt_timeout)

            if should_lock:
                now_str = datetime.datetime.now().strftime('%H:%M:%S')
                print(f"[{now_str}] [LOCK] Locking workstation...")
                lock_workstation()
                # After lock is dismissed (user logs back in), schedule next prompt
                print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] [OK] Unlocked. Resuming...")
            else:
                skip_count += 1
                now_str = datetime.datetime.now().strftime('%H:%M:%S')
                print(f"[{now_str}] [SKIP] User skipped (No). Next prompt in {interval_minutes} minute(s).")

            # Schedule next prompt
            next_prompt = datetime.datetime.now() + datetime.timedelta(seconds=interval_seconds)
            print(f" Next prompt at: {next_prompt.strftime('%H:%M:%S')}")

    except KeyboardInterrupt:
        print(f"\n\n{'=' * 60}")
        print("[!] Stopped by user (Ctrl+C)")
        print(f" Total cycles: {cycle_count}")
        print(f" Times skipped: {skip_count}")
        print(f" Times locked:  {cycle_count - skip_count}")
        print(f"{'=' * 60}")


def show_input_dialog(default_minutes=50, default_timeout=30):
    """
    Show native Windows input dialogs for interval and timeout using
    PowerShell's built-in InputBox (VisualBasic.Interaction).
    Falls back to defaults if the user cancels or PowerShell fails.

    This is more reliable in --noconsole compiled binaries than tkinter,
    since it uses native Windows UI components via PowerShell.

    Returns:
        tuple: (interval_minutes, prompt_timeout_seconds)
    """
    import subprocess
    def powershell_inputbox(title, prompt, default_value):
        """Show a native Windows InputBox via PowerShell and return the value."""
        ps_script = (
            '[System.Reflection.Assembly]::LoadWithPartialName("Microsoft.VisualBasic") | Out-Null; '
            '$result = [Microsoft.VisualBasic.Interaction]::InputBox('
            f'"{prompt}", '
            f'"{title}", '
            f'"{default_value}"'
            '); '
            'Write-Output $result'
        )
        try:
            r = subprocess.run(
                ['powershell', '-NoProfile', '-Command', ps_script],
                capture_output=True, text=True, timeout=30,
                creationflags=0x08000000  # CREATE_NO_WINDOW
            )
            output = r.stdout.strip()
            # If user clicked Cancel, output will be empty string
            return output if output else None
        except Exception:
            return None

    # Get minutes
    result = powershell_inputbox(
        "Screen Locker - Interval Setting",
        "Enter the lock interval (in minutes):" +
        "\n\nHow often should the screen lock?" +
        "\n(Default: 50 minutes)",
        str(default_minutes)
    )

    if result is None or not result.strip():
        minutes = default_minutes
    else:
        try:
            minutes = int(result.strip())
            if minutes < 1:
                minutes = 1
            if minutes > 999:
                minutes = 999
        except ValueError:
            minutes = default_minutes

    # Get timeout
    result = powershell_inputbox(
        "Screen Locker - Prompt Timeout Setting",
        "Enter the prompt timeout (in seconds):" +
        "\n\nHow many seconds to wait for your response" +
        "\nbefore the screen locks automatically?" +
        "\n(Default: 30 seconds, minimum: 5)",
        str(default_timeout)
    )

    if result is None or not result.strip():
        timeout = default_timeout
    else:
        try:
            timeout = int(result.strip())
            if timeout < 5:
                timeout = 5
            if timeout > 300:
                timeout = 300
        except ValueError:
            timeout = default_timeout

    return minutes, timeout


def main():
    """
    screen_locker: Periodically lock the screen with a skip option.
    """
    parser = argparse.ArgumentParser(
        prog='screen_locker',
description='Force regular rest breaks by periodically locking the '
                    'Windows workstation with a skip-able prompt.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  screen_locker.exe -m 30              # Prompt every 30 minutes
  screen_locker.exe -m 60 -t 10        # Prompt every 60 min, 10 sec timeout
  screen_locker.exe -m 45 --skip-first # Skip first prompt, then every 45 min
  screen_locker.exe --help             # Show this help message

Compile to EXE with PyInstaller:
  pip install pyinstaller
  pyinstaller --onefile --console screen_locker.py
  pyinstaller --onefile --noconsole screen_locker.py   (silent background)

Author: Cao Xianke (曹先科)
        """
    )

    # Enforce single instance
    mutex_handle = check_single_instance()

    parser.add_argument('-m', '--minutes', type=int, default=50,
                        help='Lock interval in minutes (default: 50)')
    parser.add_argument('-t', '--timeout', type=int, default=30,
                        help='Prompt timeout in seconds before auto-lock (default: 30)')
    parser.add_argument('--skip-first', action='store_true',
                        help='Skip the first lock cycle (start with a waiting period)')

    args = parser.parse_args()

    # Check if user explicitly provided CLI arguments
    # sys.argv contains the script name + any arguments passed
    has_cli_args = len(sys.argv) > 1

    if has_cli_args:
        # Use CLI-provided values (with validation)
        minutes = args.minutes
        timeout = args.timeout
        skip_first = args.skip_first
    else:
        # Show input dialogs to get values from the user
        print("\n[INFO] Showing input dialogs for configuration...")
        minutes, timeout = show_input_dialog(
            default_minutes=args.minutes,
            default_timeout=args.timeout
        )
        # When using the dialog, skip the first cycle so the lock
        # doesn't trigger immediately — wait for the full interval first.
        skip_first = True

    if minutes < 1:
        print("[ERROR] Interval must be at least 1 minute.")
        sys.exit(1)

    if timeout < 5:
        print("[WARN] Timeout is very short (< 5 seconds). Setting to 5 seconds minimum.")
        timeout = 5

    run_lock_loop(
        interval_minutes=minutes,
        prompt_timeout=timeout,
        skip_first=skip_first
    )


    # No need to explicitly close the mutex handle on exit;
    # the OS cleans it up when the process terminates.

if __name__ == "__main__":
    main()