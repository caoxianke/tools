# -*- coding: utf-8 -*-
"""
stay_awake_popup.py - GUI Popup version of stay_awake
======================================================
Author  : Cao Xianke (曹先科)
Platform: Windows (x64)
Created : 2026-06-11

Description:
    Same as stay_awake.py, but uses Windows popup dialogs (MessageBox/InputBox)
    instead of command-line prompts. Designed for the --noconsole (GUI) build.

    Simulates Scroll Lock key presses at regular intervals to prevent
    Windows from locking or sleeping. Optionally auto-locks the workstation
    at a scheduled time ("stay awake" then lock).

Compile to EXE (single file) with PyInstaller:
    pip install pyinstaller
    pyinstaller --onefile --console stay_awake_popup.py
    pyinstaller --onefile --noconsole stay_awake_popup.py   (silent background)
"""

import time
import ctypes
from ctypes import wintypes
import datetime
import sys
import argparse
import os
import threading
import subprocess
import tempfile

# Windows API constants
INPUT_KEYBOARD = 1
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002

# Scroll Lock scan code
SCROLL_LOCK_SCANCODE = 0x46

# MessageBox constants
MB_YESNO = 4
MB_ICONQUESTION = 32
MB_ICONINFORMATION = 64
MB_ICONWARNING = 48
MB_DEFBUTTON2 = 256
IDYES = 6
IDNO = 7

# Mutex name for single instance
MUTEX_NAME = "Global\\AntiLockScrollLock_Mutex"

# Keep mutex handle alive for the entire process lifetime
# (prevents another instance from starting)
_mutex_handle = None

# -------------------- Popup dialog helpers --------------------

def popup_yesno(title, message):
    """
    Show a Windows MessageBox with Yes/No buttons.
    Returns True if user clicked Yes, False otherwise.
    Works in both console and --noconsole modes.
    """
    result = ctypes.windll.user32.MessageBoxW(
        0, message, title, MB_YESNO | MB_ICONQUESTION | MB_DEFBUTTON2
    )
    return result == IDYES

def popup_msg(title, message, icon=MB_ICONINFORMATION):
    """
    Show a Windows MessageBox with an OK button.
    """
    ctypes.windll.user32.MessageBoxW(0, message, title, icon)

def popup_input(title, prompt, default=""):
    """
    Show a Windows InputBox via VBScript.
    Returns the user-entered string, or None if cancelled/empty.
    Works in both console and --noconsole modes.
    """
    vbs_code = """
result = InputBox("{}", "{}", "{}")
If result = "" Then
    WScript.Echo "CANCELLED"
Else
    WScript.Echo result
End If
""".format(prompt.replace('"', '""'), title.replace('"', '""'), default.replace('"', '""'))

    vbs_path = None
    try:
        # Write VBScript to a temp file
        fd, vbs_path = tempfile.mkstemp(suffix='.vbs', prefix='stay_awake_')
        os.close(fd)
        with open(vbs_path, 'w', encoding='utf-8') as f:
            f.write(vbs_code)

        # Run it silently
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE

        result = subprocess.run(
            ['wscript.exe', '//nologo', vbs_path],
            capture_output=True, text=True, timeout=120,
            startupinfo=startupinfo
        )
        output = result.stdout.strip()
        if output == "CANCELLED":
            return None
        return output if output else None
    except Exception:
        return None
    finally:
        if vbs_path and os.path.exists(vbs_path):
            try:
                os.unlink(vbs_path)
            except:
                pass

# -------------------------------------------------------------

class Inputs(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD),
                ("ki", wintypes.DWORD * 24)]

def is_frozen():
    """Check if running as a compiled EXE (PyInstaller)."""
    return getattr(sys, 'frozen', False)

def get_process_name():
    """
    Get the expected process name to search for.
    - For compiled EXE: the EXE filename (e.g. stay_awake_popup.exe)
    - For Python script: python.exe or pythonw.exe
    """
    if is_frozen():
        return os.path.basename(sys.executable).lower()
    else:
        return None

def get_target_name_identifier():
    """
    Get the name identifier to search for in command lines.
    """
    if is_frozen():
        return os.path.basename(sys.executable).lower()
    else:
        return os.path.basename(__file__).lower()

def terminate_existing_instance():
    """
    Find and terminate the existing instance of this script.
    Handles both Python script mode and compiled EXE (PyInstaller) mode.
    Returns True if successfully terminated, False otherwise.
    """
    try:
        import psutil
    except ImportError:
        popup_msg("Error", "psutil module not found.\nPlease install it: pip install psutil\nCannot auto-terminate existing instance.", MB_ICONWARNING)
        return False
    
    process_name_filter = get_process_name()
    target_identifier = get_target_name_identifier()
    current_pid = os.getpid()
    
    found_processes = []
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            proc_pid = proc.info['pid']
            if proc_pid == current_pid:
                continue
            
            proc_name = proc.info['name'].lower() if proc.info['name'] else ''
            cmdline = proc.info['cmdline']
            
            if is_frozen():
                if target_identifier in proc_name:
                    found_processes.append(proc)
            else:
                if proc_name in ['python.exe', 'pythonw.exe'] and cmdline:
                    if target_identifier in ' '.join(cmdline).lower():
                        found_processes.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    
    if not found_processes:
        return False
    
    terminated_count = 0
    for proc in found_processes:
        try:
            proc.terminate()
            proc.wait(timeout=3)
            terminated_count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
            pass
    
    if terminated_count > 0:
        time.sleep(1)
        return True
    else:
        return False

def check_single_instance():
    """
    Check if another instance is running.
    If found, show a popup asking user if they want to terminate it.
    Returns True if it's safe to continue, False otherwise.
    """
    global _mutex_handle
    
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    
    if ctypes.windll.kernel32.GetLastError() == 183:
        if popup_yesno("stay_awake - Instance Detected",
                       "Another instance of stay_awake is already running.\n\n"
                       "Do you want to terminate the existing instance and start this one?"):
            if terminate_existing_instance():
                result = ctypes.windll.kernel32.WaitForSingleObject(mutex, 0)
                if result in (0, 0x00000080):
                    _mutex_handle = mutex
                    time.sleep(0.5)
                    return True
                else:
                    ctypes.windll.kernel32.CloseHandle(mutex)
                    popup_msg("Error", "Could not acquire mutex. You may need to close it manually.", MB_ICONWARNING)
                    return False
            else:
                # Stale mutex
                result = ctypes.windll.kernel32.WaitForSingleObject(mutex, 0)
                if result in (0, 0x00000080):
                    _mutex_handle = mutex
                    return True
                else:
                    ctypes.windll.kernel32.CloseHandle(mutex)
                    popup_msg("Error", "Could not acquire mutex. You may need to close it manually.", MB_ICONWARNING)
                    return False
        else:
            sys.exit(0)
    
    _mutex_handle = mutex
    return True

def press_scroll_lock():
    """Simulate pressing the Scroll Lock key to reset the system's idle timer"""
    ctypes.windll.user32.keybd_event(0x91, SCROLL_LOCK_SCANCODE, 0, 0)
    time.sleep(0.05)
    ctypes.windll.user32.keybd_event(0x91, SCROLL_LOCK_SCANCODE, KEYEVENTF_KEYUP, 0)

def parse_until_time(until_str):
    """Parse time string (HH:MM or HH:MM:SS) into datetime.time object"""
    try:
        parts = until_str.split(':')
        if len(parts) == 2:
            hour, minute = int(parts[0]), int(parts[1])
            return datetime.time(hour, minute, 0)
        elif len(parts) == 3:
            hour, minute, second = int(parts[0]), int(parts[1]), int(parts[2])
            return datetime.time(hour, minute, second)
        else:
            raise ValueError("Invalid time format: {}".format(until_str))
    except (ValueError, IndexError):
        popup_msg("Error", "Invalid time format '{}'.\nUse HH:MM or HH:MM:SS".format(until_str), MB_ICONWARNING)
        return None

def get_target_time(until_time):
    """Get target datetime for stop/lock time (tomorrow if time passed today)"""
    now = datetime.datetime.now()
    target = datetime.datetime.combine(now.date(), until_time)
    
    if target <= now:
        target += datetime.timedelta(days=1)
    
    return target

def keep_awake(interval_seconds=60, until=None, silent=False):
    """
    Keep Windows awake by triggering Scroll Lock at regular intervals.
    Optionally auto-locks the workstation at a scheduled stop time.
    """
    
    if not silent:
        print("=" * 60)
        print("       stay_awake_popup -- by Cao Xianke")
        print("=" * 60)
        print(" Trigger interval: {} seconds".format(interval_seconds))
    
    target_time = None
    if until:
        until_time_obj = parse_until_time(until)
        if until_time_obj is None:
            if not silent:
                print(" Script will run indefinitely until Ctrl+C")
        else:
            target_time = get_target_time(until_time_obj)
            if not silent:
                print(" Scheduled stop & lock: {}".format(target_time.strftime('%Y-%m-%d %H:%M:%S')))
                print("   (Windows will LOCK automatically at this time)")
    
    if not silent:
        print(" Tip: Press Ctrl+C to stop manually (screen will NOT lock)")
        print("-" * 60)
    
    count = 0
    
    try:
        while True:
            if target_time:
                now = datetime.datetime.now()
                if now >= target_time:
                    if not silent:
                        print("\n[ {} ] [!] Scheduled stop time reached".format(now.strftime('%H:%M:%S')))
                        print(" Total Scroll Lock triggers: {}".format(count))
                        print(" Locking workstation now...")
                    ctypes.windll.user32.LockWorkStation()
                    break
            
            time.sleep(interval_seconds)
            press_scroll_lock()
            count += 1
            
            if target_time and not silent:
                remaining = target_time - datetime.datetime.now()
                if 0 < remaining.total_seconds() <= 3600:
                    minutes_left = int(remaining.total_seconds() / 60)
                    if minutes_left <= 10 or minutes_left % 30 == 0:
                        print("[{}] Triggered #{} (locking in {}m)".format(time.strftime('%H:%M:%S'), count, minutes_left))
                else:
                    print("[{}] Triggered Scroll Lock #{}".format(time.strftime('%H:%M:%S'), count))
            elif not silent:
                print("[{}] Triggered Scroll Lock #{}".format(time.strftime('%H:%M:%S'), count))
                
    except KeyboardInterrupt:
        if not silent:
            print("\n\n{}".format('=' * 60))
            print("[!] Manually stopped by user (Ctrl+C)")
            print(" Total Scroll Lock triggers: {}".format(count))
            print(" NOTE: Screen was NOT locked (manual stop)")
            print("{}".format('=' * 60))

def prompt_for_params_popup():
    """
    Show popup windows to ask user for interval, until-time, and confirm.
    Returns (interval_seconds, until, silent).
    """
    # Step 1: Interval
    while True:
        raw = popup_input("stay_awake - Interval", "Trigger interval in seconds\n(leave empty for default 60):", "60")
        if raw is None or raw == "":
            interval = 60
            break
        try:
            interval = int(raw.strip())
            if interval > 0:
                break
            popup_msg("Invalid Input", "Please enter a positive number.", MB_ICONWARNING)
        except ValueError:
            popup_msg("Invalid Input", "Please enter a valid number.", MB_ICONWARNING)

    # Step 2: Until time
    raw = popup_input("stay_awake - Lock Time", "Auto-lock time in HH:MM\n(leave empty for no auto-lock):\n\nCurrent params:\n  Interval: {} seconds".format(interval), "")
    until = raw.strip() if raw and raw.strip() else None

    # Step 3: Confirm
    confirm_msg = "stay_awake will start with:\n\n"
    confirm_msg += "  Interval: {} seconds\n".format(interval)
    if until:
        confirm_msg += "  Auto-lock: {}\n".format(until)
    else:
        confirm_msg += "  Auto-lock: none\n"
    confirm_msg += "\nClick Yes to start, No to cancel."

    if not popup_yesno("stay_awake - Confirm", confirm_msg):
        sys.exit(0)

    return interval, until, False

def main():
    """
    stay_awake_popup: Prevent Windows lock via Scroll Lock simulation.

    Uses popup dialogs for user interaction, making it suitable for
    both --console and --noconsole PyInstaller builds.
    """
    
    parser = argparse.ArgumentParser(
        prog='stay_awake_popup',
        description='Prevent Windows screen lock by simulating Scroll Lock key presses, '
                    'then auto-lock at a scheduled time (popup version)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples (Windows):
  stay_awake_popup.exe                     # Popup input, run indefinitely
  stay_awake_popup.exe -i 120              # Popup if instance exists, run with 120s
  stay_awake_popup.exe --silent            # No popups, run silently
  stay_awake_popup.exe --silent -u 08:00   # Silent mode with auto-lock at 8 AM
  stay_awake_popup.exe --help              # Show this help message

Compile to EXE with PyInstaller:
  pip install pyinstaller
  pyinstaller --onefile --console stay_awake_popup.py
  pyinstaller --onefile --noconsole stay_awake_popup.py   (silent background)

Single-instance behaviour:
  If another instance is already running, a popup will ask you
  whether to terminate the existing instance and start a new one.

Author: Cao Xianke (曹先科)
        """
    )
    
    parser.add_argument('-i', '--interval', type=int, default=60,
                        help='Trigger interval in seconds (default: 60)')
    parser.add_argument('-u', '--until', type=str, default=None,
                        help='Stop time in HH:MM or HH:MM:SS - screen will LOCK at this time')
    parser.add_argument('-s', '--silent', action='store_true',
                        help='Silent mode: suppress all console output and popups')
    
    args = parser.parse_args()
    
    # Check for existing instance
    if args.silent:
        _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if ctypes.windll.kernel32.GetLastError() == 183:
            mutex = _mutex_handle
            if terminate_existing_instance():
                result = ctypes.windll.kernel32.WaitForSingleObject(mutex, 0)
                if result in (0, 0x00000080):
                    _mutex_handle = mutex
            else:
                result = ctypes.windll.kernel32.WaitForSingleObject(mutex, 0)
                if result in (0, 0x00000080):
                    _mutex_handle = mutex
                else:
                    ctypes.windll.kernel32.CloseHandle(mutex)
                    sys.exit(1)
    else:
        if not check_single_instance():
            sys.exit(1)
    
    # If no CLI arguments given, show popup input dialog
    if not args.silent and args.interval == 60 and args.until is None:
        interval, until, silent = prompt_for_params_popup()
        args.interval = interval
        args.until = until
        args.silent = silent
    
    keep_awake(interval_seconds=args.interval, until=args.until, silent=args.silent)

    

if __name__ == "__main__":
    main()