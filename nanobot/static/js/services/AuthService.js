/**
 * AuthService - centralized authentication logic.
 * Manages JWT tokens, login/register, and session persistence via localStorage.
 */
import AppState from '../core/AppState.js';
import eventBus from '../core/EventBus.js';
import apiService from './ApiService.js';

const TOKEN_KEY = 'nanobot_token';
const USER_KEY = 'nanobot_user';

class AuthService {
    /**
     * Initialize auth state from persisted token.
     * Emits 'auth:ready' or 'auth:required'.
     */
    async init() {
        const token = localStorage.getItem(TOKEN_KEY);
        if (!token) {
            eventBus.emit('auth:required');
            return;
        }

        try {
            const userData = await this._verifyToken(token);
            this._setAuth(userData, token);
            eventBus.emit('auth:ready', {
                userId: userData.user_id,
                username: userData.username,
                tenantId: userData.tenant_id,
                role: userData.role,
            });
        } catch {
            this._clearAuth();
            eventBus.emit('auth:required');
        }
    }

    /**
     * Login with username and password.
     * @returns {{ success: boolean, message?: string }}
     */
    async login(username, password) {
        const res = await apiService.request('/api/v1/auth/login', {
            method: 'POST',
            body: JSON.stringify({ username, password }),
        }, false);

        if (res.success) {
            this._setAuth({
                user_id: res.user_id,
                username: username,
                tenant_id: res.tenant_id || 'default',
                role: res.role || 'user',
            }, res.access_token);

            eventBus.emit('auth:ready', {
                userId: res.user_id,
                username: username,
                tenantId: res.tenant_id || 'default',
                role: res.role || 'user',
            });
            return { success: true };
        }
        return { success: false, message: res.message || '登录失败' };
    }

    /**
     * Register a new user. Does NOT auto-login.
     * @returns {{ success: boolean, message?: string }}
     */
    async register(username, password) {
        try {
            const res = await apiService.request('/api/v1/auth/register', {
                method: 'POST',
                body: JSON.stringify({ username, password, tenant_id: 'default' }),
            }, false);

            if (res.success) {
                return { success: true, message: res.message || '注册成功' };
            }
            return { success: false, message: res.detail || res.message || '注册失败' };
        } catch {
            return { success: false, message: '网络错误，请稍后重试' };
        }
    }

    /**
     * Logout: clear auth state and notify listeners.
     */
    logout() {
        this._clearAuth();
        eventBus.emit('auth:logout');
    }

    /**
     * Get the current JWT token (or null).
     */
    getToken() {
        return AppState.auth?.token ?? null;
    }

    // ── private helpers ──

    async _verifyToken(token) {
        const res = await fetch(`${this._baseUrl()}/api/v1/auth/me`, {
            headers: { 'Authorization': `Bearer ${token}` },
        });
        if (!res.ok) throw new Error('Token invalid');
        return res.json();
    }

    _setAuth(userData, token) {
        AppState.auth = {
            token,
            userId: userData.user_id,
            username: userData.username,
            tenantId: userData.tenant_id || 'default',
            role: userData.role || 'user',
        };
        localStorage.setItem(TOKEN_KEY, token);
        localStorage.setItem(USER_KEY, JSON.stringify({
            userId: AppState.auth.userId,
            username: AppState.auth.username,
            tenantId: AppState.auth.tenantId,
            role: AppState.auth.role,
        }));
    }

    _clearAuth() {
        AppState.auth = null;
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(USER_KEY);
    }

    _baseUrl() {
        const protocol = window.location.protocol;
        const host = document.getElementById('serverHost')?.value || window.location.host;
        return `${protocol}//${host}`;
    }
}

export default new AuthService();
