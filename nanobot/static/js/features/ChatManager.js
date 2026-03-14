/**
 * ChatManager - handles all chat message rendering.
 * Manages: normal messages, streaming, thinking, photo buttons.
 */
import AppState from '../core/AppState.js';
import eventBus from '../core/EventBus.js';

class ChatManager {
    constructor() {
        this.chatArea = null;
    }

    init() {
        this.chatArea = document.getElementById('chatArea');
    }

    /**
     * Get avatar emoji based on agent type.
     * @param {string} type - Message type
     * @param {string|null} agentName - Agent name
     * @returns {string} Avatar emoji
     */
    getAvatarForAgent(type, agentName) {
        if (type === 'user') {
            return '&#128100;'; // User emoji
        }
        
        if (agentName) {
            const name = agentName.toLowerCase();
            if (name.includes('workflow') || name.includes('inspector') || name.includes('安检')) {
                return '&#128196;'; // Badge/Inspector emoji
            }
            if (name.includes('tool') || name.includes('worker')) {
                return '&#128736;'; // Tool emoji
            }
        }
        
        return '&#129302;'; // Default robot emoji
    }

    /**
     * Add a message to the chat area.
     * @param {string} content - HTML or text content
     * @param {string} type - 'user' | 'assistant' | 'system'
     * @param {string|null} agentName - Agent name for avatar differentiation
     */
    addMessage(content, type = 'assistant', agentName = null) {
        // Clear any existing streaming message when new message is added
        if (AppState.currentStreamingMessage) {
            AppState.currentStreamingMessage = null;
            AppState.streamingText = '';
        }

        const msg = document.createElement('div');
        msg.className = 'message ' + type;

        let displayContent = content;
        if (type === 'assistant' && typeof marked !== 'undefined') {
            displayContent = marked.parse(content);
        }

        if (type === 'system') {
            msg.innerHTML = `<div class="bubble">${displayContent}</div>`;
        } else {
            const avatar = this.getAvatarForAgent(type, agentName);
            msg.innerHTML = `
                <div class="avatar">${avatar}</div>
                <div class="bubble">${displayContent}</div>
            `;
        }

        this.chatArea.appendChild(msg);
        this.chatArea.scrollTop = this.chatArea.scrollHeight;

        if (type === 'assistant') {
            eventBus.emit('chat:assistant_message', msg);
        }
    }

    /**
     * Show thinking/processing indicator.
     */
    addThinkingMessage(content) {
        if (AppState.currentThinkingMessage) {
            AppState.currentThinkingMessage.remove();
        }

        const msg = document.createElement('div');
        msg.className = 'message assistant thinking';
        const avatar = this.getAvatarForAgent('assistant', AppState.currentAgentName);
        msg.innerHTML = `
            <div class="avatar">${avatar}</div>
            <div class="bubble">
                <span class="thinking-dots"></span>
                ${content}
            </div>
        `;

        this.chatArea.appendChild(msg);
        this.chatArea.scrollTop = this.chatArea.scrollHeight;
        AppState.currentThinkingMessage = msg;
    }

    /**
     * Streaming text output.
     * @param {string} text - Text content
     * @param {boolean} isFirst - Whether this is the first chunk
     * @param {string|null} agentName - Agent name for avatar differentiation
     */
    addStreamingMessage(text, isFirst = false, agentName = null) {
        if (text && text.trim()) {
            this.clearThinkingMessage();
        }

        if (isFirst || !AppState.currentStreamingMessage) {
            AppState.streamingText = text;
            AppState.currentStreamingAgentName = agentName;

            const msg = document.createElement('div');
            msg.className = 'message assistant';
            const avatar = this.getAvatarForAgent('assistant', agentName);
            msg.innerHTML = `
                <div class="avatar">${avatar}</div>
                <div class="bubble" data-streaming="true"></div>
            `;

            this.chatArea.appendChild(msg);
            this.chatArea.scrollTop = this.chatArea.scrollHeight;
            AppState.currentStreamingMessage = msg;
        } else {
            AppState.streamingText += text;
        }

        const bubble = AppState.currentStreamingMessage.querySelector('.bubble');
        if (bubble && typeof marked !== 'undefined') {
            try {
                bubble.innerHTML = marked.parse(AppState.streamingText);
                this.chatArea.scrollTop = this.chatArea.scrollHeight;
            } catch (e) {
                bubble.textContent = AppState.streamingText;
            }
        }
    }

    /**
     * Finish streaming output.
     * @param {string|null} agentName - Agent name to update avatar
     */
    finishStreamingMessage(agentName = null) {
        if (AppState.currentStreamingMessage) {
            const bubble = AppState.currentStreamingMessage.querySelector('.bubble');
            if (bubble) {
                bubble.removeAttribute('data-streaming');
            }
            
            // Update avatar if agent_name is provided
            if (agentName) {
                const avatarDiv = AppState.currentStreamingMessage.querySelector('.avatar');
                if (avatarDiv) {
                    const avatar = this.getAvatarForAgent('assistant', agentName);
                    avatarDiv.innerHTML = avatar;
                }
            }
            
            eventBus.emit('chat:assistant_message', AppState.currentStreamingMessage);
            AppState.currentStreamingMessage = null;
            AppState.streamingText = '';
        }
    }

    /**
     * Add photo action buttons to the last assistant message.
     */
    addPhotoButtonsToLastMessage() {
        const messages = this.chatArea.querySelectorAll('.message.assistant');
        if (messages.length === 0) return;
        
        const lastMessage = messages[messages.length - 1];
        const bubble = lastMessage.querySelector('.bubble');
        if (!bubble) return;
        
        // Check if buttons already exist
        if (bubble.querySelector('.chat-photo-actions')) return;
        
        // Add photo action buttons
        const actions = document.createElement('div');
        actions.className = 'chat-photo-actions';
        actions.innerHTML = `
            <div class="chat-photo-prompt">拍照或上传：</div>
            <button class="chat-photo-btn" data-action="chat-take-photo">
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
                    <circle cx="12" cy="13" r="4"/>
                </svg>
                拍照
            </button>
            <button class="chat-photo-btn" data-action="chat-upload-photo">
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                    <polyline points="17 8 12 3 7 8"/>
                    <line x1="12" y1="3" x2="12" y2="15"/>
                </svg>
                上传
            </button>
        `;
        bubble.appendChild(actions);
        this.chatArea.scrollTop = this.chatArea.scrollHeight;
    }

    /**
     * Thinking stream (collapsible).
     */
    addThinkingStreamingMessage(text, isFirst = false, agentName = null) {
        if (!AppState.showThinkingProcess) return;

        if (isFirst || !AppState.currentThinkingStreamMessage) {
            const msg = document.createElement('div');
            msg.className = 'message system thinking-stream';
            const avatar = this.getAvatarForAgent('assistant', agentName || AppState.currentAgentName);
            msg.innerHTML = `
                <div class="avatar">${avatar}</div>
                <div class="bubble thinking-bubble">
                    <details open>
                        <summary>正在思考...</summary>
                        <div class="thinking-content">${text}</div>
                    </details>
                </div>
            `;

            this.chatArea.appendChild(msg);
            this.chatArea.scrollTop = this.chatArea.scrollHeight;
            AppState.currentThinkingStreamMessage = msg;

            if (AppState.currentThinkingMessage && AppState.currentThinkingMessage !== msg) {
                AppState.currentThinkingMessage.remove();
            }
            AppState.currentThinkingMessage = msg;
        } else {
            const content = AppState.currentThinkingStreamMessage.querySelector('.thinking-content');
            if (content) {
                content.textContent += text;
                this.chatArea.scrollTop = this.chatArea.scrollHeight;
            }
        }
    }

    /**
     * Clear thinking indicator.
     */
    clearThinkingMessage() {
        if (AppState.currentThinkingMessage) {
            AppState.currentThinkingMessage.remove();
            AppState.currentThinkingMessage = null;
        }
    }

    /**
     * Add a system message with a reopen button for work forms.
     */
    addMessageWithReopenButton(text, userId, workType, address) {
        const msg = document.createElement('div');
        msg.className = 'message system';
        msg.innerHTML = `
            <div class="avatar">ℹ️</div>
            <div class="bubble">
                ${text}
                <button class="reopen-form-btn" data-action="reopen-work-form"
                    data-user-id="${userId}" data-work-type="${workType}" data-address="${address || ''}">
                    📋 重新打开
                </button>
            </div>
        `;

        this.chatArea.appendChild(msg);
        this.chatArea.scrollTop = this.chatArea.scrollHeight;
    }

    /**
     * Add a message with next scene button.
     */
    addMessageWithNextSceneButton(text) {
        const msg = document.createElement('div');
        msg.className = 'message assistant';
        const avatar = this.getAvatarForAgent('assistant', AppState.currentAgentName);
        msg.innerHTML = `
            <div class="avatar">${avatar}</div>
            <div class="bubble">
                ${text}
                <button class="node-next-btn" data-action="advance-to-next-node">
                    进入下一场景 →
                </button>
            </div>
        `;

        this.chatArea.appendChild(msg);
        this.chatArea.scrollTop = this.chatArea.scrollHeight;
        
        // Emit log event for backend recording
        eventBus.emit('log', { msg: `Assistant message: ${text}`, type: 'info' });
    }
}

export default new ChatManager();
