/**
 * WorkFormManager - generalized work order form management.
 * Replaces all work-specific functions with workType-parameterized logic.
 * Handles: open, close, reopen, node updates, completion, photo management.
 */
import AppState from '../core/AppState.js';
import eventBus from '../core/EventBus.js';
import apiService from '../services/ApiService.js';
import nodeRenderer from './NodeRenderer.js';
import chatManager from './ChatManager.js';
import tabManager from './TabManager.js';

class WorkFormManager {
    constructor() {
        // Will be loaded async from backend
        this._nodesLoaded = false;


    }

    /**
     * Load node definitions from backend API.
     * Populates AppState.nodes and AppState.fieldDefinitions.
     */
    async loadNodeDefinitions() {
        try {
            const result = await apiService.getNodeDefinitions();

            // API returns { success, data: { nodes: [...] } }
            // Support both 'nodes' (new) and 'nodes' (legacy) field names
            const nodes = result.data?.nodes || result.data?.nodes;

            if (result.success && nodes) {
                AppState.nodes = nodes.map(s => ({
                    id: s.id,
                    order: s.order,
                    name: s.name,
                    purpose: s.purpose || s.node_description || '',
                    canSkip: s.can_skip || false,
                    requiredFields: s.required_fields || [],
                    optionalFields: s.optional_fields || [],
                    fieldDefinitions: s.field_definitions || {}
                }));

                AppState.fieldDefinitions = {};
                for (const node of nodes) {
                    if (node.field_definitions) {
                        AppState.fieldDefinitions[node.id] = {};
                        for (const [fieldKey, fieldDef] of Object.entries(node.field_definitions)) {
                            AppState.fieldDefinitions[node.id][fieldKey] = {
                                label: fieldDef.label || fieldKey,
                                type: fieldDef.type || 'string',
                                description: fieldDef.description || '',
                                options: fieldDef.options || null,
                                unit: fieldDef.unit || null,
                                default: fieldDef.default || null,
                                ai_extract_patterns: (fieldDef.ai_extract_patterns || []).map(p => new RegExp(p, 'i'))
                            };
                        }
                    }
                }

                this._nodesLoaded = true;
                eventBus.emit('log', { msg: `场景定义加载成功: ${AppState.nodes.length} 个场景`, type: 'success' });
            } else {
                throw new Error(result.error || '加载失败');
            }
        } catch (e) {
            eventBus.emit('log', { msg: `场景定义加载失败: ${e.message}`, type: 'error' });
        }
    }

    /**
     * @deprecated Use tabManager.ensureTabBar() instead.
     * Kept for backward compatibility only.
     */
    _ensureWorkPanelWrapper() {
        return tabManager.ensureTabBar();
    }

    /**
     * @deprecated Use tabManager.activateTab() instead.
     * Kept for backward compatibility during transition.
     */
    _switchTab(tabId) {
        tabManager.activateTab(tabId);
    }

    /**
     * Public method for app.js to call when user clicks tab.
     * @param {string} tabId - Tab ID to activate
     */
    switchTabByUser(tabId) {
        tabManager.activateTab(tabId);
    }

    /**
     * Open task list in a dedicated tab.
     * @param {Object} options - { tasks, filter_summary, total }
     * @returns {string} JSON result
     */
    openTaskList({ tasks, filter_summary, total }) {
        // Use TabManager to open or activate task-list tab
        const tabId = tabManager.openOrActivate('task-list', {});
        
        // Get the tab pane for this tab
        const pane = tabManager.getTabPane(tabId);
        if (!pane) return JSON.stringify({ success: false, message: '找不到任务列表面板' });
        
        // Render task list content
        pane.innerHTML = this._renderTaskList(tasks, filter_summary, total);
        
        // Update tab title with count
        tabManager.updateTabTitle(tabId, `任务列表(${tasks.length})`);
        
        eventBus.emit('log', { msg: `显示任务列表: ${tasks.length}条`, type: 'success' });
        return JSON.stringify({ success: true, count: tasks.length, message: `已显示${tasks.length}条任务` });
    }

    /**
     * Render task list HTML.
     * @param {Array} tasks - Task list
     * @param {string} filterSummary - Filter description
     * @param {number} total - Total count before filtering
     * @returns {string} HTML string
     */
    _renderTaskList(tasks, filterSummary, total) {
        if (!tasks || tasks.length === 0) {
            return `
                <div class="task-list-filter-bar">
                    <span class="filter-text">${filterSummary || '无匹配结果'}</span>
                </div>
                <div class="task-list-empty">暂无匹配的任务</div>
            `;
        }
        
        const filterBar = `
            <div class="task-list-filter-bar">
                <span class="filter-icon">🔍</span>
                <span class="filter-text">${filterSummary}</span>
                <span class="filter-count">${tasks.length}/${total}</span>
            </div>
        `;
        
        const items = tasks.map(task => {
            const badges = (task.tags || []).map(tag => {
                let cls = 'task-badge';
                if (tag === '欠费') cls += ' debt';
                else if (tag === '红色隐患') cls += ' hazard-red';
                else if (tag === '黄色隐患') cls += ' hazard-yellow';
                else if (tag === '计划内') cls += ' scheduled';
                return `<span class="${cls}">${tag}</span>`;
            }).join('');
            
            return `
                <div class="task-list-item">
                    <div class="task-list-item-main">
                        <div class="task-list-item-name">${task.name || '未知'}</div>
                        <div class="task-list-item-address">${task.address}</div>
                        <div class="task-list-item-badges">${badges}</div>
                    </div>
                    <button class="task-list-start-btn" data-action="start-task-from-list" 
                            data-user-id="${task.user_id}" data-address="${task.address}">
                        开始
                    </button>
                </div>
            `;
        }).join('');
        
        return filterBar + `<div class="task-list-container">${items}</div>`;
    }

    /**
     * Open the work form (idempotent).
     * @param {string} userId
     * @param {string} workType - e.g. '安检', '挂表', '点火'
     * @param {string} address
     * @param {Object} options - { warnings, meterInfo, debtInfo, scheduleInfo, name, resume, taskData }
     * @returns {string} JSON result for action dispatcher
     */
    open(userId, workType, address, options = {}) {
        const { warnings = [], meterInfo = {}, debtInfo = {}, scheduleInfo = {}, name = '', resume = false, taskData = null } = options;

        // Check if tab already exists for this user
        const existingTabId = tabManager.getworkTabId(userId);
        if (existingTabId) {
            // Activate existing tab
            tabManager.activateTab(existingTabId);
            // If resuming, ensure taskId is updated in currentWorkState (for photo upload)
            if (resume && taskData?.task_id) {
                if (AppState.currentWorkState) {
                    AppState.currentWorkState.taskId = taskData.task_id;
                    eventBus.emit('log', { msg: `更新当前任务ID: ${taskData.task_id}`, type: 'info' });
                } else {
                    // Re-initialize work state with taskId
                    this._restoreWorkState(userId, workType, address, taskData);
                }
            }
            eventBus.emit('log', { msg: `工作表单已打开，切换到已有页签: userId=${userId}`, type: 'info' });
            return JSON.stringify({
                success: true, userId, workType, address,
                message: `${workType}表单已打开，继续当前任务`,
                alreadyOpen: true
            });
        }

        // Save for reopening
        window._lastWorkForm = { userId, workType, address, options };

        // If resuming existing task, restore state
        if (resume && taskData) {
            this._restoreWorkState(userId, workType, address, taskData);
        }

        // Detect desktop vs mobile
        const isDesktop = window.innerWidth >= 768;
        console.log('[open] resume=', resume, 'initState=', !resume, 'taskId=', taskData?.task_id);
        const openOptions = { warnings, meterInfo, debtInfo, scheduleInfo, name, initState: !resume, taskId: taskData?.task_id || null };
        if (isDesktop) {
            this._openInOperationPanel(userId, workType, address, openOptions);
        } else {
            this._openAsModal(userId, workType, address, openOptions);
        }

        // Add system message with reopen button
        const msg = address
            ? `已打开 ${address} ${workType}表单`
            : `已打开用户 ${userId} ${workType}表单`;
        chatManager.addMessageWithReopenButton(msg, userId, workType, address);

        eventBus.emit('log', { msg: `打开${workType}表单: userId=${userId}`, type: 'success' });
        eventBus.emit('workform:opened', { userId, workType, address });

        return JSON.stringify({
            success: true, userId, workType, address,
            message: `已打开${workType}表单`
        });
    }

    /**
     * Get current work form status: whether it's open and current task info.
     * Used by AI to determine if the form needs to be reopened.
     */
    getStatus() {
        // Check if there are any work tabs open
        const workTabs = Object.values(AppState.workTabs).filter(tab => tab.type === 'work');
        const hasWorkTabs = workTabs.length > 0;
        
        // Get current work state
        const state = AppState.currentWorkState;
        const isOpen = !!(state && state.userId);
        
        // Check if the DOM container is actually present
        const domPresent = !!(document.querySelector('.work-form-embedded') || document.querySelector('.work-form-modal') || document.querySelector('.work-form-panel'));
        
        // Check if current active tab is a work tab
        const activeTab = tabManager.getActiveTab();
        const tabActive = activeTab && activeTab.type === 'work';
        
        // Get task info from current work state
        const taskId = state?.taskId || null;
        const userId = state?.userId || null;
        const workType = state?.workType || null;
        const address = state?.address || null;
        const currentNode = state?.currentNode || null;
        
        // Determine if form is open
        const formOpen = (isOpen || hasWorkTabs) && domPresent;
        
        return JSON.stringify({
            success: true,
            is_open: formOpen,
            tab_active: tabActive,
            task_id: taskId,
            user_id: userId,
            work_type: workType,
            address: address,
            current_node: currentNode,
            work_tabs_count: workTabs.length
        });
    }

    /**
     * Get filtered nodes based on visit_type.
      * - 正常入户: 显示所有场景（除了 node_door_gap_test）
      * - 到访不遇: 只显示 node_door 和 node_door_gap_test
     */
    _getFilteredNodes() {
        const nodes = AppState.nodes || [];
        const visitType = AppState.currentWorkState?.nodeFields?.scene_door?.visit_type || '正常入户';

        if (visitType === '到访不遇') {
            // 到访不遇：只显示入户门和门缝测漏
            return nodes.filter(s => s.id === 'scene_door' || s.id === 'scene_door_gap_test');
        }
        // 正常入户：显示所有场景，但排除门缝测漏
        return nodes.filter(s => s.id !== 'scene_door_gap_test');
    }

    /**
     * Open form in the left operation panel (desktop mode).
     */
    _openInOperationPanel(userId, workType, address, options = {}) {
        const { warnings = [], meterInfo = {}, name = '', initState = true, taskId = null } = options;
        const panelHeader = document.querySelector('.operation-panel-header h2');

        // Update panel header
        if (panelHeader) {
            panelHeader.textContent = `${workType || '工作'}任务`;
        }

        // Use TabManager to open or activate work tab
        const tabId = tabManager.openOrActivate('work', {
            userId,
            workType,
            address,
            name,
            taskId,
            warnings: options.warnings,
            meterInfo: options.meterInfo,
            debtInfo: options.debtInfo,
            scheduleInfo: options.scheduleInfo
        });

        // Get the tab pane for this tab
        const tabPane = tabManager.getTabPane(tabId);
        if (!tabPane) return;

        // 只有需要新初始化时才调用（恢复模式下不重复初始化）
        if (initState) {
            this._initWorkState(userId, workType, address, options);
        }

        // Remove existing form content in this pane
        const existing = tabPane.querySelector('.work-form-embedded');
        if (existing) existing.remove();

        // Create form container
        const formContainer = document.createElement('div');
        formContainer.className = 'work-form-embedded';
        formContainer.id = 'workFormContainer';
        formContainer.dataset.tabId = tabId;

        const userInfoHtml = nodeRenderer.renderUserInfo(userId, address);
        const warningsHtml = this._renderWarnings(warnings);
        const meterInfoHtml = this._renderMeterInfo(meterInfo);
        const filteredNodes = this._getFilteredNodes();
        const nodesHtml = nodeRenderer.renderNodeList(filteredNodes, AppState.currentWorkState.currentNode);

        formContainer.innerHTML = `
            ${userInfoHtml}
            ${warningsHtml}
            ${meterInfoHtml}
            <div class="work-form-body">
                <ul class="work-form-nodes-list">
                    ${nodesHtml}
                </ul>
            </div>
            <div class="work-form-modal-footer">
                <button class="work-form-btn work-form-btn-secondary" data-action="close-work-form">关闭</button>
                <button class="work-form-btn work-form-btn-primary" data-action="complete-work">完成${workType}</button>
            </div>
        `;

        // Clear pane and add form
        tabPane.innerHTML = '';
        tabPane.appendChild(formContainer);
    }

    /**
     * Open form as modal (mobile mode).
     */
    _openAsModal(userId, workType, address, options = {}) {
        const { warnings = [], meterInfo = {}, initState = true } = options;
        // 只有需要新初始化时才调用（恢复模式下不重复初始化）   
        if (initState) {
            this._initWorkState(userId, workType, address, options);
        }

        // Remove existing modal
        const existing = document.querySelector('.work-form-modal');
        if (existing) existing.remove();

        const filteredNodes = this._getFilteredNodes();
        const nodesHtml = nodeRenderer.renderNodeList(filteredNodes, AppState.currentWorkState.currentNode);
        const warningsHtml = this._renderWarnings(warnings);
        const meterInfoHtml = this._renderMeterInfo(meterInfo);

        const modal = document.createElement('div');
        modal.className = 'work-form-modal';
        modal.innerHTML = `
            <div class="work-form-modal-overlay"></div>
            <div class="work-form-modal-content">
                <div class="work-form-modal-header">
                    <h3>${workType || '工作'}任务</h3>
                    <button class="work-form-modal-close" data-action="close-work-form-modal">\u2715</button>
                </div>
                <div class="work-form-user-info">
                    <div class="info-row">
                        <div class="info-item">
                            <span class="info-label">用户:</span>
                            <span class="info-value">${userId || '未知'}</span>
                        </div>
                        <div class="info-item">
                            <span class="info-label">地址:</span>
                            <span class="info-value">${address || '未提供'}</span>
                        </div>
                    </div>
                </div>
                ${warningsHtml}
                ${meterInfoHtml}
                <div class="work-form-modal-body" style="padding: 0;">
                    <ul class="work-form-nodes-list">
                        ${nodesHtml}
                    </ul>
                </div>
                <div class="work-form-modal-footer">
                    <button class="work-form-btn work-form-btn-secondary" data-action="close-work-form-modal">关闭</button>
                    <button class="work-form-btn work-form-btn-primary" data-action="complete-work-modal">完成${workType}</button>
                </div>
            </div>
        `;

        document.body.appendChild(modal);
    }

    /**
     * Initialize work state for a new form.
     */
    _initWorkState(userId, workType, address, options = {}) {
        const { taskId = null, warnings = [], meterInfo = {}, debtInfo = {}, scheduleInfo = {} } = options;
        console.log('[_initWorkState] called, taskId=', taskId);
        // 从场景定义中获取 visit_type 的默认值
        const doornode = AppState.nodes.find(s => s.id === 'scene_door');
        const visitTypeDefault = doornode?.fieldDefinitions?.visit_type?.default || '正常入户';

        AppState.currentWorkState = {
            userId,
            address,
            workType: workType || '工作',
            currentNode: AppState.nodes.length > 0 ? AppState.nodes[0].id : null,
            completedNodes: [],
            hazards: [],
            taskId,
            // Additional info
            warnings,
            meterInfo,
            debtInfo,
            scheduleInfo,
            // 场景字段值（包含 visit_type）
            nodeFields: {
                scene_door: { visit_type: visitTypeDefault }
            }
        };
        AppState.nodePhotos = {};
        // 清空场景字段缓存
        AppState.nodeFieldsCache = {};
    }

    /**
     * Restore work state for resuming an existing task.
     * @param {string} userId
     * @param {string} workType
     * @param {string} address
     * @param {Object} taskData - { task_id, current_node, node_states, collected_data, warnings, meter_info, debt_info, schedule_info, user_info }
     */
    _restoreWorkState(userId, workType, address, taskData) {
        console.log('[_restoreWorkState] taskData=', taskData);
        // 从场景定义中获取 visit_type 的默认值
        const doornode = AppState.nodes.find(s => s.id === 'scene_door');
        const visitTypeDefault = doornode?.fieldDefinitions?.visit_type?.default || '正常入户';

        // 从已采集的数据中提取 visit_type 值
        const collectedData = taskData.collected_data || {};
        const doorData = collectedData.scene_door || {};
        const visitType = doorData.visit_type || visitTypeDefault;

        // 计算已完成的场景列表
        const completedNodes = [];
        const nodeStates = taskData.node_states || {};
        for (const [nodeId, status] of Object.entries(nodeStates)) {
            if (status === 'completed' || status === 'skipped') {
                completedNodes.push(nodeId);
            }
        }

        console.log('[_restoreWorkState] setting taskId=', taskData.task_id);
        AppState.currentWorkState = {
            userId,
            address,
            workType: workType || '工作',
            currentNode: taskData.current_node || (AppState.nodes.length > 0 ? AppState.nodes[0].id : null),
            completedNodes,
            hazards: taskData.current_alerts || [],
            taskId: taskData.task_id,
            // Additional info
            warnings: taskData.warnings || [],
            meterInfo: taskData.meter_info || {},
            debtInfo: taskData.debt_info || {},
            scheduleInfo: taskData.schedule_info || {},
            userInfo: taskData.user_info || {},
            // 场景字段值（包含 visit_type）
            nodeFields: {
                scene_door: { visit_type: visitType, ...doorData }
            }
        };
        console.log('[_restoreWorkState] AppState.currentWorkState=', AppState.currentWorkState);
        console.log('[_restoreWorkState] AppState.currentWorkState.taskId=', AppState.currentWorkState?.taskId);

        // 恢复场景字段缓存
        AppState.nodeFieldsCache = {};
        for (const [nodeId, data] of Object.entries(collectedData)) {
            AppState.nodeFieldsCache[nodeId] = { ...data };
        }

        AppState.nodePhotos = {};
        eventBus.emit('log', { msg: `恢复任务状�? taskId=${taskData.task_id}, currentNode=${taskData.current_node}, completed=${completedNodes.length}`, type: 'info' });
    }

    /**
     * Render warnings banner HTML.
     */
    _renderWarnings(warnings) {
        if (!warnings || warnings.length === 0) return '';

        const items = warnings.map(w => {
            let iconClass = 'info';
            let icon = 'ℹ️';
            if (w.level === 'red') {
                iconClass = 'danger';
                icon = '🚨';
            } else if (w.level === 'yellow') {
                iconClass = 'warning';
                icon = '⚠️';
            }
            return `<div class="work-form-warning-item ${iconClass}"><span class="warning-icon">${icon}</span><span class="warning-text">${w.message}</span></div>`;
        }).join('');

        return `<div class="work-form-warnings">${items}</div>`;
    }

    /**
     * Render meter info HTML.
     */
    _renderMeterInfo(meterInfo) {
        if (!meterInfo || !meterInfo.meter_number) return '';

        const parts = [`<span class="meter-label">表号:</span><span class="meter-value">${meterInfo.meter_number}</span>`];
        
        if (meterInfo.previous_reading !== undefined && meterInfo.previous_reading !== null) {
            parts.push(`<span class="meter-label">上期读数:</span><span class="meter-value">${meterInfo.previous_reading}</span>`);
        }
        
        if (meterInfo.last_work_date) {
            parts.push(`<span class="meter-label">上次安检:</span><span class="meter-value">${meterInfo.last_work_date}</span>`);
        }

        return `<div class="work-form-meter-info">${parts.join('')}</div>`;
    }

    /**
     * Update a node's status and optional hazard.
     * @param {string} nodeId - Node ID
     * @param {string} status - Status: pending, active, completed
     * @param {Object} hazard - Optional hazard info
     * @param {boolean} skipValidation - If true, skip required fields validation (for jump scenario)
     * @returns {string} JSON result for action dispatcher
     */
    updateNodeStatus(nodeId, status, hazard, skipValidation = false) {
        // Check required fields only if not skipping validation
        if (status === 'completed' && !skipValidation) {
            const check = this._checkRequiredFields(nodeId);
            if (!check.complete) {
                const node = AppState.nodes.find(s => s.id === nodeId);
                const fieldDefs = AppState.fieldDefinitions?.[nodeId] || {};
                const missingLabels = check.missing.map(key => fieldDefs[key]?.label || key);
                const message = `Cannot complete: ${node?.name || nodeId} has incomplete required fields ${missingLabels.join(', ')}`;
                eventBus.emit('log', { msg: message, type: 'warning' });
                return JSON.stringify({ success: false, error: 'incomplete_fields', node_id: nodeId, missing_fields: check.missing, message });
            }
        }

        const nodeItem = document.querySelector(`.work-form-node-item[data-node-id="${nodeId}"]`);
        
        // Update DOM if node exists
        if (nodeItem) {
            nodeItem.classList.remove('pending', 'active', 'completed');
            nodeItem.classList.add(status);
            console.log(`[updateNodeStatus] Node ${nodeId} classes updated:`, nodeItem.className);

            const statusEl = nodeItem.querySelector('.node-status');
            if (statusEl) {
                statusEl.classList.remove('pending', 'active', 'completed');
                statusEl.classList.add(status);
                statusEl.textContent = status === 'completed' ? '已完成' : (status === 'active' ? '进行中' : '待检查');
            }

            if (hazard) {
                const existing = nodeItem.querySelector('.hazard-badge');
                if (!existing) {
                    const badge = document.createElement('span');
                    badge.className = `hazard-badge ${hazard.level}`;
                    badge.textContent = hazard.level === 'red' ? '红色隐患' : '黄色隐患';
                    nodeItem.querySelector('.node-info').appendChild(badge);
                }
            }
        }
        
        // Always update work state regardless of DOM presence
        if (hazard) {
            AppState.currentWorkState.hazards.push({ nodeId, ...hazard });
        }

        if (status === 'completed' && !AppState.currentWorkState.completedNodes.includes(nodeId)) {
            AppState.currentWorkState.completedNodes.push(nodeId);
            // Clear currentNode if this node was active
            if (AppState.currentWorkState.currentNode === nodeId) {
                AppState.currentWorkState.currentNode = null;
            }
        }
        if (status === 'active') {
            AppState.currentWorkState.currentNode = nodeId;
        }

        eventBus.emit('log', { msg: `场景 ${nodeId} 状态更新为 ${status}`, type: 'info' });
        return JSON.stringify({ success: true, node_id: nodeId, status, message: `场景状态已更新为 ${status}` });
    }

    /**
     * Check if all required fields for the current node are completed.
     * @param {string} nodeId - node ID to check
     * @returns {Object} { complete: boolean, missing: string[] }
     */
    _checkRequiredFields(nodeId) {
        const node = AppState.nodes.find(s => s.id === nodeId);
        if (!node) return { complete: true, missing: [] };

        const requiredFields = node.requiredFields || [];
        if (requiredFields.length === 0) return { complete: true, missing: [] };

        const nodeData = AppState.nodeFieldsCache?.[nodeId] || {};
        const missing = [];

        for (const fieldKey of requiredFields) {
            const value = nodeData[fieldKey];
            // Check if field is empty (null, undefined, empty string)
            if (value === null || value === undefined || value === '') {
                missing.push(fieldKey);
            }
        }

        return {
            complete: missing.length === 0,
            missing
        };
    }

    /**
     * Check if node has all required fields filled and show/hide the next-scene button.
     * @param {string} nodeId
     */


    /**
     * Advance to the next node in sequence.
     * Only proceeds if all required fields of current node are completed.
     */
    advanceToNextNode() {
        const currentNodeId = AppState.currentWorkState.currentNode;
        const check = this._checkRequiredFields(currentNodeId);

        if (!check.complete) {
            const node = AppState.nodes.find(s => s.id === currentNodeId);
            const fieldDefs = AppState.fieldDefinitions?.[currentNodeId] || {};
            const missingLabels = check.missing.map(key => {
                const def = fieldDefs[key];
                return def?.label || key;
            });

            eventBus.emit('log', {
                msg: `无法跳转: ${node?.name || currentNodeId} 还有未完成的必填字段 ${missingLabels.join(', ')}`,
                type: 'warning'
            });
            return false;
        }

        const currentIndex = AppState.nodes.findIndex(s => s.id === currentNodeId);
        if (currentIndex >= 0 && currentIndex < AppState.nodes.length - 1) {
            this.updateNodeStatus(currentNodeId, 'completed');
            const nextnode = AppState.nodes[currentIndex + 1];
            this.updateNodeStatus(nextnode.id, 'active');
            // Reset next scene button flag for new node
            AppState.nextSceneButtonShown = false;
            return true;
        }
        return false;
    }

    /**
     * Complete the work order.
     */
    completeWork() {
        const ws = AppState.currentWorkState;
        const completedCount = ws.completedNodes.length;
        const totalCount = AppState.nodes.length;
        const hazardCount = ws.hazards.length;
        const workType = ws.workType || '工作';

        let resultMsg = `${workType}完成！已检查 ${completedCount}/${totalCount} 个场景`;
        if (hazardCount > 0) {
            const redCount = ws.hazards.filter(h => h.level === 'red').length;
            const yellowCount = ws.hazards.filter(h => h.level === 'yellow').length;
            resultMsg += `\n发现隐患 ${hazardCount} 项`;
            if (redCount > 0) resultMsg += `（红色 ${redCount} 项`;
            if (yellowCount > 0) resultMsg += `${redCount > 0 ? '、' : ''}黄色 ${yellowCount} 项`;
            resultMsg += '。';
        }

        chatManager.addMessage(resultMsg, 'system');
        eventBus.emit('log', { msg: `${workType}完成: ${completedCount}/${totalCount} 个场景，发现 ${hazardCount} 项隐患`, type: 'success' });
        this.close();
    }

    /**
     * Generate a photo analysis prompt for the current node.
     * Includes field schema and JSON output instruction so AI returns structured data.
     * @returns {string} Prompt with workType context and field extraction requirements
     */
    getWorkPhotoPrompt() {
        const ws = AppState.currentWorkState;
        if (!ws.userId) return '';

        const node = AppState.nodes.find(s => s.id === ws.currentNode);
        if (!node) return '';

        const workType = ws.workType || '工作';
        let prompt = `【${workType}拍照】当前正在检查"${node.name}"场景，地址：${ws.address || '未知'}。请基于当前${workType}场景分析这张照片，判断是否符合该场景要求。`;

        // Build field schema from node definitions
        const fieldDefs = AppState.fieldDefinitions[node.id];
        const allFields = [...(node.requiredFields || []), ...(node.optionalFields || [])];
        const fieldLines = [];

        for (const fieldKey of allFields) {
            const def = fieldDefs ? fieldDefs[fieldKey] : null;
            if (!def || def.type === 'photo') continue;

            let typeDesc = '';
            switch (def.type) {
                case 'enum':
                    typeDesc = `选项: [${(def.options || []).join(', ')}]`;
                    break;
                case 'number':
                    typeDesc = `数字${def.unit ? '，单位' + def.unit : ''}`;
                    break;
                case 'boolean':
                    typeDesc = 'true/false';
                    break;
                default:
                    typeDesc = '字符串';
            }
            const isOptional = (node.optionalFields || []).includes(fieldKey);
            fieldLines.push(`- ${fieldKey} ${def.label} ${typeDesc}${isOptional ? '，可选' : ''}）：${def.description}`);
        }

        if (fieldLines.length > 0) {
            prompt += `\n\n请从照片中识别以下字段信息：\n${fieldLines.join('\n')}`;
        } else {
            prompt += '\n\n本场景仅需拍照确认，fields留空{}。';
        }

        prompt += '\n\n请在回复末尾输出如下JSON代码块（不要省略）：\n```json\n{"photo_valid": true, "fields": {"field_key": "识别到的值"}, "reason": "一句话说明"}\n```\n规则：photo_valid表示照片是否符合场景要求；无法识别的字段填null；布尔值用true/false；数字不加引号；枚举值必须从给定选项中选择。';

        return prompt;
    }

    /**
     * Close the form (operation panel mode).
     * Closes the currently active work tab.
     */
    close() {
        const panelHeader = document.querySelector('.operation-panel-header h2');

        // Get active tab
        const activeTab = tabManager.getActiveTab();
        if (activeTab && activeTab.type === 'work') {
            // Close the active work tab
            tabManager.closeTab(activeTab.tabId);
        }

        // Update header if no tabs remain
        if (!tabManager.getActiveTab()) {
            if (panelHeader) panelHeader.textContent = '工作';
        }

        eventBus.emit('workform:closed');
    }

    /**
     * Close a specific work tab by userId.
     * @param {string} userId - User ID
     */
    closeByUserId(userId) {
        const tabId = tabManager.getworkTabId(userId);
        if (tabId) {
            tabManager.closeTab(tabId);
            eventBus.emit('workform:closed');
        }
    }

    /**
     * Close the modal form (mobile mode).
     */
    closeModal() {
        const modal = document.querySelector('.work-form-modal');
        if (modal) modal.remove();
        eventBus.emit('workform:closed');
    }

    /**
     * Get avatar emoji based on agent type.
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
     * Reopen the form or activate existing tab.
     */
    reopen(userId, workType, address) {
        // Check if tab already exists
        const existingTabId = tabManager.getworkTabId(userId);
        if (existingTabId) {
            tabManager.activateTab(existingTabId);
            eventBus.emit('log', { msg: `激活已有的${workType}页签: userId=${userId}`, type: 'info' });
            return;
        }
        
        const isDesktop = window.innerWidth >= 768;
        if (isDesktop) {
            this._openInOperationPanel(userId, workType, address, {});
        } else {
            this._openAsModal(userId, workType, address, {});
        }
        eventBus.emit('log', { msg: `重新打开${workType}表单: userId=${userId}`, type: 'info' });
    }

    /**
     * Pick a photo for a specific node.
     */
    pickNodeImage(nodeId) {
        AppState.currentUploadingnode = nodeId;
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = 'image/*';
        input.addEventListener('change', (e) => this._onnodeFileSelected(e));
        input.click();
    }

    _onnodeFileSelected(e) {
        const file = e.target.files[0];
        if (!file || !AppState.currentUploadingnode) return;
        if (!file.type.startsWith('image/') || file.size > 10 * 1024 * 1024) {
            eventBus.emit('log', { msg: '请选择10MB以内的图片文件', type: 'warning' });
            AppState.currentUploadingnode = null;
            return;
        }

        const reader = new FileReader();
        reader.onload = (ev) => {
            const sid = AppState.currentUploadingnode;
            const dataUrl = ev.target.result;
            if (!AppState.nodePhotos[sid]) AppState.nodePhotos[sid] = [];
            AppState.nodePhotos[sid].push({ dataUrl: dataUrl, timestamp: Date.now() });
            nodeRenderer.renderNodePhotos(sid);
            eventBus.emit('log', { msg: `场景 ${sid} 已添加图片 ${AppState.nodePhotos[sid].length} 张`, type: 'success' });
            AppState.currentUploadingnode = null;

            // Upload photo to backend for persistent storage (async, non-blocking)
            const taskId = AppState.currentWorkState?.taskId;
            eventBus.emit('log', { msg: `[_onNodeFileSelected] 开始上传 nodeId=${sid} taskId=${taskId} dataLen=${dataUrl?.length}`, type: 'info' });
            apiService.uploadNodePhoto(sid, dataUrl).then(result => {
                eventBus.emit('log', { msg: `[_onNodeFileSelected] 上传响应: ${JSON.stringify(result)}`, type: result.success ? 'success' : 'warning' });
            }).catch(e => {
                eventBus.emit('log', { msg: `[_onNodeFileSelected] 上传异常: ${e.message}`, type: 'error' });
            });
        };
        reader.readAsDataURL(file);
    }

    /**
     * Update node field values display.
     * @returns {string} JSON result
     */
    updateNodeFields(nodeId, fields) {
        if (!fields || typeof fields !== 'object') {
            return JSON.stringify({ success: false, message: '未提供字段数据' });
        }

        const container = document.querySelector(`.node-fields[data-node-fields="${nodeId}"]`);
        const existingNodeFields = [...document.querySelectorAll('[data-node-fields]')].map(el=>el.dataset.nodeFields).join(',');
        console.log(`[updateNodeFields] nodeId=${nodeId}, container=`, container, ', existing:', existingNodeFields);
        if (!container) {
            console.warn(`[updateNodeFields] ⚠️ container NOT found for "${nodeId}", existing: ${existingNodeFields}`);
            eventBus.emit('log', { msg: `[updateNodeFields] ⚠️ 未找到 .node-fields[data-node-fields="${nodeId}"] 容器，DOM中现有节点: ${existingNodeFields}`, type: 'warning' });
            // 初始化场景字段缓存（如果不存在）
            if (!AppState.nodeFieldsCache) AppState.nodeFieldsCache = {};
            if (!AppState.nodeFieldsCache[nodeId]) AppState.nodeFieldsCache[nodeId] = {};
            if (!AppState.currentWorkState.nodeFields) AppState.currentWorkState.nodeFields = {};
            if (!AppState.currentWorkState.nodeFields[nodeId]) AppState.currentWorkState.nodeFields[nodeId] = {};
            
            // 构建 label→fieldKey 反查表
            const fieldDefs = AppState.fieldDefinitions?.[nodeId] || {};
            const labelToKey = {};
            for (const [key, def] of Object.entries(fieldDefs)) {
                labelToKey[def.label] = key;
            }
            
            // 将传入字段统一转换为 fieldKey 存储（支持 label 或 fieldKey 两种输入）
            // 未知字段（既不是 label 也不是 fieldKey）直接跳过，避免脏数据写入缓存
            const normalizedFields = {};
            const unknownFields = [];
            for (const [k, v] of Object.entries(fields)) {
                if (labelToKey[k]) {
                    // 输入是 label（中文），转为 fieldKey
                    normalizedFields[labelToKey[k]] = v;
                } else if (fieldDefs[k]) {
                    // 输入已经是 fieldKey
                    normalizedFields[k] = v;
                } else {
                    // 完全未知的字段，忽略并记录警告
                    unknownFields.push(k);
                }
            }
            if (unknownFields.length > 0) {
                eventBus.emit('log', { msg: `[updateNodeFields] ⚠️ 忽略未知字段: ${unknownFields.join(', ')}（非 ${nodeId} 的合法 label 或 fieldKey）`, type: 'warning' });
            }
            
            // 合并到缓存（用 fieldKey 存储）
            Object.assign(AppState.nodeFieldsCache[nodeId], normalizedFields);
            Object.assign(AppState.currentWorkState.nodeFields[nodeId], normalizedFields);
            
            eventBus.emit('log', { msg: `场景 ${nodeId} 字段更新: ${Object.entries(normalizedFields).map(([k,v]) => `${k}=${v}`).join(', ')}`, type: 'info' });
            
            // Emit event to trigger context update so AI knows about the field changes
            eventBus.emit('work:fields_updated', { nodeId, fields: normalizedFields });
            

            
            return JSON.stringify({ success: true, node_id: nodeId, updated_fields: Object.keys(normalizedFields), message: `已更新 ${Object.keys(normalizedFields).length} 个字段（节点不在DOM中，仅更新缓存）` });
        }
        console.log(`[updateNodeFields] ✅ container found for "${nodeId}", setting fields:`, fields);
        eventBus.emit('log', { msg: `[updateNodeFields] ✅ 找到容器 ${nodeId}，fields=${JSON.stringify(fields)}`, type: 'info' });

        // 初始化场景字段缓存（如果不存在）
        if (!AppState.nodeFieldsCache) AppState.nodeFieldsCache = {};
        if (!AppState.nodeFieldsCache[nodeId]) AppState.nodeFieldsCache[nodeId] = {};
        if (!AppState.currentWorkState.nodeFields) AppState.currentWorkState.nodeFields = {};
        if (!AppState.currentWorkState.nodeFields[nodeId]) AppState.currentWorkState.nodeFields[nodeId] = {};

        // 构建 label→fieldKey 反查表
        const fieldDefs = AppState.fieldDefinitions?.[nodeId] || {};
        const labelToKey = {};
        for (const [key, def] of Object.entries(fieldDefs)) {
            labelToKey[def.label] = key;
        }

        // 将传入字段统一转换为 fieldKey 存储（支持 label 或 fieldKey 两种输入）
        // 未知字段（既不是 label 也不是 fieldKey）直接跳过，避免脏数据写入缓存
        const normalizedFields = {};
        const unknownFields = [];
        for (const [k, v] of Object.entries(fields)) {
            if (labelToKey[k]) {
                // 输入是 label（中文），转为 fieldKey
                normalizedFields[labelToKey[k]] = v;
            } else if (fieldDefs[k]) {
                // 输入已经是 fieldKey
                normalizedFields[k] = v;
            } else {
                // 完全未知的字段，忽略并记录警告
                unknownFields.push(k);
            }
        }
        if (unknownFields.length > 0) {
            eventBus.emit('log', { msg: `[updateNodeFields] ⚠️ 忽略未知字段: ${unknownFields.join(', ')}（非 ${nodeId} 的合法 label 或 fieldKey）`, type: 'warning' });
        }

        // 合并到缓存（用 fieldKey 存储）
        Object.assign(AppState.nodeFieldsCache[nodeId], normalizedFields);
        Object.assign(AppState.currentWorkState.nodeFields[nodeId], normalizedFields);

        // 渲染时将 fieldKey 转回 label 显示（跳过 photo 类型字段和未知字段）
        const allFields = AppState.nodeFieldsCache[nodeId];
        const entries = Object.entries(allFields)
            .filter(([k]) => fieldDefs[k] && fieldDefs[k].type !== 'photo')
            .map(([k, v]) => {
                const label = fieldDefs[k]?.label || k;
                return `${label}: ${v}`;
            });
        container.textContent = entries.join(' | ');

        // 检查是否更新了 visit_type，刷新节点列表（控制 scene_door_gap_test 显示/隐藏）
        if (nodeId === 'scene_door' && normalizedFields['visit_type'] !== undefined) {
            this._refreshNodeList();
        }

        // 如果有 photo_urls，渲染远程照片
        if (fields.photo_urls && Array.isArray(fields.photo_urls) && fields.photo_urls.length > 0) {
            console.log(`[updateNodeFields] rendering ${fields.photo_urls.length} remote photos for ${nodeId}`);
            nodeRenderer.renderNodePhotos(nodeId, fields.photo_urls);
        }

        eventBus.emit('log', { msg: `场景 ${nodeId} 字段更新: ${Object.entries(normalizedFields).map(([k,v]) => `${k}=${v}`).join(', ')}`, type: 'info' });

        // Emit event to trigger context update so AI knows about the field changes
        eventBus.emit('work:fields_updated', { nodeId, fields: normalizedFields });

        return JSON.stringify({ success: true, node_id: nodeId, updated_fields: Object.keys(normalizedFields), message: `已更新 ${Object.keys(normalizedFields).length} 个字段` });
    }

    /**
     * Refresh node list based on current visit_type.
     * Called when visit_type changes.
     */
    _refreshNodeList() {
        const nodesList = document.querySelector('.work-form-nodes-list');
        if (!nodesList) return;

        const filteredNodes = this._getFilteredNodes();
        const currentNode = AppState.currentWorkState?.currentNode;
        const newHtml = nodeRenderer.renderNodeList(filteredNodes, currentNode);
        nodesList.innerHTML = newHtml;

        // 重新渲染已有的字段和照片（避免调用 updateNodeFields 导致递归）
        for (const nodeId in AppState.nodeFieldsCache) {
            const container = document.querySelector(`.node-fields[data-node-fields="${nodeId}"]`);
            if (container) {
                const allFields = AppState.nodeFieldsCache[nodeId];
                const nodeDefs = AppState.fieldDefinitions?.[nodeId] || {};
                // 跳过 photo 类型字段和未知字段（可能是 AI 写入的脏数据）
                const entries = Object.entries(allFields).filter(([k]) => nodeDefs[k] && nodeDefs[k].type !== 'photo');
                if (entries.length > 0) {
                    container.textContent = entries.map(([k, v]) => {
                        const label = nodeDefs[k]?.label || k;
                        return `${label}: ${v}`;
                    }).join(' | ');
                }
            }
        }
        for (const nodeId in AppState.nodePhotos) {
            nodeRenderer.renderNodePhotos(nodeId);
        }

        const visitType = AppState.currentWorkState?.nodeFields?.scene_door?.visit_type || '正常入户';
        console.log('[_refreshNodeList] done, nodeFieldsCache=', JSON.stringify(AppState.nodeFieldsCache));
        eventBus.emit('log', { msg: `入户方式切换为 ${visitType}，场景列表已更新`, type: 'info' });
    }

    /**
     * Add hazard badge to a node.
     * @returns {string} JSON result
     */
    addHazard(nodeId, level, message) {
        const nodeItem = document.querySelector(`.work-form-node-item[data-node-id="${nodeId}"]`);
        if (!nodeItem) {
            return JSON.stringify({ success: false, message: `未找到场景 ${nodeId}` });
        }

        const existing = nodeItem.querySelector(`.hazard-badge.${level}`);
        if (!existing) {
            const badge = document.createElement('span');
            badge.className = `hazard-badge ${level}`;
            badge.textContent = level === 'red' ? '红色隐患' : '黄色隐患';
            badge.title = message || '';
            nodeItem.querySelector('.node-info').appendChild(badge);
        }

        AppState.currentWorkState.hazards.push({ nodeId, level, message });
        eventBus.emit('log', { msg: `场景 ${nodeId} 标记${level === 'red' ? '红色' : '黄色'}隐患: ${message}`, type: 'warning' });
        return JSON.stringify({ success: true, node_id: nodeId, level, message: `已标记 ${level === 'red' ? '红色' : '黄色'} 隐患` });
    }
}

export default new WorkFormManager();
