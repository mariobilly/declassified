"""Desktop launcher: starts the Declassified server (if needed) and opens the app window.

Drop a photo or video onto the shortcut to open it straight away.
"""
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("DECLASSIFIED_PORT") or 48751)
URL = f"http://127.0.0.1:{PORT}/"
LOCAL = os.environ.get("LOCALAPPDATA", APP_DIR)


def probe():
    """'ours' if Declassified answers on the port, 'other' if something else does, None if free."""
    try:
        with urllib.request.urlopen(URL + "api/ping", timeout=0.8) as r:
            return "ours" if b"declassified" in r.read() else "other"
    except urllib.error.HTTPError:
        return "other"
    except Exception:
        return None


def find_browser():
    pf, pf86 = os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    for p in (os.path.join(pf, r"Google\Chrome\Application\chrome.exe"),
              os.path.join(pf86, r"Google\Chrome\Application\chrome.exe"),
              os.path.join(LOCAL, r"Google\Chrome\Application\chrome.exe"),
              os.path.join(pf86, r"Microsoft\Edge\Application\msedge.exe"),
              os.path.join(pf, r"Microsoft\Edge\Application\msedge.exe")):
        if os.path.exists(p):
            return p
    return None


def open_window():
    exe = find_browser()
    if not exe:
        webbrowser.open(URL)
        return
    profile = os.path.join(LOCAL, "Declassified", "window")
    si = None
    if hasattr(subprocess, "STARTUPINFO"):  # a normal, visible window (not an inherited minimised state)
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 1
    subprocess.Popen([exe, f"--app={URL}", f"--user-data-dir={profile}", "--no-first-run",
                      "--no-default-browser-check", "--disable-features=Translate", "--force-dark-mode",
                      "--window-size=1480,960"], startupinfo=si)


def open_in_running(path):
    req = urllib.request.Request(URL + "api/open_path", data=json.dumps({"path": path}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=30).read()
    except Exception:
        pass


def main():
    path = next((a for a in sys.argv[1:] if os.path.isfile(a)), None)
    who = probe()
    if who == "other":
        ctypes.windll.user32.MessageBoxW(None, f"Port {PORT} is used by another program, so Declassified can't "
                                         "start. Close that program and try again.", "Declassified", 0x10)
        return
    if who == "ours":
        if path:
            open_in_running(path)
        open_window()
        return

    sys.path.insert(0, APP_DIR)
    from app import server

    os.makedirs(server.DATA_DIR, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:  # pythonw has no console
        sys.stdout = sys.stderr = open(os.path.join(server.DATA_DIR, "app.log"), "a", buffering=1, encoding="utf-8")
    try:
        sock = server.make_socket(PORT)
    except OSError:
        ctypes.windll.user32.MessageBoxW(None, f"Port {PORT} is busy, so Declassified can't start.", "Declassified",
                                         0x10)
        return

    def opener():
        for _ in range(300):
            if probe() == "ours":
                break
            time.sleep(0.1)
        open_window()

    threading.Thread(target=opener, daemon=True).start()
    server.serve(auto_exit=True, sock=sock, open_path=path)


if __name__ == "__main__":
    main()
