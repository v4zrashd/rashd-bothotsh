import os
import uuid
import zipfile
import tempfile
import shutil
import subprocess
import asyncio
import psutil
import datetime
from typing import Dict, Optional, List
from fastapi import (
    FastAPI, Request, Depends, HTTPException, status, 
    Form, File, UploadFile, WebSocket, WebSocketDisconnect, Cookie
)
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from json_db import db
from manager import manager, datetime_now_str, logger
from data_sandbox import safe_join

app = FastAPI(title="Telegram Bot Hoster - V4ZTelegramBotHoster")

# Create directories if they do not exist
os.makedirs("data/bots", exist_ok=True)
os.makedirs("data/logs", exist_ok=True)

# Mount static and templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# In-memory session store: session_id -> username
active_sessions: Dict[str, str] = {}

# Dependency to check session and return full user dict
def get_current_user(session_id: Optional[str] = Cookie(None)) -> dict:
    if not session_id or session_id not in active_sessions:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    username = active_sessions[session_id]
    user = db.get_user(username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session user not found"
        )
    if user.get("status") == "banned":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been banned."
        )
    if user.get("status") == "pending":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is pending admin approval."
        )
    return user

# Helper to verify if the user has permission to manage the bot
def verify_bot_ownership(bot_id: str, user: dict) -> dict:
    bot = db.get_bot(bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail="Bot application not found")
    
    # Admins can manage any bot, users can only manage their own
    if user.get("role") != "admin" and bot.get("owner") != user.get("username"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission Denied: You do not own this bot application."
        )
    return bot

# Background loop for process monitoring
async def process_monitor_loop():
    while True:
        try:
            manager.monitor_processes()
        except Exception as e:
            logger.error(f"Error in process monitor: {str(e)}")
        await asyncio.sleep(2)

@app.on_event("startup")
async def startup_event():
    # Start the process monitor loop in the background
    asyncio.create_task(process_monitor_loop())
    # Restore active bots on startup
    manager.restore_active_bots()

# --- Auth Routes ---

@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    return templates.TemplateResponse(request=request, name="login.html")

@app.post("/login")
async def login_post(username: str = Form(...), password: str = Form(...)):
    # Standardize usernames to lowercase
    username = username.strip().lower()
    user = db.get_user(username)
    if not user or not db.verify_password(password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect username or password"
        )
    
    if user.get("status") == "banned":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been banned."
        )
    if user.get("status") == "pending":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is pending admin approval."
        )

    # Generate session
    session_id = str(uuid.uuid4())
    active_sessions[session_id] = username
    
    response = JSONResponse(content={"status": "success", "role": user.get("role", "user")})
    # HTTP-only cookie for security
    response.set_cookie(key="session_id", value=session_id, httponly=True)
    return response

def get_client_ip(request: Request) -> str:
    x_forwarded_for = request.headers.get("x-forwarded-for")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    x_real_ip = request.headers.get("x-real-ip")
    if x_real_ip:
        return x_real_ip.strip()
    return request.client.host if request.client else "unknown"

@app.post("/register")
async def register_post(request: Request, username: str = Form(...), password: str = Form(...)):
    username = username.strip().lower()
    
    # Audit log extraction (IP & Device details)
    ip_address = get_client_ip(request)
    device_name = request.headers.get("User-Agent", "Unknown Device")
    
    # Auto-approve toggle check
    auto_approve = db.get_setting("auto_approve", False)
    status_choice = "approved" if auto_approve else "pending"
    
    user = db.create_user(
        username=username,
        password_plain=password,
        role="user",
        status=status_choice,
        ip_address=ip_address,
        device_name=device_name
    )
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already taken."
        )
        
    return {"status": "success", "approval_required": not auto_approve}

class TokenResetRequest(BaseModel):
    token: str
    new_password: str

@app.post("/api/recover")
async def recover_admin(req: TokenResetRequest):
    """Reset the admin password using a Telegram bot token hosted on this panel."""
    from manager import get_telegram_bot_info
    info = get_telegram_bot_info(req.token)
    if not info:
        raise HTTPException(status_code=403, detail="Invalid Telegram token")
    bot_username = info.get("username", "")
    all_bots = db.get_all_bots()
    match = None
    for b in all_bots:
        stored = b.get("bot_username") or b.get("username") or ""
        if stored and stored.lstrip("@").lower() == bot_username.lower():
            match = b
            break
    if not match:
        raise HTTPException(status_code=403, detail="This bot is not hosted on this panel")
    ok = db.update_admin_password("admin", req.new_password)
    if not ok:
        raise HTTPException(status_code=500, detail="Password update failed")
    return {"status": "success", "bot": "@" + bot_username}

@app.get("/logout")
async def logout(session_id: Optional[str] = Cookie(None)):
    if session_id in active_sessions:
        del active_sessions[session_id]
    
    response = RedirectResponse(url="/login")
    response.delete_cookie("session_id")
    return response

# --- UI Routes ---

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, session_id: Optional[str] = Cookie(None)):
    if not session_id or session_id not in active_sessions:
        return RedirectResponse(url="/login")
        
    username = active_sessions[session_id]
    user = db.get_user(username)
    if not user or user.get("status") != "approved":
        response = RedirectResponse(url="/login")
        response.delete_cookie("session_id")
        return response
        
    return templates.TemplateResponse(request=request, name="dashboard.html")

# --- API Endpoints ---

@app.get("/api/me")
async def get_me(user: dict = Depends(get_current_user)):
    return {
        "username": user["username"], 
        "role": user["role"],
        "bot_limit": user.get("bot_limit", 3)
    }

@app.get("/api/metrics")
async def get_metrics(user: dict = Depends(get_current_user)):
    # Calculate totals based on roles
    if user.get("role") == "admin":
        total_bots = len(db.get_all_bots())
        running_bots = len(manager.processes)
    else:
        user_bots = db.get_user_bots(user["username"])
        total_bots = len(user_bots)
        running_bots = sum(1 for b in user_bots if b["id"] in manager.processes)
    
    # Get host system metrics
    cpu = psutil.cpu_percent(interval=None)
    ram = psutil.virtual_memory().percent
    
    return {
        "total_bots": total_bots,
        "running_bots": running_bots,
        "cpu_usage": cpu,
        "ram_usage": ram
    }

@app.get("/api/bots")
async def get_bots(user: dict = Depends(get_current_user)):
    if user.get("role") == "admin":
        return db.get_all_bots()
    return db.get_user_bots(user["username"])

@app.get("/api/bots/{bot_id}")
async def get_bot(bot_id: str, user: dict = Depends(get_current_user)):
    bot = verify_bot_ownership(bot_id, user)
    return bot

@app.post("/api/bots")
async def create_bot(
    name: str = Form(...),
    entrypoint: str = Form("bot.py"),
    source_type: str = Form(...), # zip, git, paste
    zip_file: Optional[UploadFile] = File(None),
    git_url: Optional[str] = Form(None),
    git_branch: Optional[str] = Form("main"),
    paste_code: Optional[str] = Form(None),
    paste_requirements: Optional[str] = Form(None),
    auto_restart: bool = Form(True),
    owner: Optional[str] = Form(None),
    user: dict = Depends(get_current_user)
):
    # Determine the owner of the bot
    bot_owner = user["username"]
    if user.get("role") == "admin" and owner:
        # Check if the target owner exists
        target_user = db.get_user(owner)
        if target_user:
            bot_owner = owner
        else:
            raise HTTPException(status_code=400, detail=f"Target owner user '{owner}' does not exist.")

    # Enforce bot limit check if owner is not admin
    owner_user_data = db.get_user(bot_owner)
    if owner_user_data and owner_user_data.get("role") != "admin":
        limit = owner_user_data.get("bot_limit", 3)
        existing_bots = db.get_user_bots(bot_owner)
        if len(existing_bots) >= limit:
            raise HTTPException(
                status_code=400,
                detail=f"User '{bot_owner}' has reached their limit of {limit} bots. Contact admin to increase it."
            )

    bot_id = f"bot_{int(asyncio.get_event_loop().time() * 1000)}"
    bot_dir = os.path.abspath(os.path.join("data", "bots", bot_id))
    log_path = os.path.join("data", "logs", f"{bot_id}.log")
    
    os.makedirs(bot_dir, exist_ok=True)

    try:
        # 1. Retrieve bot code based on source type
        if source_type == "zip":
            if not zip_file:
                raise HTTPException(status_code=400, detail="ZIP file is required")
            
            # Save file to a temporary location
            with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
                shutil.copyfileobj(zip_file.file, tmp)
                tmp_path = tmp.name
            
            # Extract ZIP
            try:
                with zipfile.ZipFile(tmp_path, 'r') as zip_ref:
                    zip_ref.extractall(bot_dir)
                # Log success
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write(f"[MANAGER] Extracted ZIP archive to {bot_dir} successfully.\n")
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to extract ZIP: {str(e)}")
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

        elif source_type == "git":
            if not git_url:
                raise HTTPException(status_code=400, detail="Git URL is required")
            
            # Write initial log
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(f"[MANAGER] Cloning git repository {git_url} branch {git_branch}...\n")
            
            # Clone in subprocess
            try:
                log_file = open(log_path, "a", encoding="utf-8")
                subprocess.run(
                    ["git", "clone", "--depth", "1", "-b", git_branch, git_url, "."],
                    cwd=bot_dir,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    check=True
                )
                log_file.write("[MANAGER] Git repository cloned successfully.\n")
                log_file.close()
            except Exception as e:
                # Cleanup directory
                if os.path.exists(bot_dir):
                    shutil.rmtree(bot_dir)
                raise HTTPException(status_code=400, detail=f"Failed to clone Git repository: {str(e)}")

        elif source_type == "paste":
            if not paste_code:
                raise HTTPException(status_code=400, detail="Pasted Python code is required")
            
            entrypoint_path = os.path.join(bot_dir, entrypoint)
            with open(entrypoint_path, "w", encoding="utf-8") as f:
                f.write(paste_code)
                
            if paste_requirements:
                req_path = os.path.join(bot_dir, "requirements.txt")
                with open(req_path, "w", encoding="utf-8") as f:
                    f.write(paste_requirements)
                
            # Log success
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(f"[MANAGER] Created entrypoint '{entrypoint}' and requirements.txt with pasted script.\n")
        else:
            raise HTTPException(status_code=400, detail="Invalid source type")

        # 2. Save bot configuration in JSON db mapping to the current owner
        bot = db.create_bot(
            bot_id=bot_id,
            name=name,
            owner=bot_owner,
            source_type=source_type,
            entrypoint=entrypoint,
            git_url=git_url,
            git_branch=git_branch,
            auto_restart=auto_restart
        )

        # Scan bot directory for a telegram bot token and sync bot details immediately
        from manager import find_telegram_token_in_dir, get_telegram_bot_info
        token = find_telegram_token_in_dir(bot_dir)
        if token:
            bot_info = get_telegram_bot_info(token)
            if bot_info:
                db.update_bot(bot_id, {
                    "bot_username": bot_info.get("username"),
                    "bot_telegram_name": bot_info.get("first_name")
                })
                # Re-fetch the updated bot dictionary
                bot = db.get_bot(bot_id) or bot

        # 3. Create virtual environment and install packages in background
        manager.create_venv_and_install_requirements(bot_id)
        
        return bot
        
    except HTTPException as he:
        raise he
    except Exception as e:
        if os.path.exists(bot_dir):
            shutil.rmtree(bot_dir)
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")

@app.delete("/api/bots/{bot_id}")
async def delete_bot(bot_id: str, user: dict = Depends(get_current_user)):
    verify_bot_ownership(bot_id, user)
    # Remove files and stop bot
    manager.delete_bot_files(bot_id)
    # Remove from DB
    db.delete_bot(bot_id)
    return {"status": "success"}

@app.post("/api/bots/{bot_id}/start")
async def start_bot(bot_id: str, user: dict = Depends(get_current_user)):
    verify_bot_ownership(bot_id, user)
    success = manager.start_bot(bot_id)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to start bot. Check console logs.")
    return {"status": "success"}

@app.post("/api/bots/{bot_id}/stop")
async def stop_bot(bot_id: str, user: dict = Depends(get_current_user)):
    verify_bot_ownership(bot_id, user)
    success = manager.stop_bot(bot_id)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to stop bot.")
    return {"status": "success"}

@app.post("/api/bots/{bot_id}/restart")
async def restart_bot(bot_id: str, user: dict = Depends(get_current_user)):
    verify_bot_ownership(bot_id, user)
    success = manager.restart_bot(bot_id)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to restart bot. Check console logs.")
    return {"status": "success"}

@app.get("/api/bots/{bot_id}/logs")
async def get_bot_logs(bot_id: str, user: dict = Depends(get_current_user)):
    verify_bot_ownership(bot_id, user)
    return HTMLResponse(content=manager.read_bot_logs(bot_id), media_type="text/plain")

@app.put("/api/bots/{bot_id}/env")
async def update_bot_env(bot_id: str, data: Dict[str, str], user: dict = Depends(get_current_user)):
    verify_bot_ownership(bot_id, user)
    db.update_bot(bot_id, {"env_vars": data})
    
    # Check if a token is in env vars and sync details immediately
    token = None
    for k, v in data.items():
        if "TOKEN" in k.upper():
            token = v
            break
    if token:
        from manager import get_telegram_bot_info
        bot_info = get_telegram_bot_info(token)
        if bot_info:
            db.update_bot(bot_id, {
                "bot_username": bot_info.get("username"),
                "bot_telegram_name": bot_info.get("first_name")
            })
            
    return {"status": "success"}

class InstallPackageRequest(BaseModel):
    package_name: str

@app.post("/api/bots/{bot_id}/install")
async def install_package(bot_id: str, req: InstallPackageRequest, user: dict = Depends(get_current_user)):
    verify_bot_ownership(bot_id, user)
    manager.install_single_module(bot_id, req.package_name)
    return {"status": "success"}

@app.post("/api/bots/{bot_id}/install-requirements")
async def install_requirements(bot_id: str, user: dict = Depends(get_current_user)):
    verify_bot_ownership(bot_id, user)
    manager.create_venv_and_install_requirements(bot_id)
    return {"status": "success"}

@app.get("/api/bots/{bot_id}/requirements")
async def get_bot_requirements(bot_id: str, user: dict = Depends(get_current_user)):
    verify_bot_ownership(bot_id, user)
    bot_dir = os.path.abspath(os.path.join("data", "bots", bot_id))
    req_path = os.path.join(bot_dir, "requirements.txt")
    
    requirements = ""
    if os.path.exists(req_path):
        try:
            with open(req_path, "r", encoding="utf-8", errors="ignore") as f:
                requirements = f.read()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to read requirements: {str(e)}")
            
    return {"requirements": requirements}

class UpdateRequirementsRequest(BaseModel):
    requirements: str

@app.put("/api/bots/{bot_id}/requirements")
async def update_bot_requirements(bot_id: str, req: UpdateRequirementsRequest, user: dict = Depends(get_current_user)):
    verify_bot_ownership(bot_id, user)
    bot_dir = os.path.abspath(os.path.join("data", "bots", bot_id))
    req_path = os.path.join(bot_dir, "requirements.txt")
    
    try:
        with open(req_path, "w", encoding="utf-8") as f:
            f.write(req.requirements)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save requirements: {str(e)}")

# --- Web IDE File Manager API Endpoints (Secured with safe_join) ---

@app.get("/api/bots/{bot_id}/files")
async def get_files(bot_id: str, user: dict = Depends(get_current_user)):
    verify_bot_ownership(bot_id, user)
    bot_dir = os.path.abspath(os.path.join("data", "bots", bot_id))
    
    file_list = []
    try:
        for root, dirs, files in os.walk(bot_dir):
            # Exclude venv and cache folders for performance/cleanliness
            if "venv" in root or "__pycache__" in root or ".git" in root:
                continue
            
            # Add directories
            for d in dirs:
                if d in ["venv", "__pycache__", ".git"]:
                    continue
                abs_path = os.path.join(root, d)
                rel_path = os.path.relpath(abs_path, bot_dir)
                file_list.append({
                    "path": rel_path.replace("\\", "/"),
                    "name": d,
                    "is_dir": True,
                    "size": 0
                })
                
            # Add files
            for f in files:
                abs_path = os.path.join(root, f)
                rel_path = os.path.relpath(abs_path, bot_dir)
                file_list.append({
                    "path": rel_path.replace("\\", "/"),
                    "name": f,
                    "is_dir": False,
                    "size": os.path.getsize(abs_path)
                })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read directories: {str(e)}")
        
    return file_list

@app.get("/api/bots/{bot_id}/files/read")
async def read_file(bot_id: str, path: str, user: dict = Depends(get_current_user)):
    verify_bot_ownership(bot_id, user)
    bot_dir = os.path.abspath(os.path.join("data", "bots", bot_id))
    
    try:
        abs_path = safe_join(bot_dir, path)
        if not os.path.exists(abs_path):
            raise HTTPException(status_code=404, detail="File not found")
        if os.path.isdir(abs_path):
            raise HTTPException(status_code=400, detail="Cannot read directories as file text")
            
        with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        return {"content": content}
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read file: {str(e)}")

class WriteFileRequest(BaseModel):
    path: str
    content: str

@app.post("/api/bots/{bot_id}/files/write")
async def write_file_endpoint(bot_id: str, req: WriteFileRequest, user: dict = Depends(get_current_user)):
    verify_bot_ownership(bot_id, user)
    bot_dir = os.path.abspath(os.path.join("data", "bots", bot_id))
    
    try:
        abs_path = safe_join(bot_dir, req.path)
        
        # Ensure directories exist
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(req.content)
            
        # Scan content for a telegram bot token and sync details if found
        import re
        token_pattern = re.compile(r"\b(\d{8,10}:[a-zA-Z0-9_-]{35})\b")
        match = token_pattern.search(req.content)
        if match:
            token = match.group(1)
            from manager import get_telegram_bot_info
            bot_info = get_telegram_bot_info(token)
            if bot_info:
                db.update_bot(bot_id, {
                    "bot_username": bot_info.get("username"),
                    "bot_telegram_name": bot_info.get("first_name")
                })
                
        return {"status": "success"}
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write file: {str(e)}")

class CreateFileRequest(BaseModel):
    path: str
    is_dir: bool

@app.post("/api/bots/{bot_id}/files/create")
async def create_file_endpoint(bot_id: str, req: CreateFileRequest, user: dict = Depends(get_current_user)):
    verify_bot_ownership(bot_id, user)
    bot_dir = os.path.abspath(os.path.join("data", "bots", bot_id))
    
    try:
        abs_path = safe_join(bot_dir, req.path)
        if os.path.exists(abs_path):
            raise HTTPException(status_code=400, detail="Path already exists")
            
        if req.is_dir:
            os.makedirs(abs_path, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write("") # Create empty file
        return {"status": "success"}
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create item: {str(e)}")

@app.delete("/api/bots/{bot_id}/files/delete")
async def delete_file_endpoint(bot_id: str, path: str, user: dict = Depends(get_current_user)):
    verify_bot_ownership(bot_id, user)
    bot_dir = os.path.abspath(os.path.join("data", "bots", bot_id))
    
    try:
        abs_path = safe_join(bot_dir, path)
        if not os.path.exists(abs_path):
            raise HTTPException(status_code=404, detail="Item not found")
            
        if os.path.isdir(abs_path):
            shutil.rmtree(abs_path)
        else:
            os.remove(abs_path)
        return {"status": "success"}
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete item: {str(e)}")

# --- Bot Metrics Endpoint ---

@app.get("/api/bots/{bot_id}/metrics")
async def get_bot_metrics(bot_id: str, user: dict = Depends(get_current_user)):
    verify_bot_ownership(bot_id, user)
    return manager.get_bot_metrics(bot_id)

# --- Admin Panel Endpoints ---

def verify_admin(user: dict = Depends(get_current_user)):
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission Denied: Admin access required."
        )
    return user

@app.get("/api/admin/users")
async def admin_get_users(admin: dict = Depends(verify_admin)):
    # Returns users, omitting hashes for security
    users = db.get_all_users()
    sanitized = []
    for u in users:
        uname = u["username"]
        bots_count = len(db.get_user_bots(uname))
        sanitized.append({
            "username": uname,
            "role": u["role"],
            "status": u["status"],
            "ip_address": u.get("ip_address", "unknown"),
            "device_name": u.get("device_name", "unknown"),
            "registered_at": u.get("registered_at"),
            "bot_limit": u.get("bot_limit", 3),
            "bots_count": bots_count
        })
    return sanitized

@app.post("/api/admin/users/{username}/approve")
async def admin_approve_user(username: str, admin: dict = Depends(verify_admin)):
    success = db.update_user_status(username, "approved")
    if not success:
        raise HTTPException(status_code=400, detail="Failed to approve user.")
    return {"status": "success"}

@app.post("/api/admin/users/{username}/ban")
async def admin_ban_user(username: str, admin: dict = Depends(verify_admin)):
    success = db.update_user_status(username, "banned")
    if not success:
        raise HTTPException(status_code=400, detail="Failed to ban user.")
    
    # Invalidate active session for banned user
    for sid, uname in list(active_sessions.items()):
        if uname == username:
            del active_sessions[sid]
            
    return {"status": "success"}

@app.post("/api/admin/users/{username}/unban")
async def admin_unban_user(username: str, admin: dict = Depends(verify_admin)):
    success = db.update_user_status(username, "approved")
    if not success:
        raise HTTPException(status_code=400, detail="Failed to unban user.")
    return {"status": "success"}

class UserLimitRequest(BaseModel):
    bot_limit: int

@app.put("/api/admin/users/{username}/limit")
async def admin_update_user_limit(username: str, req: UserLimitRequest, admin: dict = Depends(verify_admin)):
    if req.bot_limit < 0:
        raise HTTPException(status_code=400, detail="Limit must be a positive integer.")
    success = db.update_user_bot_limit(username, req.bot_limit)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to update user bot limit.")
    return {"status": "success"}

@app.get("/api/admin/users/{username}/bots")
async def admin_get_user_bots(username: str, admin: dict = Depends(verify_admin)):
    user_bots = db.get_user_bots(username)
    return user_bots

@app.delete("/api/admin/users/{username}")
async def admin_delete_user(username: str, admin: dict = Depends(verify_admin)):
    # Stop all bots owned by this user
    user_bots = db.get_user_bots(username)
    for bot in user_bots:
        manager.delete_bot_files(bot["id"])
        
    success = db.delete_user(username)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to delete user.")
        
    # Invalidate session
    for sid, uname in list(active_sessions.items()):
        if uname == username:
            del active_sessions[sid]
            
    return {"status": "success"}

@app.get("/api/admin/settings")
async def admin_get_settings(admin: dict = Depends(verify_admin)):
    return {
        "auto_approve": db.get_setting("auto_approve", False)
    }

class SettingsUpdateRequest(BaseModel):
    auto_approve: bool

@app.put("/api/admin/settings")
async def admin_update_settings(req: SettingsUpdateRequest, admin: dict = Depends(verify_admin)):
    db.set_setting("auto_approve", req.auto_approve)
    return {"status": "success"}

class ChangePasswordRequest(BaseModel):
    new_password: str

@app.post("/api/admin/change-password")
async def admin_change_password(req: ChangePasswordRequest, user: dict = Depends(get_current_user)):
    # Both admin and normal users can update their own passwords using this endpoint
    success = db.update_admin_password(user["username"], req.new_password)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to update password.")
    return {"status": "success"}

# --- WebSocket for live log streaming ---

@app.websocket("/api/bots/{bot_id}/logs/ws")
async def websocket_logs(websocket: WebSocket, bot_id: str):
    await websocket.accept()
    log_path = os.path.join("data", "logs", f"{bot_id}.log")
    
    if not os.path.exists(log_path):
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(f"--- Log Session Initialized ({datetime_now_str()}) ---\n")

    async def read_logs():
        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(0, os.SEEK_END)
                while True:
                    line = f.readline()
                    if not line:
                        await asyncio.sleep(0.2)
                        continue
                    await websocket.send_text(line)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in read_logs task: {str(e)}")

    async def listen_disconnect():
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        except asyncio.CancelledError:
            pass

    try:
        read_task = asyncio.create_task(read_logs())
        disconnect_task = asyncio.create_task(listen_disconnect())
        
        done, pending = await asyncio.wait(
            [read_task, disconnect_task],
            return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
    except Exception as e:
        logger.error(f"WebSocket error for bot {bot_id}: {str(e)}")
