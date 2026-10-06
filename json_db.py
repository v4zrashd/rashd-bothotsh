import os
import json
import hashlib
import datetime
import threading
from typing import Dict, List, Optional

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    db_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    return f"{salt.hex()}:{db_hash.hex()}"

def verify_password(plain: str, hashed: str) -> bool:
    try:
        salt_hex, hash_hex = hashed.split(":")
        salt = bytes.fromhex(salt_hex)
        expected_hash = bytes.fromhex(hash_hex)
        db_hash = hashlib.pbkdf2_hmac('sha256', plain.encode('utf-8'), salt, 100000)
        return db_hash == expected_hash
    except Exception:
        return False

class JSONDatabase:
    def __init__(self, filepath: str = "data/database.json"):
        self.filepath = filepath
        self.lock = threading.Lock()
        self._ensure_db_exists()

    def _ensure_db_exists(self):
        directory = os.path.dirname(self.filepath)
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
            if os.name != "nt":
                try:
                    os.chmod(directory, 0o711)
                except Exception:
                    pass
            
        with self.lock:
            # If DB doesn't exist, create it with default schema
            if not os.path.exists(self.filepath):
                default_data = {
                    "settings": {
                        "auto_approve": False
                    },
                    "users": {},
                    "bots": {}
                }
                default_password = os.environ.get("ADMIN_PASSWORD", "admin123")
                hashed = hash_password(default_password)
                default_data["users"]["admin"] = {
                    "username": "admin",
                    "password_hash": hashed,
                    "role": "admin",
                    "status": "approved",
                    "ip_address": "127.0.0.1",
                    "device_name": "Local Server",
                    "registered_at": datetime.datetime.now().isoformat(),
                    "bot_limit": 999
                }
                with open(self.filepath, "w") as f:
                    json.dump(default_data, f, indent=4)
            else:
                # If DB exists, make sure 'settings' key exists (for backward compatibility)
                try:
                    with open(self.filepath, "r") as f:
                        data = json.load(f)
                    changed = False
                    if "settings" not in data:
                        data["settings"] = {"auto_approve": False}
                        changed = True
                    # Check that default admin exists
                    if "admin" not in data["users"]:
                        default_password = os.environ.get("ADMIN_PASSWORD", "admin123")
                        data["users"]["admin"] = {
                            "username": "admin",
                            "password_hash": hash_password(default_password),
                            "role": "admin",
                            "status": "approved",
                            "ip_address": "127.0.0.1",
                            "device_name": "Local Server",
                            "registered_at": datetime.datetime.now().isoformat(),
                            "bot_limit": 999
                        }
                        changed = True
                    # Check roles and statuses in existing users
                    for username, user in data["users"].items():
                        if "role" not in user:
                            user["role"] = "admin" if username == "admin" else "user"
                            changed = True
                        if "status" not in user:
                            user["status"] = "approved"
                            changed = True
                        if "ip_address" not in user:
                            user["ip_address"] = "127.0.0.1" if username == "admin" else "unknown"
                            changed = True
                        if "device_name" not in user:
                            user["device_name"] = "Local Server" if username == "admin" else "unknown"
                            changed = True
                        if "registered_at" not in user:
                            # set back to some default time
                            user["registered_at"] = datetime.datetime.now().isoformat()
                            changed = True
                        if "bot_limit" not in user:
                            user["bot_limit"] = 999 if user.get("role") == "admin" else 3
                            changed = True
                    if changed:
                        with open(self.filepath, "w") as f:
                            json.dump(data, f, indent=4)
                except Exception as e:
                    print("Error updating database schema on boot:", e)
                    
        # Ensure database file permissions are private (0o600) on Linux
        if os.name != "nt" and os.path.exists(self.filepath):
            try:
                os.chmod(self.filepath, 0o600)
            except Exception:
                pass

    def _read(self) -> dict:
        with open(self.filepath, "r") as f:
            return json.load(f)

    def _write(self, data: dict):
        with open(self.filepath, "w") as f:
            json.dump(data, f, indent=4)
        if os.name != "nt":
            try:
                os.chmod(self.filepath, 0o600)
            except Exception:
                pass

    # --- Settings ---
    def get_setting(self, key: str, default=None):
        with self.lock:
            data = self._read()
            return data.get("settings", {}).get(key, default)

    def set_setting(self, key: str, value):
        with self.lock:
            data = self._read()
            if "settings" not in data:
                data["settings"] = {}
            data["settings"][key] = value
            self._write(data)

    # --- User Operations ---
    def get_user(self, username: str) -> Optional[dict]:
        with self.lock:
            data = self._read()
            return data["users"].get(username)

    def get_all_users(self) -> List[dict]:
        with self.lock:
            data = self._read()
            return list(data["users"].values())

    def create_user(self, username: str, password_plain: str, role: str = "user", 
                    status: str = "pending", ip_address: str = "unknown", 
                    device_name: str = "unknown", bot_limit: int = 3) -> Optional[dict]:
        with self.lock:
            data = self._read()
            if username in data["users"]:
                return None # User already exists
            
            user_data = {
                "username": username,
                "password_hash": hash_password(password_plain),
                "role": role,
                "status": status,
                "ip_address": ip_address,
                "device_name": device_name,
                "registered_at": datetime.datetime.now().isoformat(),
                "bot_limit": 999 if role == "admin" else bot_limit
            }
            data["users"][username] = user_data
            self._write(data)
            return user_data

    def update_user_status(self, username: str, status: str) -> bool:
        with self.lock:
            data = self._read()
            if username in data["users"] and username != "admin": # Admin status cannot be changed
                data["users"][username]["status"] = status
                self._write(data)
                return True
            return False

    def update_user_bot_limit(self, username: str, limit: int) -> bool:
        with self.lock:
            data = self._read()
            if username in data["users"]:
                data["users"][username]["bot_limit"] = limit
                self._write(data)
                return True
            return False

    def update_admin_password(self, username: str, new_password: str) -> bool:
        with self.lock:
            data = self._read()
            if username in data["users"]:
                data["users"][username]["password_hash"] = hash_password(new_password)
                self._write(data)
                return True
            return False

    def delete_user(self, username: str) -> bool:
        with self.lock:
            data = self._read()
            if username in data["users"] and username != "admin": # Admin cannot be deleted
                del data["users"][username]
                # Optional: Delete all bots belonging to this user
                bots_to_delete = [bid for bid, bot in data["bots"].items() if bot.get("owner") == username]
                for bid in bots_to_delete:
                    del data["bots"][bid]
                self._write(data)
                return True
            return False

    def verify_password(self, plain: str, hashed: str) -> bool:
        return verify_password(plain, hashed)

    # --- Bot CRUD ---
    def create_bot(self, bot_id: str, name: str, source_type: str, 
                   owner: str,
                   entrypoint: str = "bot.py", 
                   git_url: Optional[str] = None, 
                   git_branch: Optional[str] = "main",
                   env_vars: Optional[Dict[str, str]] = None,
                   auto_restart: bool = True) -> dict:
        with self.lock:
            data = self._read()
            
            bot_data = {
                "id": bot_id,
                "name": name,
                "owner": owner,
                "source_type": source_type, # 'zip', 'git', 'paste'
                "git_url": git_url,
                "git_branch": git_branch or "main",
                "entrypoint": entrypoint,
                "env_vars": env_vars or {},
                "auto_restart": auto_restart,
                "status": "stopped", # stopped, running, error, installing
                "created_at": datetime.datetime.now().isoformat(),
                "bot_username": None,
                "bot_telegram_name": None
            }
            data["bots"][bot_id] = bot_data
            self._write(data)
            return bot_data

    def get_bot(self, bot_id: str) -> Optional[dict]:
        with self.lock:
            data = self._read()
            return data["bots"].get(bot_id)

    def get_all_bots(self) -> List[dict]:
        with self.lock:
            data = self._read()
            return list(data["bots"].values())

    def get_user_bots(self, username: str) -> List[dict]:
        with self.lock:
            data = self._read()
            return [bot for bot in data["bots"].values() if bot.get("owner") == username]

    def update_bot(self, bot_id: str, updates: dict) -> Optional[dict]:
        with self.lock:
            data = self._read()
            if bot_id in data["bots"]:
                for key, val in updates.items():
                    if key in ["name", "entrypoint", "env_vars", "auto_restart", "status", "git_url", "git_branch", "owner", "bot_username", "bot_telegram_name"]:
                        data["bots"][bot_id][key] = val
                self._write(data)
                return data["bots"][bot_id]
            return None

    def delete_bot(self, bot_id: str) -> bool:
        with self.lock:
            data = self._read()
            if bot_id in data["bots"]:
                del data["bots"][bot_id]
                self._write(data)
                return True
            return False

# Global instance
db = JSONDatabase()
