/**
 * Centralized HTTP API service.
 * Encapsulates all fetch calls; other modules never call fetch directly.
 */
import AppState from '../core/AppState.js';

class ApiService {
    /**
     * Get the base URL from the server host input or window location.
     */
    _baseUrl() {
        const protocol = window.location.protocol;
        const host = document.getElementById('serverHost')?.value || window.location.host;
        return `${protocol}//${host}`;
    }

    /**
     * Return auth headers if a token is available.
     * The JWT token already contains tenant_id; backend extracts it from the token.
     */
    _authHeaders() {
        const token = AppState.auth?.token;
        return token ? { 'Authorization': `Bearer ${token}` } : {};
    }

    /**
     * Generic JSON request helper (used by AuthService and others).
     * @param {string} path - API path (e.g. '/api/v1/auth/login')
     * @param {Object} options - fetch options (method, body, headers, etc.)
     * @param {boolean} [withAuth=true] - whether to include auth headers
     * @returns {Promise<Object>}
     */
    async request(path, options = {}, withAuth = true) {
        const headers = {
            'Content-Type': 'application/json',
            ...(withAuth ? this._authHeaders() : {}),
            ...(options.headers || {}),
        };
        const res = await fetch(`${this._baseUrl()}${path}`, {
            ...options,
            headers,
        });
        return res.json();
    }

    /**
     * POST an image for AI analysis.
     * @param {File} file - Image file
     * @param {string} message - Analysis prompt
     * @param {string} userId - User ID
     * @returns {Promise<Object>} { response: string }
     */
    async postImage(file, message, userId) {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('message', message);
        formData.append('user_id', userId);

        const res = await fetch(`${this._baseUrl()}/api/v1/image`, {
            method: 'POST',
            headers: this._authHeaders(),
            body: formData
        });
        return res.json();
    }

    /**
     * Sync node data to backend state machine.
     * @param {string} sceneId - Scene ID
     * @param {Object} data - Key-value pairs to sync
     * @returns {Promise<Object>}
     */
    async updateSceneData(sceneId, data) {
        const taskId = AppState.currentWorkState?.taskId;
        const res = await fetch(`${this._baseUrl()}/api/v1/workflow/node/update`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...this._authHeaders() },
            body: JSON.stringify({ scene_id: sceneId, data, ...(taskId ? { task_id: taskId } : {}) })
        });
        return res.json();
    }

    /**
     * Load workflow definitions from backend.
     * @returns {Promise<Object>} { success, data: { scenes: [...] } }
     */
    async getNodeDefinitions() {
        const res = await fetch(`${this._baseUrl()}/api/v1/workflow/definitions`, {
            headers: this._authHeaders(),
        });
        return res.json();
    }

    /**
     * Update node data to backend.
     * @param {string} nodeId - Node ID
     * @param {Object} data - Node data to update
     * @returns {Promise<Object>} { success, updated_fields }
     */
    async updateNodeData(nodeId, data) {
        const taskId = AppState.currentWorkState?.taskId;
        const res = await fetch(`${this._baseUrl()}/api/v1/workflow/node/update`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...this._authHeaders(),
            },
            body: JSON.stringify({ scene_id: nodeId, data, ...(taskId ? { task_id: taskId } : {}) }),
        });
        return res.json();
    }

    /**
     * Upload a photo (base64 dataUrl) to backend for persistent storage.
     * @param {string} nodeId - Node ID
     * @param {string} photoDataUrl - base64 data URL (e.g. "data:image/jpeg;base64,...")
     * @returns {Promise<{success: boolean, url: string}>}
     */
    async uploadNodePhoto(nodeId, photoDataUrl) {
        const taskId = AppState.currentWorkState?.taskId;
        if (!taskId) {
            console.warn('[ApiService.uploadNodePhoto] 无taskId，跳过上传', AppState.currentWorkState);
            return { success: false, message: '无当前任务ID' };
        }
        if (!photoDataUrl) {
            console.warn('[ApiService.uploadNodePhoto] photoDataUrl为空');
            return { success: false, message: '照片数据为空' };
        }
        const url = `${this._baseUrl()}/api/v1/workflow/photo/upload`;
        console.log(`[ApiService.uploadNodePhoto] POST ${url} nodeId=${nodeId} taskId=${taskId} dataLen=${photoDataUrl.length}`);
        try {
            const res = await fetch(url, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...this._authHeaders(),
                },
                body: JSON.stringify({ task_id: taskId, node_id: nodeId, photo_data: photoDataUrl }),
            });
            if (!res.ok) {
                const text = await res.text();
                console.error(`[ApiService.uploadNodePhoto] HTTP ${res.status}: ${text}`);
                return { success: false, message: `HTTP ${res.status}` };
            }
            const result = await res.json();
            console.log('[ApiService.uploadNodePhoto] 成功:', result);
            return result;
        } catch (e) {
            console.error('[ApiService.uploadNodePhoto] fetch异常:', e);
            return { success: false, message: e.message };
        }
    }
}

export default new ApiService();
