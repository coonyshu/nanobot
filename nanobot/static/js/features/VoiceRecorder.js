/**
 * VoiceRecorder - microphone recording + PCM encoding + visualizer.
 */
import AppState from '../core/AppState.js';
import eventBus from '../core/EventBus.js';
import wsService from '../services/WebSocketService.js';

class VoiceRecorder {
    constructor() {
        this.voiceModal = null;
        this.voiceVisualizer = null;
        this.voiceText = null;
        this._animationId = null;
    }

    init() {
        this.voiceModal = document.getElementById('voiceModal');
        this.voiceVisualizer = document.getElementById('voiceVisualizer');
        this.voiceText = document.getElementById('voiceText');
        this._initVisualizerBars();
    }

    _initVisualizerBars() {
        this.voiceVisualizer.innerHTML = '';
        for (let i = 0; i < 30; i++) {
            const bar = document.createElement('div');
            bar.className = 'bar';
            bar.style.height = '4px';
            this.voiceVisualizer.appendChild(bar);
        }
    }

    /**
     * Float32 PCM -> Int16 PCM conversion.
     */
    _floatTo16BitPCM(float32Array) {
        const buffer = new ArrayBuffer(float32Array.length * 2);
        const view = new DataView(buffer);
        for (let i = 0; i < float32Array.length; i++) {
            let s = Math.max(-1, Math.min(1, float32Array[i]));
            view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
        }
        return buffer;
    }

    /**
     * Start voice recording.
     */
    async startVoice() {
        if (!AppState.isConnected || AppState.isRecording) return;

        try {
            AppState.mediaStream = await navigator.mediaDevices.getUserMedia({
                audio: { sampleRate: 16000, channelCount: 1, echoCancellation: true }
            });

            AppState.audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
            const source = AppState.audioContext.createMediaStreamSource(AppState.mediaStream);
            AppState.scriptProcessor = AppState.audioContext.createScriptProcessor(4096, 1, 1);
            AppState.analyser = AppState.audioContext.createAnalyser();
            AppState.analyser.fftSize = 256;

            source.connect(AppState.analyser);
            AppState.analyser.connect(AppState.scriptProcessor);
            AppState.scriptProcessor.connect(AppState.audioContext.destination);

            // Notify backend to start listening (specify pcm format)
            wsService.sendJSON({ type: 'start', format: 'pcm' });

            AppState.scriptProcessor.onaudioprocess = (e) => {
                if (!AppState.isRecording) return;
                const inputData = e.inputBuffer.getChannelData(0);
                const pcm16 = this._floatTo16BitPCM(inputData);
                wsService.send(pcm16);
            };

            AppState.isRecording = true;

            // UI feedback
            const btnVoice = document.getElementById('btnVoice');
            if (btnVoice) btnVoice.classList.add('recording');
            this.voiceModal.classList.add('show');
            this.voiceText.textContent = '';

            this._updateVisualizer();

            eventBus.emit('log', { msg: '开始录音', type: 'info' });
        } catch (e) {
            eventBus.emit('log', { msg: '麦克风访问失败: ' + e.message, type: 'error' });
        }
    }

    /**
     * Update visualizer bars from analyser data.
     */
    _updateVisualizer() {
        if (!AppState.isRecording || !AppState.analyser) return;

        const dataArray = new Uint8Array(AppState.analyser.frequencyBinCount);
        AppState.analyser.getByteFrequencyData(dataArray);

        const bars = this.voiceVisualizer.querySelectorAll('.bar');
        const step = Math.floor(dataArray.length / bars.length);

        bars.forEach((bar, i) => {
            const value = dataArray[i * step] || 0;
            const height = Math.max(4, (value / 255) * 60);
            bar.style.height = height + 'px';
        });

        this._animationId = requestAnimationFrame(() => this._updateVisualizer());
    }

    /**
     * Stop voice recording.
     */
    stopVoice() {
        if (!AppState.isRecording) return;

        AppState.isRecording = false;

        wsService.sendJSON({ type: 'stop' });

        if (AppState.scriptProcessor) {
            AppState.scriptProcessor.disconnect();
            AppState.scriptProcessor = null;
        }
        if (AppState.analyser) {
            AppState.analyser.disconnect();
            AppState.analyser = null;
        }
        if (AppState.audioContext) {
            AppState.audioContext.close();
            AppState.audioContext = null;
        }
        if (AppState.mediaStream) {
            AppState.mediaStream.getTracks().forEach(t => t.stop());
            AppState.mediaStream = null;
        }

        if (this._animationId) {
            cancelAnimationFrame(this._animationId);
            this._animationId = null;
        }

        const btnVoice = document.getElementById('btnVoice');
        if (btnVoice) btnVoice.classList.remove('recording');
        this.voiceModal.classList.remove('show');

        // Reset visualizer bars
        this.voiceVisualizer.querySelectorAll('.bar').forEach(bar => {
            bar.style.height = '4px';
        });

        eventBus.emit('log', { msg: '录音停止', type: 'info' });
    }
}

export default new VoiceRecorder();
