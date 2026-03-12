/**
 * Global application state singleton.
 * Replaces all global `let` variables from the original monolithic file.
 * All modules import and read/write this shared state object.
 */
const AppState = {
    // --- WebSocket ---
    ws: null,
    isConnected: false,

    // --- Auth ---
    auth: null,   // null = not logged in; { token, userId, username, tenantId, role }

    // --- Audio / Recording ---
    isRecording: false,
    audioContext: null,
    scriptProcessor: null,
    mediaStream: null,
    analyser: null,

    // --- Pending Image ---
    pendingImage: null,          // { file, dataUrl }
    chatPhotoAutoSend: false,
    lastChatPhotoDataUrl: null,

    // --- Chat streaming ---
    currentThinkingMessage: null,
    currentStreamingMessage: null,
    streamingText: '',
    currentThinkingStreamMessage: null,
    showThinkingProcess: localStorage.getItem('showThinkingProcess') !== 'false',
    currentAgentName: null,

    // --- Camera ---
    cameraStream: null,
    cameraVideo: null,
    currentFacing: 'environment',   // 'environment' = back, 'user' = front
    mediaRecorderVideo: null,
    recordedChunks: [],

    // --- Multi-Tab Support ---
    workTabs: {},              // Map<tabId, TabState>
    activeTabId: null,         // Currently active tab ID

    // --- Compatibility layer for currentWorkState (getter/setter) ---
    get currentWorkState() {
        if (!this.activeTabId || !this.workTabs[this.activeTabId]) {
            return this._fallbackWorkState || {};
        }
        return this.workTabs[this.activeTabId].workState || {};
    },
    set currentWorkState(val) {
        if (this.activeTabId && this.workTabs[this.activeTabId]) {
            this.workTabs[this.activeTabId].workState = val;
        } else {
            // Fallback for when no tab is active
            this._fallbackWorkState = val;
        }
    },

    // --- Compatibility layer for nodePhotos ---
    get nodePhotos() {
        if (!this.activeTabId || !this.workTabs[this.activeTabId]) {
            return this._fallbackNodePhotos || {};
        }
        return this.workTabs[this.activeTabId].nodePhotos || {};
    },
    set nodePhotos(val) {
        if (this.activeTabId && this.workTabs[this.activeTabId]) {
            this.workTabs[this.activeTabId].nodePhotos = val;
        } else {
            this._fallbackNodePhotos = val;
        }
    },

    // --- Compatibility layer for nodeFieldsCache ---
    get nodeFieldsCache() {
        if (!this.activeTabId || !this.workTabs[this.activeTabId]) {
            return this._fallbackFieldsCache || {};
        }
        return this.workTabs[this.activeTabId].nodeFieldsCache || {};
    },
    set nodeFieldsCache(val) {
        if (this.activeTabId && this.workTabs[this.activeTabId]) {
            this.workTabs[this.activeTabId].nodeFieldsCache = val;
        } else {
            this._fallbackFieldsCache = val;
        }
    },

    // --- Fallback state (used when no tab is active) ---
    _fallbackWorkState: {},
    _fallbackNodePhotos: {},
    _fallbackFieldsCache: {},

    // --- Node definitions (loaded from backend) ---
    nodes: [],               // Array of { id, order, name, purpose, canSkip, requiredFields, optionalFields }
    fieldDefinitions: {},      // { nodeId: { fieldKey: { label, type, description, options, unit, ai_extract_patterns: [RegExp] } } }

    // --- Node photos (legacy reference) ---
    currentUploadingNode: null,

    /**
     * Reset work form state for current active tab.
     */
    resetWorkState() {
        if (this.activeTabId && this.workTabs[this.activeTabId]) {
            this.workTabs[this.activeTabId].workState = {
                userId: null,
                address: null,
                workType: null,
                currentNode: null,
                completedNodes: [],
                hazards: []
            };
            this.workTabs[this.activeTabId].nodePhotos = {};
            this.workTabs[this.activeTabId].nodeFieldsCache = {};
        } else {
            this._fallbackWorkState = {};
            this._fallbackNodePhotos = {};
            this._fallbackFieldsCache = {};
        }
        this.currentUploadingNode = null;
    },

    /**
     * Get all tab IDs.
     */
    getTabIds() {
        return Object.keys(this.workTabs);
    },

    /**
     * Get tab state by ID.
     */
    getTab(tabId) {
        return this.workTabs[tabId] || null;
    }
};

export default AppState;
