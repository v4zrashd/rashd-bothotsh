// Global State Management
let bots = [];
let selectedBotId = null;
let logSocket = null;
let metricsInterval = null;
let botRefreshInterval = null;

let currentUser = { username: "", role: "user" };
let activeFilePath = null;
let aceEditor = null;

// Metrics Chart.js variables
let cpuChart = null;
let ramChart = null;
let metricsPollInterval = null;
let cpuDataHistory = [];
let ramDataHistory = [];
let chartLabels = [];

// Initialize Page
document.addEventListener("DOMContentLoaded", () => {
    if (document.getElementById("dashboard-page")) {
        if (history.state === null) {
            history.replaceState({ view: "dashboard" }, "", "");
        }
        
        window.addEventListener("popstate", (event) => {
            const state = event.state;
            if (state && state.view === "workspace") {
                document.getElementById("dashboard-view").style.display = "none";
                document.getElementById("workspace-view").style.display = "block";
                selectBot(state.botId, true);
            } else {
                showDashboardViewInternal();
            }
        });

        initDashboard();
    }
});

async function initDashboard() {
    // 1. Fetch current user role & authenticate
    await fetchCurrentUser();
    
    // 2. Fetch list of bots
    await fetchBots(true);
    fetchMetrics();
    
    // Restore workspace view if history state was workspace
    if (history.state && history.state.view === "workspace") {
        const botId = history.state.botId;
        if (bots.some(b => b.id === botId)) {
            viewBotWorkspace(botId);
        } else {
            history.replaceState({ view: "dashboard" }, "", "");
        }
    }
    
    // 3. Setup global polling
    metricsInterval = setInterval(fetchMetrics, 5000);
    botRefreshInterval = setInterval(() => fetchBots(false), 3000);

    // 4. Initialize Ace Editor
    initAceEditor();

    // 5. Setup Event Listeners
    document.getElementById("btn-add-bot")?.addEventListener("click", openAddBotModal);
    document.getElementById("add-bot-form")?.addEventListener("submit", handleAddBot);
    document.getElementById("env-add-row")?.addEventListener("click", addEnvRow);
    document.getElementById("btn-save-env")?.addEventListener("click", saveEnvVariables);
    document.getElementById("form-install-package")?.addEventListener("submit", handleInstallPackage);
    document.getElementById("btn-install-reqs")?.addEventListener("click", handleInstallRequirements);
    document.getElementById("btn-save-reqs")?.addEventListener("click", () => saveRequirements(true));
    
    // Admin listeners
    document.getElementById("btn-save-settings")?.addEventListener("click", saveAdminSettings);
    document.getElementById("form-change-password")?.addEventListener("submit", handleAdminChangePassword);

    // Setup Hacker-style 3D Type Toggles in Modal
    setupTypeToggles();

    // Setup 3D Scroll Reveal IntersectionObserver
    applyScrollReveal();
}

// Fetch Current User
async function fetchCurrentUser() {
    try {
        const res = await fetch("/api/me");
        if (res.status === 401) {
            window.location.href = "/login";
            return;
        }
        const data = await res.json();
        currentUser = data;
        
        document.getElementById("header-user-badge").textContent = `Operator: ${data.username} [${data.role.toUpperCase()}]`;
        updateBotLimitBadge();
        
        // If admin, show navigation tabs and load admin panel
        if (data.role === "admin") {
            document.getElementById("main-nav-tabs").style.display = "flex";
            const ownerGroup = document.getElementById("modal-owner-group");
            if (ownerGroup) ownerGroup.style.display = "block";
            fetchAdminData();
        }
    } catch (err) {
        console.error("Error fetching me details:", err);
    }
}

// Switch Main Dashboard Tabs (APPLICATIONS / ADMIN PANEL)
function switchMainTab(element, tabId) {
    document.querySelectorAll(".main-tab-content").forEach(el => {
        el.style.display = "none";
    });
    document.querySelectorAll("#main-nav-tabs .tab").forEach(tab => {
        tab.classList.remove("active");
    });

    document.getElementById(`tab-content-${tabId}`).style.display = "block";
    
    if (element) {
        element.classList.add("active");
    } else {
        const tabEl = document.querySelector(`#main-nav-tabs .tab[onclick*="${tabId}"]`);
        if (tabEl) tabEl.classList.add("active");
    }

    if (tabId === "admin-panel-tab") {
        fetchAdminData();
    } else {
        fetchBots(true);
    }
}

// Setup Hacker-style Source Type 3D Toggle Picker
function setupTypeToggles() {
    const toggles = document.querySelectorAll("#modal-type-toggles .btn-toggle");
    const hiddenInput = document.getElementById("source_type");
    
    toggles.forEach(toggle => {
        toggle.addEventListener("click", () => {
            toggles.forEach(t => t.classList.remove("active"));
            toggle.classList.add("active");
            
            const type = toggle.getAttribute("data-type");
            hiddenInput.value = type;
            toggleSourceTypeFields(type);
        });
    });
}

function toggleSourceTypeFields(type) {
    const fileGroup = document.getElementById("modal-file-group");
    const gitGroup = document.getElementById("modal-git-group");
    const pasteGroup = document.getElementById("modal-paste-group");

    fileGroup.style.display = "none";
    gitGroup.style.display = "none";
    pasteGroup.style.display = "none";

    document.getElementById("zip_file").required = false;
    document.getElementById("git_url").required = false;
    document.getElementById("paste_code").required = false;

    if (type === "zip") {
        fileGroup.style.display = "block";
        document.getElementById("zip_file").required = true;
    } else if (type === "git") {
        gitGroup.style.display = "block";
        document.getElementById("git_url").required = true;
    } else if (type === "paste") {
        pasteGroup.style.display = "block";
        document.getElementById("paste_code").required = true;
    }
}

// Fetch Stats & System Metrics
async function fetchMetrics() {
    try {
        const res = await fetch("/api/metrics");
        if (res.ok) {
            const data = await res.json();
            document.getElementById("metric-total-bots").textContent = data.total_bots;
            document.getElementById("metric-running-bots").textContent = data.running_bots;
            document.getElementById("metric-cpu").textContent = `${data.cpu_usage.toFixed(1)}%`;
            document.getElementById("metric-ram").textContent = `${data.ram_usage.toFixed(1)}%`;
        }
    } catch (err) {
        console.error("Error fetching metrics:", err);
    }
}

// Fetch Bots List
async function fetchBots(initial = false) {
    try {
        const res = await fetch("/api/bots");
        if (res.status === 401) {
            window.location.href = "/login";
            return;
        }
        const data = await res.json();
        
        const botsChanged = JSON.stringify(bots) !== JSON.stringify(data);
        bots = data;
        updateBotLimitBadge();

        if (botsChanged || initial) {
            renderBotsGrid();
            renderSidebarList();
            
            if (selectedBotId) {
                const currentBot = bots.find(b => b.id === selectedBotId);
                if (currentBot) {
                    updateWorkspaceBotDetails(currentBot);
                }
            }
        }
    } catch (err) {
        console.error("Error fetching bots:", err);
    }
}

// Render Grid of Bots on Main Dashboard View
function renderBotsGrid() {
    const grid = document.getElementById("bots-grid");
    if (!grid) return;

    if (bots.length === 0) {
        grid.innerHTML = `
            <div class="empty-state" style="grid-column: 1 / -1;">
                <svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="m12 3-1.912 5.886A2 2 0 0 1 8.18 10.18L2.293 12.09a2 2 0 0 0 0 3.82l5.886 1.912a2 2 0 0 1 1.912 1.912l1.912 5.886a2 2 0 0 0 3.82 0l1.912-5.886a2 2 0 0 1 1.912-1.912l5.886-1.912a2 2 0 0 0 0-3.82l-5.886-1.912a2 2 0 0 1-1.912-1.912L13.91 3a2 2 0 0 0-3.82 0Z"/></svg>
                <h3>No bots hosted yet</h3>
                <p>Upload a Python project ZIP file, clone a Git repository, or paste your scripts directly.</p>
                <button class="btn btn-primary" onclick="openAddBotModal()">
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                    Host New Bot
                </button>
            </div>
        `;
        return;
    }

    grid.innerHTML = bots.map(bot => `
        <div class="bot-card scroll-reveal" onclick="viewBotWorkspace('${bot.id}')">
            <div class="bot-card-header">
                <div>
                    <h4 class="bot-title">${escapeHTML(bot.name)}</h4>
                    <div class="bot-source">
                        ${bot.source_type === 'git' ? `
                            <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"/></svg>
                            Git Repository
                        ` : bot.source_type === 'zip' ? `
                            <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" x2="12" y1="3" y2="15"/></svg>
                            ZIP Archive
                        ` : `
                            <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                            Pasted Script
                        `}
                        &bull; <span class="badge-owner" style="font-size: 0.65rem; border: none; padding: 0;">${escapeHTML(bot.owner)}</span>
                    </div>
                </div>
                <span class="bot-status-badge status-${bot.status}">${bot.status}</span>
            </div>
            
            <div class="bot-card-body">
                <div class="bot-meta-item">
                    Entrypoint: <span>${escapeHTML(bot.entrypoint)}</span>
                </div>
                <div class="bot-meta-item">
                    Auto Restart: <span>${bot.auto_restart ? 'YES' : 'NO'}</span>
                </div>
                <div class="bot-meta-item">
                    Environment Vars: <span>${Object.keys(bot.env_vars).length} vars</span>
                </div>
                ${bot.bot_telegram_name ? `
                    <div class="bot-meta-item" style="margin-top: 0.5rem; padding: 0.4rem; background: rgba(0, 255, 102, 0.05); border: 1px dashed rgba(0, 255, 102, 0.2); border-radius: 4px; display: flex; flex-direction: column; gap: 0.15rem;">
                        <div style="font-size: 0.72rem; color: var(--text-main); font-weight: bold; display: flex; align-items: center; gap: 0.25rem;">
                            <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>
                            Telegram Details
                        </div>
                        <div style="font-size: 0.68rem; color: var(--text-muted);">Name: <span style="color: var(--accent);">${escapeHTML(bot.bot_telegram_name)}</span></div>
                        <div style="font-size: 0.68rem; color: var(--text-muted);">Username: <a href="https://t.me/${escapeHTML(bot.bot_username)}" target="_blank" style="color: var(--primary-light); text-decoration: underline;">@${escapeHTML(bot.bot_username)}</a></div>
                    </div>
                ` : ''}
            </div>

            <div class="bot-card-actions" onclick="event.stopPropagation()">
                ${bot.status === 'running' ? `
                    <button class="btn btn-secondary btn-icon" title="Stop Bot" onclick="controlBot('${bot.id}', 'stop')">
                        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2" ry="2"/></svg>
                    </button>
                ` : `
                    <button class="btn btn-primary btn-icon" title="Start Bot" onclick="controlBot('${bot.id}', 'start')" ${bot.status === 'installing' ? 'disabled' : ''}>
                        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                    </button>
                `}
                <button class="btn btn-secondary btn-icon" title="Restart Bot" onclick="controlBot('${bot.id}', 'restart')" ${bot.status === 'installing' ? 'disabled' : ''}>
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
                </button>
                <button class="btn btn-secondary" onclick="viewBotWorkspace('${bot.id}')">
                    Manage
                </button>
            </div>
        </div>
    `).join("");
    applyScrollReveal();
}

// Render Sidebar List inside Workspace View
function renderSidebarList() {
    const list = document.getElementById("sidebar-bots-list");
    if (!list) return;

    list.innerHTML = bots.map(bot => `
        <div class="sidebar-item ${bot.id === selectedBotId ? 'active' : ''}" onclick="selectBot('${bot.id}')">
            <div style="max-width: 70%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                <strong style="font-size: 0.8125rem; font-family: 'Fira Code', monospace;">${escapeHTML(bot.name)}</strong>
            </div>
            <span class="bot-status-badge status-${bot.status}" style="font-size: 0.6rem; padding: 0.15rem 0.4rem;">${bot.status}</span>
        </div>
    `).join("");
}

function viewBotWorkspace(botId) {
    document.getElementById("dashboard-view").style.display = "none";
    document.getElementById("workspace-view").style.display = "block";
    selectBot(botId);
}

function showDashboardView() {
    if (history.state && history.state.view === "workspace") {
        history.back();
    } else {
        showDashboardViewInternal();
    }
}

function showDashboardViewInternal() {
    closeLogSocket();
    stopMetricsPolling();
    selectedBotId = null;
    document.getElementById("workspace-view").style.display = "none";
    document.getElementById("dashboard-view").style.display = "block";
    fetchBots(true);
}

// Select Bot inside Workspace
function selectBot(botId, preventStateUpdate = false) {
    selectedBotId = botId;
    renderSidebarList();

    const bot = bots.find(b => b.id === botId);
    if (!bot) return;

    // Reset tabs
    switchTab('console');

    // Populate Details
    updateWorkspaceBotDetails(bot);

    // Initial load logs and open WebSocket stream
    loadLogs(botId);
    setupLogWebSocket(botId);

    // Setup Env variables
    loadEnvVariables(bot.env_vars);

    // Load requirements.txt
    loadRequirements(botId);
    
    // Load Web IDE files
    loadFilesIDE(botId);

    // Setup performance metrics charts
    setupMetricsCharts(botId);

    // Handle history state updates
    if (!preventStateUpdate) {
        if (history.state && history.state.view === "workspace") {
            if (history.state.botId !== botId) {
                history.replaceState({ view: "workspace", botId: botId }, "", "");
            }
        } else {
            history.pushState({ view: "workspace", botId: botId }, "", "");
        }
    }
}

function updateWorkspaceBotDetails(bot) {
    document.getElementById("ws-bot-name").textContent = bot.name;
    document.getElementById("ws-bot-owner-badge").textContent = `Owner: ${bot.owner}`;
    
    const tgBadge = document.getElementById("ws-bot-telegram-badge");
    if (tgBadge) {
        if (bot.bot_telegram_name && bot.bot_username) {
            tgBadge.innerHTML = `🤖 ${escapeHTML(bot.bot_telegram_name)} (<a href="https://t.me/${escapeHTML(bot.bot_username)}" target="_blank" style="color: var(--accent); text-decoration: underline;">@${escapeHTML(bot.bot_username)}</a>)`;
            tgBadge.style.display = "inline-block";
        } else {
            tgBadge.style.display = "none";
        }
    }
    
    const badge = document.getElementById("ws-bot-status-badge");
    badge.className = `bot-status-badge status-${bot.status}`;
    badge.textContent = bot.status;

    const startBtn = document.getElementById("ws-btn-start");
    const stopBtn = document.getElementById("ws-btn-stop");
    const restartBtn = document.getElementById("ws-btn-restart");

    if (bot.status === "running") {
        startBtn.style.display = "none";
        stopBtn.style.display = "inline-flex";
    } else {
        startBtn.style.display = "inline-flex";
        stopBtn.style.display = "none";
        
        if (bot.status === "installing") {
            startBtn.disabled = true;
            restartBtn.disabled = true;
        } else {
            startBtn.disabled = false;
            restartBtn.disabled = false;
        }
    }
}

// Switch workspace tabs
function switchTab(tabId) {
    document.querySelectorAll(".tab").forEach(tab => {
        tab.classList.remove("active");
    });
    document.querySelectorAll(".tab-content").forEach(content => {
        content.classList.remove("active");
    });

    document.querySelector(`.tab[onclick="switchTab('${tabId}')"]`).classList.add("active");
    document.getElementById(`tab-${tabId}`).classList.add("active");

    if (tabId === 'ide' && aceEditor) {
        aceEditor.resize();
        switchMobileIDETab('tree');
    }
}

function switchMobileIDETab(tab) {
    const treeBtn = document.getElementById("btn-mobile-show-tree");
    const editorBtn = document.getElementById("btn-mobile-show-editor");
    const treeContainer = document.querySelector(".ide-file-tree-container");
    const editorContainer = document.querySelector(".ide-editor-container");
    
    if (!treeBtn || !editorBtn || !treeContainer || !editorContainer) return;
    
    if (tab === 'tree') {
        treeBtn.classList.add("active");
        editorBtn.classList.remove("active");
        treeContainer.classList.add("mobile-active");
        editorContainer.classList.remove("mobile-active");
    } else {
        treeBtn.classList.remove("active");
        editorBtn.classList.add("active");
        treeContainer.classList.remove("mobile-active");
        editorContainer.classList.add("mobile-active");
        if (aceEditor) {
            aceEditor.resize();
            aceEditor.renderer.updateFull();
        }
    }
}

// Add Bot Modal
function openAddBotModal() {
    document.getElementById("add-bot-modal").classList.add("open");
    
    // Default to ZIP
    const firstToggle = document.querySelector("#modal-type-toggles button[data-type='zip']");
    if (firstToggle) firstToggle.click();
}

function closeAddBotModal() {
    document.getElementById("add-bot-modal").classList.remove("open");
    document.getElementById("add-bot-form").reset();
}

// Add Bot Form
async function handleAddBot(e) {
    e.preventDefault();
    const submitBtn = e.target.querySelector('button[type="submit"]');
    submitBtn.disabled = true;
    submitBtn.textContent = "Creating...";

    const formData = new FormData(e.target);

    const overlay = document.getElementById("creation-loading-overlay");
    const progressBar = document.getElementById("creation-progress-bar");
    const terminalLogs = document.getElementById("creation-terminal-logs");
    
    if (overlay) overlay.style.display = "flex";
    if (progressBar) progressBar.style.width = "0%";
    if (terminalLogs) {
        terminalLogs.innerHTML = `
            <div>&gt; [SYS] Handshaking with Railway container...</div>
            <div>&gt; [SYS] Creating isolated user directory...</div>
        `;
    }

    let progress = 0;
    const progressInterval = setInterval(() => {
        progress += Math.floor(Math.random() * 12) + 6;
        if (progress > 95) progress = 95;
        if (progressBar) progressBar.style.width = `${progress}%`;
        
        if (terminalLogs) {
            if (progress > 20 && terminalLogs.children.length === 2) {
                terminalLogs.innerHTML += `<div>&gt; [SYS] Checking virtual environment availability...</div>`;
                terminalLogs.scrollTop = terminalLogs.scrollHeight;
            } else if (progress > 45 && terminalLogs.children.length === 3) {
                terminalLogs.innerHTML += `<div>&gt; [SYS] Sandboxing directory and setting unprivileged UID...</div>`;
                terminalLogs.scrollTop = terminalLogs.scrollHeight;
            } else if (progress > 70 && terminalLogs.children.length === 4) {
                terminalLogs.innerHTML += `<div>&gt; [SYS] Verifying Bot Token credentials with Telegram API...</div>`;
                terminalLogs.scrollTop = terminalLogs.scrollHeight;
            }
        }
    }, 450);

    try {
        const res = await fetch("/api/bots", {
            method: "POST",
            body: formData
        });

        if (res.ok) {
            clearInterval(progressInterval);
            if (progressBar) progressBar.style.width = "100%";
            if (terminalLogs) {
                terminalLogs.innerHTML += `<div style="color: var(--accent);">&gt; [SUCCESS] Bot created successfully. Handover to runtime manager.</div>`;
                terminalLogs.scrollTop = terminalLogs.scrollHeight;
            }
            setTimeout(() => {
                closeAddBotModal();
                if (overlay) overlay.style.display = "none";
                fetchBots(true);
            }, 600);
        } else {
            clearInterval(progressInterval);
            if (overlay) overlay.style.display = "none";
            const err = await res.json();
            alert(`Error: ${err.detail || "Failed to add bot"}`);
        }
    } catch (err) {
        clearInterval(progressInterval);
        if (overlay) overlay.style.display = "none";
        console.error(err);
        alert("Failed to connect to server.");
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = "Host Bot";
    }
}

// Control Bot processes
async function controlBot(botId, action) {
    try {
        const res = await fetch(`/api/bots/${botId}/${action}`, {
            method: "POST"
        });
        if (res.ok) {
            fetchBots(false);
        } else {
            const err = await res.json();
            alert(`Failed: ${err.detail}`);
        }
    } catch (err) {
        console.error(err);
        alert("Failed to connect to server.");
    }
}

function handleWSControlBot(action) {
    if (!selectedBotId) return;
    controlBot(selectedBotId, action);
}

// Delete Bot
async function deleteSelectedBot() {
    if (!selectedBotId) return;
    const bot = bots.find(b => b.id === selectedBotId);
    if (!bot) return;

    if (!confirm(`Are you absolutely sure you want to delete '${bot.name}'? This will delete all code, packages, and logs.`)) {
        return;
    }

    try {
        const res = await fetch(`/api/bots/${selectedBotId}`, {
            method: "DELETE"
        });

        if (res.ok) {
            showDashboardView();
        } else {
            const err = await res.json();
            alert(`Error: ${err.detail || "Failed to delete bot"}`);
        }
    } catch (err) {
        console.error(err);
    }
}

// Load logs initially
async function loadLogs(botId) {
    const terminal = document.getElementById("terminal-console");
    if (!terminal) return;

    terminal.textContent = "Loading console logs...";
    try {
        const res = await fetch(`/api/bots/${botId}/logs`);
        if (res.ok) {
            const logs = await res.text();
            terminal.textContent = logs || "No logs yet.";
            terminal.scrollTop = terminal.scrollHeight;
        }
    } catch (err) {
        terminal.textContent = "Error reading logs from server.";
    }
}

// Set up WebSocket for live logs
function setupLogWebSocket(botId) {
    closeLogSocket();

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    const wsUrl = `${protocol}//${host}/api/bots/${botId}/logs/ws`;

    const terminal = document.getElementById("terminal-console");

    logSocket = new WebSocket(wsUrl);

    logSocket.onmessage = (event) => {
        terminal.textContent += event.data;
        terminal.scrollTop = terminal.scrollHeight;
    };

    logSocket.onclose = () => {
        console.log("WebSocket log stream closed.");
    };

    logSocket.onerror = (err) => {
        console.error("WebSocket Error:", err);
    };
}

function closeLogSocket() {
    if (logSocket) {
        logSocket.close();
        logSocket = null;
    }
}

function clearConsole() {
    const terminal = document.getElementById("terminal-console");
    if (terminal) terminal.textContent = "";
}

// Env Variables Manager
function loadEnvVariables(envVars) {
    const list = document.getElementById("env-variables-list");
    if (!list) return;

    list.innerHTML = "";
    Object.entries(envVars).forEach(([key, val]) => {
        addEnvRowWithValue(key, val);
    });

    if (Object.keys(envVars).length === 0) {
        addEnvRow();
    }
}

function addEnvRow() {
    addEnvRowWithValue("", "");
}

function addEnvRowWithValue(key, value) {
    const list = document.getElementById("env-variables-list");
    const row = document.createElement("div");
    row.className = "env-row";
    row.innerHTML = `
        <input type="text" class="form-control env-key text-green" placeholder="Variable Name" value="${escapeHTML(key)}" required style="font-family: 'Fira Code', monospace;">
        <input type="text" class="form-control env-value text-green" placeholder="Value" value="${escapeHTML(value)}" required style="font-family: 'Fira Code', monospace;">
        <button type="button" class="btn btn-danger btn-icon" onclick="this.parentElement.remove()" style="padding: 0.5rem;">
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
    `;
    list.appendChild(row);
}

async function saveEnvVariables() {
    if (!selectedBotId) return;

    const rows = document.querySelectorAll("#env-variables-list .env-row");
    const env_vars = {};
    let valid = true;

    rows.forEach(row => {
        const key = row.querySelector(".env-key").value.trim();
        const val = row.querySelector(".env-value").value.trim();
        if (key) {
            env_vars[key] = val;
        } else {
            valid = false;
        }
    });

    if (!valid) {
        alert("Environment variable name cannot be empty.");
        return;
    }

    const saveBtn = document.getElementById("btn-save-env");
    saveBtn.disabled = true;
    saveBtn.textContent = "Saving...";

    try {
        const res = await fetch(`/api/bots/${selectedBotId}/env`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(env_vars)
        });

        if (res.ok) {
            const bot = bots.find(b => b.id === selectedBotId);
            if (bot) bot.env_vars = env_vars;
            alert("Environment variables updated successfully!");
        } else {
            const err = await res.json();
            alert(`Error: ${err.detail}`);
        }
    } catch (err) {
        console.error(err);
        alert("Failed to save variables.");
    } finally {
        saveBtn.disabled = false;
        saveBtn.textContent = "Save Variables";
    }
}

// Single Package Pip install
async function handleInstallPackage(e) {
    e.preventDefault();
    if (!selectedBotId) return;

    const input = document.getElementById("package_name");
    const name = input.value.trim();
    if (!name) return;

    const btn = e.target.querySelector("button");
    btn.disabled = true;
    btn.textContent = "Installing...";

    try {
        const res = await fetch(`/api/bots/${selectedBotId}/install`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ package_name: name })
        });

        if (res.ok) {
            input.value = "";
            switchTab('console');
            alert(`Installing '${name}' in the background. Check console logs for progress!`);
            fetchBots(false);
        } else {
            const err = await res.json();
            alert(`Error: ${err.detail}`);
        }
    } catch (err) {
        console.error(err);
        alert("Failed to install package.");
    } finally {
        btn.disabled = false;
        btn.textContent = "Install Package";
    }
}

// requirements.txt saving and installing
async function handleInstallRequirements() {
    if (!selectedBotId) return;

    const btn = document.getElementById("btn-install-reqs");
    btn.disabled = true;

    // First save
    const saved = await saveRequirements(false);
    if (!saved) {
        btn.disabled = false;
        return;
    }

    try {
        const res = await fetch(`/api/bots/${selectedBotId}/install-requirements`, {
            method: "POST"
        });

        if (res.ok) {
            switchTab('console');
            alert("Installing requirements.txt in the background. Check console logs for progress!");
            fetchBots(false);
        } else {
            const err = await res.json();
            alert(`Error: ${err.detail}`);
        }
    } catch (err) {
        console.error(err);
        alert("Failed to start requirements installation.");
    } finally {
        btn.disabled = false;
    }
}

async function loadRequirements(botId) {
    const area = document.getElementById("requirements_content");
    if (!area) return;
    area.value = "Loading requirements.txt...";
    try {
        const res = await fetch(`/api/bots/${botId}/requirements`);
        if (res.ok) {
            const data = await res.json();
            area.value = data.requirements;
        } else {
            area.value = "Error loading requirements.txt";
        }
    } catch (err) {
        console.error(err);
        area.value = "Error loading requirements.txt";
    }
}

async function saveRequirements(showNotification = true) {
    if (!selectedBotId) return false;
    const content = document.getElementById("requirements_content").value;
    try {
        const res = await fetch(`/api/bots/${selectedBotId}/requirements`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ requirements: content })
        });
        if (res.ok) {
            if (showNotification) alert("requirements.txt saved successfully!");
            return true;
        } else {
            alert("Failed to save requirements.txt");
            return false;
        }
    } catch (err) {
        console.error(err);
        alert("Error saving requirements.");
        return false;
    }
}

// --- Web IDE Code Editor (Ace Editor CDN Integration) ---

function initAceEditor() {
    const container = document.getElementById("ide-editor");
    if (!container) return;
    
    aceEditor = ace.edit("ide-editor");
    aceEditor.setTheme("ace/theme/tomorrow_night_eighties"); // Cyberpunk-like dark theme
    aceEditor.session.setMode("ace/mode/python");
    aceEditor.setReadOnly(true);
    
    // Bind Ctrl+S shortcut to save active file
    aceEditor.commands.addCommand({
        name: 'saveFile',
        bindKey: {win: 'Ctrl-S', mac: 'Command-S'},
        exec: function(editor) {
            saveActiveFileIDE();
        }
    });
}

async function loadFilesIDE(botId) {
    const tree = document.getElementById("ide-tree-root");
    if (!tree) return;
    
    tree.innerHTML = "Scanning directories...";
    
    try {
        const res = await fetch(`/api/bots/${botId}/files`);
        if (res.ok) {
            const files = await res.json();
            renderFileTree(files);
        } else {
            tree.innerHTML = "Error listing sandbox files.";
        }
    } catch (err) {
        tree.innerHTML = "Connection error listing files.";
    }
}

function renderFileTree(files) {
    const tree = document.getElementById("ide-tree-root");
    if (!tree) return;
    
    if (files.length === 0) {
        tree.innerHTML = "<div style='color: var(--text-dark);'>No files. Add a new file to start.</div>";
        return;
    }
    
    // Sort files to put directories first, then alphabetically
    files.sort((a, b) => {
        if (a.is_dir && !b.is_dir) return -1;
        if (!a.is_dir && b.is_dir) return 1;
        return a.path.localeCompare(b.path);
    });

    tree.innerHTML = files.map(file => {
        const padding = (file.path.split("/").length - 1) * 12;
        const icon = file.is_dir ? 
            `<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="tree-item-dir"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>` :
            `<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="tree-item-file"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`;
        
        return `
            <div class="tree-item ${file.path === activeFilePath ? 'active' : ''}" 
                 style="padding-left: ${padding + 8}px;" 
                 onclick="handleTreeItemClick('${file.path}', ${file.is_dir})">
                ${icon}
                <span title="${escapeHTML(file.path)}">${escapeHTML(file.name)}</span>
                ${!file.is_dir ? `<button onclick="deleteFileIDEPrompt(event, '${file.path}')" style="background:none; border:none; color:var(--danger); cursor:pointer; margin-left:auto; font-size:0.7rem; display:none;" class="ide-delete-btn">[x]</button>` : ''}
            </div>
        `;
    }).join("");

    // Show delete buttons on hover in IDE sidebar
    document.querySelectorAll(".tree-item").forEach(item => {
        item.addEventListener("mouseenter", () => {
            const del = item.querySelector(".ide-delete-btn");
            if (del) del.style.display = "inline";
        });
        item.addEventListener("mouseleave", () => {
            const del = item.querySelector(".ide-delete-btn");
            if (del) del.style.display = "none";
        });
    });
}

function handleTreeItemClick(path, isDir) {
    if (isDir) {
        // Toggle dir? For a flat list we just do nothing on dir click
        return;
    }
    openFileIDE(path);
}

async function openFileIDE(path) {
    if (!selectedBotId) return;
    
    activeFilePath = path;
    renderSidebarList(); // Update tree active state
    
    const header = document.getElementById("ide-active-file-header");
    header.textContent = `READ_ONLY_ACCESS: Loading ${path}...`;
    
    try {
        const res = await fetch(`/api/bots/${selectedBotId}/files/read?path=${encodeURIComponent(path)}`);
        if (res.ok) {
            const data = await res.json();
            
            // Set text in editor
            aceEditor.setValue(data.content, -1);
            aceEditor.setReadOnly(false);
            
            // Set Mode
            const ext = path.split('.').pop().toLowerCase();
            let mode = "ace/mode/text";
            if (ext === "py") mode = "ace/mode/python";
            else if (ext === "json") mode = "ace/mode/json";
            else if (ext === "txt") mode = "ace/mode/text";
            aceEditor.session.setMode(mode);
            
            header.innerHTML = `EDIT_MODE: <span style="color: var(--accent); font-weight:bold;">${path}</span>`;
            document.getElementById("ide-save-btn").disabled = false;
            
            // Automatically switch to editor tab on mobile so user can write code
            switchMobileIDETab('editor');
            
            // Re-render tree to set active styling
            fetchBots(false).then(() => {
                const files = bots.find(b => b.id === selectedBotId);
                loadFilesIDE(selectedBotId);
            });
        } else {
            header.textContent = `Error reading file ${path}`;
        }
    } catch (err) {
        header.textContent = `Connection error loading file.`;
    }
}

async function saveActiveFileIDE() {
    if (!selectedBotId || !activeFilePath) return;
    
    const content = aceEditor.getValue();
    const btn = document.getElementById("ide-save-btn");
    btn.disabled = true;
    btn.textContent = "Saving...";
    
    try {
        const res = await fetch(`/api/bots/${selectedBotId}/files/write`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                path: activeFilePath,
                content: content
            })
        });
        
        if (res.ok) {
            alert(`File '${activeFilePath}' saved successfully!`);
        } else {
            alert("Failed to save file.");
        }
    } catch (err) {
        console.error(err);
        alert("Error connecting to server to save file.");
    } finally {
        btn.disabled = false;
        btn.textContent = "Save File [Ctrl+S]";
    }
}

async function createNewFilePrompt() {
    if (!selectedBotId) return;
    
    const path = prompt("Enter new File or Folder path (e.g. utils/helper.py or config/):");
    if (!path) return;
    
    const isDir = path.endsWith("/");
    
    try {
        const res = await fetch(`/api/bots/${selectedBotId}/files/create`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                path: path,
                is_dir: isDir
            })
        });
        
        if (res.ok) {
            alert(`Successfully created ${isDir ? 'Folder' : 'File'} '${path}'`);
            loadFilesIDE(selectedBotId);
        } else {
            const err = await res.json();
            alert(`Error: ${err.detail}`);
        }
    } catch (err) {
        console.error(err);
    }
}

async function deleteFileIDEPrompt(event, path) {
    event.stopPropagation();
    if (!selectedBotId) return;
    
    if (!confirm(`Are you sure you want to delete '${path}'?`)) {
        return;
    }
    
    try {
        const res = await fetch(`/api/bots/${selectedBotId}/files/delete?path=${encodeURIComponent(path)}`, {
            method: "DELETE"
        });
        
        if (res.ok) {
            alert(`Deleted file '${path}' successfully.`);
            if (activeFilePath === path) {
                activeFilePath = null;
                aceEditor.setValue("", -1);
                aceEditor.setReadOnly(true);
                document.getElementById("ide-save-btn").disabled = true;
                document.getElementById("ide-active-file-header").textContent = "No file selected.";
            }
            loadFilesIDE(selectedBotId);
        } else {
            const err = await res.json();
            alert(`Failed: ${err.detail}`);
        }
    } catch (err) {
        console.error(err);
    }
}

// --- Live Performance Metrics (Chart.js Integration) ---

function setupMetricsCharts(botId) {
    stopMetricsPolling();
    
    // Clear data history
    cpuDataHistory = Array(15).fill(0);
    ramDataHistory = Array(15).fill(0);
    chartLabels = Array(15).fill("");
    
    // Initialize Chart instances
    initMetricsCharts();
    
    // Start Polling loop
    pollBotMetrics(botId);
    metricsPollInterval = setInterval(() => pollBotMetrics(botId), 2000);
}

function initMetricsCharts() {
    const ctxCpu = document.getElementById("chart-cpu")?.getContext("2d");
    const ctxRam = document.getElementById("chart-ram")?.getContext("2d");
    
    if (!ctxCpu || !ctxRam) return;
    
    // Destroy existing chart if initialized
    if (cpuChart) cpuChart.destroy();
    if (ramChart) ramChart.destroy();
    
    const chartOptions = {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
            y: {
                beginAtZero: true,
                grid: { color: "rgba(26, 35, 61, 0.5)" },
                ticks: { color: "#9ca3af" }
            },
            x: {
                grid: { display: false },
                ticks: { display: false }
            }
        },
        plugins: {
            legend: { display: false }
        },
        animation: { duration: 0 } // No animation for real-time responsiveness
    };
    
    cpuChart = new Chart(ctxCpu, {
        type: 'line',
        data: {
            labels: chartLabels,
            datasets: [{
                data: cpuDataHistory,
                borderColor: '#00ff66',
                borderWidth: 2,
                tension: 0.2,
                fill: false
            }]
        },
        options: chartOptions
    });
    
    ramChart = new Chart(ctxRam, {
        type: 'line',
        data: {
            labels: chartLabels,
            datasets: [{
                data: ramDataHistory,
                borderColor: '#00d4ff',
                borderWidth: 2,
                tension: 0.2,
                fill: false
            }]
        },
        options: chartOptions
    });
}

async function pollBotMetrics(botId) {
    try {
        const res = await fetch(`/api/bots/${botId}/metrics`);
        if (res.ok) {
            const metrics = await res.json();
            
            // Push values
            cpuDataHistory.push(metrics.cpu);
            cpuDataHistory.shift();
            
            ramDataHistory.push(metrics.ram);
            ramDataHistory.shift();
            
            chartLabels.push(new Date().toLocaleTimeString().split(" ")[0]);
            chartLabels.shift();
            
            // Update charts
            if (cpuChart) {
                cpuChart.data.datasets[0].data = cpuDataHistory;
                cpuChart.update();
            }
            if (ramChart) {
                ramChart.data.datasets[0].data = ramDataHistory;
                ramChart.update();
            }
        }
    } catch (err) {
        console.error("Error polling bot metrics:", err);
    }
}

function stopMetricsPolling() {
    if (metricsPollInterval) {
        clearInterval(metricsPollInterval);
        metricsPollInterval = null;
    }
}

// --- Admin Controls Panel ---

async function fetchAdminData() {
    if (currentUser.role !== "admin") return;
    
    try {
        // 1. Fetch Users
        const resUsers = await fetch("/api/admin/users");
        if (resUsers.ok) {
            const users = await resUsers.json();
            renderAdminUsers(users);
            populateOwnerDropdown(users);
        }
        
        // 2. Fetch Settings
        const resSettings = await fetch("/api/admin/settings");
        if (resSettings.ok) {
            const settings = await resSettings.json();
            document.getElementById("admin-auto-approve-toggle").checked = settings.auto_approve;
        }
    } catch (err) {
        console.error("Error fetching admin data:", err);
    }
}

function renderAdminUsers(users) {
    const tbody = document.getElementById("admin-users-table-body");
    if (!tbody) return;
    
    tbody.innerHTML = users.map(u => {
        let statusClass = "status-stopped"; // pending (grey)
        if (u.status === "approved") statusClass = "status-running"; // green
        if (u.status === "banned") statusClass = "status-error"; // red
        
        const isSelf = u.username === currentUser.username;
        const regDate = (u.registered_at && u.registered_at !== "unknown") ? new Date(u.registered_at).toLocaleString() : "Unknown";
        
        return `
            <tr>
                <td>
                    <a href="javascript:void(0)" onclick="openUserManageModal('${escapeHTML(u.username)}')" style="color: var(--primary-light); text-decoration: underline; font-weight: bold; display: inline-block; transition: color 0.2s;" onmouseover="this.style.color='var(--accent)'" onmouseout="this.style.color='var(--primary-light)'">
                        ${escapeHTML(u.username)}
                    </a> 
                    ${isSelf ? '<span style="font-size:0.65rem; color:var(--accent); font-family:\'Fira Code\', monospace;">[YOU]</span>' : ''}
                </td>
                <td><span class="badge-owner" style="font-size:0.65rem;">${u.role.toUpperCase()}</span></td>
                <td><span class="bot-status-badge ${statusClass}" style="font-size:0.65rem; padding: 0.15rem 0.4rem;">${u.status}</span></td>
                <td><span style="font-family: 'Fira Code', monospace; color: var(--accent);">${escapeHTML(u.ip_address)}</span></td>
                <td style="max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${escapeHTML(u.device_name)}">${escapeHTML(u.device_name)}</td>
                <td style="color: var(--text-muted);">${regDate}</td>
                <td style="cursor: pointer;" onclick="openUserManageModal('${escapeHTML(u.username)}')">
                    <span class="badge-owner" style="font-size:0.65rem; border-color: var(--accent); color: var(--accent); cursor: pointer; transition: background 0.2s;" onmouseover="this.style.background='rgba(0, 255, 102, 0.15)'" onmouseout="this.style.background='transparent'">
                        ${u.bots_count} / ${u.role === 'admin' ? 'Infinite' : u.bot_limit} bots
                    </span>
                </td>
                <td>
                    ${!isSelf ? `
                        <div style="display:flex; gap:0.25rem;">
                            ${u.status === 'pending' ? `
                                <button class="btn btn-accent" style="padding: 0.25rem 0.5rem; font-size: 0.65rem;" onclick="adminApproveUser('${u.username}')">Approve</button>
                            ` : ''}
                            ${u.status === 'approved' ? `
                                <button class="btn btn-danger" style="padding: 0.25rem 0.5rem; font-size: 0.65rem;" onclick="adminBanUser('${u.username}')">Ban</button>
                            ` : ''}
                            ${u.status === 'banned' ? `
                                <button class="btn btn-accent" style="padding: 0.25rem 0.5rem; font-size: 0.65rem;" onclick="adminUnbanUser('${u.username}')">Unban</button>
                            ` : ''}
                            <button class="btn btn-danger" style="padding: 0.25rem 0.5rem; font-size: 0.65rem; background: transparent; border-color: var(--danger);" onclick="adminDeleteUser('${u.username}')">Delete</button>
                        </div>
                    ` : '<span style="color: var(--text-dark);">No Actions</span>'}
                </td>
            </tr>
        `;
    }).join("");
}

async function adminApproveUser(username) {
    if (!confirm(`Approve user '${username}' access to host bots?`)) return;
    try {
        const res = await fetch(`/api/admin/users/${username}/approve`, { method: "POST" });
        if (res.ok) {
            fetchAdminData();
        } else {
            alert("Failed to approve user.");
        }
    } catch (err) {
        console.error(err);
    }
}

async function adminBanUser(username) {
    if (!confirm(`Are you sure you want to BAN user '${username}'? They will be locked out immediately.`)) return;
    try {
        const res = await fetch(`/api/admin/users/${username}/ban`, { method: "POST" });
        if (res.ok) {
            fetchAdminData();
        } else {
            alert("Failed to ban user.");
        }
    } catch (err) {
        console.error(err);
    }
}

async function adminUnbanUser(username) {
    if (!confirm(`Restore access for banned user '${username}'?`)) return;
    try {
        const res = await fetch(`/api/admin/users/${username}/unban`, { method: "POST" });
        if (res.ok) {
            fetchAdminData();
        } else {
            alert("Failed to unban user.");
        }
    } catch (err) {
        console.error(err);
    }
}

async function adminDeleteUser(username) {
    if (!confirm(`CRITICAL WARNING: Are you sure you want to DELETE user '${username}'? This will delete all of their hosted bot files permanently.`)) return;
    try {
        const res = await fetch(`/api/admin/users/${username}`, { method: "DELETE" });
        if (res.ok) {
            fetchAdminData();
        } else {
            alert("Failed to delete user.");
        }
    } catch (err) {
        console.error(err);
    }
}

async function saveAdminSettings() {
    const autoApprove = document.getElementById("admin-auto-approve-toggle").checked;
    const btn = document.getElementById("btn-save-settings");
    btn.disabled = true;
    
    try {
        const res = await fetch("/api/admin/settings", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ auto_approve: autoApprove })
        });
        
        if (res.ok) {
            alert("Settings updated successfully!");
        } else {
            alert("Failed to save settings.");
        }
    } catch (err) {
        console.error(err);
    } finally {
        btn.disabled = false;
    }
}

async function handleAdminChangePassword(e) {
    e.preventDefault();
    const input = document.getElementById("admin_new_password");
    const val = input.value.trim();
    if (!val) return;
    
    const btn = e.target.querySelector("button");
    btn.disabled = true;
    
    try {
        const res = await fetch("/api/admin/change-password", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ new_password: val })
        });
        
        if (res.ok) {
            alert("Password changed successfully!");
            input.value = "";
        } else {
            alert("Failed to change password.");
        }
    } catch (err) {
        console.error(err);
    } finally {
        btn.disabled = false;
    }
}

// Utility functions
function escapeHTML(str) {
    if (!str) return "";
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// --- 3D Scroll Reveal IntersectionObserver ---
function applyScrollReveal() {
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add("revealed");
                observer.unobserve(entry.target);
            }
        });
    }, { threshold: 0.05 });

    document.querySelectorAll(".scroll-reveal").forEach(el => observer.observe(el));
}

// --- Quota / Bot Limit Badges ---
function updateBotLimitBadge() {
    const botCount = bots.length;
    const limit = currentUser.bot_limit || 3;
    const isInfinite = currentUser.role === "admin";
    
    const limitText = isInfinite ? "Infinite" : limit;
    
    const badge = document.getElementById("bot-limit-badge");
    if (badge) {
        badge.textContent = `Bots: ${botCount} / ${limitText}`;
    }
    
    const modalBadge = document.getElementById("modal-bot-limit-badge");
    if (modalBadge) {
        modalBadge.textContent = isInfinite 
            ? `Quota: ${botCount} / Infinite bots used`
            : `Quota: ${botCount} / ${limit} bots used`;
    }
}

// --- Dynamic Owner Dropdown Populator ---
function populateOwnerDropdown(users) {
    const selectEl = document.getElementById("bot_owner");
    if (!selectEl) return;
    
    const approved = users.filter(u => u.status === "approved");
    selectEl.innerHTML = approved.map(u => `
        <option value="${escapeHTML(u.username)}" ${u.username === currentUser.username ? 'selected' : ''}>
            ${escapeHTML(u.username)} (${u.role.toUpperCase()})
        </option>
    `).join("");
}

// --- Manual Logs Refresh Handler ---
async function refreshConsoleLogs() {
    if (!selectedBotId) return;
    const terminal = document.getElementById("terminal-console");
    if (!terminal) return;
    
    const refreshBtn = document.querySelector(".btn-refresh-logs");
    if (refreshBtn) {
        refreshBtn.disabled = true;
        refreshBtn.innerHTML = `
            <svg class="spin" xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
            Refreshing...
        `;
    }
    
    try {
        const res = await fetch(`/api/bots/${selectedBotId}/logs`);
        if (res.ok) {
            const logs = await res.text();
            terminal.textContent = logs || "Console output empty.";
            terminal.scrollTop = terminal.scrollHeight;
        } else {
            console.error("Failed to refresh logs");
        }
    } catch (err) {
        console.error("Error refreshing logs:", err);
    } finally {
        if (refreshBtn) {
            refreshBtn.disabled = false;
            refreshBtn.innerHTML = `
                <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
                Refresh
            `;
        }
    }
}

// --- User Management Modal Logic ---
let selectedManageUser = "";

async function openUserManageModal(username) {
    selectedManageUser = username;
    document.getElementById("manage-user-title").textContent = `Manage Operator: ${username}`;
    
    const limitInput = document.getElementById("manage-user-limit-input");
    
    try {
        const resUsers = await fetch("/api/admin/users");
        if (resUsers.ok) {
            const users = await resUsers.json();
            const u = users.find(x => x.username === username);
            if (u && limitInput) {
                limitInput.value = u.bot_limit;
            }
        }
    } catch (err) {
        console.error("Error fetching user for modal:", err);
    }
    
    await fetchAndRenderUserBots(username);
    
    document.getElementById("user-manage-modal").classList.add("open");
}

function closeUserManageModal() {
    document.getElementById("user-manage-modal").classList.remove("open");
}

async function saveUserLimit() {
    if (!selectedManageUser) return;
    const limitInput = document.getElementById("manage-user-limit-input");
    const limit = parseInt(limitInput.value);
    if (isNaN(limit) || limit < 0) {
        alert("Please enter a valid positive number.");
        return;
    }
    
    try {
        const res = await fetch(`/api/admin/users/${selectedManageUser}/limit`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ bot_limit: limit })
        });
        if (res.ok) {
            alert(`Successfully updated bot limit for ${selectedManageUser} to ${limit}!`);
            fetchAdminData();
        } else {
            const err = await res.json();
            alert(`Error: ${err.detail || "Failed to update limit."}`);
        }
    } catch (err) {
        console.error("Error saving user limit:", err);
        alert("Network error.");
    }
}

async function fetchAndRenderUserBots(username) {
    const listContainer = document.getElementById("manage-user-bots-list");
    if (!listContainer) return;
    
    listContainer.innerHTML = `<div style="color: var(--text-muted); font-size: 0.8125rem;">Loading bot instances...</div>`;
    
    try {
        const res = await fetch(`/api/admin/users/${username}/bots`);
        if (res.ok) {
            const userBots = await res.json();
            if (userBots.length === 0) {
                listContainer.innerHTML = `<div style="color: var(--text-muted); font-size: 0.8125rem;">No bots hosted by this user.</div>`;
                return;
            }
            
            listContainer.innerHTML = userBots.map(bot => {
                const statusClass = bot.status === "running" ? "status-running" : "status-stopped";
                return `
                    <div class="manage-bot-item">
                        <div>
                            <strong style="color: var(--text-main); font-size: 0.875rem;">${escapeHTML(bot.name)}</strong>
                            <span class="bot-status-badge ${statusClass}" style="font-size: 0.65rem; margin-left: 0.5rem; padding: 0.1rem 0.3rem;">${bot.status}</span>
                            <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.25rem;">
                                ID: ${escapeHTML(bot.id)} &bull; Entrypoint: ${escapeHTML(bot.entrypoint)}
                            </div>
                        </div>
                        <div style="display: flex; gap: 0.5rem; align-items: center;">
                            ${bot.status === 'running' ? `
                                <button class="btn btn-danger" style="padding: 0.25rem 0.5rem; font-size: 0.65rem;" onclick="controlUserBot('${bot.id}', 'stop')">Stop</button>
                            ` : `
                                <button class="btn btn-accent" style="padding: 0.25rem 0.5rem; font-size: 0.65rem;" onclick="controlUserBot('${bot.id}', 'start')">Start</button>
                            `}
                            <button class="btn btn-secondary" style="padding: 0.25rem 0.5rem; font-size: 0.65rem;" onclick="controlUserBot('${bot.id}', 'restart')">Restart</button>
                            <button class="btn btn-primary" style="padding: 0.25rem 0.5rem; font-size: 0.65rem;" onclick="manageUserBotWorkspace('${bot.id}')">Manage</button>
                        </div>
                    </div>
                `;
            }).join("");
        } else {
            listContainer.innerHTML = `<div style="color: var(--danger); font-size: 0.8125rem;">Failed to retrieve user bots.</div>`;
        }
    } catch (err) {
        console.error("Error fetching user bots:", err);
        listContainer.innerHTML = `<div style="color: var(--danger); font-size: 0.8125rem;">Network error.</div>`;
    }
}

async function controlUserBot(botId, action) {
    try {
        const res = await fetch(`/api/bots/${botId}/${action}`, { method: "POST" });
        if (res.ok) {
            if (selectedManageUser) {
                await fetchAndRenderUserBots(selectedManageUser);
            }
            fetchBots(false);
        } else {
            const err = await res.json();
            alert(`Error: ${err.detail || "Failed to control bot."}`);
        }
    } catch (err) {
        console.error("Error controlling user bot:", err);
    }
}

function manageUserBotWorkspace(botId) {
    closeUserManageModal();
    viewBotWorkspace(botId);
}
