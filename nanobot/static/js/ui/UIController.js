/**
 * UIController - manages header buttons, settings modal, log panel, status dot,
 * login modal, and user dropdown menu.
 */
import AppState from '../core/AppState.js';
import eventBus from '../core/EventBus.js';

class UIController {
    constructor() {
        this.statusDot = null;
        this.statusText = null;
        this.logPanel = null;
        this.settingsModal = null;
        this.textInput = null;
        this.btnVoice = null;
        this.btnSend = null;
        this.btnConnect = null;
        this.btnImage = null;
        this.fileInput = null;
        this.imagePreviewBar = null;
        this.previewImg = null;
        this.previewInfo = null;
        this.thinkingToggleBtn = null;
        this.chatReopenFab = null;

        // Auth UI
        this.loginModal = null;
        this.loginFormView = null;
        this.registerFormView = null;
        this.loginTitle = null;
        this.loginError = null;
        this.registerError = null;
        this.registerSuccess = null;
        this.btnLogin = null;
        this.btnRegister = null;
        this.headerUser = null;
        this.userMenuBtn = null;
        this.userMenuName = null;
        this.userDropdown = null;
        this.dropdownUserName = null;
        this.dropdownUserRole = null;
    }

    init() {
        this.statusDot = document.getElementById('statusDot');
        this.statusText = document.getElementById('statusText');
        this.logPanel = document.getElementById('logPanel');
        this.settingsModal = document.getElementById('settingsModal');
        this.textInput = document.getElementById('textInput');
        this.btnVoice = document.getElementById('btnVoice');
        this.btnSend = document.getElementById('btnSend');
        this.btnConnect = document.getElementById('btnConnect');
        this.btnImage = document.getElementById('btnImage');
        this.fileInput = document.getElementById('fileInput');
        this.imagePreviewBar = document.getElementById('imagePreviewBar');
        this.previewImg = document.getElementById('previewImg');
        this.previewInfo = document.getElementById('previewInfo');
        this.thinkingToggleBtn = document.getElementById('thinkingToggleBtn');
        this.chatReopenFab = document.getElementById('chatReopenFab');
        // Set initial disconnected state on FAB
        if (this.chatReopenFab) this.chatReopenFab.dataset.status = 'disconnected';

        // Auth UI refs
        this.loginModal = document.getElementById('loginModal');
        this.loginFormView = document.getElementById('loginFormView');
        this.registerFormView = document.getElementById('registerFormView');
        this.loginTitle = document.getElementById('loginTitle');
        this.loginError = document.getElementById('loginError');
        this.registerError = document.getElementById('registerError');
        this.registerSuccess = document.getElementById('registerSuccess');
        this.btnLogin = document.getElementById('btnLogin');
        this.btnRegister = document.getElementById('btnRegister');
        this.headerUser = document.getElementById('headerUser');
        this.userMenuBtn = document.getElementById('userMenuBtn');
        this.userMenuName = document.getElementById('userMenuName');
        this.userDropdown = document.getElementById('userDropdown');
        this.dropdownUserName = document.getElementById('dropdownUserName');
        this.dropdownUserRole = document.getElementById('dropdownUserRole');

        // Set server host from current location
        const serverHostInput = document.getElementById('serverHost');
        if (serverHostInput) {
            serverHostInput.value = window.location.host;
        }

        // Initialize thinking button state
        this._updateThinkingButton();

        // Check media device support
        this._checkMediaDevicesSupport();

        // Listen to log events from all modules
        eventBus.on('log', ({ msg, type }) => this.log(msg, type));

        // Close user dropdown when clicking outside
        document.addEventListener('click', (e) => {
            if (this.userDropdown && !e.target.closest('.header-user')) {
                this.hideUserDropdown();
            }
        });
    }

    // --- Status ---
    updateStatus(status, text) {
        this.statusDot.className = 'status-dot ' + status;
        this.statusText.textContent = text;
        // Sync status to FAB for animated indicator
        if (this.chatReopenFab) {
            this.chatReopenFab.dataset.status = status || 'disconnected';
        }
    }

    // --- Log ---
    log(msg, type = 'info') {
        const entry = document.createElement('div');
        entry.className = 'log-entry ' + type;
        entry.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
        this.logPanel.appendChild(entry);
        this.logPanel.scrollTop = this.logPanel.scrollHeight;
    }

    toggleLog() {
        this.logPanel.classList.toggle('show');
    }

    // --- Settings ---
    showSettings() {
        this.settingsModal.classList.add('show');
    }

    hideSettings() {
        this.settingsModal.classList.remove('show');
    }

    // --- Connection UI ---
    onConnected() {
        this.updateStatus('connected', '已连接');
        this.btnConnect.textContent = '断开';
        this.btnVoice.disabled = false;
        this.btnImage.disabled = false;
        this.hideSettings();
    }

    onDisconnected() {
        this.updateStatus('', '已断开');
        this.btnConnect.textContent = '连接';
        this.btnVoice.disabled = true;
        this.btnSend.disabled = true;
        this.btnImage.disabled = true;
    }

    // --- Login Modal ---
    showLoginModal() {
        if (!this.loginModal) return;
        this._clearLoginForms();
        this.showLoginForm();
        this.loginModal.classList.add('show');
        setTimeout(() => document.getElementById('loginUsername')?.focus(), 100);
    }

    hideLoginModal() {
        if (!this.loginModal) return;
        this.loginModal.classList.remove('show');
    }

    showLoginForm() {
        if (this.loginFormView) this.loginFormView.style.display = '';
        if (this.registerFormView) this.registerFormView.style.display = 'none';
        if (this.loginTitle) this.loginTitle.textContent = '登录';
        this._clearLoginErrors();
    }

    showRegisterForm() {
        if (this.loginFormView) this.loginFormView.style.display = 'none';
        if (this.registerFormView) this.registerFormView.style.display = '';
        if (this.loginTitle) this.loginTitle.textContent = '注册';
        this._clearLoginErrors();
        setTimeout(() => document.getElementById('registerUsername')?.focus(), 100);
    }

    setLoginLoading(isLoading) {
        if (this.btnLogin) {
            this.btnLogin.disabled = isLoading;
            this.btnLogin.textContent = isLoading ? '登录中...' : '登录';
            this.btnLogin.classList.toggle('loading', isLoading);
        }
    }

    setRegisterLoading(isLoading) {
        if (this.btnRegister) {
            this.btnRegister.disabled = isLoading;
            this.btnRegister.textContent = isLoading ? '注册中...' : '注册';
            this.btnRegister.classList.toggle('loading', isLoading);
        }
    }

    showLoginError(message) {
        if (this.loginError) {
            this.loginError.textContent = message;
            this.loginError.classList.add('show');
        }
    }

    showRegisterError(message) {
        if (this.registerError) {
            this.registerError.textContent = message;
            this.registerError.classList.add('show');
        }
        if (this.registerSuccess) this.registerSuccess.classList.remove('show');
    }

    showRegisterSuccess(message) {
        if (this.registerSuccess) {
            this.registerSuccess.textContent = message;
            this.registerSuccess.classList.add('show');
        }
        if (this.registerError) this.registerError.classList.remove('show');
        // Auto-switch to login form after a short delay
        setTimeout(() => this.showLoginForm(), 1500);
    }

    _clearLoginForms() {
        const ids = ['loginUsername', 'loginPassword', 'registerUsername', 'registerPassword', 'registerPasswordConfirm'];
        ids.forEach(id => {
            const el = document.getElementById(id);
            if (el) el.value = '';
        });
        this._clearLoginErrors();
    }

    _clearLoginErrors() {
        if (this.loginError) {
            this.loginError.textContent = '';
            this.loginError.classList.remove('show');
        }
        if (this.registerError) {
            this.registerError.textContent = '';
            this.registerError.classList.remove('show');
        }
        if (this.registerSuccess) {
            this.registerSuccess.textContent = '';
            this.registerSuccess.classList.remove('show');
        }
    }

    // --- User Menu ---
    updateUserMenu(username, role) {
        if (this.headerUser) this.headerUser.style.display = '';
        if (this.userMenuName) this.userMenuName.textContent = username;
        if (this.dropdownUserName) this.dropdownUserName.textContent = username;
        if (this.dropdownUserRole) this.dropdownUserRole.textContent = role || 'user';
    }

    clearUserMenu() {
        if (this.headerUser) this.headerUser.style.display = 'none';
        if (this.userMenuName) this.userMenuName.textContent = '';
    }

    toggleUserDropdown() {
        if (this.userDropdown) this.userDropdown.classList.toggle('show');
    }

    hideUserDropdown() {
        if (this.userDropdown) this.userDropdown.classList.remove('show');
    }

    // --- Thinking toggle ---
    toggleThinkingDisplay() {
        AppState.showThinkingProcess = !AppState.showThinkingProcess;
        localStorage.setItem('showThinkingProcess', AppState.showThinkingProcess);
        this._updateThinkingButton();
        this.log(`思考过程显示: ${AppState.showThinkingProcess ? '开启' : '关闭'}`, 'info');
    }

    _updateThinkingButton() {
        if (!this.thinkingToggleBtn) return;
        if (AppState.showThinkingProcess) {
            this.thinkingToggleBtn.style.opacity = '1';
            this.thinkingToggleBtn.style.background = 'rgba(102, 126, 234, 0.1)';
            this.thinkingToggleBtn.title = '思考过程：开启（点击关闭）';
        } else {
            this.thinkingToggleBtn.style.opacity = '0.5';
            this.thinkingToggleBtn.style.background = 'transparent';
            this.thinkingToggleBtn.title = '思考过程：关闭（点击开启）';
        }
    }

    // --- Textarea auto-resize ---
    autoResize(el) {
        el.style.height = 'auto';
        el.style.height = Math.min(el.scrollHeight, 100) + 'px';
        this.btnSend.disabled = !AppState.isConnected || (!el.value.trim() && !AppState.pendingImage);
    }

    // --- Image preview ---
    showImagePreview(file, dataUrl) {
        AppState.pendingImage = { file, dataUrl };
        this.previewImg.src = dataUrl;
        const sizeMB = (file.size / 1024 / 1024).toFixed(1);
        this.previewInfo.textContent = `${file.name} (${sizeMB}MB)`;
        this.imagePreviewBar.classList.add('show');
        this.btnSend.disabled = false;
    }

    clearImage() {
        AppState.pendingImage = null;
        this.imagePreviewBar.classList.remove('show');
        this.previewImg.src = '';
        this.fileInput.value = '';
        const cameraInput = document.getElementById('cameraInput');
        if (cameraInput) cameraInput.value = '';
        this.btnSend.disabled = !AppState.isConnected || !this.textInput.value.trim();
    }

    // --- Media device check ---
    _checkMediaDevicesSupport() {
        const isLocalhost = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
        const isHttps = window.location.protocol === 'https:';

        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            if (!isLocalhost && !isHttps) {
                this._showMediaWarning('warning',
                    `\u26A0\uFE0F 当前通过 HTTP + IP 访问，无法使用麦克风功能\n建议：在本机访问 http://localhost:${window.location.port}/voice 或配置 HTTPS`
                );
            } else {
                this._showMediaWarning('error', '\u274C 浏览器不支持麦克风访问，建议使用最新版 Chrome/Firefox/Edge');
            }
        } else if (!isLocalhost && !isHttps) {
            this._showMediaWarning('warning',
                `\u26A0\uFE0F 麦克风功能可能受限（HTTP + IP）\n如无法使用，请访问：http://localhost:${window.location.port}/voice`
            );
        }
    }

    _showMediaWarning(type, message) {
        const warningDiv = document.createElement('div');
        warningDiv.style.cssText = `
            position: fixed; top: 80px; left: 50%; transform: translateX(-50%);
            background: ${type === 'error' ? '#ff4444' : '#ff9800'};
            color: white; padding: 12px 24px; border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3); z-index: 10000;
            max-width: 90%; text-align: center; font-size: 14px;
            line-height: 1.6; white-space: pre-line;
        `;
        warningDiv.textContent = message;

        const closeBtn = document.createElement('button');
        closeBtn.textContent = '\u2715';
        closeBtn.style.cssText = 'position:absolute;top:4px;right:8px;background:none;border:none;color:white;font-size:18px;cursor:pointer;padding:0;width:24px;height:24px;';
        closeBtn.addEventListener('click', () => warningDiv.remove());

        warningDiv.appendChild(closeBtn);
        document.body.appendChild(warningDiv);

        this.log(`Media warning: ${message}`, type);
    }
}

export default new UIController();
