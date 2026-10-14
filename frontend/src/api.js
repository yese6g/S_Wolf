/**
 * api.js - SilverWolf 前端 API 层
 * 封装所有后端接口调用。同源部署（FastAPI 托管静态资源），全部走相对路径。
 * 暴露全局对象: window.SWApi
 */
(function () {
    'use strict';

    const BASE_URL = '';
    const nativeFetch = window.fetch.bind(window);
    // 同源本机页面自动领取 HttpOnly Cookie；远程部署由用户填访问令牌。
    const authReady = nativeFetch('/api/auth/bootstrap', { credentials: 'same-origin' }).then(async res => {
        if (!res.ok && !sessionStorage.getItem('sw_access_token')) {
            const token = prompt('输入工作台访问令牌。服务端文件：data/auth_token。');
            if (token) sessionStorage.setItem('sw_access_token', token.trim());
        }
        if (!res.ok && sessionStorage.getItem('sw_access_token')) {
            await nativeFetch('/api/auth/login', {method:'POST', headers:{Authorization:'Bearer '+sessionStorage.getItem('sw_access_token')}, credentials:'same-origin'});
        }
    }).catch(() => {});
    async function fetch(url, options = {}) {
        await authReady;
        const headers = new Headers(options.headers || {});
        const token = sessionStorage.getItem('sw_access_token');
        if (token) headers.set('Authorization', 'Bearer ' + token);
        const res = await nativeFetch(url, { ...options, headers, credentials: 'same-origin' });
        // 令牌失效（如服务端轮换）时后端返回 401；通知界面亮出登录闸门。
        // 登录接口自身的 401 不广播，避免闸门里点登录失败也触发。
        if (res.status === 401 && !String(url).includes('/api/auth/')) {
            window.dispatchEvent(new CustomEvent('sw-auth-required'));
        }
        return res;
    }

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
        logout:async()=>{try{return await request('/api/auth/logout',{method:'POST'});}finally{sessionStorage.removeItem('sw_access_token');}},
        rotate:async()=>{const result=await request('/api/auth/rotate',{method:'POST'});sessionStorage.removeItem('sw_access_token');return result;},
        login:async(token='')=>{const response=await nativeFetch(token?'/api/auth/login':'/api/auth/bootstrap',{method:token?'POST':'GET',credentials:'same-origin',headers:token?{Authorization:'Bearer '+token}:{}});if(!response.ok)throw new Error('登录没通过。远程访问请填写现有令牌。');if(token)sessionStorage.setItem('sw_access_token',token);return response.json();},
    };

    // ── 会话管理 ──
    const sessionApi = {
        list: () => request('/api/sessions'),
        create: (title, workspace) => request('/api/sessions', {
            method: 'POST',
            body: JSON.stringify(workspace ? { title, workspace } : { title }),
        }),
        get: (id) => request(`/api/sessions/${id}`),
        // 会话的计划块（执行前分步计划），切换到会话时恢复侧栏展示用
        plan: (id) => request(`/api/sessions/${id}/plan`),
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
                    // 工作模式下带上工作区：服务端自动建会话时用它把会话挂到该文件夹下
                    workspace: opts.workspace || null,
                    use_rag: opts.useRag,
                    kb_slug: opts.kbSlug || 'agent',
                    kb_slugs: opts.kbSlugs,
                    provider_id: opts.providerId || null,
                    model: opts.model || null,
                    attachments: opts.attachments || null,
                    // v2.0.0 Agent 开关：是否自动调用工具、是否允许联网、最大工具轮数
                    agent_mode: opts.agentMode,
                    enable_web: opts.enableWeb,
                    max_steps: opts.maxSteps,
                    run_id: opts.runId || null,
                    last_event_id: opts.lastEventId || 0,
                    resume: !!opts.resume,
                    profile: opts.profile || null,
                    developer_grant: opts.developerGrant || null,
                }),
            });
        },
    };

    // ── Agent 能力（工具清单 / 自我修改记录 / RAG 文件） ──
    const agentApi = {
        tools: () => request('/api/agent/tools'),
        changes: (limit = 50) => request(`/api/agent/changes?limit=${limit}`),
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
        presets: () => request('/api/provider-presets'),
        discoverOllama: (url) => request('/api/ollama/discover?base_url=' + encodeURIComponent(url || 'http://localhost:11434/v1')),
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
    };

    // ── 界面状态与头像（v0.9.2 服务端持久化） ──
    const uiApi = {
        appearances:()=>request('/api/appearance-state'),
        saveAppearance:(mode,data)=>request('/api/appearance-state/'+mode,{method:'PUT',body:JSON.stringify(data)}),
        presets: () => request('/api/appearance-presets'),
        uploadBackground: async (file) => {
            const form = new FormData();
            form.append('file', file);
            const res = await fetch(BASE_URL + '/api/appearance-presets/backgrounds', {method:'POST', body:form});
            if (!res.ok) {
                const data = await res.json();
                throw new Error(data.detail || '背景上传失败');
            }
            return res.json();
        },
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
    };

    // ── 知识库 ──
    const knowledgeApi = {
        list: () => request('/api/knowledge/list'),
        files: (slug) => request('/api/agent/rag/files?kb_slug=' + encodeURIComponent(slug)),
        removeNote: (slug, filename) => request('/api/agent/rag/notes/' + encodeURIComponent(slug) + '/' + encodeURIComponent(filename), {method:'DELETE'}),
        create: (kbSlug) => request('/api/knowledge/create', {
            method: 'POST',
            body: JSON.stringify({ kb_slug: kbSlug }),
        }),
        delete: (kbSlug) => request('/api/knowledge/delete', {
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
        stopRun: id => request('/api/chat/runs/'+encodeURIComponent(id)+'/stop',{method:'POST'}),
        developer: (()=>{
            let grant='';
            return {authorize:async data=>{const result=await request('/api/developer/grant',{method:'POST',body:JSON.stringify(data)});grant=result.grant;return result;},
                action:(operation,data={})=>request('/api/developer/action',{method:'POST',body:JSON.stringify({...data,operation,grant})}),
                current:()=>grant,clear:()=>{grant='';},
                workspace:{
                    get:()=>request('/api/developer/workspace'),
                    set:data=>request('/api/developer/workspace',{method:'POST',body:JSON.stringify(data)}),
                    remove:data=>request('/api/developer/workspace/remove',{method:'POST',body:JSON.stringify(data)}),
                    browse:path=>request('/api/developer/workspace/browse'+(path?('?path='+encodeURIComponent(path)):'')),
                    preview:path=>request('/api/developer/workspace/preview?path='+encodeURIComponent(path)),
                }};
        })(),
        workbench: {
            hooks:()=>request('/api/tool-hooks'),
            saveHooks:rules=>request('/api/tool-hooks',{method:'PUT',body:JSON.stringify({rules})}),
            catalog: () => request('/api/capabilities'),
            definition: name => request('/api/tools/'+encodeURIComponent(name)),
            save: (name,data) => request('/api/tools/'+encodeURIComponent(name),{method:'PUT',body:JSON.stringify(data)}),
            remove: name => request('/api/tools/'+encodeURIComponent(name),{method:'DELETE'}),
            enabled: (name,value) => request('/api/tools/'+encodeURIComponent(name)+'/enabled',{method:'PUT',body:JSON.stringify({enabled:value})}),
            test: (name,args,approved=false) => request('/api/tools/'+encodeURIComponent(name)+'/test',{method:'POST',body:JSON.stringify({args,approved})}),
        },
        skills: {
            list: () => request('/api/skills'),
            get: id => request('/api/skills/'+encodeURIComponent(id)),
            save: (id,data) => request('/api/skills/'+encodeURIComponent(id),{method:'PUT',body:JSON.stringify(data)}),
            enabled: (id,enabled) => request('/api/skills/'+encodeURIComponent(id)+'/enabled',{method:'PUT',body:JSON.stringify({enabled})}),
            remove: (id,revision) => request('/api/skills/'+encodeURIComponent(id),{method:'DELETE',body:JSON.stringify({revision})}),
            import: async file => {const body=new FormData();body.append('file',file);const res=await fetch('/api/skills/import',{method:'POST',body});if(!res.ok)throw new Error((await res.json()).detail);return res.json();},
            export: async id => {const res=await fetch('/api/skills/'+encodeURIComponent(id)+'/export');if(!res.ok)throw new Error('导出失败');const url=URL.createObjectURL(await res.blob());const a=document.createElement('a');a.href=url;a.download=id+'.zip';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);},
        },
        jobs: {
            list: () => request('/api/jobs'),
            start: (data) => request('/api/jobs',{method:'POST',body:JSON.stringify(data)}),
            output: (id) => request('/api/jobs/'+id),
            kill: (id) => request('/api/jobs/'+id+'/kill',{method:'POST'}),
            remove: id => request('/api/jobs/'+id,{method:'DELETE'}),
        },
        workspace: {
            changes: () => request('/api/workspace/changes'),
            diff: (id) => request('/api/workspace/changes/'+id),
            rollback: (id) => request('/api/workspace/changes/'+id+'/rollback',{method:'POST'}),
        },
        mcp: {
            list: () => request('/api/mcp'),
            connect: (name) => request('/api/mcp/'+encodeURIComponent(name)+'/connect',{method:'POST'}),
            disconnect: (name) => request('/api/mcp/'+encodeURIComponent(name)+'/disconnect',{method:'POST'}),
            save: (name,data) => request('/api/mcp/'+encodeURIComponent(name),{method:'PUT',body:JSON.stringify(data)}),
            remove: name => request('/api/mcp/'+encodeURIComponent(name),{method:'DELETE'}),
            inspect: (name,kind) => request('/api/mcp/'+encodeURIComponent(name)+'/inspect?kind='+encodeURIComponent(kind)),
            query: (name,data) => request('/api/mcp/'+encodeURIComponent(name)+'/query',{method:'POST',body:JSON.stringify(data)}),
        },
        system: systemApi,
        session: sessionApi,
        chat: chatApi,
        knowledge: knowledgeApi,
        provider: providerApi,
        ui: uiApi,
        agent: agentApi,
    };
})();
