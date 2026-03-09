/**
 * ActionDispatcher - handles backend action commands and dispatches to handlers.
 * Maps action names to handler functions across all action modules.
 */
import eventBus from '../core/EventBus.js';
import wsService from '../services/WebSocketService.js';
import chatManager from '../features/ChatManager.js';
import cameraActions from './CameraActions.js';
import videoActions from './VideoActions.js';
import uiActions from './UiActions.js';
import workFormActions from './WorkFormActions.js';

class ActionDispatcher {
    constructor() {
        // Action name -> handler mapping (generalized)
        this._actionMap = {
            'camera_take_photo': (p) => cameraActions.takePhoto(p),
            'camera_upload_photo': (p) => cameraActions.uploadPhoto(p),
            'camera_switch_camera': (p) => cameraActions.switchCamera(p),
            'video_start_video': (p) => videoActions.startVideo(p),
            'video_stop_video': (p) => videoActions.stopVideo(p),
            'ui_show_alert': (p) => uiActions.showAlert(p),
            'ui_show_address_selector': (p) => uiActions.showAddressSelector(p),
            'ui_show_choices': (p) => uiActions.showChoices(p),
            // Generalized: work_form_* replaces work_*
            'work_form_open_form': (p) => workFormActions.openForm(p),
            'work_form_update_node_status': (p) => workFormActions.updateNodeStatus(p),
            'work_form_update_node_fields': (p) => workFormActions.updateNodeFields(p),
            'work_form_add_hazard': (p) => workFormActions.addHazard(p),
            'work_form_show_task_list': (p) => workFormActions.showTaskList(p),
            'work_form_get_status': () => workFormActions.getStatus(),
            'work_form_restore_node_photos': (p) => workFormActions.restoreNodePhotos(p),
            // Tab management actions
            'tab_get_active_state': () => workFormActions.getActiveTabState(),
            'tab_get_all_summary': () => workFormActions.getAllTabsSummary(),
            'tab_switch': (p) => workFormActions.switchTab(p),
            'tab_close': (p) => workFormActions.closeTab(p),
            // Backward compat: keep old names working during transition
            'work_open_form': (p) => workFormActions.openForm(p),
            'work_update_node_status': (p) => workFormActions.updateNodeStatus(p),
            'work_update_node_fields': (p) => workFormActions.updateNodeFields(p),
            'work_add_hazard': (p) => workFormActions.addHazard(p),
        };

        // Action labels for UI display
        this._labels = {
            'camera_take_photo': '拍照',
            'camera_upload_photo': '上传照片',
            'camera_switch_camera': '切换摄像头',
            'video_start_video': '开始录像',
            'video_stop_video': '停止录像',
            'ui_show_alert': '显示提醒',
            'ui_show_address_selector': '显示地址选择',
            'ui_show_choices': '显示选项',
            'work_form_open_form': '打开工作表单',
            'work_form_update_node_status': '更新场景状态',
            'work_form_update_node_fields': '更新场景字段',
            'work_form_add_hazard': '标记隐患',
            'work_form_show_task_list': '显示任务列表',
            'work_form_get_status': '查询安检单状态',
            // Tab management
            'tab_get_active_state': '获取当前页签状态',
            'tab_get_all_summary': '获取所有页签摘要',
            'tab_switch': '切换页签',
            'tab_close': '关闭页签',
            // Backward compat
            'work_open_form': '打开工作表单',
            'work_update_node_status': '更新场景状态',
            'work_update_node_fields': '更新场景字段',
            'work_add_hazard': '标记隐患',
        };
    }

    /**
     * Handle an incoming action from backend.
     * Protocol: { type: "action", action_id, name, params }
     */
    async handleAction(msg) {
        const { action_id, name, params } = msg;
        eventBus.emit('log', { msg: `收到动作: ${name}(${action_id})`, type: 'info' });
        chatManager.addMessage(`[执行动作] ${this._getLabel(name)}...`, 'system');

        try {
            const result = await this._executeAction(name, params || {});
            this._sendResult(action_id, true, result);
            eventBus.emit('log', { msg: `动作完成: ${name}`, type: 'success' });
        } catch (e) {
            this._sendResult(action_id, false, e.message || String(e));
            eventBus.emit('log', { msg: `动作失败: ${name} - ${e.message}`, type: 'error' });
        }
    }

    _getLabel(name) {
        return this._labels[name] || name;
    }

    async _executeAction(name, params) {
        const handler = this._actionMap[name];
        if (handler) {
            return await handler(params);
        }
        return `未知动作: ${name}`;
    }

    _sendResult(actionId, success, result) {
        wsService.sendJSON({
            type: 'action_result',
            action_id: actionId,
            success,
            result
        });
    }
}

export default new ActionDispatcher();
