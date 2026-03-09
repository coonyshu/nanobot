/**
 * TabManager - Multi-tab lifecycle management for work area.
 * Handles tab creation, activation, closing, and state management.
 */
import AppState from '../core/AppState.js';
import eventBus from '../core/EventBus.js';

class TabManager {
    constructor() {
        this._initialized = false;
    }

    /**
     * Generate tab ID based on type and options.
     * @param {string} type - "task-list" | "work"
     * @param {object} options - { userId, ... }
     * @returns {string} Unique tab ID
     */
    _getTabId(type, options = {}) {
        if (type === 'task-list') {
            return 'tab_task_list';
        }
        if (type === 'work' && options.userId) {
            return `tab_insp_${options.userId}`;
        }
        return `tab_${type}_${Date.now()}`;
    }

    /**
     * Ensure the tab bar container exists (idempotent).
     * @returns {HTMLElement} The wrapper element
     */
    ensureTabBar() {
        const panelContent = document.getElementById('operationPanelContent');
        const panelEmpty = document.getElementById('operationPanelEmpty');

        let wrapper = document.getElementById('workPanelWrapper');
        if (wrapper) return wrapper;

        // Hide empty state
        if (panelEmpty) panelEmpty.style.display = 'none';

        // Create Tab container
        wrapper = document.createElement('div');
        wrapper.id = 'workPanelWrapper';
        wrapper.innerHTML = `
            <div class="tab-bar-scroll-container">
                <div class="work-panel-tab-bar" id="tabBarInner"></div>
            </div>
            <div class="tab-content-area" id="tabContentArea"></div>
        `;

        panelContent.appendChild(wrapper);
        this._initialized = true;
        return wrapper;
    }

    /**
     * Create or activate a tab.
     * @param {string} type - "task-list" | "work"
     * @param {object} options - Tab options
     * @returns {string} Tab ID
     */
    openOrActivate(type, options = {}) {
        const tabId = this._getTabId(type, options);

        if (AppState.workTabs[tabId]) {
            // Already exists, activate it
            this.activateTab(tabId);
            return tabId;
        }

        // Create new tab
        this._createTab(tabId, type, options);
        this.activateTab(tabId);
        return tabId;
    }

    /**
     * Create a new tab.
     * @param {string} tabId - Unique tab ID
     * @param {string} type - Tab type
     * @param {object} options - Tab options
     */
    _createTab(tabId, type, options = {}) {
        this.ensureTabBar();

        // Create tab state
        const tabState = {
            tabId,
            type,
            title: this._generateTitle(type, options),
            closable: true,
            userId: options.userId || null,
            address: options.address || null,
            workType: options.workType || null,
            taskId: options.taskId || null,
            workState: {
                userId: options.userId || null,
                address: options.address || null,
                workType: options.workType || null,
                taskId: options.taskId || null,
                currentNode: options.currentNode || (AppState.nodes.length > 0 ? AppState.nodes[0].id : null),
                completedNodes: [],
                hazards: [],
                warnings: options.warnings || [],
                meterInfo: options.meterInfo || {},
                debtInfo: options.debtInfo || {},
                scheduleInfo: options.scheduleInfo || {}
            },
            nodePhotos: {},
            nodeFieldsCache: {}
        };

        AppState.workTabs[tabId] = tabState;

        // Create tab button
        this._createTabButton(tabId, tabState.title);

        // Create tab pane
        this._createTabPane(tabId);

        eventBus.emit('log', { msg: `创建页签: ${tabState.title}`, type: 'info' });
    }

    /**
     * Generate tab title based on type and options.
     */
    _generateTitle(type, options) {
        if (type === 'task-list') {
            return '任务列表';
        }
        if (type === 'work') {
            const name = options.name || options.address || options.userId || '未知';
            // Truncate long names
            const shortName = name.length > 8 ? name.substring(0, 8) + '...' : name;
            return `${shortName}安检`;
        }
        return '新页签';
    }

    /**
     * Create tab button in tab bar.
     */
    _createTabButton(tabId, title) {
        const tabBar = document.getElementById('tabBarInner');
        if (!tabBar) return;

        const btn = document.createElement('button');
        btn.className = 'work-panel-tab';
        btn.dataset.tabId = tabId;
        btn.dataset.action = 'tab-switch';
        btn.innerHTML = `
            <span class="tab-title">${title}</span>
            <span class="tab-close-btn" data-action="tab-close" data-tab-id="${tabId}">×</span>
        `;

        tabBar.appendChild(btn);
    }

    /**
     * Create tab pane in content area.
     */
    _createTabPane(tabId) {
        const contentArea = document.getElementById('tabContentArea');
        if (!contentArea) return;

        const pane = document.createElement('div');
        pane.className = 'tab-pane';
        pane.dataset.tabId = tabId;

        contentArea.appendChild(pane);
    }

    /**
     * Activate a specific tab.
     * @param {string} tabId - Tab ID to activate
     */
    activateTab(tabId) {
        const tabState = AppState.workTabs[tabId];
        if (!tabState) return;

        // Already active
        if (AppState.activeTabId === tabId) return;

        // Update active tab ID
        AppState.activeTabId = tabId;

        // Update tab bar UI
        this._updateTabBarUI();

        // Update pane visibility
        this._updatePaneVisibility();

        // Emit event for context update
        eventBus.emit('tab:activated', tabState);
        eventBus.emit('log', { msg: `激活页�? ${tabState.title}`, type: 'info' });
    }

    /**
     * Update tab bar active states.
     */
    _updateTabBarUI() {
        const tabBar = document.getElementById('tabBarInner');
        if (!tabBar) return;

        tabBar.querySelectorAll('.work-panel-tab').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.tabId === AppState.activeTabId);
        });
    }

    /**
     * Update pane visibility.
     */
    _updatePaneVisibility() {
        const contentArea = document.getElementById('tabContentArea');
        if (!contentArea) return;

        contentArea.querySelectorAll('.tab-pane').forEach(pane => {
            pane.classList.toggle('active', pane.dataset.tabId === AppState.activeTabId);
        });
    }

    /**
     * Close a tab.
     * @param {string} tabId - Tab ID to close
     */
    closeTab(tabId) {
        const tab = AppState.workTabs[tabId];
        if (!tab) return;

        eventBus.emit('log', { msg: `关闭页签: ${tab.title}`, type: 'info' });

        // Remove DOM elements
        this._removeTabDOM(tabId);

        // Clean up state
        delete AppState.workTabs[tabId];

        // If closing active tab, switch to another
        if (AppState.activeTabId === tabId) {
            const remainingTabs = Object.keys(AppState.workTabs);
            if (remainingTabs.length > 0) {
                // Activate the last tab
                this.activateTab(remainingTabs[remainingTabs.length - 1]);
            } else {
                AppState.activeTabId = null;
                this._showEmptyState();
            }
        }

        // Emit close event
        eventBus.emit('tab:closed', { tabId, type: tab.type });
    }

    /**
     * Remove tab DOM elements.
     */
    _removeTabDOM(tabId) {
        // Remove tab button
        const tabBtn = document.querySelector(`.work-panel-tab[data-tab-id="${tabId}"]`);
        if (tabBtn) tabBtn.remove();

        // Remove tab pane
        const pane = document.querySelector(`.tab-pane[data-tab-id="${tabId}"]`);
        if (pane) pane.remove();
    }

    /**
     * Show empty state when no tabs are open.
     */
    _showEmptyState() {
        const wrapper = document.getElementById('workPanelWrapper');
        if (wrapper) wrapper.remove();

        const panelEmpty = document.getElementById('operationPanelEmpty');
        if (panelEmpty) panelEmpty.style.display = 'flex';

        const panelHeader = document.querySelector('.operation-panel-header h2');
        if (panelHeader) panelHeader.textContent = '工作台';

        this._initialized = false;
    }

    /**
     * Get the currently active tab state.
     * @returns {object|null} Active tab state
     */
    getActiveTab() {
        return AppState.workTabs[AppState.activeTabId] || null;
    }

    /**
     * Get all tab states.
     * @returns {object[]} Array of tab states
     */
    getAllTabs() {
        return Object.values(AppState.workTabs);
    }

    /**
     * Get tab pane element by tab ID.
     * @param {string} tabId - Tab ID
     * @returns {HTMLElement|null} Tab pane element
     */
    getTabPane(tabId) {
        return document.querySelector(`.tab-pane[data-tab-id="${tabId}"]`);
    }

    /**
     * Update tab title.
     * @param {string} tabId - Tab ID
     * @param {string} title - New title
     */
    updateTabTitle(tabId, title) {
        const tab = AppState.workTabs[tabId];
        if (tab) {
            tab.title = title;
        }

        const tabBtn = document.querySelector(`.work-panel-tab[data-tab-id="${tabId}"] .tab-title`);
        if (tabBtn) {
            tabBtn.textContent = title;
        }
    }

    /**
     * Check if a tab exists.
     * @param {string} tabId - Tab ID
     * @returns {boolean}
     */
    hasTab(tabId) {
        return !!AppState.workTabs[tabId];
    }

    /**
     * Get tab ID for a user's work task.
     * @param {string} userId - User ID
     * @returns {string|null} Tab ID if exists
     */
    getworkTabId(userId) {
        const tabId = `tab_insp_${userId}`;
        return AppState.workTabs[tabId] ? tabId : null;
    }
}

export default new TabManager();
