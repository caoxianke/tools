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
    # Escape quotes for VBScript and replace newlines with vbCrLf
    vbs_prompt = prompt.replace('"', '""').replace('\n', '" & vbCrLf & "')
    vbs_title = title.replace('"', '""').replace('\n', '" & vbCrLf & "')
    vbs_default = default.replace('"', '""').replace('\n', '" & vbCrLf & "')
    vbs_code = """
result = InputBox("{}", "{}", "{}")
If result = "" Then
    WScript.Echo "CANCELLED"
Else
    WScript.Echo result
End If
""".format(vbs_prompt, vbs_title, vbs_default)

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
    Show a single HTA dialog combining interval and until-time inputs.
    Returns (interval_seconds, until, silent).
    If user cancels, exits the process.
    """
    hta_code = r"""<!DOCTYPE html>
<html>
<head>
<title>stay_awake - Configuration</title>
<HTA:APPLICATION ID="StayAwakeConfig"
    APPLICATIONNAME="stay_awake"
    BORDER="dialog"
    CAPTION="yes"
    SHOWINTASKBAR="no"
    SINGLEINSTANCE="yes"
    SYSMENU="yes"
    WINDOWSTATE="normal"
    MAXIMIZEBUTTON="no"
    MINIMIZEBUTTON="no"
    WIDTH="150" HEIGHT="140">
<style>
body { font-family: 'Segoe UI', sans-serif; font-size: 18px; margin: 10px; }
h3 { margin: 0 0 8px; color: #333; font-size: 20px; }
.field-group { margin-bottom: 8px; }
label { display: block; margin-bottom: 2px; color: #444; }
input[type=text] { width: 100%%; padding: 6px 8px; border: 1px solid #aaa; border-radius: 4px; font-size: 18px; box-sizing: border-box; }
.hint { font-size: 14px; color: #888; margin-top: 1px; }
.btn-row { text-align: right; margin-top: 10px; }
.btn-row button { padding: 6px 20px; margin-left: 6px; font-size: 16px; border: 1px solid #aaa; border-radius: 4px; cursor: pointer; }
.btn-ok { background-color: #0078d7; color: #fff; border-color: #0078d7; }
.btn-cancel { background-color: #f0f0f0; }
.error { color: #d00; font-size: 12px; display: none; }
</style>
<script language=VBScript>
Dim fso, ts, resultPath
Set fso = CreateObject("Scripting.FileSystemObject")
resultPath = "RESULT_FILE_PLACEHOLDER"

Function validateAndClose()
    Dim intervalStr, untilStr, intervalNum
    intervalStr = Trim(document.getElementById("interval").value)
    untilStr = Trim(document.getElementById("until").value)
    If intervalStr = "" Then intervalStr = "60"
    If Not IsNumeric(intervalStr) Then
        document.getElementById("intervalError").style.display = "block"
        document.getElementById("intervalError").innerText = "Enter a valid number."
        document.getElementById("interval").focus()
        validateAndClose = False: Exit Function
    End If
    intervalNum = CInt(intervalStr)
    If intervalNum <= 0 Then
        document.getElementById("intervalError").style.display = "block"
        document.getElementById("intervalError").innerText = "Enter a positive number."
        document.getElementById("interval").focus()
        validateAndClose = False: Exit Function
    End If
    If untilStr <> "" Then
        Dim parts, h, m
        parts = Split(untilStr, ":")
        If UBound(parts) < 1 Or UBound(parts) > 2 Then
            document.getElementById("untilError").style.display = "block"
            document.getElementById("untilError").innerText = "Use HH:MM or HH:MM:SS."
            document.getElementById("until").focus()
            validateAndClose = False: Exit Function
        End If
        If Not IsNumeric(parts(0)) Or Not IsNumeric(parts(1)) Then
            document.getElementById("untilError").style.display = "block"
            document.getElementById("untilError").innerText = "Must be numbers."
            document.getElementById("until").focus()
            validateAndClose = False: Exit Function
        End If
        h = CInt(parts(0)): m = CInt(parts(1))
        If h < 0 Or h > 23 Or m < 0 Or m > 59 Then
            document.getElementById("untilError").style.display = "block"
            document.getElementById("untilError").innerText = "HH:0-23, MM:0-59."
            document.getElementById("until").focus()
            validateAndClose = False: Exit Function
        End If
    End If
    Set ts = fso.CreateTextFile(resultPath, True)
    ts.WriteLine intervalStr
    ts.WriteLine untilStr
    ts.Close
    window.Close()
End Function

Function cancelAndClose()
    Set ts = fso.CreateTextFile(resultPath, True)
    ts.WriteLine "CANCELLED"
    ts.Close
    window.Close()
End Function
</script>
</head>
<body>
<h3>stay_awake settings</h3>
<div class=field-group>
<label for=interval>Trigger interval (seconds):</label>
<input type=text id=interval value=60>
<div class=hint>Leave empty for default 60s.</div>
<div class=error id=intervalError></div>
</div>
<div class=field-group>
<label for=until>Auto-lock time (optional):</label>
<input type=text id=until value="" placeholder="e.g. 08:00">
<div class=hint>HH:MM or HH:MM:SS. Leave empty for no auto-lock.</div>
<div class=error id=untilError></div>
</div>
<div class=btn-row>
<button class=btn-cancel onclick=cancelAndClose()>Cancel</button>
<button class=btn-ok onclick=validateAndClose()>Start</button>
</div>
</body>
</html>"""

    # Create a unique temp file path for the result
    result_fd, result_path = tempfile.mkstemp(suffix='.txt', prefix='stay_awake_result_')
    os.close(result_fd)
    
    # Replace placeholder with actual result file path
    final_hta = hta_code.replace("RESULT_FILE_PLACEHOLDER", result_path.replace("\\", "\\\\"))
    
    hta_file_path = None
    try:
        # Write HTA to a temp file
        fd, hta_file_path = tempfile.mkstemp(suffix='.hta', prefix='stay_awake_')
        os.close(fd)
        with open(hta_file_path, 'w', encoding='utf-8') as f:
            f.write(final_hta)

        # Run mshta.exe (blocks until HTA window closes)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 1  # SW_SHOWNORMAL
        
        subprocess.run(
            ['mshta.exe', hta_file_path],
            capture_output=True, text=True, timeout=3600,
            startupinfo=startupinfo
        )

        # Read the result file
        if os.path.exists(result_path):
            with open(result_path, 'r', encoding='utf-8') as f:
                lines = [line.strip() for line in f.readlines() if line.strip()]
            
            if len(lines) >= 1 and lines[0] == "CANCELLED":
                sys.exit(0)
            elif len(lines) >= 1:
                interval = int(lines[0])
                until = lines[1] if len(lines) >= 2 and lines[1] else None
                return interval, until, False
        
        # Fallback: user closed window without clicking any button
        sys.exit(0)
        
    except subprocess.TimeoutExpired:
        popup_msg("Error", "Configuration dialog timed out.", MB_ICONWARNING)
        sys.exit(1)
    except Exception as e:
        popup_msg("Error", "Failed to open configuration dialog:\n{}".format(str(e)), MB_ICONWARNING)
        sys.exit(1)
    finally:
        # Clean up temp files
        if hta_file_path and os.path.exists(hta_file_path):
            try:
                os.unlink(hta_file_path)
            except:
                pass
        if os.path.exists(result_path):
            try:
                os.unlink(result_path)
            except:
                pass

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