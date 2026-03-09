/**
 * app.js - Application entry point.
 * Imports all modules, wires up dependencies, registers event handlers.
 */
import AppState from './core/AppState.js';
import eventBus from './core/EventBus.js';
import wsService from './services/WebSocketService.js';
import apiService from './services/ApiService.js';
import authService from './services/AuthService.js';
import chatManager from './features/ChatManager.js';
import voiceRecorder from './features/VoiceRecorder.js';
import photoHandler from './features/PhotoHandler.js';
import workFormManager from './features/WorkFormManager.js';
import sceneRenderer from './features/NodeRenderer.js';
import uiController from './ui/UIController.js';
import actionDispatcher from './tools/ActionDispatcher.js';
import { registerFrontendTools } from './tools/FrontendToolRegistry.js';
import uiActions from './tools/UiActions.js';
import tabManager from './features/TabManager.js';

// ==================== Initialization ====================

window.addEventListener('DOMContentLoaded', async () => {
    // Restore chat panel collapsed state before first paint
    _restoreChatPanelState();

    // Initialize all modules that need DOM references
    uiController.init();
    chatManager.init();
    voiceRecorder.init();
    photoHandler.init();

    uiController.log('页面已加载');

    // Kick off auth flow: will emit 'auth:ready' or 'auth:required'
    await authService.init();
});

// Cleanup on unload
window.addEventListener('beforeunload', () => {
    wsService.disconnect();
});

// ==================== Auth Event Wiring ====================

eventBus.on('auth:ready', async (user) => {
    uiController.hideLoginModal();
    uiController.updateUserMenu(user.username, user.role);
    uiController.log(`已登录: ${user.username} (${user.tenantId})`);

    // Load scene definitions now that we have auth
    await workFormManager.loadNodeDefinitions();

    // Auto-connect WebSocket
    const host = document.getElementById('serverHost').value;
    wsService.connect(host, user.userId);
});

eventBus.on('auth:required', () => {
    uiController.clearUserMenu();
    uiController.showLoginModal();
});

eventBus.on('auth:logout', () => {
    wsService.disconnect();
    uiController.clearUserMenu();
    uiController.showLoginModal();
});

// ==================== WebSocket Event Wiring ====================

eventBus.on('ws:connected', () => {
    uiController.onConnected();
    chatManager.addMessage('已连接到 AI 助理', 'system');
    registerFrontendTools();

    // Enable input listener
    const textInput = document.getElementById('textInput');
    textInput.addEventListener('input', () => {
        const btnSend = document.getElementById('btnSend');
        btnSend.disabled = !textInput.value.trim();
    });
});

eventBus.on('ws:disconnected', () => {
    uiController.onDisconnected();
});

eventBus.on('ws:audio', (blob) => {
    uiController.log('收到音频: ' + blob.size + ' bytes');
    _enqueueAudio(blob);
});

// ==================== Audio Queue (sequential TTS playback) ====================

const _audioQueue = [];
let _audioPlaying = false;
let _backendIdle = false;   // true when backend sent 'idle' but audio still in queue

function _enqueueAudio(blob) {
    const url = URL.createObjectURL(blob);
    _audioQueue.push(url);
    _backendIdle = false;
    if (!_audioPlaying) _playNextAudio();
}

function _playNextAudio() {
    if (_audioQueue.length === 0) {
        _audioPlaying = false;
        // Resume idle only after both queue empty AND backend signalled idle
        if (_backendIdle) {
            uiController.updateStatus('connected', '已连接');
        }
        return;
    }
    _audioPlaying = true;
    const url = _audioQueue.shift();
    const audio = new Audio(url);
    uiController.updateStatus('speaking', '回复中...');
    const onDone = () => { URL.revokeObjectURL(url); _playNextAudio(); };
    audio.addEventListener('ended', onDone);
    audio.addEventListener('error', onDone);
    audio.play().catch(e => {
        uiController.log('播放失败: ' + e.message, 'error');
        onDone();
    });
}

eventBus.on('ws:message', (msg) => {
    uiController.log('收到: ' + msg.type);

    switch (msg.type) {
        case 'hello':
            uiController.log('会话: ' + msg.session_id, 'success');
            break;
        case 'listening':
            uiController.updateStatus('listening', '聆听中...');
            break;
        case 'processing':
            uiController.updateStatus('processing', '思考中...');
            document.getElementById('asrInterim').classList.remove('show');
            chatManager.addThinkingMessage('正在思考...');
            break;
        case 'speaking':
            uiController.updateStatus('speaking', '回复中...');
            _backendIdle = false;
            chatManager.clearThinkingMessage();
            break;
        case 'idle':
            _backendIdle = true;
            // Only switch status if audio queue is already empty
            if (!_audioPlaying) {
                uiController.updateStatus('connected', '已连接');
            }
            break;
        case 'asr_result':
            if (msg.is_final) {
                chatManager.addMessage(msg.text, 'user');
                document.getElementById('asrInterim').classList.remove('show');
                document.getElementById('voiceText').textContent = '';
            } else {
                document.getElementById('asrInterim').textContent = msg.text;
                document.getElementById('asrInterim').classList.add('show');
                document.getElementById('voiceText').textContent = msg.text;
            }
            break;
        case 'text':
            chatManager.clearThinkingMessage();
            chatManager.addMessage(msg.text, 'assistant');
            break;
        case 'text_chunk':
            chatManager.addStreamingMessage(msg.chunk, msg.is_first || false);
            break;
        case 'thinking_chunk':
            chatManager.addThinkingStreamingMessage(msg.chunk, msg.is_first || false);
            break;
        case 'text_complete':
            chatManager.finishStreamingMessage();
            break;
        case 'thinking':
            chatManager.addThinkingMessage(msg.text);
            break;
        case 'clear_thinking':
            chatManager.clearThinkingMessage();
            break;
        case 'action':
            actionDispatcher.handleAction(msg);
            break;
        case 'tools_registered':
            uiController.log(`工具已注册: ${(msg.tools || []).join(', ')}`, 'success');
            break;
        case 'error':
            uiController.log('错误: ' + msg.error, 'error');
            chatManager.addMessage('错误: ' + msg.error, 'system');
            break;
    }
});

// ==================== UI Send Text Event ====================

eventBus.on('ui:send_text', (text) => {
    if (!text || !AppState.isConnected) return;
    chatManager.addMessage(text, 'user');
    wsService.sendJSON({
        type: 'send_text',
        text: text,
        show_thinking: AppState.showThinkingProcess
    });
});

// ==================== Tab Event Wiring ====================

eventBus.on('tab:activated', (tabState) => {
    // Send context_update to backend so AI knows current tab
    if (AppState.isConnected) {
        const context = {
            activeTabId: tabState.tabId,
            type: tabState.type,
            title: tabState.title
        };
        if (tabState.type === 'inspection') {
            context.userId = tabState.userId;
            context.address = tabState.address;
            context.workType = tabState.workType;
            context.currentScene = tabState.workState ? tabState.workState.currentScene : null;
            context.completedScenes = tabState.workState ? tabState.workState.completedScenes : [];
            // Include current scene collected fields so AI knows what's already captured
            const currentScene = tabState.workState ? tabState.workState.currentScene : null;
            if (currentScene && tabState.sceneFieldsCache && tabState.sceneFieldsCache[currentScene]) {
                context.sceneFields = tabState.sceneFieldsCache[currentScene];
            }
        }
        wsService.sendJSON({
            type: 'context_update',
            context
        });
        uiController.log(`页签上下文已同步: ${tabState.title}`);
    }
});

eventBus.on('tab:closed', ({ tabId, type }) => {
    // Notify backend about tab closure
    if (AppState.isConnected) {
        wsService.sendJSON({
            type: 'context_update',
            context: {
                closedTabId: tabId,
                closedTabType: type,
                activeTabId: AppState.activeTabId,
                activeTab: tabManager.getActiveTab()
            }
        });
    }
});

// When fields are updated, send context_update to backend so AI knows current field status
eventBus.on('work:fields_updated', ({ nodeId, fields }) => {
    if (AppState.isConnected) {
        const activeTab = tabManager.getActiveTab();
        if (activeTab && activeTab.type === 'inspection') {
            const context = {
                activeTabId: activeTab.tabId,
                type: activeTab.type,
                title: activeTab.title,
                userId: activeTab.userId,
                address: activeTab.address,
                workType: activeTab.workType,
                currentScene: activeTab.workState ? activeTab.workState.currentScene : null,
                completedScenes: activeTab.workState ? activeTab.workState.completedScenes : [],
            };
            // Include current scene collected fields
            const currentScene = activeTab.workState ? activeTab.workState.currentScene : null;
            if (currentScene && activeTab.sceneFieldsCache && activeTab.sceneFieldsCache[currentScene]) {
                context.sceneFields = activeTab.sceneFieldsCache[currentScene];
            }
            wsService.sendJSON({
                type: 'context_update',
                context
            });
            uiController.log(`场景字段已同步到后端: ${Object.keys(fields).join(', ')}`);
        }
    }
});

// ==================== Global Event Delegation ====================

document.body.addEventListener('click', (e) => {
    const target = e.target.closest('[data-action]');
    if (!target) return;

    const action = target.dataset.action;

    switch (action) {
        // --- Header buttons ---
        case 'toggle-log':
            uiController.toggleLog();
            break;
        case 'show-settings':
            uiController.showSettings();
            break;
        case 'hide-settings':
            uiController.hideSettings();
            break;
        case 'toggle-thinking':
            uiController.toggleThinkingDisplay();
            break;
        case 'toggle-connection':
            if (AppState.isConnected) {
                wsService.disconnect();
            } else {
                const host = document.getElementById('serverHost').value;
                const userId = AppState.auth?.userId;
                if (!userId) {
                    uiController.showLoginModal();
                    break;
                }
                wsService.connect(host, userId);
            }
            break;

        // --- Auth ---
        case 'login-submit':
            _handleLogin();
            break;
        case 'register-submit':
            _handleRegister();
            break;
        case 'show-register':
            uiController.showRegisterForm();
            break;
        case 'show-login':
            uiController.showLoginForm();
            break;
        case 'toggle-user-menu':
            uiController.toggleUserDropdown();
            break;
        case 'switch-user':
            uiController.hideUserDropdown();
            authService.logout();
            break;
        case 'logout':
            uiController.hideUserDropdown();
            authService.logout();
            break;

        // --- Chat panel collapse/expand ---
        case 'toggle-chat-panel': {
            _toggleChatPanel();
            break;
        }

        // --- Image ---
        case 'pick-image':
            document.getElementById('fileInput').click();
            break;
        case 'clear-image':
            uiController.clearImage();
            break;

        // --- Send message ---
        case 'send-message':
            _sendMessage();
            break;

        // --- Voice ---
        // Voice uses mousedown/mouseup, handled separately below

        // --- Chat photo buttons ---
        case 'chat-take-photo': {
            const actions = target.closest('.chat-photo-actions');
            if (actions) actions.querySelectorAll('button').forEach(b => b.disabled = true);
            AppState.chatPhotoAutoSend = true;
            document.getElementById('cameraInput').click();
            break;
        }
        case 'chat-upload-photo': {
            const actions = target.closest('.chat-photo-actions');
            if (actions) actions.querySelectorAll('button').forEach(b => b.disabled = true);
            AppState.chatPhotoAutoSend = true;
            document.getElementById('fileInput').click();
            break;
        }

        // --- Choice buttons ---
        case 'on-choice-selected':
            uiActions.onChoiceSelected(target);
            break;
        case 'on-address-selected':
            uiActions.onAddressSelected(target);
            break;

        // --- Work form ---
        case 'close-work-form':
            workFormManager.close();
            break;
        case 'close-work-form-modal':
            workFormManager.closeModal();
            break;
        case 'complete-work':
            workFormManager.completeWork();
            break;
        case 'complete-work-modal':
            workFormManager.completeWork();
            workFormManager.closeModal();
            break;
        case 'reopen-work-form': {
            const uid = target.dataset.userId;
            const wt = target.dataset.workType;
            const addr = target.dataset.address;
            workFormManager.reopen(uid, wt, addr);
            break;
        }
        case 'pick-node-image': {
            e.stopPropagation();
            const nodeId = target.dataset.nodeId;
            workFormManager.pickNodeImage(nodeId);
            break;
        }
        case 'advance-to-next-node': {
            e.stopPropagation();
            workFormManager.advanceToNextNode();
            break;
        }
        case 'work-panel-tab-switch':
        case 'tab-switch': {
            const tabId = target.dataset.tabId || target.closest('[data-tab-id]')?.dataset.tabId;
            if (tabId) {
                tabManager.activateTab(tabId);
            }
            break;
        }
        case 'tab-close': {
            e.stopPropagation();
            const tabId = target.dataset.tabId;
            if (tabId) {
                tabManager.closeTab(tabId);
            }
            break;
        }
        case 'start-task-from-list': {
            const address = target.dataset.address;
            wsService.sendJSON({
                type: 'send_text',
                text: `开始 ${address} 的安检`,
                show_thinking: AppState.showThinkingProcess
            });
            chatManager.addMessage(`开始 ${address} 的安检`, 'user');
            break;
        }
        case 'save-photo-to-node': {
            const nodeId = target.dataset.nodeId;
            photoHandler.savePhotoToNode(nodeId);
            // Disable buttons
            const group = target.closest('.chat-choices-btns');
            if (group) group.querySelectorAll('button').forEach(b => b.disabled = true);
            break;
        }
        case 'retake-photo': {
            const mode = target.dataset.mode;
            const group = target.closest('.chat-choices-btns');
            if (group) group.querySelectorAll('button').forEach(b => b.disabled = true);
            if (mode === 'camera') {
                document.getElementById('cameraInput').click();
            } else {
                document.getElementById('fileInput').click();
            }
            AppState.chatPhotoAutoSend = true;
            break;
        }
    }
});

// --- Voice button (mousedown/mouseup/touchstart/touchend) ---
const btnVoice = document.getElementById('btnVoice');
if (btnVoice) {
    btnVoice.addEventListener('mousedown', () => voiceRecorder.startVoice());
    btnVoice.addEventListener('mouseup', () => voiceRecorder.stopVoice());
    btnVoice.addEventListener('touchstart', (e) => { e.preventDefault(); voiceRecorder.startVoice(); });
    btnVoice.addEventListener('touchend', (e) => { e.preventDefault(); voiceRecorder.stopVoice(); });
    btnVoice.addEventListener('contextmenu', (e) => e.preventDefault());
}

// --- FAB: short-press = toggle chat panel, long-press = voice input ---
const chatReopenFab = document.getElementById('chatReopenFab');
if (chatReopenFab) {
    const LONG_PRESS_MS = 300;
    let _fabTimer = null;
    let _fabLongPressed = false;

    const _fabPressStart = (e) => {
        if (e.type === 'touchstart') e.preventDefault();
        _fabLongPressed = false;
        _fabTimer = setTimeout(async () => {
            _fabLongPressed = true;
            await voiceRecorder.startVoice();
            chatReopenFab.classList.add('recording');
            chatReopenFab.setAttribute('aria-label', '松开手指停止录音');
        }, LONG_PRESS_MS);
    };

    const _fabPressEnd = (e) => {
        clearTimeout(_fabTimer);
        if (_fabLongPressed && AppState.isRecording) {
            voiceRecorder.stopVoice();
            chatReopenFab.classList.remove('recording');
            chatReopenFab.setAttribute('aria-label', '打开 AI 助理，长按语音输入');
        }
    };

    // Prevent click from triggering panel toggle after a long press
    chatReopenFab.addEventListener('click', (e) => {
        if (_fabLongPressed) {
            e.stopImmediatePropagation();
            _fabLongPressed = false;
        }
    });

    chatReopenFab.addEventListener('mousedown', _fabPressStart);
    chatReopenFab.addEventListener('touchstart', _fabPressStart, { passive: false });
    chatReopenFab.addEventListener('mouseup', _fabPressEnd);
    chatReopenFab.addEventListener('touchend', _fabPressEnd);
    chatReopenFab.addEventListener('mouseleave', _fabPressEnd);
    chatReopenFab.addEventListener('touchcancel', _fabPressEnd);
    chatReopenFab.addEventListener('contextmenu', (e) => e.preventDefault());
}

// --- Textarea events ---
const textInput = document.getElementById('textInput');
if (textInput) {
    textInput.addEventListener('input', () => uiController.autoResize(textInput));
    textInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            _sendMessage();
        }
    });
}

// --- File input events ---
const fileInput = document.getElementById('fileInput');
if (fileInput) {
    fileInput.addEventListener('change', (e) => {
        _onFileSelected(e);
        // 用户取消选择时，重新启用拍照/上传按钮
        if (!e.target.files || e.target.files.length === 0) {
            _enableChatPhotoButtons();
        }
    });
}
const cameraInput = document.getElementById('cameraInput');
if (cameraInput) {
    cameraInput.addEventListener('change', (e) => {
        _onFileSelected(e);
        // 用户取消选择时，重新启用拍照/上传按钮
        if (!e.target.files || e.target.files.length === 0) {
            _enableChatPhotoButtons();
        }
    });
}

// ==================== Helper Functions ====================

// --- Auth handlers ---
async function _handleLogin() {
    const username = document.getElementById('loginUsername')?.value.trim();
    const password = document.getElementById('loginPassword')?.value;
    if (!username || !password) {
        uiController.showLoginError('请输入用户名和密码');
        return;
    }
    uiController.setLoginLoading(true);
    try {
        const res = await authService.login(username, password);
        if (!res.success) {
            uiController.showLoginError(res.message);
        }
    } catch {
        uiController.showLoginError('网络错误，请稍后重试');
    } finally {
        uiController.setLoginLoading(false);
    }
}

async function _handleRegister() {
    const username = document.getElementById('registerUsername')?.value.trim();
    const password = document.getElementById('registerPassword')?.value;
    const confirm = document.getElementById('registerPasswordConfirm')?.value;
    if (!username || !password) {
        uiController.showRegisterError('请输入用户名和密码');
        return;
    }
    if (password !== confirm) {
        uiController.showRegisterError('两次密码输入不一致');
        return;
    }
    if (password.length < 4) {
        uiController.showRegisterError('密码长度不能少于4位');
        return;
    }
    uiController.setRegisterLoading(true);
    try {
        const res = await authService.register(username, password);
        if (res.success) {
            uiController.showRegisterSuccess(res.message || '注册成功，请登录');
        } else {
            uiController.showRegisterError(res.message);
        }
    } catch {
        uiController.showRegisterError('网络错误，请稍后重试');
    } finally {
        uiController.setRegisterLoading(false);
    }
}

// --- Login/Register Enter key support ---
document.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    if (e.target.id === 'loginPassword' || e.target.id === 'loginUsername') {
        e.preventDefault();
        _handleLogin();
    } else if (e.target.id === 'registerPasswordConfirm') {
        e.preventDefault();
        _handleRegister();
    }
});

/**
 * 重新启用聊天区域的拍照/上传按钮
 * 当用户取消文件选择时调用
 */
function _enableChatPhotoButtons() {
    const actions = document.querySelector('.chat-photo-actions');
    if (actions) {
        actions.querySelectorAll('button').forEach(b => b.disabled = false);
    }
    // 同时重置自动发送标志
    AppState.chatPhotoAutoSend = false;
}

function _toggleChatPanel() {
    const chatPanel = document.getElementById('chatPanel');
    const appContainer = document.querySelector('.app-container');
    const toggleBtn = document.getElementById('chatToggleBtn');
    const fab = document.getElementById('chatReopenFab');
    const isCollapsed = chatPanel.classList.toggle('collapsed');
    appContainer.classList.toggle('chat-collapsed', isCollapsed);
    // Persist state
    localStorage.setItem('chatPanelCollapsed', isCollapsed ? '1' : '0');
    if (toggleBtn) {
        toggleBtn.setAttribute('aria-label', isCollapsed ? '打开 AI 助理' : '收起 AI 助理');
        toggleBtn.setAttribute('title', isCollapsed ? '打开 AI 助理' : '收起 AI 助理');
        const icon = toggleBtn.querySelector('svg');
        if (icon) icon.style.transform = isCollapsed ? 'scaleX(-1)' : '';
    }
    if (fab) {
        fab.setAttribute('aria-label', isCollapsed ? '打开 AI 助理，长按语音输入' : '收起 AI 助理');
    }
}

/** Restore chat panel state from localStorage (call once on init). */
function _restoreChatPanelState() {
    if (localStorage.getItem('chatPanelCollapsed') !== '1') return;
    const chatPanel = document.getElementById('chatPanel');
    const appContainer = document.querySelector('.app-container');
    const toggleBtn = document.getElementById('chatToggleBtn');
    const fab = document.getElementById('chatReopenFab');
    // Apply collapsed state without transition flash
    chatPanel.style.transition = 'none';
    chatPanel.classList.add('collapsed');
    appContainer.classList.add('chat-collapsed');
    // Re-enable transition after next paint
    requestAnimationFrame(() => chatPanel.style.transition = '');
    if (toggleBtn) {
        toggleBtn.setAttribute('aria-label', '打开 AI 助理');
        toggleBtn.setAttribute('title', '打开 AI 助理');
        const icon = toggleBtn.querySelector('svg');
        if (icon) icon.style.transform = 'scaleX(-1)';
    }
    if (fab) {
        fab.setAttribute('aria-label', '打开 AI 助理，长按语音输入');
    }
}

function _sendMessage() {
    if (AppState.pendingImage) {
        const text = textInput.value.trim();
        photoHandler.sendImage(AppState.pendingImage, text);
        textInput.value = '';
        textInput.style.height = 'auto';
        uiController.clearImage();
    } else {
        const text = textInput.value.trim();
        if (!text || !AppState.isConnected) return;
        chatManager.addMessage(text, 'user');
        wsService.sendJSON({
            type: 'send_text',
            text: text,
            show_thinking: AppState.showThinkingProcess
        });
        textInput.value = '';
        textInput.style.height = 'auto';
        document.getElementById('btnSend').disabled = true;
    }
}

function _onFileSelected(e) {
    const file = e.target.files[0];
    if (!file) return;

    if (!file.type.startsWith('image/')) {
        uiController.log('请选择图片文件', 'warning');
        return;
    }
    if (file.size > 10 * 1024 * 1024) {
        uiController.log('图片不能超过10MB', 'warning');
        return;
    }

    const reader = new FileReader();
    reader.onload = (ev) => {
        uiController.showImagePreview(file, ev.target.result);

        // Auto-send if triggered from chat buttons
        if (AppState.chatPhotoAutoSend) {
            AppState.chatPhotoAutoSend = false;
            setTimeout(() => _sendMessage(), 100);
        }
    };
    reader.readAsDataURL(file);
}
