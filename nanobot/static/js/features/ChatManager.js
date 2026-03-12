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
            <div class="avatar">\u2139\uFE0F</div>
            <div class="bubble">
                ${text}
                <button class="reopen-form-btn" data-action="reopen-work-form"
                    data-user-id="${userId}" data-work-type="${workType}" data-address="${address || ''}">
                    \uD83D\uDCCB 重新打开
                </button>
            </div>
        `;

        this.chatArea.appendChild(msg);
        this.chatArea.scrollTop = this.chatArea.scrollHeight;
    }
}

export default new ChatManager();
