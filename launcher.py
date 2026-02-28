"""
AI Optimization Arena – Launcher
"""
import tkinter as tk
from tkinter import messagebox, ttk
import subprocess
import threading
import queue
import os
import sys
import time
import re
import json
import urllib.request
import signal
import shutil
import argparse
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
import webbrowser

# ── Constants ──────────────────────────────────────────────────────────────────
BACKEND_PORT           = 8000
FRONTEND_PORT          = 5173
NETWORK_INTERFACE_PORT = 8001
BASE_DIR         = Path(__file__).parent.resolve()
BACKEND_DIR      = BASE_DIR
FRONTEND_DIR     = BASE_DIR / "frontend"
DATA_DIR         = BASE_DIR / "backend" / "data"
DATABASE_PATH    = DATA_DIR / "arena.db"
PROBLEMS_DIR     = DATA_DIR / "problems"

# ── Tailwind Dark Theme ────────────────────────────────────────────────────────
BG_900       = "#111827"
BG_800       = "#1F2937"
BG_700       = "#374151"
TEXT_PRIMARY = "#F3F4F6"
TEXT_MUTED   = "#9CA3AF"
BLUE_600     = "#2563EB"
BLUE_500     = "#3B82F6"
RED_600      = "#DC2626"
GREEN_600    = "#16A34A"
ORANGE_500   = "#F97316"
FONT_MONO    = ("Consolas", 9)
FONT_UI      = ("Segoe UI", 9)

# ── Utilities ──────────────────────────────────────────────────────────────────
_ANSI = re.compile(
    r'\x1b\[[0-9;:<=>?]*[ -/]*[@-~]'
    r'|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)'
    r'|\x1b[@-_]'
    r'|\x1b[0-9]'
)

# Regex to detect standard HTTP logs (e.g. "GET /api/v1/status HTTP/1.1" 200 OK)
_HTTP_LOG_RE = re.compile(r'"(?:GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)\s+.*?\s+HTTP/[0-9.]+"\s+\d{3}')

def _strip_ansi(s: str) -> str:
    return _ANSI.sub('', s)

def _clean_env() -> dict:
    env = os.environ.copy()
    env["NO_COLOR"]         = "1"
    env["FORCE_COLOR"]      = "0"
    env["TERM"]             = "dumb"
    env["VITE_CLI_COLOR"]   = "false"
    # CRITICAL: Forces Python to flush stdout instantly rather than block-buffering
    env["PYTHONUNBUFFERED"] = "1" 
    return env


# ── Process & Log Interfaces ───────────────────────────────────────────────────
class ProcessManager:
    def __init__(self, log_target, name: str, push_callback):
        self._log  = log_target
        self.name  = name
        self._push_cb = push_callback
        self.proc  = None
        self._stop = threading.Event()

    def _push(self, line: str):
        self._push_cb(self._log, line)

    def start(self, cmd, cwd=None):
        if self.proc:
            self._kill()
        self._stop.clear()
        self._push(f"▶  Starting {self.name}…")
        self.proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=True,
            bufsize=1,
            env=_clean_env(),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        for line in iter(self.proc.stdout.readline, ""):
            if self._stop.is_set():
                break
            cleaned = _strip_ansi(line.rstrip())
            if cleaned:
                self._push(cleaned)
        if self.proc:
            self.proc.stdout.close()

    def _kill(self):
        if not self.proc:
            return
        self._stop.set()
        self._push(f"■  Stopping {self.name}…")
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                           capture_output=True)
        else:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
        self.proc = None

    def stop(self):
        self._kill()

    def wait_port_free(self, port: int, timeout: int = 10):
        self._push(f"⏳  Waiting for port {port} to be free…")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=0.5):
                    pass
                time.sleep(0.3)
            except Exception:
                self._push(f"✔  Port {port} is free")
                return
        self._push(f"⚠  Port {port} still busy after {timeout} s, continuing anyway")


# ── Network Server ─────────────────────────────────────────────────────────────
class NetworkHandler(BaseHTTPRequestHandler):
    _app = None

    def log_message(self, fmt, *args):
        if self._app:
            self._app.log_webserver(f"{self.address_string()} – {fmt % args}")

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status": "ok", "service": "ArenaLauncher"}')

    def do_POST(self):
        if not self._app:
            self.send_response(503)
            self.end_headers()
            return
        routes = {
            "/restart":      self._app._async_restart,
            "/delete-db":    self._app._async_delete_db,
            "/delete-cache": self._app._async_delete_cache,
        }
        fn = routes.get(self.path)
        if fn:
            threading.Thread(target=fn, daemon=True).start()
            self.send_response(200)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


# ── Core Controller (Shared GUI/Headless Logic) ────────────────────────────────
class AppCore:
    def __init__(self, backend_log, frontend_log, set_status_cb):
        self.backend_log = backend_log
        self.frontend_log = frontend_log
        self.set_status = set_status_cb
        
        self.backend_mgr  = ProcessManager(self.backend_log, "Backend", self.push_log)
        self.frontend_mgr = ProcessManager(self.frontend_log, "Frontend", self.push_log)
        
        NetworkHandler._app = self
        threading.Thread(target=self._serve_network, daemon=True).start()

    def push_log(self, target, line):
        pass

    def log_webserver(self, msg):
        pass

    def startup(self):
        self._kill_ports()
        self._do_start_backend()

    def shutdown(self):
        self.backend_mgr.stop()
        self.frontend_mgr.stop()
        self._kill_ports()

    def _async_restart(self):
        self.backend_mgr.stop()
        self.frontend_mgr.stop()
        self._kill_ports() # Aggressively clean up ports
        self.backend_mgr.wait_port_free(BACKEND_PORT)
        self._do_start_backend()

    def _kill_port_force(self, port):
        """Actively hunts down the PID bound to a port and snipes it."""
        if os.name == "nt":
            try:
                out = subprocess.check_output(
                    f"netstat -aon | findstr :{port} | findstr LISTENING",
                    shell=True).decode()
                for line in out.strip().splitlines():
                    parts = line.split()
                    if parts and parts[-1].isdigit():
                        pid = parts[-1]
                        self.backend_mgr._push(f"✂  Sniping orphan PID {pid} on port {port}…")
                        subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
            except subprocess.CalledProcessError:
                pass
        else:
            subprocess.run(f"fuser -k {port}/tcp", shell=True, capture_output=True)

    def _kill_ports(self):
        self.set_status("Cleaning up ports…", TEXT_MUTED)
        self._kill_port_force(BACKEND_PORT)
        self._kill_port_force(FRONTEND_PORT)

    def _async_delete_db(self):
        self.set_status("Stopping backend…", ORANGE_500)
        self.backend_mgr.stop()
        
        # Kill the port explicitly since Windows process trees can orphan the python driver
        self._kill_port_force(BACKEND_PORT)
        self.backend_mgr.wait_port_free(BACKEND_PORT)

        if DATABASE_PATH.exists():
            for attempt in range(10):
                try:
                    DATABASE_PATH.unlink()
                    self.backend_mgr._push("■  Database deleted successfully")
                    break
                except PermissionError as e:
                    if attempt == 9:
                        self.backend_mgr._push(f"✖  Failed to delete DB (Lock persisted): {e}")
                        return
                    time.sleep(0.5)
        else:
            self.backend_mgr._push("■  Database file not found")

        self._do_start_backend()

    def _async_delete_cache(self):
        if PROBLEMS_DIR.exists():
            try:
                shutil.rmtree(PROBLEMS_DIR)
                self.backend_mgr._push("■  Problems cache deleted")
            except Exception as e:
                self.backend_mgr._push(f"✖  Error deleting cache: {e}")
        else:
            self.backend_mgr._push("■  Problems directory not found")

    def _do_start_backend(self):
        self.set_status("Starting backend…", ORANGE_500)
        self.backend_mgr.start("uv run python -m backend.main", cwd=str(BACKEND_DIR))
        self._wait_for_backend()

    def _wait_for_backend(self):
        status_url = f"http://127.0.0.1:{BACKEND_PORT}/api/v1/status"
        health_url = f"http://127.0.0.1:{BACKEND_PORT}/"

        for _ in range(30):
            try:
                with urllib.request.urlopen(health_url, timeout=1) as r:
                    if r.status == 200:
                        break
            except Exception:
                pass
            time.sleep(1)
        else:
            self.backend_mgr._push("✖  Backend failed to start within 30 s")
            self._do_start_frontend()
            return

        self.backend_mgr._push("✔  Backend connected, waiting for initialisation…")
        for _ in range(300):
            try:
                with urllib.request.urlopen(status_url, timeout=1) as r:
                    if r.status == 200:
                        data = json.loads(r.read().decode())
                        if data.get("ready"):
                            self.backend_mgr._push("✔  Backend is ready")
                            self._do_start_frontend()
                            return
                        self.set_status(data.get("message", "Initializing…"), ORANGE_500)
            except Exception as e:
                self.backend_mgr._push(f"✖  Connection lost: {e}")
                break
            time.sleep(1)

        self.backend_mgr._push("⚠  Backend init timed out – starting frontend anyway")
        self._do_start_frontend()

    def _do_start_frontend(self):
        self.set_status("Starting frontend…", ORANGE_500)
        self.frontend_mgr.start("npm run dev", cwd=str(FRONTEND_DIR))
        self._wait_for_frontend()

    def _wait_for_frontend(self):
        url = f"http://localhost:{FRONTEND_PORT}"
        for _ in range(30):
            try:
                with urllib.request.urlopen(url, timeout=1) as r:
                    if r.status == 200:
                        self.set_status("● Running", GREEN_600)
                        if not hasattr(self, 'is_headless') or not self.is_headless:
                            webbrowser.open(url)
                        return
            except Exception:
                pass
            time.sleep(1)
        self.set_status("● Running (frontend check failed)", ORANGE_500)

    def _serve_network(self):
        try:
            self.log_webserver(f"✔ Local Network Interface listening on port {NETWORK_INTERFACE_PORT}")
            HTTPServer(("localhost", NETWORK_INTERFACE_PORT), NetworkHandler).serve_forever()
        except Exception as e:
            self.log_webserver(f"✖ Network interface failed: {e}")


# ── Headless Launcher ──────────────────────────────────────────────────────────
class HeadlessLauncher(AppCore):
    def __init__(self):
        self.is_headless = True
        super().__init__("BACKEND", "FRONTEND", self._print_status)
    
    def push_log(self, target, line):
        is_http = bool(_HTTP_LOG_RE.search(line))
        if target == "BACKEND":
            if is_http:
                print(f"\033[96m[BACKEND HTTP]\033[0m {line}")
            else:
                print(f"\033[94m[{target}]\033[0m {line}")
        else:
            print(f"\033[92m[{target}]\033[0m {line}")

    def log_webserver(self, msg):
        print(f"\033[95m[WEBSERVER]\033[0m {msg}")

    def _print_status(self, text, color):
        print(f"\033[93m[STATUS]\033[0m {text}")

    def run(self):
        print("\033[1m=== AI Optimization Arena (Headless) ===\033[0m")
        threading.Thread(target=self.startup, daemon=True).start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nShutting down...")
            self.shutdown()


# ── GUI Launcher ───────────────────────────────────────────────────────────────
class DarkLog(tk.Frame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg=BG_900, bd=0, highlightthickness=0)
        self._text = tk.Text(
            self, bg=BG_900, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
            selectbackground=BLUE_600, relief="flat", bd=0, font=FONT_MONO,
            wrap=tk.WORD, state="disabled", padx=8, pady=8, **kwargs,
        )
        self._sb = ttk.Scrollbar(self, orient="vertical", command=self._text.yview)
        self._text.configure(yscrollcommand=self._sb.set)
        self._sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    def append(self, line: str):
        self._text.configure(state="normal")
        self._text.insert(tk.END, line + "\n")
        self._text.see(tk.END)
        self._text.configure(state="disabled")

class GUILauncher(AppCore):
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("AI Optimization Arena")
        self.root.geometry("1100x720")
        self.root.configure(bg=BG_900)
        self._apply_dark_title_bar()

        self._q = queue.Queue()
        self._style_ttk()
        self._build_ui()

        super().__init__(self.backend_log, self.frontend_log, self._set_status)

        self._drain()
        self.root.after(100, lambda: threading.Thread(target=self.startup, daemon=True).start())

    def push_log(self, target, line):
        if target == self.backend_log and _HTTP_LOG_RE.search(line):
            self._q.put(("log", self.backend_http_log, line))
        else:
            self._q.put(("log", target, line))

    def log_webserver(self, msg):
        self._q.put(("log", self.webserver_log, msg))

    def _set_status(self, text: str, color: str = TEXT_MUTED):
        self._q.put(("status", text, color))

    def _drain(self):
        try:
            while True:
                msg = self._q.get_nowait()
                if msg[0] == "log":
                    msg[1].append(msg[2])
                elif msg[0] == "status":
                    self.status_label.config(text=msg[1], fg=msg[2])
        except queue.Empty:
            pass
        self.root.after(50, self._drain)

    def _apply_dark_title_bar(self):
        if os.name == 'nt':
            try:
                import ctypes
                self.root.update()
                set_window_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
                hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
                value = ctypes.c_int(2)
                set_window_attribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
            except Exception:
                pass

    def _style_ttk(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("TNotebook", background=BG_900, borderwidth=0, tabmargins=0)
        s.configure("TNotebook.Tab", background=BG_800, foreground=TEXT_MUTED,
                    padding=[14, 6], font=FONT_UI, borderwidth=0)
        s.map("TNotebook.Tab", background=[("selected", BG_900)], foreground=[("selected", BLUE_500)])
        s.configure("TFrame", background=BG_900)
        
        s.configure("Vertical.TScrollbar", gripcount=0, background=BG_800, 
                    darkcolor=BG_900, lightcolor=BG_900, troughcolor=BG_900, 
                    bordercolor=BG_900, arrowcolor=TEXT_PRIMARY)
        s.map("Vertical.TScrollbar", background=[("active", BG_700)])

    def _btn(self, parent, text, cmd, bg=BLUE_600, hover=BLUE_500, width=14):
        btn = tk.Button(parent, text=text, command=cmd, bg=bg, fg=TEXT_PRIMARY,
                        activebackground=hover, activeforeground=TEXT_PRIMARY,
                        relief="flat", bd=0, padx=10, pady=6, font=FONT_UI, cursor="hand2", width=width)
        btn.bind("<Enter>", lambda e: btn.config(bg=hover))
        btn.bind("<Leave>", lambda e: btn.config(bg=bg))
        return btn

    def _build_ui(self):
        tb = tk.Frame(self.root, bg=BG_800, height=52)
        tb.pack(fill=tk.X)
        tb.pack_propagate(False)
        tk.Label(tb, text="⚡  AI Optimization Arena", bg=BG_800, fg=TEXT_PRIMARY,
                 font=("Segoe UI", 13, "bold")).pack(side=tk.LEFT, padx=16)
        self.status_label = tk.Label(tb, text="● Initializing", bg=BG_800, fg=TEXT_MUTED, font=("Segoe UI", 9))
        self.status_label.pack(side=tk.RIGHT, padx=16)

        bar = tk.Frame(self.root, bg=BG_800, height=46)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)
        row = tk.Frame(bar, bg=BG_800)
        row.pack(side=tk.LEFT, padx=10, pady=7)
        
        self._btn(row, "↺  Restart", lambda: threading.Thread(target=self._async_restart, daemon=True).start(), BLUE_600, BLUE_500, 12).pack(side=tk.LEFT, padx=4)
        self._btn(row, "🗑  Delete DB", self._ui_delete_db, RED_600, "#EF4444", 12).pack(side=tk.LEFT, padx=4)
        self._btn(row, "🗑  Delete Cache", self._ui_delete_cache, RED_600, "#EF4444", 14).pack(side=tk.LEFT, padx=4)
        self._btn(row, "🌐  Open App", lambda: webbrowser.open(f"http://localhost:{FRONTEND_PORT}"), GREEN_600, "#22C55E", 12).pack(side=tk.LEFT, padx=4)
        self._btn(row, "📡  Ping Server", self._ping_server, BG_700, "#4B5563", 12).pack(side=tk.LEFT, padx=4)

        tk.Frame(self.root, bg=BG_700, height=1).pack(fill=tk.X)

        nb = ttk.Notebook(self.root)
        nb.pack(expand=True, fill=tk.BOTH)

        self.backend_log      = DarkLog(nb)
        self.backend_http_log = DarkLog(nb)
        self.frontend_log     = DarkLog(nb)
        self.webserver_log    = DarkLog(nb)
        
        nb.add(self.backend_log,      text="  Backend  ")
        nb.add(self.backend_http_log, text="  Backend HTTP  ")
        nb.add(self.frontend_log,     text="  Frontend  ")
        nb.add(self.webserver_log,    text="  Webserver  ")

    def _ui_delete_db(self):
        if messagebox.askyesno("Confirm", "Delete database (arena.db)?\nBackend will stop, DB deleted, then restart."):
            threading.Thread(target=self._async_delete_db, daemon=True).start()

    def _ui_delete_cache(self):
        if messagebox.askyesno("Confirm", "Delete problems cache?"):
            threading.Thread(target=self._async_delete_cache, daemon=True).start()

    def _ping_server(self):
        def _ping():
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{NETWORK_INTERFACE_PORT}/", timeout=2)
            except Exception as e:
                self.log_webserver(f"Ping failed: {e}")
        threading.Thread(target=_ping, daemon=True).start()

    def on_close(self):
        self.shutdown()
        self.root.destroy()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Optimization Arena Launcher")
    parser.add_argument("--headless", action="store_true", help="Run without the GUI")
    args = parser.parse_args()

    if args.headless:
        HeadlessLauncher().run()
    else:
        root = tk.Tk()
        app = GUILauncher(root)
        root.protocol("WM_DELETE_WINDOW", app.on_close)
        root.mainloop()