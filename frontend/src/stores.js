/**
 * stores.js - SilverWolf 前端状态层（替代 Pinia）
 * 基于 Vue 3 内置的 Vue.reactive 实现轻量响应式 store。
 * 暴露全局对象: window.SWStore = { session, chat, kb }
 */
(function () {
    'use strict';

    // 默认标签图标（v2.2.0 起为像素狼头，来自 icons.js）；
    // icons.js 未加载时回退成简单的字母 S，保证页面不空
    const FAVICON_S = (window.SWIcon && window.SWIcon.wolfFaviconURI)
        ? window.SWIcon.wolfFaviconURI()
        : "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text x='50' y='72' text-anchor='middle' font-size='62' font-weight='800' font-family='Segoe UI,sans-serif' fill='%23A79BFF'>S</text></svg>";

    // Vue 3 运行时缺失时（例如前端资源尚未就绪），提供空 store 占位，避免报错
    if (window.__NO_VUE__ || typeof Vue === 'undefined') {
        window.SWStore = { session: {}, chat: {}, kb: {} };
        return;
    }

    const { reactive } = Vue;

    // ── 会话 Store ──
    const session = reactive({
        sessions: [],
        currentId: null,
        currentSession: null,
        loading: false,

        async loadSessions() {
            this.loading = true;
            try {
                this.sessions = await SWApi.session.list();
            } catch (e) {
                console.error('加载会话列表失败:', e);
            } finally {
                this.loading = false;
            }
        },

        async loadSession(id) {
            this.currentId = id;
            try {
                const session = await SWApi.session.get(id);
                let plan=[];
                try{plan=(await SWApi.session.plan(id)).steps||[];}catch(_){}
                // 历史消息在库里 role 存的是 assistant，前端统一转成 bot，
                // 否则气泡不识别、Markdown 也不渲染（离开会话再回来就变纯文本的根因）
                this.currentSession = {
                    ...session,
                    messages: (session.messages || []).map(m => ({
                        ...m,
                        role: m.role === 'assistant' ? 'bot' : m.role,
                        // v2.0.0：历史消息里保存的 meta（token/工具/搜索/RAG）还原到气泡字段，
                        // 这样刷新或重新打开会话时，底部总结依然能显示。
                        ...(m.meta ? {
                            usage: m.meta.usage,
                            tools_used: m.meta.tools_used,
                            web_searches: m.meta.web_searches,
                            rag_hits: m.meta.rag_hits,
                            knowledge_updates: m.meta.knowledge_updates,
                            // v2.0.1：思考过程（狼尊时刻）随消息落库，重开后仍可展开查看
                            reasoning: m.meta.reasoning,
                            stopped: m.meta.stopped,
                            // v3.3.1：云端断链切本地的提示也随消息落库，回看历史仍可见
                            fallback_warning: m.meta.fallback_warning,
                        } : {}),
                    })),
                };
                const lastBot=[...this.currentSession.messages].reverse().find(m=>m.role==='bot');
                if(lastBot && plan.length)lastBot.plan=plan;
            } catch (e) {
                console.error('加载会话失败:', e);
                throw e;
            }
        },

        async renameSession(id, title) {
            await SWApi.session.rename(id, title);
            const s = this.sessions.find(x => x.id === id);
            if (s) s.title = title;
            if (this.currentId === id && this.currentSession) {
                this.currentSession.title = title;
            }
        },

        async deleteSession(id) {
            await SWApi.session.delete(id);
            this.sessions = this.sessions.filter(x => x.id !== id);
            if (this.currentId === id) {
                this.currentId = null;
                this.currentSession = null;
            }
        },

        clearCurrent() {
            this.currentId = null;
            this.currentSession = null;
        },
    });

    // ── 聊天 Store ──
    const chat = reactive({
        greeting: '说吧，目标是什么？',
        messages: [],
        sending: false,
        // v2.3.0：高危工具待审批（弹窗 allow/deny 后由 resolve 唤醒流式循环）
        pendingApproval: null,
        // v2.3.0：「停止生成」用的 AbortController（当前请求的取消句柄）
        abortController: null,
        activeRunId: null,
        authRequired:false,
        useRag: true,
        kbSlug: 'agent',
        kbSlugs: ['agent', 'python', 'cyberSecurity'],
        ragAutoLearn: true,
        toast: { show: false, message: '', type: 'info' },
        // 运行时配置（来自 /api/config 与 /api/health）
        config: null,
        health: null,
        // v0.6.0：模型提供商（工作台式模型管理）
        providers: [],
        activeProviderId: null,
        activeModel: null,
        // 模式在浏览器切换，外观逐模式同步到本机服务；权限仍由服务端决定。
        mode: 'normal',
        appearanceCatalog: { backgrounds: [], bot: [], user: [] },
        modeAppearances: {},
        uploadModes: {},
        appearanceReady: false,
        studioOpen: false,
        studioTab: 'analyze',
        commandPanel: '',
        activeDomain: 'agent',
        // 输入框芯片：可同时挂 1~3 个专业方向（例如 Python + 网络安全）。
        // 只作用于下一条消息，发完即清——不挂芯片的消息保持全量工具。
        pendingProfiles: [],
        // 工作区：工作模式下绑定的项目文件夹（形如 DSH 的工作区）。后端持久化，重启仍在。
        workspace: { root: '', default: '', is_default: true, stale: false, recent: [] },
        workspaceReady: false,
        // v0.8.0：聊天附件（文本/压缩包/代码/文档等）
        pendingFiles: [],
        uploading: false,
        // v0.9.0：外观（主题色 / 背景图，仅存本机浏览器）
        // v2.2.0：默认配色校正为银狼的蓝紫色系（官方基调），深色值 = 同系数调暗
        theme: {
            accent: '#7C6BFF',
            accentDeep: '#594DB8',
            cyan: '#3ED8F0',
            userBubble: '#6C4DE6',
            userDeep: '#4C36A1',
            bgImage: '',
            bgShade: 0.3,
        },
        themePresets: [
            { name: '银狼紫', accent: '#7C6BFF', cyan: '#3ED8F0', user: '#6C4DE6' },
            { name: '霓虹青', accent: '#0EA5E9', cyan: '#22D3EE', user: '#0369A1' },
            { name: '赛博绿', accent: '#10B981', cyan: '#34D399', user: '#047857' },
            { name: '熔岩橙', accent: '#F97316', cyan: '#FBBF24', user: '#C2410C' },
            { name: '幽灵蓝', accent: '#6366F1', cyan: '#818CF8', user: '#4338CA' },
            { name: '绯红', accent: '#EF4444', cyan: '#F87171', user: '#B91C1C' },
        ],
        // v0.9.1：头像（仅存本机浏览器）
        avatarSelections: {bot:'', user:''},
        avatars: { bot: '', user: '' },
        // v2.0.0：Agent 开关（服务端持久化；默认开启 Agent 与联网）
        agentMode: true,
        enableWeb: true,
        maxSteps: 4,
        // v2.0.4 起：Tavily 开关与上下文管理（都在设置里可调，服务端持久化）
        tavilyEnabled: true,
        // v2.1.x：上下文窗口对齐主流长上下文模型（4K ~ 1M），滑块按下面的档位走
        contextBudget: 200000,
        minKeepTurns: 10,
        unlimitedTurns: false,      // 无限轮数：一条历史都不丢
        compressContext: true,
        toolOutputLimit: 3000,
        replyMaxTokens: 8192,
        replyUnlimited: false,      // 无限长度：单次回复不设上限
        // 上下文窗口的滑块档位（不是线性：越往上档位越稀疏，方便一眼选常见值）
        ctxPresets: [8000, 16000, 32000, 64000, 128000, 200000, 320000, 512000, 1000000],
        replyPresets: [1024, 2048, 4096, 8192, 16384, 32768],

        _parseHex(hex) {
            const m = /^#?([0-9a-f]{6})$/i.exec((hex || '').trim());
            return m ? parseInt(m[1], 16) : null;
        },
        hexToRgb(hex) {
            const n = this._parseHex(hex);
            if (n == null) return '124, 107, 255';
            return `${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}`;
        },
        mixWithBlack(hex, ratio) {
            const n = this._parseHex(hex);
            if (n == null) return hex;
            const mix = (v) => Math.round(v * (1 - ratio)).toString(16).padStart(2, '0');
            return '#' + mix((n >> 16) & 255) + mix((n >> 8) & 255) + mix(n & 255);
        },
        mixWithWhite(hex, ratio) {
            const n = this._parseHex(hex);
            if (n == null) return hex;
            const mix = (v) => Math.round(v * (1 - ratio) + 255 * ratio).toString(16).padStart(2, '0');
            return '#' + mix((n >> 16) & 255) + mix((n >> 8) & 255) + mix(n & 255);
        },
        loadTheme() {
            try {
                const saved = localStorage.getItem('sw_theme');
                if (saved) this.theme = { ...this.theme, ...JSON.parse(saved) };
            } catch (e) { /* ignore */ }
            this.applyTheme();
        },
        saveTheme() {
            try {
                localStorage.setItem('sw_theme', JSON.stringify(this.theme));
            } catch (e) { /* ignore */ }
            this.applyTheme();
            this.saveAppearance();
        },
        // 模式名字不变，只分职责：银狼=聊天协作（不绑工作区），银狼 Lv.999=工作（绑定工作区做项目）。
        // 内部值仍是 normal/lv999——外观预设 id 前缀、CSS [data-mode]、ui_state.json 都用它。
        modeLabel() { return this.mode === 'lv999' ? '银狼 Lv.999' : '银狼'; },
        // 工作区名（取路径最后一段）与最近工作区名，供侧栏显示
        wsName() {
            const root = this.workspace.root || '';
            if (!root) return '默认工作区';
            const parts = root.split(/[\\/]/).filter(Boolean);
            return parts.length ? parts[parts.length - 1] : root;
        },
        wsBaseName(path) {
            const parts = String(path || '').split(/[\\/]/).filter(Boolean);
            return parts.length ? parts[parts.length - 1] : String(path || '');
        },
        async loadWorkspace() {
            try {
                this.workspace = await SWApi.developer.workspace.get();
                this.workspaceReady = true;
            } catch (e) { /* 未授权或后端未就绪时保持默认，不打扰聊天 */ }
        },
        async setWorkspace(path) {
            // approved 是后端要求的"已确认切换"标记：切换会关闭终端并影响变更账本归属
            this.workspace = await SWApi.developer.workspace.set(
                path ? { path, approved: true } : { default: true, approved: true });
            this.workspaceReady = true;
            return this.workspace;
        },
        async removeWorkspace(path) {
            // 只从常驻列表移除，不删目录；移除当前项时后端会回退默认工作区
            await SWApi.developer.workspace.remove({ path, approved: true });
            await this.loadWorkspace();
        },
        customBackgroundKey() { return this.mode === 'lv999' ? 'sw_custom_background_lv999' : 'sw_custom_background'; },
        modeAssets(group) {
            // 背景与银狼头像按模式过滤：银狼模式只列银狼素材，Lv.999 只列 Lv.999 素材。
            // 开拓者（user）头像是委托人自己的形象，与银狼皮肤无关，两套变体一直可选。
            if (group === 'user') return (this.appearanceCatalog[group] || []);
            return (this.appearanceCatalog[group] || []).filter(p => {
                if (p.origin === 'upload') {
                    const owners = this.uploadModes[p.id] || ['normal'];
                    return Array.isArray(owners) ? owners.includes(this.mode) : owners === this.mode;
                }
                return this.assetMode(p) === this.mode;
            });
        },
        assetMode(preset) {
            // 素材归属哪套外观：id 带 lv999- 前缀即 Lv.999，其余归银狼本尊。
            if (!preset) return this.mode;
            return String(preset.id || '').startsWith('lv999-') ? 'lv999' : 'normal';
        },
        markUpload(id) {
            const existing = this.uploadModes[id];
            const legacy = Object.values(this.appearanceCatalog).some(rows => Array.isArray(rows) && rows.some(p => p.id === id));
            const owners = Array.isArray(existing) ? existing : existing ? [existing] : legacy ? ['normal'] : [];
            this.uploadModes[id] = [...new Set([...owners, this.mode])];
        },
        captureAppearance() {
            return { theme: {...this.theme}, avatars: {...this.avatars}, selections: {...this.avatarSelections} };
        },
        saveAppearance(sync=true) {
            if (!this.appearanceReady) return;
            this.modeAppearances[this.mode] = this.captureAppearance();
            try {
                localStorage.setItem('sw_appearance_modes', JSON.stringify({version: 1, active: this.mode,
                    modes: this.modeAppearances, uploads: this.uploadModes}));
            } catch (_) { this.showToast('外观暂未保存。浏览器存储空间不足或已被禁用。', 'warning'); }
            if(sync && SWApi.ui.saveAppearance && !this.theme.bgImage?.startsWith('data:')) {
                this._appearancePending=this._appearancePending||{};
                this._appearancePending[this.mode]={appearance:this.captureAppearance(),uploads:{...this.uploadModes}};
                clearTimeout(this._appearanceSyncTimer);
                this._appearanceSyncTimer=setTimeout(()=>this.syncAppearances(),300);
            }
        },
        async syncAppearances() {
            if(this._syncingAppearance)return;
            this._syncingAppearance=true;
            try {
                while(Object.keys(this._appearancePending||{}).length){
                    const mode=Object.keys(this._appearancePending)[0],data=this._appearancePending[mode];delete this._appearancePending[mode];
                    const result=await SWApi.ui.saveAppearance(mode,{...data,revision:this._appearanceRevisions?.[mode]||'absent'});
                    this._appearanceRevisions=this._appearanceRevisions||{};this._appearanceRevisions[mode]=result.revision;
                }
            } catch(e) {this._appearancePending={};this.showToast('本机外观已保留；服务端未同步：'+e.message,'warning');}
            finally{this._syncingAppearance=false;}
        },
        async loadAppearances() {
            try { this.appearanceCatalog = await SWApi.ui.presets(); } catch (_) {}
            // 旧头像 URL 跟随服务端选择变化，先转成固定收藏 URL，才能隔离两个模式。
            for (const role of ['bot', 'user']) {
                const rows = this.appearanceCatalog[role] || [];
                const id = this.avatarSelections[role];
                const item = rows.find(p => p.id === id) || (!id && rows.find(p => p.file && p.file.split('.')[0] === role));
                if (this.avatars[role] && item) {
                    this.avatars[role] = item.url;
                    this.avatarSelections[role] = item.id;
                }
            }
            this.modeAppearances.normal = this.captureAppearance();
            let active = 'normal';
            try {
                const saved = JSON.parse(localStorage.getItem('sw_appearance_modes') || 'null');
                if (saved && saved.version === 1) {
                    for (const mode of ['normal', 'lv999']) {
                        const a = saved.modes && saved.modes[mode];
                        if (a && a.theme && a.avatars && a.selections) this.modeAppearances[mode] = a;
                    }
                    this.uploadModes = saved.uploads || {};
                    active = saved.active === 'lv999' ? 'lv999' : 'normal';
                }
            } catch (_) {}
            if(SWApi.ui.appearances)try {
                const state=await SWApi.ui.appearances();
                for(const mode of ['normal','lv999'])if(state.modes?.[mode])this.modeAppearances[mode]=state.modes[mode];
                this.uploadModes={...this.uploadModes,...state.uploads};this._appearanceRevisions=state.revisions||{};
            } catch(_){}
            this.appearanceReady = true;
            this.switchMode(active, true);
        },
        switchMode(mode, initial = false) {
            if (!['normal', 'lv999'].includes(mode) || !this.appearanceReady) return;
            if (this.sending || this.uploading) { this.showToast('等这轮结束，再切换模式。', 'info'); return; }
            if (!initial) this.saveAppearance(false);
            this.mode = mode;
            if (!this.modeAppearances[mode]) {
                const pick = group => (this.appearanceCatalog[group] || []).find(p => p.id.startsWith('lv999-'));
                this.modeAppearances[mode] = {
                    theme: {accent:'#A58BFF',accentDeep:'#64529F',cyan:'#F16CC4',userBubble:'#754ABB',userDeep:'#49276E',bgShade:0.45,bgImage:pick('backgrounds')?.url || ''},
                    avatars: {bot:pick('bot')?.url || '',user:pick('user')?.url || ''},
                    selections: {bot:pick('bot')?.id || 'default',user:pick('user')?.id || 'default'},
                };
            }
            const a = this.modeAppearances[mode];
            // 开拓者（user）头像是委托人自己的形象，不跟银狼换风格：切模式时保持不动。
            const keepUser = { url: this.avatars.user, id: this.avatarSelections.user };
            this.theme = {...a.theme};
            this.avatars = {...a.avatars, user: keepUser.url || a.avatars.user || ''};
            this.avatarSelections = {...a.selections, user: keepUser.id || a.selections.user || ''};
            document.documentElement.dataset.mode = mode;
            this.studioOpen = false;
            this.applyTheme(); this.applyFavicon(); this.saveAppearance(!initial);
        },
        applyTheme() {
            const t = this.theme;
            const accent = t.accent || '#7C6BFF';
            const cyan = t.cyan || '#3ED8F0';
            const user = t.userBubble || '#6C4DE6';
            const root = document.documentElement;
            const rgb = this.hexToRgb(accent);
            root.style.setProperty('--accent', accent);
            root.style.setProperty('--accent-rgb', rgb);
            root.style.setProperty('--accent-deep', t.accentDeep || this.mixWithBlack(accent, 0.28));
            root.style.setProperty('--accent-bright', this.mixWithWhite(accent, 0.45));
            root.style.setProperty('--accent-glow', `rgba(${rgb}, 0.35)`);
            root.style.setProperty('--border-glow', `rgba(${rgb}, 0.45)`);
            root.style.setProperty('--neon-cyan', cyan);
            root.style.setProperty('--cyan-rgb', this.hexToRgb(cyan));
            root.style.setProperty('--user-bubble', user);
            root.style.setProperty('--user-rgb', this.hexToRgb(user));
            root.style.setProperty('--user-deep', t.userDeep || this.mixWithBlack(user, 0.3));
            if (t.bgImage) {
                root.style.setProperty('--bg-image', `url("${t.bgImage}")`);
                // 用户上传背景后加一层暗色遮罩，保证文字可读
                root.style.setProperty(
                    '--bg-overlay',
                    `linear-gradient(180deg, rgba(7,4,19,${Number(t.bgShade ?? 0.3)}) 0%, rgba(7,4,19,${Math.min(0.85,Number(t.bgShade ?? 0.3)+0.2)}) 100%)`
                );
            } else {
                root.style.removeProperty('--bg-image');
                root.style.removeProperty('--bg-overlay');
            }
        },
        setThemeAccent(accent, cyan, user) {
            this.theme.accent = accent;
            this.theme.accentDeep = this.mixWithBlack(accent, 0.28);
            this.theme.cyan = cyan;
            this.theme.userBubble = user;
            this.theme.userDeep = this.mixWithBlack(user, 0.3);
            this.saveTheme();
        },
        applyThemePreset(p) {
            if (p) this.setThemeAccent(p.accent, p.cyan, p.user);
        },
        setCustomAccent(color) {
            if (color) this.setThemeAccent(color, this.theme.cyan, this.theme.userBubble);
        },
        setBgImage(dataUrl) {
            this.theme.bgImage = dataUrl || '';
            this.saveTheme();
        },
        clearBgImage() {
            this.theme.bgImage = '';
            this.saveTheme();
        },
        useBackgroundPreset(url) {
            // 连换两个本机预设时，也不能把之前的自定义背景备份覆盖掉。
            if (this.theme.bgImage && !this.theme.bgImage.startsWith('/static/presets/')
                && !this.theme.bgImage.startsWith('/api/appearance-presets/')) {
                try { localStorage.setItem(this.customBackgroundKey(), this.theme.bgImage); } catch (_) {}
            }
            // 背景决定模式：选了 Lv.999 的背景就切到 Lv.999，选了银狼的切回银狼。
            const preset = (this.appearanceCatalog.backgrounds || []).find(p => p.url === url);
            const target = this.assetMode(preset);
            if (target !== this.mode) {
                this.switchMode(target);
                if (this.mode !== target) return;   // 生成中被挡下时不落地，避免半途换皮
            }
            this.setBgImage(url);
        },
        restoreCustomBackground() {
            const value = localStorage.getItem(this.customBackgroundKey());
            if (value) this.setBgImage(value);
            else this.showToast('还没保存过自定义背景，先上传一张。', 'info');
        },
        async useAvatarPreset(role, id) {
            try {
                // 固定资源 URL + 分模式本地选择；切换皮肤不修改后端权限或另一套头像。
                const rows = this.appearanceCatalog[role] || [];
                const result = rows.find(p => id ? p.id === id : p.origin === 'upload');
                if (!result) { this.showToast('还没有这张图片。先上传，之后随时切回。', 'info'); return; }
                if (role === 'bot') {
                    // 银狼头像绑定模式：选了 Lv.999 的头像就切到 Lv.999。
                    const target = this.assetMode(result);
                    if (target !== this.mode) {
                        this.switchMode(target);
                        if (this.mode !== target) return;
                    }
                }
                // role === 'user'（开拓者）只换图，不动模式 —— 委托人形象与银狼的皮肤无关。
                this.avatars[role] = result.url || '';
                this.avatarSelections[role] = result.id || '';
                this.applyFavicon();
                this.saveAppearance();
            } catch (e) { this.showToast('头像切换失败：' + e.message, 'error'); }
        },
        async loadUiState() {
            // v0.9.2：知识库 / RAG 开关 / 头像全部由服务端持久化，重启与换浏览器都不丢
            try {
                const st = await SWApi.ui.state();
                if (st) {
                    if (st.kb_slug) this.kbSlug = st.kb_slug;
                    if (Array.isArray(st.kb_slugs)) this.kbSlugs = st.kb_slugs;
                    if (typeof st.rag_auto_learn === 'boolean') this.ragAutoLearn = st.rag_auto_learn;
                    if (typeof st.use_rag === 'boolean') this.useRag = st.use_rag;
                    if (typeof st.agent_mode === 'boolean') this.agentMode = st.agent_mode;
                    if (typeof st.enable_web === 'boolean') this.enableWeb = st.enable_web;
                    if (typeof st.max_steps === 'number') this.maxSteps = st.max_steps;
                    if (typeof st.tavily_enabled === 'boolean') this.tavilyEnabled = st.tavily_enabled;
                    if (typeof st.context_budget === 'number') this.contextBudget = st.context_budget;
                    if (typeof st.min_keep_turns === 'number') this.minKeepTurns = st.min_keep_turns;
                    if (typeof st.unlimited_turns === 'boolean') this.unlimitedTurns = st.unlimited_turns;
                    if (typeof st.context_compress === 'boolean') this.compressContext = st.context_compress;
                    if (typeof st.tool_output_limit === 'number') this.toolOutputLimit = st.tool_output_limit;
                    if (typeof st.reply_max_tokens === 'number') this.replyMaxTokens = st.reply_max_tokens;
                    if (typeof st.reply_unlimited === 'boolean') this.replyUnlimited = st.reply_unlimited;
                    this.avatarSelections.bot = st.avatar_bot_preset || '';
                    this.avatarSelections.user = st.avatar_user_preset || '';
                    this.avatars.bot = st.avatar_bot ? `/api/avatar/bot?t=${Date.now()}` : '';
                    this.avatars.user = st.avatar_user ? `/api/avatar/user?t=${Date.now()}` : '';
                    this.applyFavicon();
                }
            } catch (e) { /* 服务未就绪时保留默认值 */ }
        },
        async uploadAvatar(role, file) {
            const res = await SWApi.ui.uploadAvatar(role, file);
            this.avatarSelections[role] = res.id || '';
            this.markUpload(res.id);
            this.appearanceCatalog = await SWApi.ui.presets();
            const item=(this.appearanceCatalog[role]||[]).find(p=>p.id===res.id);
            if(item?.url)this.avatars[role]=item.url;
            this.applyFavicon();
            this.saveAppearance();
            return res;
        },
        async removeAvatar(role) {
            this.avatars[role] = '';
            this.avatarSelections[role] = 'default';
            this.applyFavicon();
            this.saveAppearance();
        },
        applyFavicon() {
            // v0.9.5：有银狼头像时浏览器标签图标用头像，否则用像素狼头（v2.2.0 起）
            const link = document.querySelector('link[rel="icon"]');
            if (!link) return;
            link.href = this.avatars.bot ? this.avatars.bot : FAVICON_S;
        },
        async importCustomBackground() {
            const backup = localStorage.getItem(this.customBackgroundKey());
            const current = this.theme.bgImage;
            // 旧备份和当前图可能不同，两张都收入收藏，不能只保留其中一张。
            for (const old of [...new Set([backup, current])]) {
                if (!old || !old.startsWith('data:image/')) continue;
                const blob = await (await fetch(old)).blob();
                const ext = {'image/jpeg':'jpg','image/png':'png','image/webp':'webp','image/gif':'gif'}[blob.type];
                if (!ext) continue;
                const result = await SWApi.ui.uploadBackground(new File([blob], 'background.' + ext, {type:blob.type}));
                this.markUpload(result.id);
                if (old === backup || !backup) localStorage.setItem(this.customBackgroundKey(), result.url);
                if (this.theme.bgImage === old) this.setBgImage(result.url);
            }
        },
        async uploadBackground(file) {
            const result = await SWApi.ui.uploadBackground(file);
            this.markUpload(result.id);
            localStorage.setItem(this.customBackgroundKey(), result.url);
            this.appearanceCatalog = await SWApi.ui.presets();
            this.setBgImage(result.url);
            return result;
        },
        async setKbEnabled(slug, value) {
            const next = value ? [...new Set([...this.kbSlugs, slug])] : this.kbSlugs.filter(s => s !== slug);
            try {
                await SWApi.ui.saveState({kb_slugs: next});
                this.kbSlugs = next;
                if (!next.includes(this.kbSlug)) this.kbSlug = next[0] || 'agent';
            } catch (e) { this.showToast('知识库设置没保存：' + e.message, 'error'); }
        },
        async setAutoLearn(value) {
            try {
                await SWApi.ui.saveState({rag_auto_learn: !!value});
                this.ragAutoLearn = !!value;
            } catch (e) { this.showToast('积累开关没保存：' + e.message, 'error'); }
        },
        setKbSlug(slug) {
            this.kbSlug = slug;
            SWApi.ui.saveState({ kb_slug: slug }).catch(() => {});
        },
        async setUseRag(value) {
            try {
                await SWApi.ui.saveState({use_rag: !!value});
                this.useRag = !!value;
            } catch (e) { this.showToast('检索开关没保存：' + e.message, 'error'); }
        },
        setAgentMode(value) {
            this.agentMode = !!value;
            SWApi.ui.saveState({ agent_mode: this.agentMode }).catch(() => {});
        },
        setEnableWeb(value) {
            this.enableWeb = !!value;
            SWApi.ui.saveState({ enable_web: this.enableWeb }).catch(() => {});
        },
        setMaxSteps(value) {
            const n = Math.max(1, Math.min(parseInt(value, 10) || 4, 12));
            this.maxSteps = n;
            SWApi.ui.saveState({ max_steps: n }).catch(() => {});
        },
        setTavilyEnabled(value) {
            this.tavilyEnabled = !!value;
            SWApi.ui.saveState({ tavily_enabled: this.tavilyEnabled }).catch(() => {});
        },
        setContextBudget(value) {
            const n = Math.max(4000, Math.min(parseInt(value, 10) || 32000, 1000000));
            this.contextBudget = n;
            SWApi.ui.saveState({ context_budget: n }).catch(() => {});
        },
        setMinKeepTurns(value) {
            const n = Math.max(1, Math.min(parseInt(value, 10) || 10, 20));
            this.minKeepTurns = n;
            SWApi.ui.saveState({ min_keep_turns: n }).catch(() => {});
        },
        setUnlimitedTurns(value) {
            this.unlimitedTurns = !!value;
            SWApi.ui.saveState({ unlimited_turns: this.unlimitedTurns }).catch(() => {});
        },
        setCompressContext(value) {
            this.compressContext = !!value;
            SWApi.ui.saveState({ context_compress: this.compressContext }).catch(() => {});
        },
        setToolOutputLimit(value) {
            const n = Math.max(500, Math.min(parseInt(value, 10) || 3000, 50000));
            this.toolOutputLimit = n;
            SWApi.ui.saveState({ tool_output_limit: n }).catch(() => {});
        },
        setReplyMaxTokens(value) {
            const n = Math.max(256, Math.min(parseInt(value, 10) || 8192, 32768));
            this.replyMaxTokens = n;
            SWApi.ui.saveState({ reply_max_tokens: n }).catch(() => {});
        },
        setReplyUnlimited(value) {
            this.replyUnlimited = !!value;
            SWApi.ui.saveState({ reply_unlimited: this.replyUnlimited }).catch(() => {});
        },

        // ── 上下文面板的展示辅助 ──
        // 把 token 数格式化成 "32K" / "1M" 这种一眼能读的写法
        fmtTokens(n) {
            const v = parseInt(n, 10) || 0;
            if (v >= 1000000) return (v / 1000000).toFixed(v % 1000000 === 0 ? 0 : 1) + 'M';
            if (v >= 1000) return Math.round(v / 1000) + 'K';
            return String(v);
        },
        // 滑块位置 → 档位下标（用户手输的数值会自动吸附到最近的档位）
        ctxSliderIndex() {
            const list = this.ctxPresets;
            let best = 0, bestDiff = Infinity;
            list.forEach((v, i) => {
                const d = Math.abs(v - this.contextBudget);
                if (d < bestDiff) { bestDiff = d; best = i; }
            });
            return best;
        },
        replySliderIndex() {
            const list = this.replyPresets;
            let best = 0, bestDiff = Infinity;
            list.forEach((v, i) => {
                const d = Math.abs(v - this.replyMaxTokens);
                if (d < bestDiff) { bestDiff = d; best = i; }
            });
            return best;
        },
        // 当前窗口大约等于多少字（中文按 1 token ≈ 1.4 字估算，给用户一个直观量级）
        ctxChineseChars() {
            return Math.round(this.contextBudget * 1.4);
        },

        _normalizeProviders(list) {
            return (list || []).map(p => ({
                id: p.id,
                name: p.name || p.id || '未命名提供商',
                kind: p.kind === 'local' ? 'local' : 'cloud',
                base_url: p.base_url || '',
                model: p.model || '',
                models: Array.isArray(p.models) ? p.models.filter(Boolean) : [],
                is_default: !!p.is_default,
                has_api_key: !!p.has_api_key,
                api_key_masked: p.api_key_masked || null,
            }));
        },

        modelLabel() {
            const p = this.providers.find(x => x.id === this.activeProviderId);
            if (!p) return '未选择模型';
            // v2.2.0：不再拼 emoji（本地/云端图标由模板里的 sw-icon 渲染）
            return `${p.name || p.id || '未命名提供商'} · ${this.activeModel || p.model || '未指定'}`;
        },

        // 输入框模型菜单用：当前提供商的类型 / 名字 / 正在用的模型名
        activeProvider() {
            return this.providers.find(x => x.id === this.activeProviderId) || null;
        },
        activeKind() {
            const p = this.activeProvider();
            return p ? p.kind : '';
        },
        activeModelName() {
            const p = this.activeProvider();
            return this.activeModel || (p && p.model) || '未选择模型';
        },

        modelList(p) {
            // v0.7.0：输入框下拉只显示用户在设置里显式添加的模型
            return (p.models || []).filter(Boolean);
        },

        displayModels(p) {
            return this.modelList(p).slice(0, 100);
        },

        currentModelKey() {
            return (this.activeProviderId || '') + '::' + (this.activeModel || '');
        },

        applyConfig(cfg) {
            this.config = cfg;
            this.greeting = cfg.assistant_greeting || this.greeting;
            this.providers = this._normalizeProviders(cfg.providers);
            let target = null;
            let savedModel = null;
            try {
                const sp = localStorage.getItem('sw_active_provider');
                savedModel = localStorage.getItem('sw_active_model');
                target = this.providers.find(p => p.id === sp) || null;
            } catch (e) { /* ignore */ }
            if (!target) {
                target = this.providers.find(p => p.is_default)
                    || this.providers[0]
                    || null;
            }
            if (target) {
                this.setActiveProvider(target.id, savedModel || target.models[0] || target.model);
            }
        },

        setActiveProvider(providerId, model) {
            const p = this.providers.find(x => x.id === providerId);
            if (!p) return;
            const firstAdded = (p.models && p.models.length) ? p.models[0] : '';
            this.activeProviderId = p.id;
            this.activeModel = model || firstAdded || p.model || '';
            try {
                localStorage.setItem('sw_active_provider', p.id);
                localStorage.setItem('sw_active_model', this.activeModel);
            } catch (e) { /* ignore */ }
        },

        async loadProviders(preferredId) {
            try {
                const res = await SWApi.provider.list();
                this.providers = this._normalizeProviders(res.providers);
                const cur = this.providers.find(p => p.id === (preferredId || this.activeProviderId));
                const target = cur
                    || this.providers.find(p => p.is_default)
                    || this.providers[0];
                if (target) this.setActiveProvider(target.id, this.activeModel || target.models[0] || target.model);
                return this.providers;
            } catch (e) {
                console.error('加载模型提供商失败:', e);
                return [];
            }
        },

        async saveProvider(data) {
            const saved = await SWApi.provider.save(data);
            this.showToast('提供商已保存', 'success');
            await this.loadProviders(this.activeProviderId);
            return saved;
        },

        async removeProvider(id) {
            await SWApi.provider.remove(id);
            this.showToast('提供商已删除', 'success');
            if (this.activeProviderId === id) {
                this.activeProviderId = null;
                this.activeModel = null;
            }
            await this.loadProviders(this.activeProviderId);
        },

        async setDefaultProvider(id) {
            await SWApi.provider.setDefault(id);
            this.showToast('已设为默认提供商', 'success');
            await this.loadProviders(this.activeProviderId);
        },

        async testProvider(id) {
            const res = await SWApi.provider.test(id);
            if (res.ok) {
                this.showToast(`连接成功，可用模型: ${(res.models || []).join(', ')}`, 'success');
            } else {
                this.showToast('连接失败: ' + (res.message || '未知错误'), 'error');
            }
            return res;
        },

        // ── 聊天附件 ──
        attachFiles(fileList) {
            const arr = Array.from(fileList || []);
            for (const f of arr) {
                if (this.pendingFiles.some(x => x.name === f.name && x.size === f.size)) continue;
                this.pendingFiles.push({ name: f.name, size: f.size, file: f });
            }
        },

        removePendingFile(index) {
            if (index >= 0 && index < this.pendingFiles.length) {
                this.pendingFiles.splice(index, 1);
            }
        },

        clearPendingFiles() {
            this.pendingFiles = [];
        },

        async uploadPendingFiles() {
            if (!this.pendingFiles.length) return [];
            this.uploading = true;
            try {
                const res = await SWApi.chat.uploadAttachments(this.pendingFiles.map(x => x.file));
                const records = (res && res.files) || [];
                const failed = records.filter(r => r.error);
                if (failed.length) {
                    this.showToast(
                        '部分附件上传失败: ' + failed.map(f => `${f.name}: ${f.error}`).join('; '),
                        'warning'
                    );
                }
                return records.filter(r => !r.error);
            } finally {
                this.uploading = false;
            }
        },

        showToast(message, type = 'info') {
            this.toast = { show: true, message, type };
            clearTimeout(this._toastTimer);
            this._toastTimer = setTimeout(() => {
                this.toast.show = false;
            }, 3200);
        },

        addMessage(role, content, extra = {}) {
            const msg = Object.assign({
                id: Date.now() + Math.random(),
                role,
                content,
            }, extra);
            this.messages.push(msg);
            // 注意 Vue 3 响应式陷阱：push 后必须返回数组里的响应式代理，
            // 否则外部直接改 msg.content 不会触发视图更新 ——
            // 这就是「流式输出不显示，刷新后才出现」的根因。
            return this.messages[this.messages.length - 1];
        },

        clearMessages() {
            this.messages = [];
        },

        async sendMessage(text) {
            if (this.sending) return false;
            const content = (text || '').trim() || (this.pendingFiles.length ? '请分析这些附件' : '');
            if (!content && !this.pendingFiles.length) return false;

            // v0.7.0：必须先在设置里为该提供商添加模型
            const curProvider = this.providers.find(x => x.id === this.activeProviderId);
            if (!curProvider) {
                this.showToast('还没有可用的模型提供商，请先打开设置添加', 'warning');
                return false;
            }
            if (!this.activeModel) {
                this.showToast(`提供商「${curProvider.name}」还没有模型，请先在设置中添加`, 'warning');
                return false;
            }

            this.sending = true;
            let attachments = [];
            let botMsg = null;
            let requestController = null;
            // v3.3.1：生成期间用户可能切走会话——记录发起时的会话与脱离状态，
            // 切走后不再回写 currentId，流式输出只落在发起会话的气泡上。
            const originSessionId = session.currentId;
            let newSessionId = null;
            let detached = false;

            try {
                // v2.3.0：每次发送挂一个新的取消句柄（停止生成按钮用）
                this.abortController = new AbortController();
                requestController = this.abortController;
                const abortSignal = this.abortController.signal;
                const runId = Array.from(crypto.getRandomValues(new Uint8Array(16)),n=>n.toString(16).padStart(2,'0')).join('');
                this.activeRunId = runId;
                // v0.8.0：先把待发附件上传拿到引用，再随消息发送
                if (this.pendingFiles.length) {
                    attachments = await this.uploadPendingFiles();
                    this.clearPendingFiles();
                }

                // 本条消息的专业方向（由输入框芯片指定，可多个）：发出去就消费掉，只影响这一条。
                const messageProfiles = this.pendingProfiles.slice();
                this.addMessage('user', content, {
                    ...(attachments.length ? { attachments } : {}),
                    ...(messageProfiles.length ? { profiles: messageProfiles } : {}),
                });
                if (messageProfiles.length) this.pendingProfiles = [];
                botMsg = this.addMessage('bot', '', { streaming: true });

                const streamOptions = {
                        sessionId: session.currentId,
                        useRag: this.useRag,
                        kbSlug: this.kbSlug,
                        kbSlugs: this.kbSlugs,
                        providerId: this.activeProviderId,
                        model: this.activeModel,
                        attachments: attachments.map(a => ({ name: a.name, path: a.path })),
                        // v2.0.0：Agent 相关开关（自动调用工具 / 联网 / 最大轮数）
                        agentMode: this.agentMode,
                        enableWeb: this.enableWeb,
                        maxSteps: this.maxSteps,
                        // v2.3.0：停止生成的取消信号
                        signal: abortSignal,
                        runId,
                        profiles: messageProfiles,
                        developerGrant:this.mode==='lv999'?SWApi.developer.current():null,
                    };
                const response = await SWApi.chat.stream(content,streamOptions);

                if (!response.ok) {
                    let detail = `HTTP ${response.status}`;
                    try {
                        const j = await response.json();
                        detail = j.detail || detail;
                    } catch (e) { /* ignore */ }
                    throw new Error(detail);
                }

                let reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';
                let fullText = '';
                let fullReasoning = '';
                let streamError = null;
                let sequence=0, packetId=0, discardPacket=false, completed=false, reconnects=0;

                while (true) {
                    let part, timer;
                    try {
                        part=await Promise.race([reader.read(),new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('流读取超时')),35000);})]);
                        if(part.done && !completed)throw new Error('连接中断');
                    } catch(e) {
                        if(abortSignal.aborted || completed || reconnects>=3)throw e;
                        reconnects++;
                        try{await reader.cancel();}catch(_){}
                        const retry=await SWApi.chat.stream(content,{...streamOptions,resume:true,lastEventId:sequence});
                        if(!retry.ok)throw new Error(retry.status===410?'续传缓存已过期，回答已保存到会话历史。':'续传失败：HTTP '+retry.status);
                        reader=retry.body.getReader();buffer='';this.showToast('连接已恢复，继续接收。','info');
                        continue;
                    } finally {clearTimeout(timer);}
                    const {done,value}=part;
                    if(done)break;

                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop();

                    for (const line of lines) {
                        if(line.startsWith('id: ')) {packetId=Number(line.slice(4));discardPacket=packetId<=sequence;continue;}
                        if (!line.startsWith('data: ')) continue;
                        if(discardPacket)continue;
                        const data = line.slice(6);
                        if (data === '[DONE]') {completed=true;sequence=Math.max(sequence,packetId);continue;}
                        try {
                            const parsed = JSON.parse(data);
                            sequence=Math.max(sequence,packetId);
                            if(parsed.stopped)botMsg.stopped=true;
                            // 会话 ID（新会话由后端创建）。只有仍停在发起会话时才跟随；
                            // 用户中途切走 → detached，之后的包不再改 currentId。
                            if (parsed.session_id) {
                                newSessionId = parsed.session_id;
                                if (!detached) {
                                    if (session.currentId === originSessionId || session.currentId === newSessionId) {
                                        session.currentId = newSessionId;
                                    } else {
                                        detached = true;
                                    }
                                }
                            }
                            // 内容增量
                            if (parsed.content) {
                                fullText += parsed.content;
                                botMsg.content = fullText;
                            }
                            // 中间步骤的"过程话"（"搜索结果全是导航站，没用"）：从回答里撤回，
                            // 转进思考过程。不做这一步的话，回答气泡里全是碎碎念而没有答案。
                            if (parsed.demote) {
                                const dropped = parsed.demote;
                                // 与后端落库同口径：只在尾部匹配时撤下（过程话紧跟正文末尾）。
                                // 匹配不上就保留 —— 全局删除会误伤正文里天然的相同短语。
                                if (fullText.endsWith(dropped)) {
                                    fullText = fullText.slice(0, fullText.length - dropped.length);
                                }
                                botMsg.content = fullText;
                                const trimmed = dropped.trim();
                                if (trimmed) {
                                    fullReasoning += (fullReasoning ? '\n' : '') + trimmed;
                                    botMsg.reasoning = fullReasoning;
                                }
                            }
                            // 思考过程增量（DeepSeek 等 reasoning 模型）
                            if (parsed.reasoning) {
                                fullReasoning += parsed.reasoning;
                                botMsg.reasoning = fullReasoning;
                            }
                            // 后端流式异常
                            if (parsed.error) {
                                streamError = parsed.error;
                            }
                            // v2.0.0：Agent 事件
                            // 1) 高危工具请求授权：挂起读取，等用户「允许/拒绝」
                            //    （弹窗按钮会调 /api/agent/approval 并执行 resolve）
                            //    60 秒没人点就自动继续——后端线程同样会超时自动拒绝，不会两边卡死
                            if (parsed.tool_approval) {
                                this.pendingApproval = {
                                    requestId: parsed.tool_approval.request_id,
                                    name: parsed.tool_approval.name,
                                    args: parsed.tool_approval.args || {},
                                    resolve: null,
                                };
                                let timeoutId;
                                await new Promise((resolve) => {
                                    this.pendingApproval.resolve = resolve;
                                    timeoutId = setTimeout(resolve, 60000);
                                });
                                // 用户提前点了允许/拒绝也要收掉兜底定时器，别让它白挂 60 秒
                                clearTimeout(timeoutId);
                                this.pendingApproval = null;
                            }
                            // 1) 工具调用开始
                            if (parsed.tool_call) {
                                botMsg.tools = botMsg.tools || [];
                                botMsg.tools.push({
                                    name: parsed.tool_call.name,
                                    args: parsed.tool_call.args,
                                    status: 'running',
                                });
                            }
                            // 2) 工具调用结果
                            if (parsed.tool_result) {
                                const list = botMsg.tools || (botMsg.tools = []);
                                const last = [...list].reverse().find(t => t.name === parsed.tool_result.name);
                                if (last) {
                                    last.ok = parsed.tool_result.ok;
                                    last.summary = parsed.tool_result.summary;
                                    last.status = 'done';
                                } else {
                                    list.push({ ...parsed.tool_result, status: 'done' });
                                }
                            }
                            // 2.1) 任务计划清单（update_plan 工具，前端渲染进度）
                            if (parsed.plan) {
                                botMsg.plan = parsed.plan;
                            }
                            // 3) 额度统计（结束时后端会发一次）
                            if (parsed.usage) {
                                botMsg.usage = parsed.usage;
                            }
                            // 4) Agent 降级提示（模型不支持工具调用等）
                            if (parsed.warning) {
                                botMsg.agent_warning = parsed.warning;
                            }
                            // 云端→本地回退提示：独立读取，不再藏在 rag_used 分支里
                            //（后端回退包只有 fallback_warning，没有 rag_used，之前永远读不到）
                            if (parsed.fallback_warning) {
                                botMsg.fallback_warning = parsed.fallback_warning;
                            }
                            // 元信息（最后一个数据块）
                            if (parsed.rag_used !== undefined) {
                                botMsg.rag_used = parsed.rag_used;
                                botMsg.rag_sources = parsed.rag_sources || [];
                                if (parsed.provider_id) {
                                    botMsg.provider_id = parsed.provider_id;
                                }
                                if (parsed.model) {
                                    botMsg.model = parsed.model;
                                }
                                // 5) Agent 汇总（工具 / 联网来源 / RAG 命中 / token）
                                if (parsed.tools_used) botMsg.tools_used = parsed.tools_used;
                                if (parsed.web_searches) botMsg.web_searches = parsed.web_searches;
                                if (Array.isArray(parsed.rag_hits) && parsed.rag_hits.length) {
                                    botMsg.rag_hits = parsed.rag_hits;
                                }
                                if (parsed.knowledge_updates) botMsg.knowledge_updates = parsed.knowledge_updates;
                                if (parsed.usage) botMsg.usage = parsed.usage;
                                if (parsed.agent_mode !== undefined) botMsg.agent_mode = parsed.agent_mode;
                            }
                        } catch (e) { /* 忽略非 JSON 行 */ }
                    }
                }

                botMsg.streaming = false;
                if (streamError) {
                    botMsg.agent_warning = '出错了：' + streamError;
                    if(!fullText)botMsg.content=botMsg.agent_warning;
                } else if (!fullText) {
                    botMsg.content = '…（无内容返回）';
                }

                // 新会话：记录后端返回的 session_id；列表刷新与当前会话镜像在 finally 统一做
                if (newSessionId && !detached) {
                    session.currentId = newSessionId;
                }
                return true;
            } catch (err) {
                if (botMsg) {
                    botMsg.streaming = false;
                    // v2.3.0：用户主动停止 → 不是报错，保留已生成的部分并注明
                    if (err && err.name === 'AbortError') {
                        botMsg.content = botMsg.content || '（已停止生成）';
                        botMsg.stopped = true;
                    } else {
                        botMsg.agent_warning = '请求失败：' + err.message;
                        if(!botMsg.content)botMsg.content=botMsg.agent_warning;
                        this.showToast(err.message, 'error');
                    }
                } else if (!(err && err.name === 'AbortError')) {
                    this.showToast(err.message, 'error');
                }
                // 用户消息已经挂上了就当成发送成功（不把文本退回输入框）
                return !!botMsg;
            } finally {
                await session.loadSessions();
                if (newSessionId && !detached) {
                    const found = session.sessions.find(s => s.id === newSessionId);
                    if (found) {
                        session.currentSession = { ...found, messages: this.messages.slice() };
                    }
                }
                if(this.abortController===requestController){
                    this.sending = false;
                    this.abortController = null;
                    this.activeRunId = null;
                }
            }
        },
        // v2.3.0：停止生成（取消当前流式请求）
        stopGenerating() {
            if(this.activeRunId)SWApi.stopRun(this.activeRunId).catch(()=>{});
            if (this.abortController) {
                try { this.abortController.abort(); } catch (e) { /* ignore */ }
                // 挂起中的审批弹窗也一并解除，避免等 60 秒超时
                if (this.pendingApproval && this.pendingApproval.resolve) {
                    this.pendingApproval.resolve(false);
                    this.pendingApproval = null;
                }
            }
            this.sending = false;
            const last = this.messages[this.messages.length - 1];
            if (last && last.role === 'bot' && last.streaming) {
                last.streaming = false;
                if (!last.content) last.content = '（已停止生成）';
                last.stopped = true;
            }
        },
    });

    // ── 知识库 Store ──
    const kb = reactive({
        knowledgeBases: [],
        uploading: false,
        uploadResults: [],
        supportedExtensions: [],

        async loadKnowledgeBases() {
            try {
                this.knowledgeBases = await SWApi.knowledge.list();
            } catch (e) {
                console.error('加载知识库列表失败:', e);
            }
        },

        async loadSupportedExtensions() {
            try {
                const res = await SWApi.knowledge.supportedExtensions();
                this.supportedExtensions = res.extensions || [];
            } catch (e) {
                console.error('加载支持的文件类型失败:', e);
            }
        },

        async uploadFiles(files, kbSlug) {
            this.uploading = true;
            this.uploadResults = [];
            try {
                this.uploadResults = await SWApi.knowledge.uploadFiles(files, kbSlug);
                await this.loadKnowledgeBases();
                return this.uploadResults;
            } finally {
                this.uploading = false;
            }
        },

        createKb(kbSlug) {
            return SWApi.knowledge.create(kbSlug);
        },

        deleteKb(kbSlug) {
            return SWApi.knowledge.delete(kbSlug);
        },
    });

    // 任一 API 收到 401（令牌失效/被轮换）时亮出登录闸门；幂等，重复触发无害。
    window.addEventListener('sw-auth-required', () => { chat.authRequired = true; });

    window.SWStore = { session, chat, kb };
})();
