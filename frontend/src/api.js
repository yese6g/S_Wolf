/**
 * api.js - SilverWolf 前端 API 层
 * 封装所有后端接口调用。同源部署（FastAPI 托管静态资源），全部走相对路径。
 * 暴露全局对象: window.SWApi
 */
(function () {
    'use strict';

    const BASE_URL = '';

    async function request(path, options = {}) {
        const url = BASE_URL + path;
        const res = await fetch(url, {
            headers: { 'Content-Type': 'application/json' },
            ...options,
        });
        if (!res.ok) {
            let detail = `HTTP ${res.status}`;
            try {
                const j = await res.json();
                detail = j.detail || j.message || detail;
            } catch (e) { /* ignore */ }
            const err = new Error(detail);
            err.status = res.status;
            throw err;
        }
        return res.json();
    }

    // ── 系统状态 ──
    const systemApi = {
        config: () => request('/api/config'),
        health: () => request('/api/health'),
    };

    // ── 会话管理 ──
    const sessionApi = {
        list: () => request('/api/sessions'),
        create: (title) => request('/api/sessions', {
            method: 'POST',
            body: JSON.stringify({ title }),
        }),
        get: (id) => request(`/api/sessions/${id}`),
        rename: (id, title) => request(`/api/sessions/${id}`, {
            method: 'PUT',
            body: JSON.stringify({ title }),
        }),
        delete: (id) => request(`/api/sessions/${id}`, {
            method: 'DELETE',
        }),
        // v2.3.0：导出会话为 Markdown（直接触发浏览器下载）
        exportMd: async (id) => {
            const res = await fetch(BASE_URL + `/api/sessions/${encodeURIComponent(id)}/export`);
            if (!res.ok) {
                let detail = `导出失败: HTTP ${res.status}`;
                try {
                    const j = await res.json();
                    detail = j.detail || detail;
                } catch (e) { /* ignore */ }
                throw new Error(detail);
            }
            const text = await res.text();
            const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `silverwolf_session_${String(id).slice(0, 8)}.md`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        },
    };

    // ── 聊天 ──
    const chatApi = {
        uploadAttachments: async (files) => {
            const formData = new FormData();
            for (const f of files) {
                formData.append('files', f);
            }
            const res = await fetch(BASE_URL + '/api/chat/attachments', {
                method: 'POST',
                body: formData,
            });
            if (!res.ok) {
                let detail = `上传失败: HTTP ${res.status}`;
                try {
                    const j = await res.json();
                    detail = j.detail || detail;
                } catch (e) { /* ignore */ }
                throw new Error(detail);
            }
            return res.json();
        },
        send: async (message, opts = {}) => {
            return request('/api/chat', {
                method: 'POST',
                body: JSON.stringify({
                    message,
                    session_id: opts.sessionId || null,
                    use_rag: opts.useRag,
                    kb_slug: opts.kbSlug || 'default_knowledge_base',
                    provider_id: opts.providerId || null,
                    model: opts.model || null,
                    attachments: opts.attachments || null,
                    agent_mode: opts.agentMode,
                    enable_web: opts.enableWeb,
                    max_steps: opts.maxSteps,
                }),
            });
        },
        // 流式聊天（SSE）：返回原始 fetch Response，由调用方逐行解析
        // v2.3.0：支持 signal 取消（「停止生成」按钮用 AbortController）
        stream: (message, opts = {}) => {
            return fetch(BASE_URL + '/api/chat/stream', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                signal: opts.signal || null,
                body: JSON.stringify({
                    message,
                    session_id: opts.sessionId || null,
                    use_rag: opts.useRag,
                    kb_slug: opts.kbSlug || 'default_knowledge_base',
                    provider_id: opts.providerId || null,
                    model: opts.model || null,
                    attachments: opts.attachments || null,
                    // v2.0.0 Agent 开关：是否自动调用工具、是否允许联网、最大工具轮数
                    agent_mode: opts.agentMode,
                    enable_web: opts.enableWeb,
                    max_steps: opts.maxSteps,
                }),
            });
        },
    };

    // ── Agent 能力（工具清单 / 自我修改记录 / RAG 文件） ──
    const agentApi = {
        tools: () => request('/api/agent/tools'),
        changes: (limit = 50) => request(`/api/agent/changes?limit=${limit}`),
        ragFiles: (kbSlug) => request(`/api/agent/rag/files?kb_slug=${encodeURIComponent(kbSlug)}`),
        // engine 可选：auto / tavily / bing（auto 时是否用 Tavily 由设置开关决定）
        searchTest: (engine = 'auto') => request('/api/agent/search_test', {
            method: 'POST',
            body: JSON.stringify({ engine }),
        }),
        // 长期记忆（跨会话）：查看 / 手动添加 / 删除 / 清空
        memoryList: () => request('/api/agent/memory'),
        memoryAdd: (content, tags) => request('/api/agent/memory', {
            method: 'POST',
            body: JSON.stringify({ content, tags }),
        }),
        memoryDelete: (memId) => request(`/api/agent/memory/${encodeURIComponent(memId)}`, { method: 'DELETE' }),
        memoryClear: () => request('/api/agent/memory', { method: 'DELETE' }),
        // 高危工具审批回调（前端弹窗允许/拒绝 → 唤醒后端等待中的线程）
        approval: (requestId, approve) => request('/api/agent/approval', {
            method: 'POST',
            body: JSON.stringify({ request_id: requestId, approve }),
        }),
    };

    // ── 模型提供商 ──
    const providerApi = {
        list: () => request('/api/providers'),
        save: (data) => request('/api/providers', {
            method: 'POST',
            body: JSON.stringify(data),
        }),
        remove: (id) => request(`/api/providers/${id}`, {
            method: 'DELETE',
        }),
        setDefault: (id) => request(`/api/providers/${id}/default`, {
            method: 'POST',
        }),
        test: (id) => request(`/api/providers/${id}/test`, {
            method: 'POST',
        }),
        models: (id) => request(`/api/providers/${id}/models`),
    };

    // ── 界面状态与头像（v0.9.2 服务端持久化） ──
    const uiApi = {
        state: () => request('/api/ui/state'),
        saveState: (data) => request('/api/ui/state', {
            method: 'POST',
            body: JSON.stringify(data),
        }),
        uploadAvatar: async (role, file) => {
            const formData = new FormData();
            formData.append('file', file);
            const res = await fetch(BASE_URL + `/api/avatar/${role}`, {
                method: 'POST',
                body: formData,
            });
            if (!res.ok) {
                let detail = `上传失败: HTTP ${res.status}`;
                try {
                    const j = await res.json();
                    detail = j.detail || detail;
                } catch (e) { /* ignore */ }
                throw new Error(detail);
            }
            return res.json();
        },
        removeAvatar: (role) => request(`/api/avatar/${role}`, {
            method: 'DELETE',
        }),
    };

    // ── 知识库 ──
    const knowledgeApi = {
        list: () => request('/api/knowledge/list'),
        create: (kbSlug) => request('/api/knowledge/create', {
            method: 'POST',
            body: JSON.stringify({ kb_slug: kbSlug }),
        }),
        delete: (kbSlug) => request('/api/knowledge/delete', {
            method: 'POST',
            body: JSON.stringify({ kb_slug: kbSlug }),
        }),
        rebuild: (kbSlug) => request('/api/knowledge/rebuild', {
            method: 'POST',
            body: JSON.stringify({ kb_slug: kbSlug }),
        }),
        uploadFiles: async (files, kbSlug) => {
            const formData = new FormData();
            for (const file of files) {
                formData.append('files', file);
            }
            formData.append('kb_slug', kbSlug);
            const res = await fetch(BASE_URL + '/api/knowledge/upload', {
                method: 'POST',
                body: formData,
            });
            if (!res.ok) {
                let detail = `上传失败: HTTP ${res.status}`;
                try {
                    const j = await res.json();
                    detail = j.detail || detail;
                } catch (e) { /* ignore */ }
                throw new Error(detail);
            }
            return res.json();
        },
        supportedExtensions: () => request('/api/knowledge/supported-extensions'),
    };

    window.SWApi = {
        system: systemApi,
        session: sessionApi,
        chat: chatApi,
        knowledge: knowledgeApi,
        provider: providerApi,
        ui: uiApi,
        agent: agentApi,
    };
})();
