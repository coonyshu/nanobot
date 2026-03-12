/**
 * PhotoHandler - handles photo interactions in chat context.
 * Manages: validity detection, auto-save, field extraction, backend sync.
 */
import AppState from '../core/AppState.js';
import eventBus from '../core/EventBus.js';
import apiService from '../services/ApiService.js';
import chatManager from './ChatManager.js';
import workFormManager from './WorkFormManager.js';
import nodeRenderer from './NodeRenderer.js';

class PhotoHandler {
    constructor() {
        // Cached parsed JSON block from last AI response
        this._lastParsedBlock = null;
    }

    init() {
        // Photo buttons should be triggered by LLM calling frontend tools, not by keyword detection
        // This allows LLM to have full control over when to show photo buttons
    }

    /**
     * Append quick photo action buttons if AI response mentions taking photos.
     * DEPRECATED: This method is no longer used. Photo buttons should be triggered
     * by LLM calling frontend tools (camera_take_photo, camera_upload_photo).
     */
    _appendPhotoButtons(msgElement) {
        // This method is deprecated and should not be called
        // Photo buttons should be triggered by LLM calling frontend tools
        return;
    }

    /**
     * Send an image for AI analysis (via HTTP API).
     * Called from the main input flow.
     * @param {Object} pendingImage - { file, dataUrl }
     * @param {string} userText - Optional user prompt
     */
    async sendImage(pendingImage, userText) {
        if (!pendingImage || !AppState.isConnected) return;

        let text = userText || '';

        // Determine work photo context from state (not from text pattern)
        const ws = AppState.currentWorkState;
        const isWorkPhoto = !!(ws.userId && ws.currentNode);

        // Always inject structured work prompt when in work context
        if (isWorkPhoto) {
            const workPrompt = workFormManager.getWorkPhotoPrompt();
            if (workPrompt) {
                text = text ? `${workPrompt}\n\n用户补充说明：${text}` : workPrompt;
            }
        }
        if (!text) text = '请描述这张图片的内容';

        const sentDataUrl = pendingImage.dataUrl;

        // Show image in chat
        const imgHtml = `<img src="${pendingImage.dataUrl}" onclick="this.requestFullscreen && this.requestFullscreen()">` +
            (!isWorkPhoto && text !== '请描述这张图片的内容' ? `<div>${text}</div>` : '');
        chatManager.addMessage(imgHtml, 'user');

        // Status indicator
        const statusMsg = document.createElement('div');
        statusMsg.className = 'message system';
        statusMsg.id = 'imageStatus';
        statusMsg.innerHTML = '<div class="bubble">正在识别图片...</div>';
        const chatArea = document.getElementById('chatArea');
        chatArea.appendChild(statusMsg);
        chatArea.scrollTop = chatArea.scrollHeight;

        const userId = AppState.auth?.userId || '';

        try {
            const data = await apiService.postImage(pendingImage.file, text, userId);

            const el = document.getElementById('imageStatus');
            if (el) el.querySelector('.bubble').textContent = '已识别';
            chatManager.addMessage(data.response, 'assistant');

            // Work photo: check validity and auto-save
            if (isWorkPhoto && ws.userId) {
                AppState.lastChatPhotoDataUrl = sentDataUrl;

                const isValid = this.checkPhotoValidity(data.response);
                if (isValid) {
                    this.autoSavePhotoToNode(data.response);
                } else {
                    this._appendPhotoActionButtons();
                }
            }
        } catch (e) {
            eventBus.emit('log', { msg: '图片发送失败 ' + e.message, type: 'error' });
            const el = document.getElementById('imageStatus');
            if (el) el.querySelector('.bubble').textContent = '识别失败';
            chatManager.addMessage('图片识别失败: ' + e.message, 'system');
        } finally {
            const el = document.getElementById('imageStatus');
            if (el) el.removeAttribute('id');
        }
    }

    /**
     * Parse structured JSON block from AI response.
     * Expected format: ```json {"photo_valid": bool, "fields": {...}, "reason": "..."} ```
     * @returns {Object|null} Parsed object or null if not found/invalid
     */
    _parseAIJsonBlock(aiResponse) {
        if (!aiResponse) return null;
        try {
            // Priority 1: match ```json {...} ``` code block
            const codeBlockMatch = aiResponse.match(/```(?:json)?\s*(\{[\s\S]*?\})\s*```/);
            if (codeBlockMatch) {
                const parsed = JSON.parse(codeBlockMatch[1]);
                eventBus.emit('log', { msg: `AI JSON解析成功: photo_valid=${parsed.photo_valid}, fields=${Object.keys(parsed.fields || {}).length}个`, type: 'info' });
                return parsed;
            }
            // Priority 2: match bare JSON with photo_valid key
            const bareMatch = aiResponse.match(/\{\s*"photo_valid"[\s\S]*?\}/);
            if (bareMatch) {
                const parsed = JSON.parse(bareMatch[0]);
                eventBus.emit('log', { msg: `AI JSON解析成功: photo_valid=${parsed.photo_valid}`, type: 'info' });
                return parsed;
            }
        } catch (e) {
            eventBus.emit('log', { msg: `AI JSON解析失败，降级为正则提取: ${e.message}`, type: 'warning' });
        }
        return null;
    }

    /**
     * Check if AI response indicates the photo meets node requirements.
     * Priority: JSON photo_valid field �?keyword fallback.
     */
    checkPhotoValidity(aiResponse) {
        if (!aiResponse) return false;

        // Parse JSON and cache for extractFieldsFromAIResponse()
        this._lastParsedBlock = this._parseAIJsonBlock(aiResponse);

        // Priority: structured JSON
        if (this._lastParsedBlock && typeof this._lastParsedBlock.photo_valid === 'boolean') {
            return this._lastParsedBlock.photo_valid;
        }

        // Fallback: keyword matching
        const text = aiResponse.toLowerCase();
        const validKeywords = ['符合要求', '符合场景', '确认正确', '门牌号正确', '地址正确', '可以确认', '拍摄正确', '照片合格', '符合'];
        const invalidKeywords = ['不符合', '不是', '错误', '看不清', '无法确认', '不清楚', '重新拍', '再拍', '没有看到', '缺少'];

        for (const kw of invalidKeywords) {
            if (text.includes(kw)) return false;
        }
        for (const kw of validKeywords) {
            if (text.includes(kw)) return true;
        }
        return false;
    }

    /**
     * Extract field values from AI response.
     * Priority: JSON fields block > regex ai_extract_patterns > fallback.
     */
    extractFieldsFromAIResponse(nodeId, aiResponse) {
        if (!aiResponse || !nodeId) return {};

        const fieldDefs = AppState.fieldDefinitions[nodeId];
        if (!fieldDefs) return {};

        const extracted = {};

        // Priority: structured JSON fields from cached parse
        if (this._lastParsedBlock && this._lastParsedBlock.fields) {
            for (const [fieldKey, value] of Object.entries(this._lastParsedBlock.fields)) {
                if (value === null || value === undefined) continue;
                if (!fieldDefs[fieldKey]) continue;
                extracted[fieldDefs[fieldKey].label] = value;
                eventBus.emit('log', { msg: `从AI JSON提取字段 ${fieldDefs[fieldKey].label}: ${value}`, type: 'info' });
            }
            if (Object.keys(extracted).length > 0) return extracted;
        }

        // Fallback: regex matching using ai_extract_patterns
        for (const [fieldKey, fieldDef] of Object.entries(fieldDefs)) {
            const patterns = fieldDef.ai_extract_patterns || [];
            for (const pattern of patterns) {
                const match = aiResponse.match(pattern);
                if (match && match[1]) {
                    extracted[fieldDef.label] = match[1];
                    eventBus.emit('log', { msg: `从AI正则提取字段 ${fieldDef.label}: ${match[1]}`, type: 'info' });
                    break;
                }
            }
        }
        return extracted;
    }

    /**
     * Auto-save photo to current node and sync to backend.
     */
    autoSavePhotoToNode(aiResponse) {
        const nodeId = AppState.currentWorkState?.currentNode;
        if (!AppState.lastChatPhotoDataUrl || !nodeId) {
            eventBus.emit('log', { msg: `自动保存失败: 无照片或无当前场景 (lastChatPhotoDataUrl=${!!AppState.lastChatPhotoDataUrl}, currentWorkState=${JSON.stringify(AppState.currentWorkState)})`, type: 'warning' });
            return;
        }

        // Save photo locally
        if (!AppState.nodePhotos[nodeId]) AppState.nodePhotos[nodeId] = [];
        AppState.nodePhotos[nodeId].push({ dataUrl: AppState.lastChatPhotoDataUrl, timestamp: Date.now() });
        nodeRenderer.renderNodePhotos(nodeId);

        // Upload photo to backend for persistent storage (async, non-blocking)
        const _photoDataUrl = AppState.lastChatPhotoDataUrl;
        const _taskId = AppState.currentWorkState?.taskId;
        eventBus.emit('log', { msg: `[uploadPhoto] 开始上传 nodeId=${nodeId} taskId=${_taskId} dataLen=${_photoDataUrl?.length}`, type: 'info' });
        apiService.uploadNodePhoto(nodeId, _photoDataUrl).then(result => {
            eventBus.emit('log', { msg: `[uploadPhoto] 响应: ${JSON.stringify(result)}`, type: result.success ? 'success' : 'warning' });
        }).catch(e => {
            eventBus.emit('log', { msg: `[uploadPhoto] 异常: ${e.message}`, type: 'error' });
        });

        // Mark photo field as uploaded in nodeFieldsCache so _checkRequiredFields passes
        if (!AppState.nodeFieldsCache[nodeId]) AppState.nodeFieldsCache[nodeId] = {};
        AppState.nodeFieldsCache[nodeId]['photo'] = 'uploaded';

        const node = AppState.nodes.find(s => s.id === nodeId);
        const nodeName = node ? node.name : nodeId;

        // Extract fields from AI response
        const extractedFields = this.extractFieldsFromAIResponse(nodeId, aiResponse);

        // Update field display on form
        if (Object.keys(extractedFields).length > 0) {
            const result = workFormManager.updateNodeFields(nodeId, extractedFields);
            eventBus.emit('log', { msg: `[autoSave] updateNodeFields(${nodeId}) result: ${result}`, type: 'info' });
        }

        // Build backend sync data
        const backendData = {};
        // All nodes use unified 'photo' field name
        backendData['photo'] = 'uploaded';
        for (const [label, value] of Object.entries(extractedFields)) {
            const fieldDefs = AppState.fieldDefinitions[nodeId];
            if (fieldDefs) {
                for (const [key, def] of Object.entries(fieldDefs)) {
                    if (def.label === label) {
                        backendData[key] = value;
                        break;
                    }
                }
            }
        }

        // Sync to backend
        this._syncNodeDataToBackend(nodeId, backendData);

        // Emit event to trigger context update so AI knows about the photo and fields
        eventBus.emit('work:fields_updated', { nodeId, fields: backendData });

        chatManager.addMessage(`已自动保存照片到「${nodeName}」${Object.keys(extractedFields).length > 0 ? '，并提取字段: ' + Object.entries(extractedFields).map(([k,v]) => `${k}=${v}`).join(', ') : ''}`, 'system');

        eventBus.emit('log', { msg: `照片已自动保存到 ${nodeName}`, type: 'success' });
        AppState.lastChatPhotoDataUrl = null;
    }

    /**
     * Manual save photo to node (user clicked "still save").
     */
    savePhotoToNode(nodeId) {
        if (!AppState.lastChatPhotoDataUrl || !nodeId) return;

        if (!AppState.nodePhotos[nodeId]) AppState.nodePhotos[nodeId] = [];
        AppState.nodePhotos[nodeId].push({ dataUrl: AppState.lastChatPhotoDataUrl, timestamp: Date.now() });
        nodeRenderer.renderNodePhotos(nodeId);

        // Upload photo to backend for persistent storage (async, non-blocking)
        const _photoDataUrl2 = AppState.lastChatPhotoDataUrl;
        const _taskId2 = AppState.currentWorkState?.taskId;
        eventBus.emit('log', { msg: `[uploadPhoto] 开始上传 nodeId=${nodeId} taskId=${_taskId2} dataLen=${_photoDataUrl2?.length}`, type: 'info' });
        apiService.uploadNodePhoto(nodeId, _photoDataUrl2).then(result => {
            eventBus.emit('log', { msg: `[uploadPhoto] 响应: ${JSON.stringify(result)}`, type: result.success ? 'success' : 'warning' });
        }).catch(e => {
            eventBus.emit('log', { msg: `[uploadPhoto] 异常: ${e.message}`, type: 'error' });
        });

        // Mark photo field as uploaded in nodeFieldsCache so _checkRequiredFields passes
        if (!AppState.nodeFieldsCache[nodeId]) AppState.nodeFieldsCache[nodeId] = {};
        AppState.nodeFieldsCache[nodeId]['photo'] = 'uploaded';

        // All nodes use unified 'photo' field name
        this._syncNodeDataToBackend(nodeId, { photo: 'uploaded' });

        // Emit event to trigger context update so AI knows about the photo
        eventBus.emit('work:fields_updated', { nodeId, fields: { photo: 'uploaded' } });

        const node = AppState.nodes.find(s => s.id === nodeId);
        chatManager.addMessage(`已保存照片到「${node ? node.name : nodeId}」`, 'system');
        AppState.lastChatPhotoDataUrl = null;
    }

    /**
     * Append "save / retake / re-upload" buttons for invalid photos.
     */
    _appendPhotoActionButtons() {
        const node = AppState.nodes.find(s => s.id === AppState.currentWorkState.currentNode);
        const nodeName = node ? node.name : '当前场景';
        const nodeId = AppState.currentWorkState.currentNode;

        let html = '<div class="chat-choices">';
        html += '<div class="chat-choices-prompt">照片可能不符合场景要求，请选择操作：</div>';
        html += '<div class="chat-choices-btns">';
        html += `<button class="chat-choice-btn chat-choice-save" data-action="save-photo-to-node" data-node-id="${nodeId}">仍然保存到「${nodeName}」</button>`;
        html += '<button class="chat-choice-btn" data-action="retake-photo" data-mode="camera">重新拍照</button>';
        html += '<button class="chat-choice-btn" data-action="retake-photo" data-mode="upload">重新上传</button>';
        html += '</div></div>';

        chatManager.addMessage(html, 'assistant');
    }

    /**
     * Sync node data to backend.
     */
    async _syncNodeDataToBackend(nodeId, data) {
        try {
            const result = await apiService.updateNodeData(nodeId, data);
            if (result.success) {
                eventBus.emit('log', { msg: `后端场景数据已同步成功: ${nodeId} (${result.updated_fields?.join(', ')})`, type: 'info' });
            } else {
                eventBus.emit('log', { msg: `后端场景数据同步失败: ${result.message || result.error}`, type: 'warning' });
            }
        } catch (e) {
            eventBus.emit('log', { msg: `后端场景数据同步异常: ${e.message}`, type: 'error' });
        }
    }
}

export default new PhotoHandler();
