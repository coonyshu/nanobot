/**
 * CameraActions - handle camera take_photo, upload_photo, switch_camera.
 */
import AppState from '../core/AppState.js';
import eventBus from '../core/EventBus.js';
import chatManager from '../features/ChatManager.js';

class CameraActions {
    /**
     * Take a photo using the device camera.
     */
    async takePhoto(params) {
        const purpose = params.purpose || '拍照';

        if (!AppState.cameraStream) {
            AppState.cameraStream = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: AppState.currentFacing, width: { ideal: 1280 }, height: { ideal: 720 } },
                audio: false
            });
        }

        if (!AppState.cameraVideo) {
            AppState.cameraVideo = document.createElement('video');
            AppState.cameraVideo.setAttribute('playsinline', '');
            AppState.cameraVideo.autoplay = true;
        }
        AppState.cameraVideo.srcObject = AppState.cameraStream;
        await AppState.cameraVideo.play();

        await new Promise(r => setTimeout(r, 500));

        const canvas = document.createElement('canvas');
        canvas.width = AppState.cameraVideo.videoWidth || 1280;
        canvas.height = AppState.cameraVideo.videoHeight || 720;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(AppState.cameraVideo, 0, 0);

        const dataUrl = canvas.toDataURL('image/jpeg', 0.85);

        chatManager.addMessage(`<img src="${dataUrl}" style="max-width:200px;border-radius:8px">`, 'system');

        this.stopCamera();

        const base64Data = dataUrl.split(',')[1];
        return JSON.stringify({
            text: `拍照成功，用途：${purpose}`,
            image: base64Data,
            mime_type: 'image/jpeg'
        });
    }

    /**
     * Stop camera stream.
     */
    stopCamera() {
        if (AppState.cameraStream) {
            AppState.cameraStream.getTracks().forEach(t => t.stop());
            AppState.cameraStream = null;
        }
        if (AppState.cameraVideo) {
            AppState.cameraVideo.srcObject = null;
        }
    }

    /**
     * Upload a photo from file system.
     */
    async uploadPhoto(params) {
        const purpose = params.purpose || '上传照片';

        return new Promise((resolve) => {
            const input = document.createElement('input');
            input.type = 'file';
            input.accept = 'image/*';

            let resolved = false;
            const focusHandler = () => {
                setTimeout(() => {
                    if (!resolved) {
                        resolved = true;
                        window.removeEventListener('focus', focusHandler);
                        resolve(JSON.stringify({ text: '用户取消了上传', cancelled: true }));
                    }
                }, 1000);
            };
            window.addEventListener('focus', focusHandler);

            input.onchange = (e) => {
                resolved = true;
                window.removeEventListener('focus', focusHandler);

                const file = e.target.files[0];
                if (!file) {
                    resolve(JSON.stringify({ text: '未选择文件', cancelled: true }));
                    return;
                }

                if (!file.type.startsWith('image/') || file.size > 10 * 1024 * 1024) {
                    resolve(JSON.stringify({ text: '请选择10MB以内的图片文件', error: true }));
                    return;
                }

                const reader = new FileReader();
                reader.onload = (ev) => {
                    const dataUrl = ev.target.result;
                    chatManager.addMessage(`<img src="${dataUrl}" style="max-width:200px;border-radius:8px">`, 'system');

                    const base64Data = dataUrl.split(',')[1];
                    resolve(JSON.stringify({
                        text: `照片上传成功，用途：${purpose}`,
                        image: base64Data,
                        mime_type: file.type || 'image/jpeg'
                    }));
                };
                reader.readAsDataURL(file);
            };

            input.click();
        });
    }

    /**
     * Switch between front and back camera.
     */
    async switchCamera(params) {
        const facing = params.facing || 'back';
        AppState.currentFacing = facing === 'front' ? 'user' : 'environment';

        this.stopCamera();

        return JSON.stringify({
            text: `已切换到${facing === 'front' ? '前置' : '后置'}摄像头`,
            facing: AppState.currentFacing
        });
    }
}

export default new CameraActions();
