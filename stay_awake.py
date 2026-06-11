# -*- coding: utf-8 -*-
"""
stay_awake.py - Prevent Windows Screen Lock via Scroll Lock
=============================================================
Author  : Cao Xianke (曹先科)
Platform: Windows (x64)
Created : 2026-06-10

Description:
    Simulates Scroll Lock key presses at regular intervals to prevent
    Windows from locking or sleeping. Optionally auto-locks the workstation
    at a scheduled time ("stay awake" then lock).

    Also known as an "anti-idle" or "keep-awake" tool for Windows.

Compile to EXE (single file) with PyInstaller:
    pip install pyinstaller
    pyinstaller --onefile --console stay_awake.py
    # Output: dist/stay_awake.exe

    For silent background mode (no console window):
    pyinstaller --onefile --noconsole stay_awake.py
    # Output: dist/stay_awake.exe
"""

import time
import ctypes
from ctypes import wintypes
import datetime
import sys
import argparse
import os
import threading

# Windows API constants
INPUT_KEYBOARD = 1
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002

# Scroll Lock scan code
SCROLL_LOCK_SCANCODE = 0x46

# Mutex name for single instance
MUTEX_NAME = "Global\\AntiLockScrollLock_Mutex"

# Keep mutex handle alive for the entire process lifetime
# (prevents another instance from starting)
_mutex_handle = None

class Inputs(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD),
                ("ki", wintypes.DWORD * 24)]

def timed_input(prompt, timeout=10):
    """
    Get user input with a timeout.
    Returns the input string or None if timeout occurs.
    """
    print(prompt, end=' ', flush=True)
    user_input = [None]
    
    def get_input():
        try:
            user_input[0] = sys.stdin.readline().strip().lower()
        except:
            pass
    
    input_thread = threading.Thread(target=get_input)
    input_thread.daemon = True
    input_thread.start()
    input_thread.join(timeout)
    
    if input_thread.is_alive():
        print("\n[!] No response within {} seconds. Auto-terminating existing instance...".format(timeout))
        return None
    else:
        return user_input[0]

def is_frozen():
    """Check if running as a compiled EXE (PyInstaller)."""
    return getattr(sys, 'frozen', False)

def get_process_name():
    """
    Get the expected process name to search for.
    - For compiled EXE: the EXE filename (e.g. stay_awake.exe)
    - For Python script: python.exe or pythonw.exe
    """
    if is_frozen():
        # Running as compiled EXE -- use the executable filename
        return os.path.basename(sys.executable).lower()
    else:
        # Running as Python script -- search for Python processes
        return None  # We'll check both python.exe and pythonw.exe

def get_target_name_identifier():
    """
    Get the name identifier to search for in command lines.
    - For compiled EXE: the EXE filename (e.g. stay_awake.exe)
    - For Python script: the .py script filename
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
        print("[!] psutil module not found. Please install it: pip install psutil")
        print("   Cannot auto-terminate existing instance.")
        return False
    
    # Determine how to identify instances
    process_name_filter = get_process_name()
    target_identifier = get_target_name_identifier()
    current_pid = os.getpid()
    
    if process_name_filter:
        print("[*] Searching for existing instances of {} (compiled EXE mode)...".format(target_identifier))
    else:
        print("[*] Searching for existing instances of {} (script mode)...".format(target_identifier))
    
    found_processes = []
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            proc_pid = proc.info['pid']
            if proc_pid == current_pid:
                continue
            
            proc_name = proc.info['name'].lower() if proc.info['name'] else ''
            cmdline = proc.info['cmdline']
            
            if is_frozen():
                # EXE mode: match by process name containing the EXE filename
                if target_identifier in proc_name:
                    found_processes.append(proc)
            else:
                # Script mode: match Python processes whose cmdline contains the .py file
                if proc_name in ['python.exe', 'pythonw.exe'] and cmdline:
                    if target_identifier in ' '.join(cmdline).lower():
                        found_processes.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    
    if not found_processes:
        print("[!] No existing instance found (mutex may be stale).")
        return False
    
    print("[^] Found {} existing instance(s):".format(len(found_processes)))
    for proc in found_processes:
        cmd_preview = ' '.join(proc.info['cmdline'])[:80]
        print("   - PID: {}, Command: {}...".format(proc.info['pid'], cmd_preview))
    
    terminated_count = 0
    for proc in found_processes:
        try:
            proc.terminate()
            proc.wait(timeout=3)
            print("[+] Terminated instance (PID: {})".format(proc.info['pid']))
            terminated_count += 1
        except psutil.NoSuchProcess:
            print("[i] Process {} already terminated".format(proc.info['pid']))
        except psutil.AccessDenied:
            print("[!] Access denied - cannot terminate PID: {} (run as Administrator?)".format(proc.info['pid']))
        except Exception as e:
            print("[!] Failed to terminate PID {}: {}".format(proc.info['pid'], e))
    
    if terminated_count > 0:
        print("\n[+] Successfully terminated {} instance(s)".format(terminated_count))
        time.sleep(1)  # Give time for mutex to be released
        return True
    else:
        print("\n[!] Failed to terminate any instances. Cannot start new instance.")
        return False

def check_single_instance():
    """
    Check if another instance is running.
    If found, ask user if they want to terminate it (10 second timeout).
    If no response in 10 seconds, automatically terminate and continue.
    Returns True if it's safe to continue, False otherwise.
    """
    global _mutex_handle
    
    # Try to create mutex
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    
    # Check if mutex already exists
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        print("[!] Another instance of this script is already running!")
        print()
        
        # Prompt with timeout
        response = timed_input(
            "[?] Do you want to shutdown the existing instance and run this one? (y/N): ",
            timeout=10
        )
        
        # Auto-terminate if no response or user says yes
        if response is None or response in ['y', 'yes']:
            if response is None:
                print("[->] Auto-terminating existing instance...")
            else:
                print("\n[->] Terminating existing instance...")
            
            if terminate_existing_instance():
                print("\n[+] Existing instance terminated. Starting new instance...")
                # Acquire ownership of the mutex via WaitForSingleObject
                result = ctypes.windll.kernel32.WaitForSingleObject(mutex, 0)
                if result == 0x00000080:  # WAIT_ABANDONED
                    print("   Acquired ownership of abandoned mutex.")
                elif result == 0:  # WAIT_OBJECT_0
                    print("   Acquired mutex ownership.")
                _mutex_handle = mutex  # Keep the handle alive for process lifetime
                time.sleep(0.5)
                return True
            else:
                # Mutex may be stale (process already crashed) -- abandoned mutex.
                # Use WaitForSingleObject to take ownership.
                print("\n[!] Could not find running process (mutex may be stale).")
                print("   Attempting to take ownership of stale mutex...")
                
                # WaitForSingleObject with 0 timeout:
                #   returns 0x00000080 (WAIT_ABANDONED) if the owning process crashed
                #   returns 0x00000000 (WAIT_OBJECT_0) if we acquired normally
                #   both mean WE NOW OWN the mutex
                result = ctypes.windll.kernel32.WaitForSingleObject(mutex, 0)
                if result in (0, 0x00000080):  # WAIT_OBJECT_0 or WAIT_ABANDONED
                    msg = "abandoned" if result == 0x00000080 else "acquired"
                    print("   Detected {} mutex and took ownership.".format(msg))
                    _mutex_handle = mutex  # Keep handle alive for process lifetime
                    return True
                else:
                    ctypes.windll.kernel32.CloseHandle(mutex)
                    print("[!] Could not acquire mutex (WaitForSingleObject returned {}). You may need to close it manually.".format(result))
                    return False
        else:
            print("\n[!] Exiting as requested. Please close the other instance manually and try again.")
            return False
    
    # Mutex created successfully -- keep handle alive for process lifetime
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
        print("Error: Invalid time format '{}'. Use HH:MM or HH:MM:SS".format(until_str))
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
        print("       stay_awake -- by Cao Xianke")
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

def main():
    """
    stay_awake: Prevent Windows lock via Scroll Lock simulation.

    Parses command-line arguments, ensures single-instance execution,
    then runs the keep-awake loop.
    """
    
    parser = argparse.ArgumentParser(
        prog='stay_awake',
        description='Prevent Windows screen lock by simulating Scroll Lock key presses, '
                    'then auto-lock at a scheduled time',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples (Windows):
  stay_awake.exe                           # Run indefinitely (60s interval)
  stay_awake.exe -i 120                    # Trigger every 120 seconds
  stay_awake.exe -u 18:30                  # Stop and LOCK at 6:30 PM
  stay_awake.exe --silent                  # Run with no console output
  stay_awake.exe --silent -u 08:00         # Silent mode with auto-lock at 8 AM
  stay_awake.exe --help                    # Show this help message

Compile to EXE with PyInstaller:
  pip install pyinstaller
  pyinstaller --onefile --console stay_awake.py
  pyinstaller --onefile --noconsole stay_awake.py   (silent background)

Single-instance behaviour:
  If another instance is already running, you have 10 seconds to respond.
  No response = existing instance is automatically terminated and new one starts.

Author: Cao Xianke (曹先科)
        """
    )
    
    parser.add_argument('-i', '--interval', type=int, default=60,
                        help='Trigger interval in seconds (default: 60)')
    parser.add_argument('-u', '--until', type=str, default=None,
                        help='Stop time in HH:MM or HH:MM:SS - screen will LOCK at this time')
    parser.add_argument('-s', '--silent', action='store_true',
                        help='Silent mode: suppress all console output (auto-terminates without prompt)')
    
    args = parser.parse_args()
    
    # Check for existing instance
    if args.silent:
        # In silent mode, automatically terminate existing instance without prompt
        _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if ctypes.windll.kernel32.GetLastError() == 183:
            print("[!] Another instance detected. Auto-terminating in silent mode...")
            mutex = _mutex_handle
            if terminate_existing_instance():
                # Take ownership of the abandoned mutex
                result = ctypes.windll.kernel32.WaitForSingleObject(mutex, 0)
                if result == 0x00000080:
                    print("   Acquired ownership of abandoned mutex.")
                print("[+] Instance terminated. Starting new instance silently...")
            else:
                # Stale mutex fallback using WaitForSingleObject
                print("\n[!] Could not find running process (mutex may be stale).")
                print("   Attempting to take ownership of stale mutex...")
                result = ctypes.windll.kernel32.WaitForSingleObject(mutex, 0)
                if result in (0, 0x00000080):
                    msg = "abandoned" if result == 0x00000080 else "acquired"
                    print("   Detected {} mutex and took ownership.".format(msg))
                    _mutex_handle = mutex
                else:
                    ctypes.windll.kernel32.CloseHandle(mutex)
                    print("[!] Could not acquire mutex (WaitForSingleObject returned {}). You may need to close it manually.".format(result))
                    sys.exit(1)
    else:
        # Interactive mode - prompt with 10-second timeout
        if not check_single_instance():
            sys.exit(1)
    
    keep_awake(interval_seconds=args.interval, until=args.until, silent=args.silent)

    # Mutex is released automatically by Windows when the process exits.
    # The handle is kept alive via the global _mutex_handle variable.

    

if __name__ == "__main__":
    main()