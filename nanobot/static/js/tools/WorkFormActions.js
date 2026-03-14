/**
 * WorkFormActions - generalized work form tool actions.
 * Replaces all inspection_* action handlers.
 */
import workFormManager from '../features/WorkFormManager.js';
import tabManager from '../features/TabManager.js';
import AppState from '../core/AppState.js';

class WorkFormActions {
    /**
     * Open work form (idempotent).
     */
    async openForm(params) {
        const { userId, workType, address, task_id, warnings, meterInfo, debtInfo, scheduleInfo, name, 
            collected_data, node_states, current_node, user_info } = params;
        console.log('[openForm] params.task_id=', task_id);
        // If task_id is provided, we're resuming an existing task
        const isResume = !!task_id;
        
        const taskData = isResume ? { 
            task_id,
            collected_data: collected_data || {},
            node_states: node_states || {},
            current_node: current_node || null,
            user_info: user_info || {},
            warnings: warnings || [],
            meter_info: meterInfo || {},
            debt_info: debtInfo || {},
            schedule_info: scheduleInfo || {}
        } : null;

        return workFormManager.open(userId, workType, address, {
            resume: isResume,
            taskData: taskData,
            warnings: warnings || [],
            meterInfo: meterInfo || {},
            debtInfo: debtInfo || {},
            scheduleInfo: scheduleInfo || {},
            name: name || ''
        });
    }

    /**
     * Update node status.
     */
    async updateNodeStatus(params) {
        const { node_id, status, skip_validation } = params;
        const result = workFormManager.updateNodeStatus(node_id, status, null, skip_validation);
        // If marked as completed, remind AI to call backend transition
        if (status === 'completed') {
            try {
                const parsed = JSON.parse(result);
                if (parsed.success) {
                    parsed._next_step_required = '✅ Frontend status updated. Next step must call mcp_workflow-engine_transition_to_next_node(task_id=...) to get next node, then call work_form_update_node_status(next_node_id, "active").';
                    return JSON.stringify(parsed);
                }
            } catch (e) { /* ignore parse error */ }
        }
        return result;
    }

    /**
     * Update scene field data.
     */
    async updateNodeFields(params) {
        const { node_id, fields } = params;
        return workFormManager.updateNodeFields(node_id, fields);
    }

    /**
     * Add hazard to a scene.
     */
    async addHazard(params) {
        const { node_id, level, message } = params;
        return workFormManager.addHazard(node_id, level, message);
    }

    /**
     * Show task list in the task list tab.
     */
    async showTaskList(params) {
        const { tasks, filter_summary, total } = params;
        return workFormManager.openTaskList({ tasks, filter_summary, total });
    }

    /**
     * Get current work form status (is it open on screen).
     */
    async getStatus() {
        return workFormManager.getStatus();
    }

    /**
     * Restore photo thumbnails for a node using remote URLs (from backend persistence).
     * Called during task resume to show previously uploaded photos.
     */
    async restoreNodePhotos(params) {
        const { node_id, photo_urls } = params;
        if (!node_id || !photo_urls || !Array.isArray(photo_urls) || photo_urls.length === 0) {
            return JSON.stringify({ success: false, message: '未提供照片URLs' });
        }
        // Import nodeRenderer dynamically to avoid circular deps
        const { default: nodeRenderer } = await import('../features/NodeRenderer.js');

        // Wait for the node photo container to appear in DOM (open_form is async)
        const waitForContainer = (selector, timeout = 5000) => new Promise((resolve) => {
            const el = document.querySelector(selector);
            if (el) { resolve(el); return; }
            const observer = new MutationObserver(() => {
                const found = document.querySelector(selector);
                if (found) { observer.disconnect(); resolve(found); }
            });
            observer.observe(document.body, { childList: true, subtree: true });
            setTimeout(() => { observer.disconnect(); resolve(null); }, timeout);
        });

        await waitForContainer(`[data-node-photos="${node_id}"]`);
        nodeRenderer.renderNodePhotos(node_id, photo_urls);
        return JSON.stringify({ success: true, node_id, restored: photo_urls.length });
    }

    /**
     * Get current active tab state.
     * Used by AI to query current page context.
     */
    async getActiveTabState() {
        const activeTab = tabManager.getActiveTab();
        if (!activeTab) {
            return JSON.stringify({
                success: true,
                hasActiveTab: false,
                message: '当前没有打开的页签'
            });
        }

        // Build response based on tab type
        const response = {
            success: true,
            hasActiveTab: true,
            tabId: activeTab.tabId,
            type: activeTab.type,
            title: activeTab.title
        };

        if (activeTab.type === 'work') {
            // Include work-specific state
            response.userId = activeTab.userId;
            response.address = activeTab.address;
            response.workType = activeTab.workType;
            response.workState = activeTab.workState;
            response.nodePhotos = Object.keys(activeTab.nodePhotos || {});
            response.nodeFieldsCache = activeTab.nodeFieldsCache;
            // 强制提示：节点转换必须调用后端路由
            response._transition_reminder = '⚠️ 节点转换规则：完成节点时必须先调用 mcp_workflow-engine_transition_to_next_node(task_id=...) 获取下一节点ID，禁止根据此处数据自行推断下一节点。';
        } else if (activeTab.type === 'task-list') {
            response.message = '当前在任务列表页签';
        }

        return JSON.stringify(response);
    }

    /**
     * Get all open tabs summary.
     */
    async getAllTabsSummary() {
        const tabs = tabManager.getAllTabs();
        const activeTabId = AppState.activeTabId;
        
        const summary = tabs.map(tab => ({
            tabId: tab.tabId,
            type: tab.type,
            title: tab.title,
            isActive: tab.tabId === activeTabId,
            userId: tab.userId || null
        }));

        return JSON.stringify({
            success: true,
            count: tabs.length,
            activeTabId,
            tabs: summary
        });
    }

    /**
     * Switch to a specific tab by ID.
     */
    async switchTab(params) {
        const { tabId } = params;
        if (!tabId) {
            return JSON.stringify({ success: false, message: '未指定 tabId' });
        }
        
        if (!tabManager.hasTab(tabId)) {
            return JSON.stringify({ success: false, message: `页签 ${tabId} 不存在` });
        }

        tabManager.activateTab(tabId);
        return JSON.stringify({ success: true, tabId, message: `已切换到页签 ${tabId}` });
    }

    /**
     * Close a specific tab by ID.
     */
    async closeTab(params) {
        const { tabId } = params;
        if (!tabId) {
            return JSON.stringify({ success: false, message: '未指定 tabId' });
        }

        if (!tabManager.hasTab(tabId)) {
            return JSON.stringify({ success: false, message: `页签 ${tabId} 不存在` });
        }

        tabManager.closeTab(tabId);
        return JSON.stringify({ success: true, tabId, message: `已关闭页签 ${tabId}` });
    }


}

export default new WorkFormActions();
