/**
 * UiActions - show_alert, show_choices, show_address_selector.
 */
import chatManager from '../features/ChatManager.js';
import eventBus from '../core/EventBus.js';
import AppState from '../core/AppState.js';

class UiActions {
    /**
     * Show an alert notification.
     */
    async showAlert(params) {
        const { title, message, level } = params;
        const levelEmoji = level === 'error' ? '\u274C' : (level === 'warning' ? '\u26A0\uFE0F' : '\u2139\uFE0F');
        const displayMsg = title ? `${levelEmoji} **${title}**\n${message}` : `${levelEmoji} ${message}`;
        chatManager.addMessage(displayMsg, 'system');
        return JSON.stringify({ success: true, message: '已显示提醒' });
    }

    /**
     * Show choice buttons in chat (BLOCKING tool).
     */
    async showChoices(params) {
        const { prompt, choices } = params;

        let html = '<div class="chat-choices">';
        if (prompt) html += `<div class="chat-choices-prompt">${prompt}</div>`;
        html += '<div class="chat-choices-btns">';
        (choices || []).forEach(choice => {
            html += `<button class="chat-choice-btn" data-action="on-choice-selected" data-choice="${choice}">${choice}</button>`;
        });
        html += '</div></div>';

        chatManager.addMessage(html, 'assistant', AppState.currentAgentName);

        return JSON.stringify({
            success: true,
            message: '已显示选项，等待用户选择',
            instruction: 'STOP_AND_WAIT_FOR_USER_INPUT'
        });
    }

    /**
     * Handle choice button click - called from action dispatcher.
     */
    onChoiceSelected(btn) {
        const choice = btn.dataset.choice;
        const group = btn.closest('.chat-choices-btns');
        if (!group) return;

        // Disable all buttons, highlight selected
        group.querySelectorAll('.chat-choice-btn').forEach(b => {
            b.classList.add('chat-choice-disabled');
            b.disabled = true;
        });
        btn.classList.add('chat-choice-selected');

        // Send the choice as a user message
        const textInput = document.getElementById('textInput');
        if (textInput) {
            textInput.value = choice;
        }
        // Trigger send via event
        eventBus.emit('ui:send_text', choice);
    }

    /**
     * Show address selector (multiple matching addresses).
     */
    async showAddressSelector(params) {
        const { addresses, workType, prompt } = params;
        if (!addresses || !addresses.length) {
            return JSON.stringify({ success: false, message: '地址列表为空' });
        }

        let html = '<div class="chat-choices">';
        html += `<div class="chat-choices-prompt">${prompt || '找到多个匹配地址，请选择：'}</div>`;
        html += '<div class="chat-choices-btns" style="flex-direction:column">';
        addresses.forEach(addr => {
            const label = `${addr.address}（用户号: ${addr.userId}）`;
            // Encode the full address data as JSON in data attribute
            const data = JSON.stringify({ userId: addr.userId, address: addr.address, workType });
            html += `<button class="chat-choice-btn" style="text-align:left;white-space:normal" data-action="on-address-selected" data-address-info='${data}'>${label}</button>`;
        });
        html += '</div></div>';

        chatManager.addMessage(html, 'assistant', AppState.currentAgentName);

        return JSON.stringify({
            success: true,
            message: `已显示 ${addresses.length} 个地址选项`,
            instruction: 'STOP_AND_WAIT_FOR_USER_INPUT'
        });
    }

    /**
     * Handle address selection.
     */
    onAddressSelected(btn) {
        const group = btn.closest('.chat-choices-btns');
        if (!group) return;

        group.querySelectorAll('.chat-choice-btn').forEach(b => {
            b.classList.add('chat-choice-disabled');
            b.disabled = true;
        });
        btn.classList.add('chat-choice-selected');

        const addressInfo = JSON.parse(btn.dataset.addressInfo || '{}');
        const selectedText = `选择了地址: ${addressInfo.address}（用户号: ${addressInfo.userId}）`;
        eventBus.emit('ui:send_text', selectedText);
    }
}

export default new UiActions();
