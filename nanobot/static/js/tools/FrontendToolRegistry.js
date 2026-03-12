/**
 * FrontendToolRegistry - FRONTEND_DESCRIPTORS and registration.
 * Generalized: 'inspection' group -> 'work_form' group.
 */
import wsService from '../services/WebSocketService.js';
import eventBus from '../core/EventBus.js';

const FRONTEND_DESCRIPTORS = [
    {
        name: "camera",
        description: "设备摄像头控制，支持拍照和切换前后摄像头",
        properties: {
            facing: {
                type: "string",
                description: "当前摄像头方�?front/back)"
            }
        },
        methods: {
            take_photo: {
                description: "控制设备摄像头拍照，拍照后可以对照片进行AI分析识别",
                parameters: {
                    purpose: {
                        type: "string",
                        description: "拍照用途说明，如'工作拍照'、'记录现场'等"
                    },
                    realtime_notify: {
                        type: "boolean",
                        description: "是否需要立即分析并播报照片内容。true=拍照后立即语音播报识别结果，false=仅返回照片数据不主动播报",
                        default: true
                    }
                }
            },
            switch_camera: {
                description: "切换前后摄像头",
                parameters: {
                    facing: {
                        type: "string",
                        description: "摄像头方向，front=前置，back=后置",
                        enum: ["front", "back"]
                    }
                }
            },
            upload_photo: {
                description: "从本地相册或文件系统选择并上传照片，用于设备无摄像头或无法拍照时替代拍照功能，上传后可对照片进行AI分析识别",
                parameters: {
                    purpose: {
                        type: "string",
                        description: "上传照片的用途说明，如'工作拍照'、'记录现场'等"
                    },
                    realtime_notify: {
                        type: "boolean",
                        description: "是否需要立即分析并播报照片内容。true=上传后立即语音播报识别结果，false=仅返回照片数据不主动播报",
                        default: true
                    }
                }
            }
        }
    },
    {
        name: "video",
        description: "设备视频录制控制",
        methods: {
            start_video: {
                description: "开始录制视频",
                parameters: {
                    purpose: { type: "string", description: "录像用途说明" }
                }
            },
            stop_video: {
                description: "停止录制视频并保存",
                parameters: {}
            }
        }
    },
    {
        name: "ui",
        description: "设备界面交互控制",
        methods: {
            show_alert: {
                description: "在设备上显示一条提醒通知，用于重要信息提示",
                parameters: {
                    title: { type: "string", description: "提醒标题" },
                    message: { type: "string", description: "提醒内容", required: true },
                    level: { type: "string", description: "提醒级别", enum: ["info", "warning", "error"] }
                }
            },
            show_address_selector: {
                description: "显示地址选择列表，让用户从多个匹配地址中选择一个",
                parameters: {
                    addresses: { type: "array", description: "地址候选列表，每个元素包含 userId, address, score, confidence 字段", required: true },
                    workType: { type: "string", description: "工作类型（安检/复检/维修/挂表/点火等）", required: true },
                    prompt: { type: "string", description: "提示文本", default: "找到多个匹配地址，请选择一个" }
                }
            },
            show_choices: {
                description: "【阻断性工具】在聊天对话中显示单选按钮组，让用户点击选择。调用此工具后必须立即结束当前响应，禁止继续调用其他工具。用户点击后会自动将选择内容作为新消息发送给AI，届时再继续处理。",
                parameters: {
                    prompt: { type: "string", description: "提示文本，显示在按钮上方", required: true },
                    choices: { type: "array", description: "选项列表，每个元素为字符串", required: true }
                }
            },
            show_photo_actions: {
                description: "在聊天对话中显示拍照和上传按钮，让用户选择拍照或上传照片。调用此工具后必须立即结束当前响应，禁止继续调用其他工具。用户点击后会自动触发相应的拍照或上传操作。",
                parameters: {
                    prompt: { type: "string", description: "提示文本，显示在按钮上方", required: true }
                }
            }
        }
    },
    {
        name: "work_form",
        description: "工单业务表单操作（支持安检/挂表/点火/工程等多种工作类型）",
        agent: "workflow-inspector",
        methods: {
            open_form: {
                description: "打开指定用户的工作表单，显示用户信息、警告提示和场景列表",
                parameters: {
                    userId: { type: "string", description: "用户编号/业务主键", required: true },
                    workType: { type: "string", description: "工作类型（安检/复检/维修/挂表/点火等）", required: true },
                    address: { type: "string", description: "地址信息（用于显示确认）" },
                    task_id: { type: "string", description: "后端任务ID，由 mcp_gas-inspection_load_workflow_task 返回的 task_id，必须传入以确保前端与后端数据同步" },
                    warnings: { type: "array", description: "警告提示列表，每个元素包含 type/level/message 字段，如欠费提醒、计划内安检提示等" },
                    meterInfo: { type: "object", description: "表具信息，包含 meter_number/previous_reading/last_inspection_date 等字段" },
                    debtInfo: { type: "object", description: "欠费信息，包含 debt_amount/debt_months/has_debt 字段" },
                    scheduleInfo: { type: "object", description: "安检计划信息，包含 is_scheduled/scheduled_date/inspection_cycle 字段" }
                }
            },
            update_node_status: {
                description: "更新工作场景状态和进度。⚠️ 重要约束：将节点标记为 completed 之前，必须先调用后端 mcp_workflow-engine_transition_to_next_node 获取下一节点，再用返回的 next_node 调用本工具设置 active。禁止跳过后端路由直接猜测下一节点。",
                parameters: {
                    node_id: { type: "string", description: "场景ID，如node_door、scene_leak等", required: true },
                    status: { type: "string", description: "场景状态：pending=待开始，active=进行中，completed=已完成（标记completed前必须先调用transition_to_next_node）", enum: ["pending", "active", "completed"], required: true }
                }
            },
            update_node_fields: {
                description: '更新场景已采集的字段数据，在工作表单上显示。⚠️ fields 的 key 必须使用工作流定义中的中文标签名（如"入户方式"、"门牌号"），不得自造不存在的 label（如"到访类型"），否则将被忽略。可通过 mcp_gas-inspection_get_workflow_definitions 查看每个节点的合法字段。',
                parameters: {
                    node_id: { type: "string", description: "场景ID", required: true },
                    fields: { type: "object", description: '字段标签名和值的键值对，key 必须是工作流定义中的 label，如 {"入户方式": "正常入户", "门牌号": "202"}' }
                }
            },
            add_hazard: {
                description: "在场景上标记隐患",
                parameters: {
                    node_id: { type: "string", description: "场景ID", required: true },
                    level: { type: "string", description: "隐患级别", enum: ["red", "yellow"], required: true },
                    message: { type: "string", description: "隐患描述" }
                }
            },
            show_task_list: {
                description: "在工作区任务列表标签页显示安检任务列表，支持筛选条件展示，每行任务可点击开始安检",
                parameters: {
                    tasks: { type: "array", description: "任务摘要列表，每项含 user_id/name/address/tags/debt_amount 等字段", required: true },
                    filter_summary: { type: "string", description: "筛选条件的中文描述，显示在列表顶部" },
                    total: { type: "number", description: "未筛选时的总任务数" }
                }
            },
            get_status: {
                description: "查询当前安检单界面是否已打开。返回 is_open=true 表示安检单已显示在设备屏幕上；is_open=false 表示安检单未打开或已关闭，需要调用 open_form 重新打开。",
                parameters: {}
            },
            restore_node_photos: {
                description: "恢复场景照片（从后端持久化的远程URL），在任务恢复时显示之前上传的照片",
                parameters: {
                    node_id: { type: "string", description: "场景ID", required: true },
                    photo_urls: { type: "array", description: "照片URL列表", required: true }
                }
            }
        }
    },
    {
        name: "tab",
        description: "工作区页签管理，支持多任务并行，每个安检任务对应一个独立页签",
        agent: "workflow-inspector",
        properties: {
            activeTabId: {
                type: "string",
                description: "当前激活的页签ID"
            }
        },
        methods: {
            get_active_state: {
                description: "获取当前激活页签的完整状态，包括页签类型、用户信息、工作进度等。注意：返回的数据仅用于了解当前状态，不能据此推断下一节点ID——节点转换必须调用 mcp_workflow-engine_transition_to_next_node 由后端路由决定。",
                parameters: {}
            },
            get_all_summary: {
                description: "获取所有打开页签的摘要信息，用于了解当前打开了哪些任务",
                parameters: {}
            },
            switch: {
                description: "切换到指定页签",
                parameters: {
                    tabId: { type: "string", description: "目标页签ID", required: true }
                }
            },
            close: {
                description: "关闭指定页签",
                parameters: {
                    tabId: { type: "string", description: "目标页签ID", required: true }
                }
            }
        }
    }
];

/**
 * Register frontend tool descriptors with the backend via WebSocket.
 */
function registerFrontendTools() {
    wsService.sendJSON({
        type: 'register_tools',
        descriptors: FRONTEND_DESCRIPTORS
    });
    eventBus.emit('log', { msg: '已发送前端能力注冊信息', type: 'info' });
}

export { FRONTEND_DESCRIPTORS, registerFrontendTools };
