"""A lean Chrome DevTools driver for the UI test.

Headless Chrome (--headless=new) so no window gets stuck; only the Runtime, DOM, Input and Page domains are used
(never Network, which makes Chrome copy request bodies into the DevTools stream).
"""
import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request

import websocket  # websocket-client


def find_chrome():
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe")):
        if os.path.exists(p):
            return p
    raise RuntimeError("Chrome not found")


class Chrome:
    def __init__(self, url, width=1480, height=940, headless=True):
        self.profile = tempfile.mkdtemp(prefix="declassified-chrome-")
        args = [find_chrome(), f"--user-data-dir={self.profile}", "--remote-debugging-port=0",
                "--remote-allow-origins=*", "--no-first-run", "--no-default-browser-check",
                "--disable-features=Translate", "--force-dark-mode", f"--window-size={width},{height}"]
        if headless:
            args += ["--headless=new", "--hide-scrollbars"]
        self.proc = subprocess.Popen(args + [url])
        port_file = os.path.join(self.profile, "DevToolsActivePort")
        port = None
        for _ in range(300):
            try:   # on Windows, reading fails while Chrome still has the file open for writing
                with open(port_file) as fh:
                    first = fh.read().split()
                if first:
                    port = int(first[0])
                    break
            except (OSError, ValueError):
                pass
            time.sleep(0.05)
        if port is None:
            self.proc.kill()
            raise RuntimeError("Chrome did not start (no DevToolsActivePort file)")
        page = None
        for _ in range(100):
            targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list"))
            page = next((t for t in targets if t["type"] == "page" and t["url"].startswith(url.split("#")[0])), None)
            if page:
                break
            time.sleep(0.1)
        self.ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=600, suppress_origin=True)
        self.next_id = 0
        self.logs = []
        self.send("Runtime.enable")
        self.send("Page.enable")
        self.send("Emulation.setDeviceMetricsOverride", {"width": width, "height": height, "deviceScaleFactor": 1,
                                                          "mobile": False})
        # The start-up navigation can still be pending (or stuck on about:blank) when we attach: navigate again.
        self.send("Page.navigate", {"url": url})
        self.wait_for(f"location.href.startsWith({json.dumps(url)}) && document.readyState === 'complete'", 30)

    def send(self, method, params=None, timeout=600):
        self.next_id += 1
        mid = self.next_id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        end = time.time() + timeout
        while time.time() < end:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error'].get('message')}")
                return msg.get("result", {})
            self.on_event(msg)
        raise TimeoutError(method)

    def on_event(self, msg):
        m, p = msg.get("method"), msg.get("params", {})
        if m == "Runtime.consoleAPICalled":
            text = " ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args", []))
            self.logs.append(f"{p.get('type')}: {text}")
        elif m == "Runtime.exceptionThrown":
            d = p.get("exceptionDetails", {})
            self.logs.append(f"pageerror: {d.get('exception', {}).get('description') or d.get('text')}")

    def js(self, expr, timeout=600):
        r = self.send("Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True,
                                           "userGesture": True}, timeout)
        if "exceptionDetails" in r:
            d = r["exceptionDetails"]
            raise RuntimeError(d.get("exception", {}).get("description") or d.get("text"))
        return r.get("result", {}).get("value")

    def wait_for(self, expr, timeout=60, interval=0.1):
        end = time.time() + timeout
        while time.time() < end:
            if self.js(f"!!({expr})"):
                return True
            time.sleep(interval)
        where = self.js("location.href + ' [' + document.readyState + '] ' + document.title")
        raise TimeoutError(f"waiting for {expr} (page: {where}; console: {self.logs[-5:]})")

    def sleep(self, s):
        end = time.time() + s
        while time.time() < end:
            self.js("1")
            time.sleep(min(0.1, max(0, end - time.time())))

    def bbox(self, sel):
        return self.js(f"(() => {{ const r = document.querySelector({json.dumps(sel)}).getBoundingClientRect();"
                       " return {x: r.x, y: r.y, width: r.width, height: r.height}; })()")

    def mouse(self, kind, x, y, buttons=0):
        params = {"type": kind, "x": x, "y": y}
        if kind in ("mousePressed", "mouseReleased"):
            params.update(button="left", clickCount=1)
        else:
            params.update(button="left" if buttons else "none", buttons=buttons)
        self.send("Input.dispatchMouseEvent", params)

    def click(self, sel):
        self.js(f"document.querySelector({json.dumps(sel)}).scrollIntoView({{block: 'nearest'}})")
        b = self.bbox(sel)
        x, y = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
        self.mouse("mouseMoved", x, y)
        self.mouse("mousePressed", x, y)
        self.mouse("mouseReleased", x, y)

    def drag_slider(self, sel, frac):
        """Drag a range input's thumb to `frac` of its width with real mouse events."""
        self.js(f"document.querySelector({json.dumps(sel)}).scrollIntoView({{block: 'center'}})")
        b = self.bbox(sel)
        y = b["y"] + b["height"] / 2
        cur = self.js(f"(() => {{ const e = document.querySelector({json.dumps(sel)});"
                      " return (e.value - e.min) / (e.max - e.min); })()")
        x0 = b["x"] + 7 + (b["width"] - 14) * cur
        x1 = b["x"] + 7 + (b["width"] - 14) * frac
        self.mouse("mouseMoved", x0, y)
        self.mouse("mousePressed", x0, y)
        for i in range(1, 7):
            self.mouse("mouseMoved", x0 + (x1 - x0) * i / 6, y, buttons=1)
        self.mouse("mouseReleased", x1, y)

    def key(self, key, code=None, vk=0, ctrl=False):
        mods = 2 if ctrl else 0
        self.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": key, "code": code or key,
                                             "windowsVirtualKeyCode": vk, "modifiers": mods})
        self.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": key, "code": code or key,
                                             "windowsVirtualKeyCode": vk, "modifiers": mods})

    def text(self, sel):
        return self.js(f"document.querySelector({json.dumps(sel)}).textContent")

    def set_files(self, sel, path):
        root = self.send("DOM.getDocument", {"depth": 0})["root"]["nodeId"]
        node = self.send("DOM.querySelector", {"nodeId": root, "selector": sel})["nodeId"]
        self.send("DOM.setFileInputFiles", {"files": [os.path.abspath(path)], "nodeId": node})

    def screenshot(self, path):
        data = self.send("Page.captureScreenshot", {"format": "png"})["data"]
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(data))

    def close(self):
        try:
            self.send("Browser.close", timeout=5)
        except Exception:
            pass
        try:
            self.ws.close()
        except Exception:
            pass
        try:
            self.proc.wait(10)
        except Exception:
            self.proc.kill()   # only our own Chrome (its own --user-data-dir); never taskkill /T
        shutil.rmtree(self.profile, ignore_errors=True)
