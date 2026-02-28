import tkinter as tk
from tkinter import scrolledtext, messagebox, ttk
import subprocess
import threading
import os
import sys
import time
import urllib.request
import signal
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
import webbrowser

# Constants
BACKEND_PORT = 8000
FRONTEND_PORT = 5173
NETWORK_INTERFACE_PORT = 8001
BASE_DIR = Path(__file__).parent.resolve()
BACKEND_DIR = BASE_DIR
FRONTEND_DIR = BASE_DIR / "frontend"
DATA_DIR = BASE_DIR / "backend" / "data"
DATABASE_PATH = DATA_DIR / "arena.db"
PROBLEMS_REPO_DIR = DATA_DIR / "problems_repo"

class ProcessManager:
    def __init__(self, log_widget, name):
        self.log_widget = log_widget
        self.name = name
        self.process = None
        self.stop_event = threading.Event()

    def log(self, message):
        self.log_widget.configure(state='normal')
        self.log_widget.insert(tk.END, message + "\n")
        self.log_widget.see(tk.END)
        self.log_widget.configure(state='disabled')

    def start(self, cmd, cwd=None):
        if self.process:
            self.stop()

        self.stop_event.clear()
        self.log(f"--- Starting {self.name} ---")
        
        # Use shell=True for Windows to correctly handle command strings
        self.process = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            shell=True,
            bufsize=1,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
        )

        threading.Thread(target=self._read_output, daemon=True).start()

    def _read_output(self):
        for line in iter(self.process.stdout.readline, ''):
            if self.stop_event.is_set():
                break
            self.log(line.strip())
        if self.process:
            self.process.stdout.close()

    def stop(self):
        if self.process:
            self.stop_event.set()
            self.log(f"--- Stopping {self.name} ---")
            if os.name == 'nt':
                # Kill the process group on Windows
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(self.process.pid)], capture_output=True)
            else:
                try:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
            self.process = None

class NetworkHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length).decode('utf-8')
        
        if self.path == '/restart':
            launcher.restart_all()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Restarting all processes...")
        elif self.path == '/delete-db':
            launcher.delete_db()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Database deleted.")
        elif self.path == '/delete-cache':
            launcher.delete_cache()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Cache deleted.")
        else:
            self.send_response(404)
            self.end_headers()

class ArenaLauncher:
    def __init__(self, root):
        self.root = root
        self.root.title("AI Optimization Arena Launcher")
        self.root.geometry("1000x700")

        self.setup_ui()
        
        self.backend_manager = ProcessManager(self.backend_log, "Backend")
        self.frontend_manager = ProcessManager(self.frontend_log, "Frontend")

        # Start network interface
        threading.Thread(target=self.start_network_interface, daemon=True).start()

        # Initial cleanup and startup
        self.root.after(100, self.initial_startup)

    def setup_ui(self):
        # Control Panel
        control_frame = tk.Frame(self.root)
        control_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)

        tk.Button(control_frame, text="Restart All", command=self.restart_all, bg="orange").pack(side=tk.LEFT, padx=5)
        tk.Button(control_frame, text="Delete DB", command=self.delete_db, bg="red", fg="white").pack(side=tk.LEFT, padx=5)
        tk.Button(control_frame, text="Delete Cache", command=self.delete_cache, bg="red", fg="white").pack(side=tk.LEFT, padx=5)
        
        self.status_label = tk.Label(control_frame, text="Status: Ready", font=("Arial", 10, "bold"))
        self.status_label.pack(side=tk.RIGHT, padx=10)

        # Logs Notebook
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(expand=True, fill=tk.BOTH, padx=5, pady=5)

        self.backend_log = scrolledtext.ScrolledText(self.notebook, state='disabled', wrap=tk.WORD, font=("Consolas", 9))
        self.notebook.add(self.backend_log, text="Backend Logs")

        self.frontend_log = scrolledtext.ScrolledText(self.notebook, state='disabled', wrap=tk.WORD, font=("Consolas", 9))
        self.notebook.add(self.frontend_log, text="Frontend Logs")

        # Settings Tab
        self.settings_frame = tk.Frame(self.notebook)
        self.notebook.add(self.settings_frame, text="Settings")
        self.setup_settings_ui()

    def setup_settings_ui(self):
        # Metadata Parsing Settings
        meta_frame = tk.LabelFrame(self.settings_frame, text="Metadata Parsing", padx=10, pady=10)
        meta_frame.pack(fill=tk.X, padx=10, pady=10)

        # Model
        tk.Label(meta_frame, text="Parsing Model:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.meta_model = tk.Entry(meta_frame, width=50)
        self.meta_model.insert(0, "google/gemini-2.0-flash-001")
        self.meta_model.grid(row=0, column=1, sticky=tk.W, pady=2)

        # Fallback Model
        tk.Label(meta_frame, text="Fallback Model (no editorial):").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.meta_fallback_model = tk.Entry(meta_frame, width=50)
        self.meta_fallback_model.insert(0, "google/gemini-2.0-pro-exp-02-05")
        self.meta_fallback_model.grid(row=1, column=1, sticky=tk.W, pady=2)

        # Prompt
        tk.Label(meta_frame, text="Parsing Prompt:").grid(row=2, column=0, sticky=tk.NW, pady=2)
        self.meta_prompt = scrolledtext.ScrolledText(meta_frame, width=60, height=10, font=("Arial", 9))
        default_prompt = (
            "Extract a short 2-sentence description and a list of algorithmic tags "
            "from this competitive programming problem statement and editorial.\n\n"
            "Respond ONLY with a JSON object:\n"
            "{\n"
            "  \"short_description\": \"...\",\n"
            "  \"tags\": [\"tag1\", \"tag2\", ...]\n"
            "}"
        )
        self.meta_prompt.insert(tk.END, default_prompt)
        self.meta_prompt.grid(row=2, column=1, sticky=tk.W, pady=2)

        # Action Button
        tk.Button(meta_frame, text="Start Parsing Metadata", command=self.trigger_metadata_parsing, bg="lightblue").grid(row=3, column=1, sticky=tk.E, pady=10)

    def trigger_metadata_parsing(self):
        model = self.meta_model.get()
        fallback = self.meta_fallback_model.get()
        prompt = self.meta_prompt.get("1.0", tk.END).strip()

        if not model or not prompt:
            messagebox.showerror("Error", "Model and Prompt are required")
            return

        def run_request():
            import json
            import urllib.request
            url = f"http://127.0.0.1:{BACKEND_PORT}/api/v1/problems/parse-metadata"
            data = json.dumps({
                "model": model,
                "prompt": prompt,
                "fallback_model": fallback if fallback else None
            }).encode('utf-8')

            try:
                req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(req) as response:
                    if response.status == 200:
                        self.root.after(0, lambda: messagebox.showinfo("Success", "Metadata parsing started in background"))
                    else:
                        self.root.after(0, lambda: messagebox.showerror("Error", f"Failed to start parsing: {response.status}"))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("Error", f"Connection error: {e}"))

        threading.Thread(target=run_request, daemon=True).start()

    def start_network_interface(self):
        try:
            server = HTTPServer(('localhost', NETWORK_INTERFACE_PORT), NetworkHandler)
            server.serve_forever()
        except Exception as e:
            print(f"Network interface failed: {e}")

    def kill_ports(self):
        self.status_label.config(text="Status: Cleaning up ports...")
        for port in [BACKEND_PORT, FRONTEND_PORT]:
            if os.name == 'nt':
                # Windows command to find PID on port and kill it
                try:
                    output = subprocess.check_output(f'netstat -aon | findstr :{port} | findstr LISTENING', shell=True).decode()
                    for line in output.strip().split('\n'):
                        parts = line.split()
                        if parts:
                            pid = parts[-1]
                            subprocess.run(['taskkill', '/F', '/PID', pid], capture_output=True)
                except subprocess.CalledProcessError:
                    pass # Port not in use
            else:
                subprocess.run(f"fuser -k {port}/tcp", shell=True, capture_output=True)

    def delete_db(self):
        if messagebox.askyesno("Confirm", "Delete database (arena.db)?"):
            self.backend_manager.stop()
            time.sleep(1) # Wait for file handles to release
            if DATABASE_PATH.exists():
                try:
                    DATABASE_PATH.unlink()
                    self.backend_manager.log("--- Database deleted ---")
                except Exception as e:
                    self.backend_manager.log(f"--- Error deleting DB: {e} ---")
            else:
                self.backend_manager.log("--- Database file not found ---")

    def delete_cache(self):
        if messagebox.askyesno("Confirm", "Delete problems cache?"):
            if PROBLEMS_REPO_DIR.exists():
                try:
                    import shutil
                    shutil.rmtree(PROBLEMS_REPO_DIR)
                    self.backend_manager.log("--- Cache deleted ---")
                except Exception as e:
                    self.backend_manager.log(f"--- Error deleting cache: {e} ---")
            else:
                self.backend_manager.log("--- Cache directory not found ---")

    def start_backend(self):
        self.status_label.config(text="Status: Starting Backend...")
        self.backend_manager.start("uv run python -m backend.main", cwd=str(BACKEND_DIR))
        threading.Thread(target=self.wait_for_backend, daemon=True).start()

    def wait_for_backend(self):
        import json
        url = f"http://127.0.0.1:{BACKEND_PORT}/api/v1/status"
        health_url = f"http://127.0.0.1:{BACKEND_PORT}/"
        
        # Phase 1: Wait for any response from backend (server started)
        retries = 30
        connected = False
        while retries > 0:
            try:
                with urllib.request.urlopen(health_url, timeout=1) as response:
                    if response.status == 200:
                        connected = True
                        break
            except:
                pass
            time.sleep(1)
            retries -= 1
        
        if not connected:
            self.backend_manager.log("--- Backend server failed to start within 30s ---")
            self.root.after(0, self.start_frontend)
            return

        # Phase 2: Wait for backend to be "ready"
        self.backend_manager.log("--- Backend connected, waiting for initialization... ---")
        retries = 300 # 5 minutes
        while retries > 0:
            try:
                with urllib.request.urlopen(url, timeout=1) as response:
                    if response.status == 200:
                        data = json.loads(response.read().decode())
                        if data.get("ready"):
                            self.backend_manager.log("--- Backend is ready ---")
                            self.root.after(0, self.start_frontend)
                            return
                        else:
                            msg = data.get("message", "Initializing...")
                            self.status_label.config(text=f"Status: {msg}")
            except Exception as e:
                self.backend_manager.log(f"--- Connection lost while waiting for ready: {e} ---")
                break
            time.sleep(1)
            retries -= 1
        
        self.backend_manager.log("--- Backend initialization timed out, starting frontend anyway ---")
        self.root.after(0, self.start_frontend)

    def start_frontend(self):
        self.status_label.config(text="Status: Starting Frontend...")
        self.frontend_manager.start("npm run dev", cwd=str(FRONTEND_DIR))
        threading.Thread(target=self.wait_for_frontend, daemon=True).start()

    def wait_for_frontend(self):
        url = f"http://localhost:{FRONTEND_PORT}"
        retries = 30
        while retries > 0:
            try:
                with urllib.request.urlopen(url, timeout=1) as response:
                    if response.status == 200:
                        self.status_label.config(text="Status: Running")
                        webbrowser.open(url)
                        return
            except:
                pass
            time.sleep(1)
            retries -= 1
        self.status_label.config(text="Status: Running (Frontend check failed)")

    def restart_all(self):
        self.backend_manager.stop()
        self.frontend_manager.stop()
        self.kill_ports()
        self.start_backend()

    def initial_startup(self):
        self.kill_ports()
        self.start_backend()

    def on_close(self):
        self.backend_manager.stop()
        self.frontend_manager.stop()
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    launcher = ArenaLauncher(root)
    root.protocol("WM_DELETE_WINDOW", launcher.on_close)
    root.mainloop()
