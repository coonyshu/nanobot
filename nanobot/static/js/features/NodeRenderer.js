/**
 * NodeRenderer - pure view module for rendering node list HTML.
 * Shared between desktop (embedded) and mobile (modal) modes.
 */
import AppState from '../core/AppState.js';

class NodeRenderer {
    /**
     * Generate user info HTML.
     */
    renderUserInfo(userId, address) {
        return `
            <div class="work-form-user-info">
                <div class="info-row">
                    <div class="info-item">
                        <span class="info-label">用户号:</span>
                        <span class="info-value">${userId || '未知'}</span>
                    </div>
                    <div class="info-item">
                        <span class="info-label">地址:</span>
                        <span class="info-value">${address || '未提供'}</span>
                    </div>
                </div>
            </div>
        `;
    }

    /**
     * Generate node list HTML.
     * @param {Array} nodes - Node definitions
     * @param {string|null} currentNodeId - Currently active node ID
     * @returns {string} HTML string
     */
    renderNodeList(nodes, currentNodeId) {
        return nodes.map((node, index) => {
            const isFirst = (currentNodeId ? node.id === currentNodeId : index === 0);
            const statusClass = isFirst ? 'active' : 'pending';
            const statusText = isFirst ? '进行中' : '待检';
            return `
                <div class="work-form-node-item ${statusClass}" data-node-id="${node.id}">
                    <div class="node-order">${node.order}</div>
                    <div class="node-info">
                        <div class="node-name">${node.name}${node.canSkip ? ' <span style="font-size:11px;color:#999">(可跳过)</span>' : ''}</div>
                        <div class="node-purpose">${node.purpose}</div>
                        <div class="node-fields" data-node-fields="${node.id}"></div>
                        <div class="node-photos" data-node-photos="${node.id}"></div>
                        <button class="node-next-btn" data-action="advance-to-next-node" data-node-id="${node.id}" style="display:none">进入下一场景 →</button>
                    </div>
                    <span class="node-status ${statusClass}">${statusText}</span>
                    <button class="node-upload-btn" data-action="pick-node-image" data-node-id="${node.id}" title="拍照/上传">
                        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
                            <circle cx="12" cy="13" r="4"/>
                        </svg>
                    </button>
                </div>
            `;
        }).join('');
    }

    /**
     * Render photo thumbnails in a node's photo container.
     * Supports both local dataUrl photos (AppState.nodePhotos) and remote URL photos.
     * @param {string} nodeId
     * @param {string[]} [remoteUrls] - Optional array of remote photo URLs to display
     */
    renderNodePhotos(nodeId, remoteUrls) {
        const container = document.querySelector(`[data-node-photos="${nodeId}"]`);
        if (!container) return;
        container.innerHTML = '';

        // Render local (dataUrl) photos first
        const localPhotos = AppState.nodePhotos[nodeId] || [];
        localPhotos.forEach((p) => {
            const img = document.createElement('img');
            img.className = 'node-photo-thumb';
            img.src = p.dataUrl;
            img.addEventListener('click', () => this.showPhotoPreview(p.dataUrl));
            container.appendChild(img);
        });

        // Render remote URL photos (from backend persistence)
        const urls = remoteUrls || [];
        urls.forEach((url) => {
            // Skip if we already have a local photo for this node (avoid duplicates)
            if (localPhotos.length > 0) return;
            const img = document.createElement('img');
            img.className = 'node-photo-thumb';
            img.src = url;
            img.addEventListener('click', () => this.showPhotoPreview(url));
            container.appendChild(img);
        });
    }

    /**
     * Show full-screen photo preview overlay.
     */
    showPhotoPreview(dataUrl) {
        const overlay = document.createElement('div');
        overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.9);z-index:9999;display:flex;align-items:center;justify-content:center;cursor:pointer';
        overlay.innerHTML = `<img src="${dataUrl}" style="max-width:90%;max-height:90%;object-fit:contain;border-radius:8px">`;
        overlay.addEventListener('click', () => overlay.remove());
        document.body.appendChild(overlay);
    }
}

export default new NodeRenderer();
