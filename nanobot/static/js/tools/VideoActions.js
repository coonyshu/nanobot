/**
 * VideoActions - start/stop video recording.
 */
import AppState from '../core/AppState.js';
import eventBus from '../core/EventBus.js';
import chatManager from '../features/ChatManager.js';

class VideoActions {
    async startVideo(params) {
        const purpose = params.purpose || '录像';

        try {
            const stream = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: AppState.currentFacing, width: { ideal: 1280 }, height: { ideal: 720 } },
                audio: true
            });

            AppState.mediaRecorderVideo = new MediaRecorder(stream, { mimeType: 'video/webm' });
            AppState.recordedChunks = [];

            AppState.mediaRecorderVideo.ondataavailable = (e) => {
                if (e.data.size > 0) AppState.recordedChunks.push(e.data);
            };

            AppState.mediaRecorderVideo.start();

            return JSON.stringify({ text: `开始录像，用途：${purpose}`, recording: true });
        } catch (e) {
            return JSON.stringify({ text: `录像启动失败: ${e.message}`, error: true });
        }
    }

    async stopVideo() {
        if (!AppState.mediaRecorderVideo) {
            return JSON.stringify({ text: '当前没有正在录制的视频', error: true });
        }

        return new Promise((resolve) => {
            AppState.mediaRecorderVideo.onstop = () => {
                const blob = new Blob(AppState.recordedChunks, { type: 'video/webm' });
                const url = URL.createObjectURL(blob);

                chatManager.addMessage(`<video src="${url}" controls style="max-width:200px;border-radius:8px"></video>`, 'system');

                AppState.mediaRecorderVideo.stream.getTracks().forEach(t => t.stop());
                AppState.mediaRecorderVideo = null;
                AppState.recordedChunks = [];

                resolve(JSON.stringify({ text: '录像完成', duration_ms: blob.size }));
            };

            AppState.mediaRecorderVideo.stop();
        });
    }
}

export default new VideoActions();
