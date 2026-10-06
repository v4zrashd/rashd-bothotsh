import os
import sys
import shutil
import subprocess
import threading
import re
import logging
import urllib.request
import urllib.error
import json
import psutil
import datetime
from typing import Dict, Optional
from json_db import db

# Set up logging for the manager
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("BotManager")

def verify_telegram_token(token: str) -> str:
    """Verifies a Telegram Bot Token against the official Telegram Bot API."""
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "V4ZTelegramBotHoster"})
        with urllib.request.urlopen(req, timeout=5) as response:
            res_data = json.loads(response.read().decode())
            if res_data.get("ok"):
                username = res_data["result"].get("username")
                return f"[TOKEN CHECK] SUCCESS: Token is VALID. Bot Username: @{username}"
    except urllib.error.HTTPError as he:
        if he.code == 401:
            return "[TOKEN CHECK] WARNING: Token is INVALID (401 Unauthorized). Please check your BOT_TOKEN."
    except Exception as e:
        return f"[TOKEN CHECK] INFO: Verification skipped/failed due to connection error: {str(e)}"
    return "[TOKEN CHECK] WARNING: Verification failed. The token may be incorrect."

def get_telegram_bot_info(token: str) -> Optional[dict]:
    """Fetches bot info (first_name, username) from Telegram Bot API using token."""
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "V4ZTelegramBotHoster"})
        with urllib.request.urlopen(req, timeout=5) as response:
            res_data = json.loads(response.read().decode())
            if res_data.get("ok"):
                return res_data["result"]
    except Exception as e:
        logger.info(f"Failed to fetch Telegram bot info: {e}")
    return None

def find_telegram_token_in_dir(bot_dir: str) -> Optional[str]:
    """Scans standard text/script files in the bot directory for a Telegram token."""
    # Matches typical telegram token format: 123456789:abcdef... (8-10 digit ID, then 35 character string)
    token_pattern = re.compile(r"\b(\d{8,10}:[a-zA-Z0-9_-]{35})\b")
    
    for root, dirs, files in os.walk(bot_dir):
        # Avoid traversing virtual environment or system folders
        dirs[:] = [d for d in dirs if d not in ['.git', '__pycache__', 'venv', '.env', '.idea', '.vscode']]
        for file in files:
            file_path = os.path.join(root, file)
            # Scan files with script extensions and .env configs
            if file.endswith(('.py', '.txt', '.env', '.json', '.cfg', '.ini', '.sh')):
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                        match = token_pattern.search(content)
                        if match:
                            return match.group(1)
                except Exception:
                    pass
    return None

class BotManager:
    def __init__(self):
        # Maps bot_id -> subprocess.Popen
        self.processes: Dict[str, subprocess.Popen] = {}
        self.lock = threading.Lock()
        
        # Ensure log directory exists
        os.makedirs("data/logs", exist_ok=True)
        os.makedirs("data/bots", exist_ok=True)

    def get_python_executable(self, bot_dir: str) -> str:
        """Get the path to the python executable in the bot's venv or fallback to system python."""
        if os.name == "nt": # Windows
            venv_python = os.path.join(bot_dir, "venv", "Scripts", "python.exe")
        else: # Linux/Mac
            venv_python = os.path.join(bot_dir, "venv", "bin", "python")

        if os.path.exists(venv_python):
            return venv_python
        return sys.executable # Fallback to current system python

    def get_pip_executable(self, bot_dir: str) -> str:
        """Get the path to the pip executable in the bot's venv or fallback to system pip."""
        if os.name == "nt": # Windows
            venv_pip = os.path.join(bot_dir, "venv", "Scripts", "pip.exe")
        else: # Linux/Mac
            venv_pip = os.path.join(bot_dir, "venv", "bin", "pip")

        if os.path.exists(venv_pip):
            return venv_pip
        return "pip"

    def start_bot(self, bot_id: str) -> bool:
        with self.lock:
            bot = db.get_bot(bot_id)
            if not bot:
                logger.error(f"Bot {bot_id} not found in database.")
                return False

            # If already running
            if bot_id in self.processes:
                proc = self.processes[bot_id]
                if proc.poll() is None:
                    logger.info(f"Bot {bot_id} is already running.")
                    return True
                else:
                    # Clean up defunct process
                    del self.processes[bot_id]

            bot_dir = os.path.abspath(os.path.join("data", "bots", bot_id))
            entrypoint = bot.get("entrypoint", "bot.py")
            entrypoint_path = os.path.join(bot_dir, entrypoint)

            # Command injection sanitization check: entrypoint must be a python file in same folder
            if ".." in entrypoint or "/" in entrypoint or "\\" in entrypoint or not entrypoint.endswith(".py"):
                logger.error(f"Invalid/Unsafe entrypoint name: {entrypoint}")
                db.update_bot(bot_id, {"status": "error"})
                log_path = os.path.join("data", "logs", f"{bot_id}.log")
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"\n[SECURITY WARNING] Invalid entrypoint '{entrypoint}'. Entrypoint must be a single local python file name (e.g. bot.py).\n")
                return False

            if not os.path.exists(entrypoint_path):
                logger.error(f"Entrypoint {entrypoint_path} does not exist.")
                db.update_bot(bot_id, {"status": "error"})
                log_path = os.path.join("data", "logs", f"{bot_id}.log")
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"\n[MANAGER ERROR] Entrypoint file '{entrypoint}' not found in bot directory.\n")
                return False

            # Sandboxing: Change owner recursively to nobody on Linux/Docker
            # This allows the 'nobody' user to read/write files inside the bot's own folder
            is_linux = (os.name != "nt")
            is_root = False
            if is_linux:
                try:
                    is_root = (os.getuid() == 0)
                except Exception:
                    pass

            if is_linux and is_root:
                try:
                    # chown and chmod the bot directory and files to nobody for sandboxed execution
                    shutil.chown(bot_dir, user="nobody", group="nogroup")
                    os.chmod(bot_dir, 0o700)
                    for root, dirs, files in os.walk(bot_dir):
                        for d in dirs:
                            path = os.path.join(root, d)
                            shutil.chown(path, user="nobody", group="nogroup")
                            os.chmod(path, 0o700)
                        for f in files:
                            path = os.path.join(root, f)
                            shutil.chown(path, user="nobody", group="nogroup")
                            # Files in venv bin/Scripts folders or shell scripts need execute permissions (0o700)
                            parts = path.replace("\\", "/").split("/")
                            if "bin" in parts or "Scripts" in parts or f.endswith(".sh"):
                                os.chmod(path, 0o700)
                            else:
                                os.chmod(path, 0o600)
                    logger.info(f"Chowned and secured bot folder {bot_id} to nobody:nogroup (700/600/700-exec).")
                except Exception as ex:
                    logger.warning(f"Sandboxing owner/permission adjustment failed: {str(ex)}. Proceeding anyway.")

            python_exe = self.get_python_executable(bot_dir)
            
            # Setup environment variables
            env = os.environ.copy()
            # Inject bot specific env vars
            token_to_verify = None
            for k, v in bot.get("env_vars", {}).items():
                env[k] = v
                if "TOKEN" in k.upper():
                    token_to_verify = v
            
            # If not found in env vars, scan the directory files for a token
            if not token_to_verify:
                token_to_verify = find_telegram_token_in_dir(bot_dir)

            log_path = os.path.join("data", "logs", f"{bot_id}.log")
            
            # Start process
            try:
                # Verify token if present
                token_check_msg = ""
                if token_to_verify:
                    bot_info = get_telegram_bot_info(token_to_verify)
                    if bot_info:
                        bot_username = bot_info.get("username")
                        bot_telegram_name = bot_info.get("first_name")
                        token_check_msg = f"[TOKEN CHECK] SUCCESS: Token is VALID. Bot Username: @{bot_username}"
                        db.update_bot(bot_id, {
                            "bot_username": bot_username,
                            "bot_telegram_name": bot_telegram_name
                        })
                    else:
                        token_check_msg = verify_telegram_token(token_to_verify)

                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"\n--- Starting Bot: {bot['name']} ({datetime_now_str()}) ---\n")
                    if token_check_msg:
                        f.write(token_check_msg + "\n")
                    f.flush()

                # Build popen args
                popen_kwargs = {
                    "cwd": bot_dir,
                    "env": env,
                    "stdout": subprocess.PIPE,
                    "stderr": subprocess.STDOUT,
                    "text": True,
                    "bufsize": 1
                }
                
                # Sandboxing: On Linux running as root, spawn subprocess as unprivileged 'nobody' user
                if is_linux and is_root:
                    popen_kwargs["user"] = "nobody"

                proc = subprocess.Popen(
                    [python_exe, entrypoint],
                    **popen_kwargs
                )
                self.processes[bot_id] = proc
                db.update_bot(bot_id, {"status": "running", "last_started": datetime.datetime.now().isoformat()})
                
                # Start background log streaming & rotation thread
                threading.Thread(target=self._stream_and_rotate_logs, args=(proc, bot_id, log_path), daemon=True).start()
                
                logger.info(f"Successfully started bot {bot_id}")
                return True
            except Exception as e:
                logger.error(f"Failed to start bot {bot_id}: {str(e)}")
                db.update_bot(bot_id, {"status": "error"})
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"\n[MANAGER ERROR] Failed to spawn bot process: {str(e)}\n")
                return False

    def _stream_and_rotate_logs(self, proc: subprocess.Popen, bot_id: str, log_path: str):
        """Asynchronously streams stdout/stderr from a process to the log file with 5MB rotation."""
        for line in iter(proc.stdout.readline, ''):
            try:
                # 1. Scrape protection: Mask bot tokens to prevent security leaks
                # Simple replacement for sensitive strings matching Telegram bot tokens: \d+:[A-Za-z0-9_-]{35}
                # (Can implement token hiding if desired, we will pass it as is for raw logs but rotate size)
                
                # 2. Log Rotator: Keep logs capped at 5MB
                if os.path.exists(log_path) and os.path.getsize(log_path) > 5 * 1024 * 1024:
                    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                        lines = f.readlines()
                    with open(log_path, "w", encoding="utf-8") as f:
                        f.write(f"--- Log file truncated (exceeded 5MB limit) at {datetime_now_str()} ---\n")
                        # Keep the last 1000 lines
                        f.writelines(lines[-1000:])
                        
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.flush()
            except Exception:
                pass
        proc.stdout.close()

    def stop_bot(self, bot_id: str) -> bool:
        with self.lock:
            bot = db.get_bot(bot_id)
            if not bot:
                return False

            db.update_bot(bot_id, {"status": "stopped"})

            if bot_id in self.processes:
                proc = self.processes[bot_id]
                try:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                except Exception as e:
                    logger.error(f"Error terminating bot {bot_id}: {str(e)}")
                finally:
                    del self.processes[bot_id]

            # Write termination log
            log_path = os.path.join("data", "logs", f"{bot_id}.log")
            try:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"\n--- Bot Stopped ({datetime_now_str()}) ---\n")
            except Exception:
                pass
                
            logger.info(f"Stopped bot {bot_id}")
            return True

    def restart_bot(self, bot_id: str) -> bool:
        self.stop_bot(bot_id)
        return self.start_bot(bot_id)

    def get_bot_metrics(self, bot_id: str) -> dict:
        """Returns CPU, RAM, and uptime metrics for a running bot process."""
        proc = self.processes.get(bot_id)
        
        # If process is not running or has terminated
        if not proc or proc.poll() is not None:
            return {
                "running": False,
                "cpu": 0.0,
                "ram": 0.0,
                "uptime": 0
            }
            
        try:
            p = psutil.Process(proc.pid)
            with p.oneshot():
                # Memory usage in Megabytes
                ram_mb = p.memory_info().rss / (1024 * 1024)
                
                # CPU usage percentage (non-blocking call, first call might return 0)
                cpu_pct = p.cpu_percent(interval=None)
                
                # Calculate uptime
                create_time = p.create_time()
                uptime_sec = int(datetime.datetime.now().timestamp() - create_time)
                
            return {
                "running": True,
                "cpu": round(cpu_pct, 1),
                "ram": round(ram_mb, 1),
                "uptime": uptime_sec
            }
        except Exception:
            return {
                "running": False,
                "cpu": 0.0,
                "ram": 0.0,
                "uptime": 0
            }

    def create_venv_and_install_requirements(self, bot_id: str):
        """Runs in background thread to build venv and install packages."""
        bot_dir = os.path.abspath(os.path.join("data", "bots", bot_id))
        log_path = os.path.join("data", "logs", f"{bot_id}.log")

        def task():
            db.update_bot(bot_id, {"status": "installing"})
            try:
                with open(log_path, "a", encoding="utf-8") as log_file:
                    log_file.write(f"\n--- Creating Virtual Environment ({datetime_now_str()}) ---\n")
                    log_file.flush()

                    # 1. Create Venv
                    venv_dir = os.path.join(bot_dir, "venv")
                    if not os.path.exists(venv_dir):
                        proc = subprocess.Popen(
                            [sys.executable, "-m", "venv", "venv"],
                            cwd=bot_dir,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1
                        )
                        for line in iter(proc.stdout.readline, ''):
                            log_file.write(line)
                            log_file.flush()
                        proc.wait()
                        if proc.returncode != 0:
                            raise Exception(f"Venv creation failed with code {proc.returncode}")
                        log_file.write("Virtual environment created successfully.\n")
                    else:
                        log_file.write("Virtual environment already exists.\n")
                    log_file.flush()

                    # 2. Check if requirements.txt exists
                    req_path = os.path.join(bot_dir, "requirements.txt")
                    if os.path.exists(req_path):
                        log_file.write("Found requirements.txt, installing packages...\n")
                        log_file.flush()
                        
                        pip_exe = self.get_pip_executable(bot_dir)
                        proc = subprocess.Popen(
                            [pip_exe, "install", "-r", "requirements.txt"],
                            cwd=bot_dir,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1
                        )
                        for line in iter(proc.stdout.readline, ''):
                            log_file.write(line)
                            log_file.flush()
                        proc.wait()
                        if proc.returncode != 0:
                            raise Exception(f"Pip install failed with code {proc.returncode}")
                        log_file.write("Dependencies installed successfully.\n")
                    else:
                        log_file.write("No requirements.txt found. Skipping pip install.\n")
                    log_file.flush()

                db.update_bot(bot_id, {"status": "stopped"})
                logger.info(f"Venv and installation completed for bot {bot_id}")
            except Exception as e:
                logger.error(f"Failed to setup environment for bot {bot_id}: {str(e)}")
                db.update_bot(bot_id, {"status": "error"})
                try:
                    with open(log_path, "a", encoding="utf-8") as log_file:
                        log_file.write(f"\n[ENV SETUP ERROR] Failed: {str(e)}\n")
                except Exception:
                    pass

        threading.Thread(target=task, daemon=True).start()

    def install_single_module(self, bot_id: str, module_name: str):
        """Install a single python module/package in the bot's venv."""
        bot_dir = os.path.abspath(os.path.join("data", "bots", bot_id))
        log_path = os.path.join("data", "logs", f"{bot_id}.log")

        def task():
            db.update_bot(bot_id, {"status": "installing"})
            try:
                with open(log_path, "a", encoding="utf-8") as log_file:
                    log_file.write(f"\n--- Installing module: {module_name} ({datetime_now_str()}) ---\n")
                    log_file.flush()

                    pip_exe = self.get_pip_executable(bot_dir)
                    proc = subprocess.Popen(
                        [pip_exe, "install", module_name],
                        cwd=bot_dir,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1
                    )
                    for line in iter(proc.stdout.readline, ''):
                        log_file.write(line)
                        log_file.flush()
                    proc.wait()
                    if proc.returncode != 0:
                        raise Exception(f"Pip install failed with code {proc.returncode}")
                    log_file.write(f"Module '{module_name}' installed successfully.\n")
                    log_file.flush()

                db.update_bot(bot_id, {"status": "stopped"})
                logger.info(f"Installed module {module_name} for bot {bot_id}")
            except Exception as e:
                logger.error(f"Failed to install module {module_name} for bot {bot_id}: {str(e)}")
                db.update_bot(bot_id, {"status": "error"})
                try:
                    with open(log_path, "a", encoding="utf-8") as log_file:
                        log_file.write(f"\n[PIP INSTALL ERROR] Failed to install {module_name}: {str(e)}\n")
                except Exception:
                    pass

        threading.Thread(target=task, daemon=True).start()

    def delete_bot_files(self, bot_id: str):
        """Stop bot and delete all associated files and logs."""
        self.stop_bot(bot_id)
        
        bot_dir = os.path.join("data", "bots", bot_id)
        if os.path.exists(bot_dir):
            try:
                shutil.rmtree(bot_dir)
            except Exception as e:
                logger.error(f"Failed to delete bot folder {bot_dir}: {str(e)}")

        log_file = os.path.join("data", "logs", f"{bot_id}.log")
        if os.path.exists(log_file):
            try:
                os.remove(log_file)
            except Exception as e:
                logger.error(f"Failed to delete bot log {log_file}: {str(e)}")

    def read_bot_logs(self, bot_id: str, limit: int = 200) -> str:
        """Read the last N lines of bot logs."""
        log_path = os.path.join("data", "logs", f"{bot_id}.log")
        if not os.path.exists(log_path):
            return "No logs available yet."

        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
                return "".join(lines[-limit:])
        except Exception as e:
            return f"Error reading logs: {str(e)}"

    def monitor_processes(self):
        """Checks if running processes exited, handles auto-restart, and updates status."""
        with self.lock:
            for bot_id in list(self.processes.keys()):
                proc = self.processes[bot_id]
                exit_code = proc.poll()
                if exit_code is not None:
                    # Process exited!
                    logger.info(f"Bot {bot_id} exited with code {exit_code}")
                    del self.processes[bot_id]
                    
                    bot = db.get_bot(bot_id)
                    if bot:
                        if bot.get("auto_restart") and bot.get("status") == "running":
                            # Restart it!
                            logger.info(f"Auto-restarting bot {bot_id}...")
                            threading.Thread(target=self.start_bot, args=(bot_id,), daemon=True).start()
                        else:
                            # Set status to stopped if normal stop, or error if crash
                            status = "stopped" if exit_code == 0 else "error"
                            db.update_bot(bot_id, {"status": status})
                            log_path = os.path.join("data", "logs", f"{bot_id}.log")
                            try:
                                with open(log_path, "a", encoding="utf-8") as f:
                                    f.write(f"\n[MANAGER] Bot process exited with code {exit_code}.\n")
                            except Exception:
                                pass

    def restore_active_bots(self):
        """Restores bots marked as running when the system restarts."""
        bots = db.get_all_bots()
        for bot in bots:
            if bot.get("status") == "running":
                logger.info(f"Restoring active bot on boot: {bot['name']} ({bot['id']})")
                # Check if it has venv, if not build it first, else start it.
                bot_dir = os.path.join("data", "bots", bot["id"])
                venv_dir = os.path.join(bot_dir, "venv")
                if not os.path.exists(venv_dir):
                    self.create_venv_and_install_requirements(bot["id"])
                else:
                    self.start_bot(bot["id"])

def datetime_now_str() -> str:
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# Global process manager
manager = BotManager()
