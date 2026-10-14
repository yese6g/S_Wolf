/**
 * app.js - SilverWolf 前端应用（Vue 3 本地运行时版）
 * 组件全部使用模板字符串，由本地 vue.global.prod.js（完整版，含模板编译器）
 * 在运行时编译，无构建步骤、无 CDN 依赖。
 * 需要 frontend/vendor/vue.global.prod.js（缺失时由 index.html 给出提示）。
 * 图标统一走 icons.js 的 sw-icon 组件（像素风），模板里不再出现 emoji。
 */
(function () {
    'use strict';

    // Vue 3 缺失时直接返回（index.html 已显示友好提示）
    if (window.__NO_VUE__ || typeof Vue === 'undefined' || typeof Vue.createApp !== 'function') {
        return;
    }

    const { session, chat, kb } = window.SWStore;

    // ══════════════════════════════════════════════════
    // 组件定义（模板语法为 Vue 3 兼容写法）
    // ══════════════════════════════════════════════════
    const components = {};

    // 弹层共用键盘边界，避免焦点落到被遮挡的聊天或设置按钮。
    function dialogKey(event, root, close) {
        if (event.key === 'Escape') { event.preventDefault(); close(); return; }
        if (event.key !== 'Tab') return;
        const items = [...root.querySelectorAll('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),a[href],[tabindex="0"]')].filter(el => el.getClientRects().length);
        if (!items.length) return;
        const index = items.indexOf(document.activeElement);
        if (index < 0 || (event.shiftKey && index === 0) || (!event.shiftKey && index === items.length-1)) {
            event.preventDefault(); (event.shiftKey ? items[items.length-1] : items[0]).focus();
        }
    }
    function focusDialog(vm, opened, selector) {
        if (opened) { vm._returnFocus = document.activeElement; vm.$nextTick(() => vm.$el.querySelector(selector)?.focus()); }
        else vm.$nextTick(() => { if (vm._returnFocus?.isConnected) vm._returnFocus.focus(); });
    }

    // 拖拽调宽（抽屉左缘 / 文件树分栏）：宽度写进 :root 的 CSS 变量，
    // 面板宽度与 .main 的让位边距共用变量，拖动时布局同步收放；松手回调存 localStorage。
    function varPx(name, fallback) {
        const v = parseInt(getComputedStyle(document.documentElement).getPropertyValue(name), 10);
        return Number.isFinite(v) ? v : fallback;
    }
    function storedPx(key, fallback) {
        const v = parseInt(localStorage.getItem(key) || '', 10);
        return Number.isFinite(v) && v > 0 ? v : fallback;
    }
    function dragResize(event, derive, min, maxOf, onMove, onDone) {
        event.preventDefault();
        document.body.classList.add('sw-resizing');
        const move = ev => onMove(Math.min(Math.max(derive(ev), min), maxOf()));
        const up = () => {
            document.body.classList.remove('sw-resizing');
            window.removeEventListener('pointermove', move);
            window.removeEventListener('pointerup', up);
            if (onDone) onDone();
        };
        window.addEventListener('pointermove', move);
        window.addEventListener('pointerup', up);
    }

    // 三个专业方向：加号菜单、输入框芯片、工作台面板都从这里取，避免三处各写一份
    const DOMAINS = [
        { id: 'agent', label: 'Agent', icon: 'bot', hint: '任务拆解、工具调用与执行记录' },
        { id: 'python', label: 'Python', icon: 'monitor', hint: '环境与依赖核对、静态检查' },
        { id: 'cyberSecurity', label: '网络安全', icon: 'shield', hint: '地址范围与响应头被动体检' },
    ];
    function domainInfo(id) { return DOMAINS.find(d => d.id === id) || DOMAINS[0]; }

    // 工具返回的是 {ok, content, meta}：content 已经是给人看的正文（含结论与结构化摘要），
    // 直接把整个信封 JSON 化会让面板塞满转义符；只有拿不到正文时才回退到 JSON。
    function renderToolResult(res) {
        if (!res || typeof res !== 'object') return String(res ?? '');
        if (typeof res.content === 'string' && res.content.trim()) return res.content;
        return JSON.stringify(res, null, 2);
    }

    // 消息气泡
    components['message-bubble'] = {
        props: ['message'],
        data() {
            return { chat, markdownBlocks: [], markdownTail: '' };
        },
        watch: {
            'message.content': {immediate:true, handler() { this.queueMarkdown(); }},
            'message.streaming'() { this.queueMarkdown(); },
        },
        beforeUnmount() { clearTimeout(this._markdownTimer); },
        methods: {
            queueMarkdown() {
                if (!this.message.streaming) { clearTimeout(this._markdownTimer); this._markdownTimer = null; this.renderMarkdown(); return; }
                if (!this._markdownTimer) this._markdownTimer = setTimeout(() => { this._markdownTimer = null; this.renderMarkdown(); },100);
            },
            renderMarkdown() {
                const m = this.message, text = m.content || '', render = window.SWMarkdown?.render;
                if (m.role !== 'bot' || !render) return;
                if (!m.streaming) { this.markdownBlocks = text ? [render(text)] : []; this.markdownTail = ''; this._scanAt = 0; this._blockAt = 0; this._observed = 0; return; }
                if (this._scanAt == null || text.length < this._observed) {
                    this._scanAt = 0; this._blockAt = 0; this._inFence = false; this.markdownBlocks = [];
                }
                let end;
                while ((end = text.indexOf('\n',this._scanAt)) !== -1) {
                    const line = text.slice(this._scanAt,end);
                    if (/^\x60\x60\x60[\w+-]*\s*$/.test(line)) this._inFence = !this._inFence;
                    if (!this._inFence && !line.trim()) {
                        const block = text.slice(this._blockAt,end+1);
                        if (block.trim()) this.markdownBlocks.push(render(block));
                        this._blockAt = end+1;
                    }
                    this._scanAt = end+1;
                }
                this._observed = text.length;
                this.markdownTail = text.slice(this._blockAt);
            },
            reasoningTokens() {
                const u = this.message.usage || {};
                const n = u.reasoning_tokens ?? u.completion_tokens_details?.reasoning_tokens;
                return typeof n === 'number' && Number.isFinite(n) && n >= 0 ? n : null;
            },
            // ── v2.0.0：气泡底部「本轮总结」的辅助方法 ──
            totalWebCount() {
                return (this.message.web_searches || [])
                    .reduce((n, s) => n + ((s.sources || []).length), 0);
            },
            flatWebSources() {
                const out = [];
                (this.message.web_searches || []).forEach(s => {
                    (s.sources || []).forEach(item => out.push(item));
                });
                return out;
            },
            topWebSources() {
                return this.flatWebSources().slice(0, 3);
            },
        },
        template: `
            <div class="message" :class="message.role">
                <div class="avatar">
                    <template v-if="message.role === 'user'">
                        <img v-if="chat.avatars.user" :src="chat.avatars.user" alt="你" class="avatar-img">
                        <sw-icon v-else name="user" :size="20"></sw-icon>
                    </template>
                    <template v-else>
                        <img v-if="chat.avatars.bot" :src="chat.avatars.bot" alt="银狼" class="avatar-img">
                        <sw-icon v-else name="wolf" :size="22" class="bot-avatar-default"></sw-icon>
                    </template>
                </div>
                <div class="bubble" :class="{ streaming: message.streaming }">
                    <details v-if="message.reasoning" class="thinking-block">
                        <summary>
                            <sw-icon name="bolt" :size="20" class="think-bolt"></sw-icon>
                            <span>思考过程</span>
                        </summary>
                        <div class="thinking-content">{{ message.reasoning }}</div>
                    </details>
                    <div v-if="message.attachments && message.attachments.length" class="msg-attachments">
                        <div v-for="a in message.attachments" :key="a.path || a.name" class="msg-attach-chip"
                            :title="'SHA256: ' + (a.sha256 || '')">
                            <sw-icon name="clip" :size="20"></sw-icon>
                            <span class="attach-name">{{ a.name }}</span>
                            <span class="attach-size">{{ a.size_label || a.size || '' }}</span>
                        </div>
                    </div>
                    <template v-if="message.role === 'bot' && message.content">
                        <div v-for="(block,i) in markdownBlocks" :key="i" class="markdown-body" v-html="block"></div>
                        <span v-if="markdownTail" class="stream-tail">{{markdownTail}}</span>
                    </template>
                    <span v-else>{{ message.content || '…' }}</span>
                    <!-- v2.3.0：任务计划进度清单（update_plan 工具） -->
                    <div v-if="message.plan && message.plan.length" class="plan-block">
                        <div class="plan-title">
                            <sw-icon name="check" :size="20"></sw-icon> 任务清单
                        </div>
                        <div v-for="(s, idx) in message.plan" :key="s.id || idx" class="plan-step" :class="s.status">
                            <span class="plan-dot"></span>
                            <span class="plan-text">{{ s.text }}</span>
                            <span v-if="s.status === 'running'" class="plan-badge">进行中</span>
                            <span v-else-if="s.status === 'done'" class="plan-badge done">完成</span>
                        </div>
                    </div>
                    <div v-if="message.fallback_warning" class="fallback-warning">
                        <sw-icon name="warning" :size="20"></sw-icon> {{ message.fallback_warning }}
                    </div>
                    <div v-if="message.stopped" class="stopped-label"><sw-icon name="warning" :size="18"></sw-icon> 已停止 · 已收到的内容已保留</div>
                    <div v-if="message.rag_sources && message.rag_sources.length" class="rag-sources">
                        <sw-icon name="book" :size="20"></sw-icon> RAG 来源: {{ message.rag_sources.join(', ') }}
                    </div>
                    <div v-if="message.model" class="model-tag">
                        <sw-icon name="chip" :size="20"></sw-icon> {{ message.model }}
                    </div>
                    <div v-for="note in (message.knowledge_updates || [])" :key="note.filename" class="rag-sources">
                        <sw-icon name="book" :size="20"></sw-icon> 已积累 · {{note.kb_slug}} · {{note.title}}（{{note.status}}）
                    </div>
                    <!-- v2.0.0：Agent 本轮总结（token / 工具 / 联网来源 / RAG 命中） -->
                    <div v-if="message.usage || message.reasoning || (message.tools_used && message.tools_used.length) || (message.web_searches && message.web_searches.length) || (message.rag_hits && message.rag_hits.length)"
                        class="msg-footer">
                        <div v-if="message.usage" class="footer-line usage-line">
                            <sw-icon name="coin" :size="20"></sw-icon> 本次消耗 {{ message.usage.total_tokens }} tokens
                            <span class="usage-dim">（输入 {{ message.usage.prompt_tokens }} / 输出 {{ message.usage.completion_tokens }}<template v-if="message.usage.source !== 'provider'"> · {{message.usage.source==='mixed'?'上报与估算混合':'估算'}}</template>）</span>
                            <span v-if="message.usage.steps > 1" class="usage-dim">· {{ message.usage.steps }} 次模型调用</span>
                        </div>
                        <div v-if="message.usage || message.reasoning" class="footer-line usage-breakdown">
                            <sw-icon name="brain" :size="20"></sw-icon>
                            深度思考 · {{ reasoningTokens() === null ? '未单独上报' : reasoningTokens() + ' tokens' }}
                            <span v-if="reasoningTokens()===null && message.usage?.reasoning_known_steps" class="usage-dim">已上报部分 {{message.usage.reasoning_known_tokens}} tokens；{{message.usage.reasoning_unknown_steps}} 次未给明细。</span>
                            <span class="usage-dim">输出用量包含思考；缺明细时不拆算。</span>
                        </div>
                        <details v-if="message.usage?.calls?.length" class="footer-details"><summary>用量账本 · {{message.usage.calls.length}} 次模型调用</summary><div v-for="(call,i) in message.usage.calls" :key="i">{{call.purpose}} · 输入 {{call.prompt_tokens}} / 输出 {{call.completion_tokens}} · 思考 {{call.reasoning_tokens ?? '未单独上报'}}<span v-if="call.source==='estimate'"> · 估算</span></div></details>
                        <div v-if="message.tools_used && message.tools_used.length" class="footer-line">
                            <sw-icon name="wrench" :size="20"></sw-icon> 调用工具 {{ message.tools_used.length }} 次
                            <span v-for="(t, i) in message.tools_used" :key="i" class="mini-tag">{{ t.name }}</span>
                        </div>
                        <div v-if="message.web_searches && message.web_searches.length" class="footer-line">
                            <sw-icon name="globe" :size="20"></sw-icon> 联网搜索 {{ totalWebCount() }} 条
                            <span v-for="(s, i) in topWebSources()" :key="i" class="src-preview">
                                {{ s.title || s.url }}
                            </span>
                            <details v-if="flatWebSources().length > 3" class="footer-details">
                                <summary>查看全部来源</summary>
                                <ul class="src-list">
                                    <li v-for="(s, i) in flatWebSources()" :key="i">
                                        <a :href="s.url" target="_blank" rel="noopener">{{ s.title || s.url }}</a>
                                        <div class="src-url">{{ s.url }}</div>
                                    </li>
                                </ul>
                            </details>
                        </div>
                        <div v-if="message.rag_hits && message.rag_hits.length" class="footer-line">
                            <sw-icon name="book" :size="20"></sw-icon> RAG 命中
                            <span v-for="(r, i) in message.rag_hits" :key="i" class="mini-tag">
                                {{ (r.sources || []).join('、') || r.kb_slug }}
                            </span>
                        </div>
                    </div>
                </div>
            </div>
        `,
    };

    // 侧边栏
    components['sidebar-component'] = {
        data() {
            return { session, chat };
        },
        computed: {
            // 按会话归属的工作区分组；同一工作区大小写不一致时合并到一组（Windows 路径）
            sessionGroups() {
                const groups = new Map();
                for (const s of this.session.sessions) {
                    const path = s.workspace || '';
                    const key = path ? path.toLowerCase() : '';
                    if (!groups.has(key)) {
                        const parts = path.split(/[\\/]/).filter(Boolean);
                        groups.set(key, {
                            key,
                            path,
                            label: path ? (parts[parts.length - 1] || path) : '聊天',
                            sessions: [],
                        });
                    }
                    groups.get(key).sessions.push(s);
                }
                return [...groups.values()];
            },
            // 只有"确实分了组"才显示组标题：纯聊天用户看到的仍是原来的平铺列表
            showGroupHeads() {
                return this.sessionGroups.length > 1 || !!(this.sessionGroups[0] && this.sessionGroups[0].path);
            },
        },
        template: `
            <aside class="sidebar">
                <div class="workbench-strip"><span class="eyebrow">PUNKLORDE // LINK</span><span class="mode-tag">{{chat.mode === 'lv999' ? 'LV.999' : 'PLAYER 01'}}</span></div>
                <div class="sidebar-header">
                    <button class="new-chat-btn" @click="$emit('new-chat')">
                        <sw-icon name="plus" :size="20"></sw-icon>
                        <span>新开一局</span>
                    </button>
                </div>
                <div class="ws-section">
                    <div class="ws-head">
                        <span class="eyebrow">工作区</span>
                        <button class="ws-add" @click="$emit('workspace')" title="添加工作区（选择文件夹）">
                            <sw-icon name="plus" :size="16"></sw-icon>
                            <span>添加</span>
                        </button>
                    </div>
                    <template v-if="chat.mode === 'lv999'">
                        <p v-if="!chat.workspace.workspaces.length" class="ws-empty">
                            还没有工作区，点「添加」选一个文件夹。
                        </p>
                        <div v-for="w in chat.workspace.workspaces" :key="w.path" class="ws-row"
                            :class="{ active: w.active, missing: w.missing }">
                            <button class="ws-pick" :title="w.path" @click="$emit('workspace', w.path)">
                                <sw-icon name="folder" :size="18"></sw-icon>
                                <span class="ws-name">{{ w.name }}<small v-if="w.default">默认</small><small v-else-if="w.missing">目录不在了</small></span>
                                <span class="ws-path">{{ w.path }}</span>
                            </button>
                            <button v-if="!w.default" class="ws-remove" :title="'从列表移除 ' + w.name"
                                @click="$emit('workspace-remove', w)">
                                <sw-icon name="x" :size="16"></sw-icon>
                            </button>
                        </div>
                    </template>
                    <button v-else class="ws-hint" @click="chat.switchMode('lv999')">
                        切到工作模式即可绑定文件夹 →
                    </button>
                </div>
                <div class="sidebar-list">
                    <div v-if="session.loading" class="session-empty">读档中…</div>
                    <div v-else-if="session.sessions.length === 0" class="session-empty">
                        还没有存档<br>点上面「新开一局」开个头
                    </div>
                    <!-- 会话按所属工作区分组（像 DSH）：工作模式下建的会话挂在那个文件夹下，
                         聊天模式的会话归到「聊天」一组 -->
                    <template v-for="g in sessionGroups" :key="g.key || 'chat'">
                        <div v-if="showGroupHeads" class="session-group">
                            <sw-icon :name="g.path ? 'folder' : 'book'" :size="14"></sw-icon>
                            <span class="session-group-name" :title="g.path || '不属于任何工作区'">{{ g.label }}</span>
                            <span class="session-group-count">{{ g.sessions.length }}</span>
                        </div>
                        <div v-for="s in g.sessions" :key="s.id"
                            class="session-item" :class="{ active: s.id === session.currentId }"
                            role="button" tabindex="0" @keydown.enter="$emit('select', s.id)" @keydown.space.prevent="$emit('select', s.id)"
                            @click="$emit('select', s.id)">
                            <span class="session-item-title" :title="s.title">{{ s.title }}</span>
                            <div class="session-item-actions">
                                <button title="导出 Markdown" @click.stop="$emit('export', s.id)">
                                    <sw-icon name="download" :size="20"></sw-icon>
                                </button>
                                <button title="重命名" @click.stop="$emit('rename', s.id, s.title)">
                                    <sw-icon name="pencil" :size="20"></sw-icon>
                                </button>
                                <button class="delete-btn" title="删除" @click.stop="$emit('delete', s.id)">
                                    <sw-icon name="trash" :size="20"></sw-icon>
                                </button>
                            </div>
                        </div>
                    </template>
                </div>
            </aside>
        `,
    };

    // 顶栏
    components['header-bar'] = {
        data() {
            return { chat, modeMenu: false };
        },
        mounted() {
            this._dismiss = e => { if (!this.$el.contains(e.target)) this.modeMenu = false; };
            document.addEventListener('pointerdown', this._dismiss);
        },
        beforeUnmount() { document.removeEventListener('pointerdown', this._dismiss); },
        methods: {
            openModes() { this.modeMenu = !this.modeMenu; if (this.modeMenu) this.$nextTick(() => this.$refs.normalMode?.focus()); },
            selectMode(mode) { chat.switchMode(mode); this.modeMenu = false; this.$refs.modeTrigger?.focus(); },
            menuKey(e) {
                if (e.key === 'Escape') { this.modeMenu = false; this.$refs.modeTrigger?.focus(); }
                if (['ArrowDown','ArrowUp','Tab'].includes(e.key)) {
                    e.preventDefault();
                    (document.activeElement === this.$refs.normalMode ? this.$refs.fullMode : this.$refs.normalMode)?.focus();
                }
            },
            toggleRag() {
                if (!chat.kbSlugs.length) { chat.showToast('先在设置勾选知识库。', 'info'); return; }
                chat.setUseRag(!chat.useRag);
            },
        },
        template: `
            <div class="header">
                <div class="header-left">
                    <button ref="modeTrigger" class="logo mode-switch" @click="openModes" @contextmenu.prevent="openModes" :disabled="!chat.appearanceReady || chat.sending" :aria-expanded="modeMenu" aria-controls="mode-menu" title="切换银狼模式 · 也可右击"><span>{{chat.modeLabel()}}</span><sw-icon name="caret" :size="18"></sw-icon></button>
                    <span class="badge">{{chat.mode === 'lv999' ? 'CREATOR // LV.999' : '星核猎手 // ONLINE'}}</span>
                    <div v-if="modeMenu" id="mode-menu" class="mode-menu" role="menu" @keydown="menuKey">
                        <button ref="normalMode" role="menuitemradio" :aria-checked="chat.mode === 'normal'" @click="selectMode('normal')"><sw-icon name="wolf" :size="24"></sw-icon><span>银狼<small>聊天协作 · 不绑定工作区</small></span></button>
                        <button ref="fullMode" role="menuitemradio" :aria-checked="chat.mode === 'lv999'" @click="selectMode('lv999')"><sw-icon name="chip" :size="24"></sw-icon><span>银狼 Lv.999<small>绑定工作区 · 项目编辑 · 终端</small></span></button>
                    </div>
                </div>
                <div class="header-right">
                    <template v-if="chat.mode === 'lv999'">
                    <button class="icon-btn" :class="{ active: chat.studioOpen && chat.studioTab === 'terminal' }"
                        @click="chat.openStudio('terminal')" title="接管工作区管理终端：打开交互式命令行">
                        <sw-icon name="monitor" :size="22"></sw-icon>
                        <span>终端</span>
                    </button>
                    <button class="icon-btn" :class="{ active: chat.filesOpen }"
                        @click="chat.toggleFiles()" title="查看工作区文件结构，接管后可编辑">
                        <sw-icon name="folder" :size="22"></sw-icon>
                        <span>文件</span>
                    </button>
                    <button class="icon-btn" :class="{ active: chat.studioOpen && chat.studioTab === 'tools' }"
                        @click="chat.openStudio('tools')" title="查看与试运行工作区能力里的工具">
                        <sw-icon name="wrench" :size="22"></sw-icon>
                        <span>工具</span>
                    </button>
                    <button class="icon-btn" :class="{ active: chat.studioOpen && chat.studioTab === 'skills' }"
                        @click="chat.openStudio('skills')" title="管理 Skills：导入、编辑、开启工作方法">
                        <sw-icon name="book" :size="22"></sw-icon>
                        <span>技能</span>
                    </button>
                    <button class="icon-btn" :class="{ active: chat.studioOpen && chat.studioTab === 'tasks' }"
                        @click="chat.openStudio('tasks')" title="启动与查看后台 Agent 任务">
                        <sw-icon name="bolt" :size="22"></sw-icon>
                        <span>任务</span>
                    </button>
                    </template>
                    <span class="model-badge" :title="chat.modelLabel()">
                        <span class="model-badge-dot"></span>
                        <span class="model-badge-text">{{ chat.modelLabel() }}</span>
                    </span>
                    <button class="icon-btn" :class="{ active: chat.useRag && chat.kbSlugs.length }"
                        @click="toggleRag" :aria-pressed="!!(chat.useRag && chat.kbSlugs.length)" title="切换知识库检索，范围在设置中勾选">
                        <sw-icon name="book" :size="20"></sw-icon>
                        <span>{{ chat.useRag && chat.kbSlugs.length ? '知识库 · 开 ' + chat.kbSlugs.length : '知识库 · 关' }}</span>
                    </button>
                </div>
            </div>
        `,
    };

    // 聊天区
    components['chat-area'] = {
        data() {
            return { chat, followOutput: true };
        },
        computed: { tailProgress() { const m = chat.messages[chat.messages.length-1]; return m ? m.id + ':' + (m.content || '').length + ':' + (m.reasoning || '').length : ''; } },
        watch: {
            'chat.messages.length'() { this.scheduleScroll(); },
            tailProgress() { this.scheduleScroll(); },
        },
        beforeUnmount() { cancelAnimationFrame(this._scrollFrame); },
        methods: {
            scheduleScroll() {
                if (!this._scrollFrame) this._scrollFrame = requestAnimationFrame(() => { this._scrollFrame = null; this.scrollToBottom(); });
            },
            scrollToBottom() {
                const el = this.$refs.container;
                if (el && this.followOutput) el.scrollTop = el.scrollHeight;
            },
            onScroll() { const e = this.$refs.container; this.followOutput = e.scrollHeight-e.scrollTop-e.clientHeight < 100; },
        },
        template: `
            <div class="chat-container" ref="container" @scroll.passive="onScroll">
                <div v-if="chat.messages.length === 0" class="welcome">
                    <img v-if="chat.avatars.bot" :src="chat.avatars.bot" class="welcome-avatar" alt="银狼">
                    <sw-icon v-else name="wolf" :size="64" class="welcome-wolf"></sw-icon>
                    <h2>{{ chat.modeLabel() }}</h2>
                    <p class="welcome-tag">{{ chat.mode === 'lv999' ? 'SILVER WOLF // LV.999' : 'SILVER WOLF // 星核猎手' }}</p>
                    <p class="greeting">{{ chat.mode === 'lv999' ? '满级之后，轮到我改规则。' : chat.greeting }}</p>
                    <p class="status-line">当前使用 <strong>{{ chat.modelLabel() }}</strong></p>
                    <p class="status-line" style="margin-top:8px;">
                        RAG 知识库: <strong>{{ chat.useRag ? '已启用' : '已禁用' }}</strong>
                    </p>
                </div>
                <message-bubble v-for="m in chat.messages" :key="m.id" :message="m"></message-bubble>
            </div>
        `,
    };

    // 输入区
    components['input-area'] = {
        data() {
            return { chat, text: '', modelMenuOpen: false, capabilityMenu: false, commandIndex: 0, commandDismissed: false, commands: [
                {name:'/help', hint:'快捷指令'}, {name:'/status', hint:'当前运行状态'}, {name:'/model', hint:'切换已添加模型'}
            ] };
        },
        computed: {
            commandChoices() { return !this.commandDismissed && /^\/[a-z]*$/i.test(this.text.trim()) ? this.commands.filter(c => c.name.startsWith(this.text.trim().toLowerCase())) : []; },
            DOMAINS() { return DOMAINS; },
        },
        watch: {
            text() { this.commandIndex = 0; this.commandDismissed = false; },
        },
        mounted() {
            // 点菜单以外的地方自动收起（否则菜单会"粘"在屏幕上）
            this._onDocClick = (e) => {
                const box = this.$el && this.$el.querySelector('.model-select');
                if (box && !box.contains(e.target)) this.modelMenuOpen = false;
                const plus = this.$el && this.$el.querySelector('.plus-btn');
                const menu = this.$el && this.$el.querySelector('.capability-menu');
                if (this.capabilityMenu && plus && !plus.contains(e.target) && (!menu || !menu.contains(e.target))) {
                    this.capabilityMenu = false;
                }
            };
            document.addEventListener('click', this._onDocClick);
        },
        beforeUnmount() {
            document.removeEventListener('click', this._onDocClick);
        },
        methods: {
            toggleCapabilityMenu() { this.capabilityMenu = !this.capabilityMenu; },
            // 点一个方向 = 挂上/取消「仅本条」的芯片；可以同时挂多个（最多三个方向本身）。
            // 不做常驻切换：发完消息芯片即清，下一条不受影响。
            toggleDomain(id) {
                const list = chat.pendingProfiles;
                const at = list.indexOf(id);
                if (at >= 0) chat.pendingProfiles = list.filter(x => x !== id);
                else chat.pendingProfiles = [...list, id];
                chat.activeDomain = chat.pendingProfiles[chat.pendingProfiles.length - 1] || 'agent';
            },
            pickCapability(id) {
                this.toggleDomain(id);
                this.$nextTick(() => this.$refs.input?.focus());
            },
            domainInfo(id) { return domainInfo(id); },
            onKeydown(e) {
                if (e.isComposing || e.keyCode === 229) return;
                if (e.key === 'Escape') { this.modelMenuOpen = false; this.capabilityMenu = false; this.commandDismissed = true; chat.commandPanel = ''; return; }
                if (this.commandChoices.length && ['ArrowDown','ArrowUp','Tab'].includes(e.key)) {
                    e.preventDefault();
                    if (e.key === 'Tab') this.text = this.commandChoices[this.commandIndex].name;
                    else this.commandIndex = (this.commandIndex + (e.key === 'ArrowDown' ? 1 : -1) + this.commandChoices.length) % this.commandChoices.length;
                    return;
                }
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    if (this.commandChoices.length) this.text = this.commandChoices[this.commandIndex].name;
                    this.send();
                }
            },
            autoResize(e) {
                const el = e.target;
                el.style.height = 'auto';
                el.style.height = Math.min(el.scrollHeight, 120) + 'px';
            },
            toggleModelMenu() {
                if (this.chat.sending) return;
                this.modelMenuOpen = !this.modelMenuOpen;
                if (this.modelMenuOpen) this.$nextTick(() => this.$el.querySelector('.model-menu button')?.focus());
            },
            pickModel(provider, model) {
                const name = String(model || '').trim();
                if (!name) return;
                this.chat.setActiveProvider(provider.id, name);
                this.modelMenuOpen = false;
                this.$refs.input?.focus();
            },
            onAttachChange(e) {
                this.chat.attachFiles(e.target.files);
                e.target.value = '';
            },
            formatSize(bytes) {
                if (bytes == null) return '';
                if (bytes < 1024) return bytes + ' B';
                if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
                return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
            },
            async send() {
                if (this.chat.sending) return;
                if (!this.text.trim() && !this.chat.pendingFiles.length) return;
                const cmd = this.text.trim().toLowerCase();
                if (['/help','/status','/model'].includes(cmd)) {
                    this.text = '';
                    if (cmd === '/model') { this.modelMenuOpen = true; this.$nextTick(() => this.$el.querySelector('.model-menu button')?.focus()); }
                    else {
                        chat.commandPanel = cmd.slice(1);
                        if (cmd === '/status') {
                            chat.health = null;
                            try { chat.health = await SWApi.system.health(); }
                            catch (e) { chat.showToast('状态读取失败：' + e.message, 'error'); }
                        }
                    }
                    return;
                }
                if (/^\/[a-z][a-z0-9_-]*(?:\s.*)?$/i.test(this.text.trim())) {
                    chat.showToast('可用指令：/help、/status、/model。其他指令还没接入。', 'info'); return;
                }
                const content = this.text;
                this.text = '';
                const el = this.$refs.input;
                if (el) el.style.height = 'auto';
                // v3.3.1：没提供商/没模型时发送会被拦下（返回 false），
                // 此处要把已输入的正文交还输入框，不能清空了事。
                const started = await this.chat.sendMessage(content);
                if (started === false) {
                    this.text = content;
                    this.$nextTick(() => {
                        const input = this.$refs.input;
                        if (!input) return;
                        input.style.height = 'auto';
                        input.style.height = Math.min(input.scrollHeight, 120) + 'px';
                        input.focus();
                    });
                }
            },
        },
        template: `
            <div class="input-area">
                <div v-if="commandChoices.length" id="command-options" class="command-menu" role="listbox" aria-label="快捷指令">
                    <button v-for="(c,i) in commandChoices" :key="c.name" :id="'command-option-' + i" class="command-item" role="option" :aria-selected="i === commandIndex" @click="text = c.name; $refs.input.focus()"><code>{{c.name}}</code><span>{{c.hint}}</span></button>
                </div>
                <button class="plus-btn" :aria-expanded="capabilityMenu" aria-haspopup="menu"
                    @click="toggleCapabilityMenu" title="专业工作台：Agent / Python / 网络安全">
                    <sw-icon name="plus" :size="24"></sw-icon>
                </button>
                <div v-if="capabilityMenu" class="capability-menu" role="menu" aria-label="专业方向">
                    <button v-for="d in DOMAINS" :key="d.id" role="menuitemcheckbox"
                        :aria-checked="chat.pendingProfiles.includes(d.id)"
                        :class="{ picked: chat.pendingProfiles.includes(d.id) }" @click="pickCapability(d.id)">
                        <sw-icon :name="d.icon" :size="22"></sw-icon>
                        <span>{{ d.label }}<small>{{ d.hint }}</small></span>
                        <sw-icon v-if="chat.pendingProfiles.includes(d.id)" class="capability-check" name="check" :size="20"></sw-icon>
                    </button>
                    <p class="capability-tip">可多选，只作用于下一条消息；发完自动清空。</p>
                </div>
                <div class="input-box">
                    <div class="model-picker">
                        <!-- v2.1.0：原生 <select> 的下拉列表字号由系统控制、没法美化，
                             换成自绘菜单：分组更清楚、字号更大、当前项有勾选标记 -->
                        <div class="model-select" :class="{ open: modelMenuOpen }">
                            <button type="button" class="model-trigger" :disabled="chat.sending"
                                @click="toggleModelMenu" :title="chat.modelLabel()">
                                <sw-icon class="model-trigger-icon"
                                    :name="chat.activeKind() === 'local' ? 'monitor' : (chat.activeKind() ? 'cloud' : 'bot')"
                                    :size="20"></sw-icon>
                                <span class="model-trigger-text">
                                    <span class="model-trigger-name">{{ chat.activeModelName() }}</span>
                                    <span class="model-trigger-provider">{{ chat.activeProvider() ? chat.activeProvider().name : '还没有模型' }}</span>
                                </span>
                                <sw-icon class="model-trigger-caret" name="caret" :size="20"></sw-icon>
                            </button>

                            <div v-if="modelMenuOpen" class="model-menu" @keydown.escape.stop="modelMenuOpen = false; $refs.input.focus()">
                                <div v-if="!chat.providers.length" class="model-menu-empty">
                                    还没有添加模型 —— 去左下角的设置
                                    <sw-icon name="settings" :size="20"></sw-icon>
                                    里添加提供商和模型。
                                </div>
                                <template v-for="p in chat.providers" :key="p.id">
                                    <div class="model-menu-group">
                                        <sw-icon class="model-menu-group-icon"
                                            :name="p.kind === 'local' ? 'monitor' : 'cloud'" :size="20"></sw-icon>
                                        <span class="model-menu-group-name">{{ p.name }}</span>
                                        <span class="model-menu-group-kind">{{ p.kind === 'local' ? '本地' : '云端' }}</span>
                                    </div>
                                    <button v-if="chat.displayModels(p).length === 0" type="button"
                                        class="model-menu-item muted" @click="pickModel(p, p.model)">
                                        <span class="model-menu-name">{{ p.model || '未填写模型名' }}</span>
                                        <span class="model-menu-tag">默认</span>
                                    </button>
                                    <button v-for="m in chat.displayModels(p)" :key="p.id + '::' + m"
                                        type="button" class="model-menu-item"
                                        :class="{ active: chat.currentModelKey() === p.id + '::' + m }"
                                        @click="pickModel(p, m)">
                                        <span class="model-menu-name">{{ m }}</span>
                                        <span v-if="p.is_default && m === p.model" class="model-menu-tag">默认</span>
                                        <sw-icon v-if="chat.currentModelKey() === p.id + '::' + m"
                                            class="model-menu-check" name="check" :size="20"></sw-icon>
                                    </button>
                                </template>
                            </div>
                        </div>
                        <button class="attach-btn" @click="$refs.fileInput.click()"
                            :disabled="chat.sending" title="上传附件（文本/压缩包/代码/文档等）">
                            <sw-icon name="clip" :size="20"></sw-icon>
                        </button>
                        <input ref="fileInput" type="file" multiple style="display:none;" @change="onAttachChange">
                    </div>
                    <div v-if="chat.pendingProfiles.length" class="pending-domains">
                        <span v-for="id in chat.pendingProfiles" :key="id" class="domain-chip">
                            <sw-icon :name="domainInfo(id).icon" :size="18"></sw-icon>
                            <span>{{ domainInfo(id).label }}</span>
                            <button class="pf-remove" @click="toggleDomain(id)" :title="'取消 ' + domainInfo(id).label">
                                <sw-icon name="x" :size="18"></sw-icon>
                            </button>
                        </span>
                        <span class="domain-chip-scope">仅本条</span>
                    </div>
                    <div v-if="chat.pendingFiles.length" class="pending-files">
                        <span v-for="(f, i) in chat.pendingFiles" :key="i" class="pending-file-chip">
                            <sw-icon name="clip" :size="20"></sw-icon>
                            <span>{{ f.name }}</span>
                            <span class="pf-size">{{ formatSize(f.size) }}</span>
                            <button class="pf-remove" @click="chat.removePendingFile(i)" title="移除">
                                <sw-icon name="x" :size="20"></sw-icon>
                            </button>
                        </span>
                        <span v-if="chat.uploading" class="pf-uploading">上传中…</span>
                    </div>
                    <textarea ref="input" rows="1"
                        placeholder="交给我。输入 / 查看指令，Shift + Enter 换行"
                        aria-label="消息或快捷指令"
                        :aria-expanded="!!commandChoices.length" :aria-controls="commandChoices.length ? 'command-options' : null" :aria-activedescendant="commandChoices.length ? 'command-option-' + commandIndex : null" aria-autocomplete="list"
                        v-model="text" @input="autoResize" @keydown="onKeydown"></textarea>
                </div>
                <button class="send-btn" :class="{ sending: chat.sending }"
                    @click="chat.sending ? chat.stopGenerating() : send()"
                    :title="chat.sending ? '停止生成' : '发送'">
                    <span v-if="chat.sending" class="stop-square"></span>
                    <sw-icon v-else name="send" :size="24"></sw-icon>
                </button>
            </div>
        `,
    };

    // 设置弹窗（左下角齿轮按钮打开，左侧导航 + 右侧内容）
    components['settings-modal'] = {
        props: ['open'],
        data() {
            return {
                chat,
                kb,
                activeTab: 'providers',
                editing: null,
                providerSaving: false,
                newModelName: '',
                dragOver: false,
                newKbName: '',
                // v2.0.0：Agent 能力面板数据（工具清单 / 自我修改记录）
                agentTools: [],
                agentChanges: [],
                searchForm: { tavilyKey: '', firecrawlKey: '', tavilyConfigured: false, firecrawlConfigured: false },
                searchTestMsg: '',
                // v2.3.0：长期记忆面板数据（跨会话记忆的查看/删除）
                memories: [],
                memoryForm: { content: '', tags: '' },
                providerPresets: [],
                modelCandidates: [],
                scanningModels: false,
                scanMessage: '',
                appearancePresets: { backgrounds: [], bot: [], user: [] },
                learningNotes: [],
                jobs: [], selectedJob: null, jobMessage: '', jobBusy: false,
                workspaceChanges: [], selectedDiff: '', mcpServers: [],
                mcpBusy: false, mcpResult: '',
                server: { name: '', command: '', args: '[]', cwd: '', env_refs: '{}', category: 'core', transport: 'stdio', url: '', allow_loopback: false },
                queryServer: '', queryMethod: 'resources/read', queryParams: '{}', queryApproved: false,
            };
        },
        watch: { open(value) { focusDialog(this,value,'.settings-close'); } },
        mounted() {
            this.loadCatalogs();
            this._jobTimer = setInterval(() => {
                if (this.open && this.activeTab === 'tasks') this.loadTaskInfo();
            }, 2000);
        },
        beforeUnmount() { clearInterval(this._jobTimer); },
        methods: {
            dialogKey(e) { dialogKey(e,this.$el,() => this.$emit('close')); },
            async logout() {
                this.chat.stopGenerating();this.chat.studioOpen=false;this.chat.filesOpen=false;
                try{if(SWApi.developer.current())await SWApi.developer.action('revoke');await SWApi.system.logout();}
                catch(e){this.chat.showToast(e.message,'error');}
                finally{SWApi.developer.clear();this.chat.grant=null;this.chat.authRequired=true;this.chat.clearMessages();session.clearCurrent();session.sessions=[];this.$emit('close');}
            },
            async rotateToken() {try{await SWApi.system.rotate();SWApi.developer.clear();this.chat.showToast('令牌已轮换；其他客户端需重新登录。','success');}catch(e){this.chat.showToast(e.message,'error');}},
            async loadCatalogs() {
                try { await this.chat.importCustomBackground(); } catch (e) { this.chat.showToast('原背景仍保留在浏览器，暂时没存入收藏。', 'info'); }
                try { this.providerPresets = (await SWApi.provider.presets()).presets || []; } catch (_) {}
                try { this.appearancePresets = await SWApi.ui.presets(); this.chat.appearanceCatalog = this.appearancePresets; } catch (_) {}
            },
            async useProviderPreset(preset) {
                if (!this.editing || this.editing.id) this.newProvider();
                Object.assign(this.editing, {name:preset.name,kind:preset.kind,base_url:preset.base_url,
                    model:preset.models[0] || '',models:preset.models.length ? [preset.models[0]] : []});
                this.modelCandidates = preset.models.slice();
                this.scanMessage = preset.note || '';
                if (preset.kind === 'local') await this.scanOllama();
            },
            async scanOllama() {
                if (!this.editing || this.scanningModels) return;
                this.scanningModels = true;
                try {
                    const res = await SWApi.provider.discoverOllama(this.editing.base_url);
                    this.modelCandidates = res.models || [];
                    this.scanMessage = res.message;
                    if (res.ok && !this.editing.model && this.modelCandidates.length) this.pickCandidate(this.modelCandidates[0]);
                } catch (e) { this.scanMessage = e.message; }
                finally { this.scanningModels = false; }
            },
            providerKindChanged() {
                if (this.editing.kind === 'local') {
                    if (!this.editing.base_url) this.editing.base_url = 'http://localhost:11434/v1';
                    if (!this.editing.name) this.editing.name = '本地 Ollama';
                    this.scanOllama();
                }
            },
            pickCandidate(name) {
                if (!this.editing.models.includes(name)) this.editing.models.push(name);
                if (!this.editing.model) this.editing.model = name;
            },
            async loadTaskInfo() {
                try {
                    this.jobs = (await SWApi.jobs.list()).jobs || [];
                    this.workspaceChanges = (await SWApi.workspace.changes()).changes || [];
                    if (this.selectedJob) this.selectedJob = await SWApi.jobs.output(this.selectedJob.id);
                    this._taskPollError = null;
                } catch (e) {
                    // 2 秒轮询在断连时会每轮都进 catch：同一条错误只提示一次，恢复后再失败才再提示
                    if (this._taskPollError !== e.message) {
                        this._taskPollError = e.message;
                        this.chat.showToast('任务面板没连上：' + e.message, 'error');
                    }
                }
            },
            async startJob() {
                if (!this.jobMessage.trim() || this.jobBusy) return;
                this.jobBusy = true;
                try {
                    const row = await SWApi.jobs.start({message:this.jobMessage,provider_id:this.chat.activeProviderId,model:this.chat.activeModel});
                    this.jobMessage = ''; this.selectedJob = await SWApi.jobs.output(row.id); await this.loadTaskInfo();
                } catch (e) { this.chat.showToast('任务没启动：' + e.message, 'error'); }
                finally { this.jobBusy = false; }
            },
            async inspectJob(id) { this.selectedJob = await SWApi.jobs.output(id); },
            async killJob(id) { await SWApi.jobs.kill(id); await this.loadTaskInfo(); },
            jobApproval(job) {
                const events = job.events || [];
                const last = [...events].reverse().find(e => e.type === 'tool_approval');
                if (!last || events.slice(events.lastIndexOf(last)+1).some(e => e.type === 'tool_result' && e.name === last.name)) return null;
                return ['running','queued'].includes(job.status) ? last : null;
            },
            async approveJob(event, approved) {
                await SWApi.agent.approval(event.request_id, approved);
                await this.loadTaskInfo();
            },
            async showDiff(id) { this.selectedDiff = (await SWApi.workspace.diff(id)).diff || '文件内容未变。'; },
            async rollbackChange(id) {
                if (!confirm('把这次修改回滚？后续手工改动会被保护，不会强行覆盖。')) return;
                try { await SWApi.workspace.rollback(id); await this.loadTaskInfo(); }
                catch (e) { this.chat.showToast('回滚被拦下：' + e.message, 'error'); }
            },
            async loadMcp() {
                try { this.mcpServers = (await SWApi.mcp.list()).servers || []; }
                catch (e) { this.chat.showToast('MCP 列表没拉到：' + e.message, 'error'); }
            },
            async mcpAct(fn) {
                if (this.mcpBusy) return;
                this.mcpBusy = true;
                try { return await fn(); } catch (e) { this.chat.showToast('MCP 操作失败：' + e.message, 'error'); }
                finally { this.mcpBusy = false; }
            },
            saveServer() {this.mcpAct(async()=>{const {name,...spec}=this.server;await SWApi.mcp.save(name,{...spec,args:JSON.parse(spec.args),env_refs:JSON.parse(spec.env_refs)});await this.loadMcp();this.mcpResult=spec.transport==='http'?'MCP配置已保存；连接时访问上面的服务器。':'MCP配置已保存；连接时启动上面这条固定命令。';});},
            editServer(s) {this.server={...s,args:JSON.stringify(s.args||[]),env_refs:JSON.stringify(s.env_refs||{})};},
            connectServer(s) {this.mcpAct(async()=>{if(s.connected)await SWApi.mcp.disconnect(s.name);else await SWApi.mcp.connect(s.name);await this.loadMcp();});},
            inspectServer(s,kind) {this.queryServer=s.name;this.queryApproved=false;this.mcpAct(async()=>{this.mcpResult=JSON.stringify(await SWApi.mcp.inspect(s.name,kind),null,2);});},
            queryMcp() {this.mcpAct(async()=>{this.mcpResult=JSON.stringify(await SWApi.mcp.query(this.queryServer,{method:this.queryMethod,params:JSON.parse(this.queryParams),approved:this.queryApproved}),null,2);this.queryApproved=false;});},
            removeServer(s) {this.mcpAct(async()=>{await SWApi.mcp.remove(s.name);await this.loadMcp();});},
            showTab(tab) {
                this.activeTab = tab;
                if (tab === 'agent') this.loadAgentInfo();
                if (tab === 'memory') this.loadMemories();
                if (tab === 'tasks') this.loadTaskInfo();
                if (tab === 'mcp') this.loadMcp();
                if (tab === 'appearance') this.loadCatalogs();
            },
            // ── 长期记忆：查看 / 手动添加 / 删除 / 清空 ──
            async loadMemories() {
                try {
                    const res = await SWApi.agent.memoryList();
                    this.memories = res.items || [];
                } catch (e) { /* 服务未就绪时忽略 */ }
            },
            async addMemoryEntry() {
                const content = (this.memoryForm.content || '').trim();
                if (!content) return;
                try {
                    await SWApi.agent.memoryAdd(content, this.memoryForm.tags || '');
                    this.memoryForm.content = '';
                    this.memoryForm.tags = '';
                    this.chat.showToast('记忆已存档', 'success');
                    this.loadMemories();
                } catch (e) {
                    this.chat.showToast('存档失败: ' + e.message, 'error');
                }
            },
            async delMemory(memId) {
                const it = this.memories.find((m) => m.id === memId);
                if (!it || !confirm(`确定删除这条记忆吗？\n「${(it.content || '').slice(0, 60)}」`)) return;
                try {
                    await SWApi.agent.memoryDelete(memId);
                    this.loadMemories();
                } catch (e) {
                    this.chat.showToast('删除失败: ' + e.message, 'error');
                }
            },
            async clearMemories() {
                if (!this.memories.length) return;
                if (!confirm(`确定清空全部 ${this.memories.length} 条记忆吗？删了就真的忘了。`)) return;
                try {
                    await SWApi.agent.memoryClear();
                    this.memories = [];
                    this.chat.showToast('记忆已清空', 'success');
                } catch (e) {
                    this.chat.showToast('清空失败: ' + e.message, 'error');
                }
            },
            fmtMemoryTime(ts) {
                if (!ts) return '';
                return new Date(Number(ts) * 1000).toLocaleString('zh-CN', { hour12: false });
            },
            async loadAgentInfo() {
                try {
                    const res = await SWApi.agent.tools();
                    this.agentTools = res.tools || [];
                } catch (e) { /* 服务未就绪时忽略 */ }
                try {
                    const res = await SWApi.agent.changes(20);
                    this.agentChanges = res.changes || [];
                } catch (e) { /* ignore */ }
                try {
                    const st = await SWApi.ui.state();
                    this.searchForm.tavilyConfigured = !!st.tavily_configured;
                    this.searchForm.firecrawlConfigured = !!st.firecrawl_configured;
                } catch (e) { /* ignore */ }
            },
            async saveSearchConfig() {
                const payload = {};
                // 只有填了新 Key 才覆盖（留空表示保留服务端已保存的 Key）
                if (this.searchForm.tavilyKey.trim()) payload.tavily_api_key = this.searchForm.tavilyKey.trim();
                // Firecrawl 是可选的网页正文抓取服务，同样留空表示保留原值
                if (this.searchForm.firecrawlKey.trim()) payload.firecrawl_api_key = this.searchForm.firecrawlKey.trim();
                try {
                    await SWApi.ui.saveState(payload);
                    this.searchForm.tavilyKey = '';
                    this.searchForm.firecrawlKey = '';
                    this.searchForm.tavilyConfigured = this.searchForm.tavilyConfigured || !!payload.tavily_api_key;
                    this.chat.showToast('搜索配置已保存', 'success');
                } catch (e) {
                    this.chat.showToast('保存失败: ' + e.message, 'error');
                }
            },
            async testSearch(engine = 'auto') {
                this.searchTestMsg = '测试中…';
                try {
                    const r = await SWApi.agent.searchTest(engine);
                    this.searchTestMsg = r.ok
                        ? `${engine === 'auto' ? '自动' : engine} 可用：引擎 ${r.engine}，命中 ${r.count} 条`
                        : `${engine === 'auto' ? '自动' : engine} 不可用：${r.error || '未知错误'}`;
                } catch (e) {
                    this.searchTestMsg = '测试失败: ' + e.message;
                }
            },
            openProvider(p) {
                this.modelCandidates = [];
                this.scanMessage = '';
                this.editing = {
                    id: p.id,
                    name: p.name,
                    kind: p.kind,
                    base_url: p.base_url,
                    model: p.model,
                    api_key: '',
                    api_key_env:p.api_key_env || '',
                    clear_key: false,
                    is_default: !!p.is_default,
                    api_key_placeholder: p.has_api_key ? ('已保存: ' + (p.api_key_masked || '***')) : null,
                    models: (p.models || []).slice(),
                };
                this.activeTab = 'providers';
                if (p.kind === 'local') this.scanOllama();
            },
            newProvider() {
                this.modelCandidates = [];
                this.scanMessage = '';
                this.editing = {
                    id: null,
                    name: '',
                    kind: 'cloud',
                    base_url: '',
                    model: '',
                    api_key: '',
                    api_key_env:'',
                    clear_key: false,
                    is_default: false,
                    api_key_placeholder: null,
                    models: [],
                };
                this.activeTab = 'providers';
            },
            addModelToEdit() {
                const name = (this.newModelName || '').trim();
                if (!name || !this.editing) return;
                if (!this.editing.models.includes(name)) this.editing.models.push(name);
                this.newModelName = '';
            },
            removeModelFromEdit(model) {
                if (!this.editing) return;
                this.editing.models = this.editing.models.filter(m => m !== model);
                if (this.editing.model === model) this.editing.model = this.editing.models[0] || '';
            },
            async saveProviderForm() {
                const f = this.editing;
                if (!f || !f.name.trim() || !f.base_url.trim() || !f.model.trim()) {
                    this.chat.showToast('名称、接口地址、默认模型不能为空', 'warning');
                    return;
                }
                this.providerSaving = true;
                try {
                    const data = {
                        id: f.id || undefined,
                        name: f.name.trim(),
                        kind: f.kind,
                        base_url: f.base_url.trim(),
                        model: f.model.trim(),
                        models: f.models,
                        is_default: !!f.is_default,
                        api_key_env:f.api_key_env?.trim() || '',
                    };
                    if (f.api_key && f.api_key.trim()) data.api_key = f.api_key.trim();
                    else if (f.clear_key) data.api_key = '';
                    const saved = await this.chat.saveProvider(data);
                    const fresh = this.chat.providers.find(p => p.id === saved.id);
                    this.editing = null;
                    if (fresh) this.openProvider(fresh);
                    else this.newProvider();
                } catch (e) {
                    this.chat.showToast('保存失败: ' + e.message, 'error');
                } finally {
                    this.providerSaving = false;
                }
            },
            async removeProvider(id) {
                if (!confirm('确定删除该模型提供商吗？')) return;
                try {
                    await this.chat.removeProvider(id);
                    if (this.editing && this.editing.id === id) this.editing = null;
                } catch (e) {
                    this.chat.showToast('删除失败: ' + e.message, 'error');
                }
            },
            async setDefault(id) {
                try {
                    await this.chat.setDefaultProvider(id);
                } catch (e) {
                    this.chat.showToast('设置默认失败: ' + e.message, 'error');
                }
            },
            async testProvider(id) {
                try {
                    await this.chat.testProvider(id);
                } catch (e) {
                    this.chat.showToast('测试失败: ' + e.message, 'error');
                }
            },
            onFileChange(e) {
                const files = e.target.files;
                if (files && files.length) this.handleUpload(Array.from(files));
                e.target.value = '';
            },
            onDrop(e) {
                this.dragOver = false;
                const files = e.dataTransfer.files;
                if (files && files.length) this.handleUpload(Array.from(files));
            },
            async onBgUpload(e) {
                const file = e.target.files && e.target.files[0];
                e.target.value = '';
                if (!file) return;
                if (file.size > 3 * 1024 * 1024) {
                    this.chat.showToast('背景图请控制在 3MB 以内', 'warning');
                    return;
                }
                try {
                    await this.chat.uploadBackground(file);
                    await this.loadCatalogs();
                    this.chat.showToast('背景已加入收藏', 'success');
                } catch (e) { this.chat.showToast(e.message, 'error'); }
            },
            onAvatarUpload(role) {
                this._avatarRole = role;
                if (this.$refs.avatarInput) this.$refs.avatarInput.click();
            },
            onAvatarFile(e) {
                const file = e.target.files && e.target.files[0];
                e.target.value = '';
                if (!file) return;
                if (file.size > 2 * 1024 * 1024) {
                    this.chat.showToast('头像请控制在 2MB 以内', 'warning');
                    return;
                }
                const role = this._avatarRole || 'bot';
                this.chat.uploadAvatar(role, file)
                    .then(async () => { await this.loadCatalogs(); this.chat.showToast('头像已加入收藏', 'success'); })
                    .catch(err => this.chat.showToast('头像上传失败: ' + err.message, 'error'));
            },
            async handleUpload(files) {
                if (!this.chat.kbSlugs.includes(this.chat.kbSlug)) { this.chat.showToast('先勾选一个知识库。', 'info'); return; }
                try {
                    const results = await this.kb.uploadFiles(files, this.chat.kbSlug);
                    const successCount = results.filter(r => r.success).length;
                    const failCount = results.length - successCount;
                    this.chat.showToast(
                        `上传完成: 成功 ${successCount} 个${failCount > 0 ? `，失败 ${failCount} 个` : ''}`,
                        failCount > 0 ? 'warning' : 'success'
                    );
                } catch (e) {
                    this.chat.showToast('上传失败: ' + e.message, 'error');
                }
            },
            async createKb() {
                const name = this.newKbName.trim();
                if (!name) return;
                try {
                    const result = await this.kb.createKb(name);
                    if (result && result.success) {
                        this.chat.showToast('知识库创建成功', 'success');
                        this.newKbName = '';
                        await this.kb.loadKnowledgeBases();
                    } else {
                        this.chat.showToast((result && result.error) || '创建失败', 'error');
                    }
                } catch (e) {
                    this.chat.showToast('创建失败: ' + e.message, 'error');
                }
            },
            async deleteKb(kbSlug) {
                if (!confirm(`确定删除知识库 "${kbSlug}" 吗？`)) return;
                try {
                    const result = await this.kb.deleteKb(kbSlug);
                    if (result && result.success) {
                        this.chat.showToast('知识库已删除', 'success');
                        await this.kb.loadKnowledgeBases();
                    }
                } catch (e) {
                    this.chat.showToast('删除失败: ' + e.message, 'error');
                }
            },
            async loadLearningNotes() {
                try {
                    await this.kb.loadKnowledgeBases();
                    const groups = await Promise.all(this.chat.kbSlugs.map(async slug => {
                        const result = await SWApi.knowledge.files(slug);
                        return result.files.filter(f => /^note-[a-f0-9]{24}\.md$/.test(f.filename)).map(f => ({...f, kb_slug:slug}));
                    }));
                    this.learningNotes = groups.flat().sort((a,b) => b.modified_at-a.modified_at).slice(0,30);
                } catch (e) { this.chat.showToast(e.message, 'error'); }
            },
            async removeLearningNote(note) {
                if (!confirm('撤回这条经验？向量索引也会移除。')) return;
                try { await SWApi.knowledge.removeNote(note.kb_slug, note.filename); await this.loadLearningNotes(); await this.kb.loadKnowledgeBases(); }
                catch (e) { this.chat.showToast(e.message, 'error'); }
            },
        },
        template: `
            <div v-if="open" class="settings-overlay" @click.self="$emit('close')" @keydown="dialogKey">
                <div class="settings-dialog" role="dialog" aria-modal="true" aria-label="工作台设置">
                    <div class="settings-nav">
                        <div class="settings-nav-header">
                            <span class="settings-nav-title">
                                <sw-icon name="settings" :size="20"></sw-icon> 设置
                            </span>
                            <button class="settings-close" @click="$emit('close')" title="关闭">
                                <sw-icon name="x" :size="20"></sw-icon>
                            </button>
                        </div>
                        <div class="settings-nav-section">模型提供商</div>
                        <button class="settings-nav-item" data-tone="violet" :class="{ active: activeTab === 'providers' && !editing }"
                            @click="showTab('providers'); editing = null">
                            <sw-icon name="plug" :size="20"></sw-icon> 所有提供商
                        </button>
                        <button v-for="p in chat.providers" :key="p.id"
                            class="settings-nav-item provider-nav-item"
                            :class="{ active: editing && editing.id === p.id }"
                            @click="openProvider(p)" :title="p.name">
                            <span class="kind-dot" :class="p.kind"></span>
                            <span class="nav-provider-name">{{ p.name }}</span>
                            <span class="px-dot" :class="{ on: p.has_api_key }"
                                :title="p.has_api_key ? ('API Key: ' + p.api_key_masked) : '未设置 API Key'"></span>
                            <sw-icon v-if="p.is_default" class="nav-default" name="star" :size="20"
                                title="默认提供商"></sw-icon>
                        </button>
                        <button class="settings-nav-add" @click="newProvider">
                            <sw-icon name="plus" :size="20"></sw-icon> 添加提供商
                        </button>

                        <div class="settings-nav-section">其他</div>
                        <button class="settings-nav-item" data-tone="cyan" :class="{active:activeTab === 'tasks'}" @click="showTab('tasks')">
                            <sw-icon name="chip" :size="20"></sw-icon> 任务工作台
                        </button>
                        <button class="settings-nav-item" data-tone="blue" :class="{active:activeTab === 'mcp'}" @click="showTab('mcp')">
                            <sw-icon name="plug" :size="20"></sw-icon> MCP 连接
                        </button>
                        <button class="settings-nav-item" data-tone="lime" :class="{ active: activeTab === 'agent' }"
                            @click="showTab('agent')">
                            <sw-icon name="bot" :size="20"></sw-icon> Agent 能力
                        </button>
                        <button class="settings-nav-item" data-tone="pink" :class="{ active: activeTab === 'memory' }"
                            @click="showTab('memory')">
                            <sw-icon name="brain" :size="20"></sw-icon> 记忆
                        </button>
                        <button class="settings-nav-item" data-tone="blue" :class="{ active: activeTab === 'context' }"
                            @click="showTab('context')">
                            <sw-icon name="chip" :size="20"></sw-icon> 上下文
                        </button>
                        <button class="settings-nav-item" data-tone="pink" :class="{ active: activeTab === 'appearance' }"
                            @click="showTab('appearance')">
                            <sw-icon name="brush" :size="20"></sw-icon> 外观
                        </button>
                        <button class="settings-nav-item" data-tone="amber" :class="{ active: activeTab === 'kb' }"
                            @click="showTab('kb')">
                            <sw-icon name="book" :size="20"></sw-icon> 知识库 / RAG
                        </button>
                        <button class="settings-nav-item" data-tone="cyan" :class="{ active: activeTab === 'system' }"
                            @click="showTab('system')">
                            <sw-icon name="server" :size="20"></sw-icon> 系统状态
                        </button>
                    </div>

                    <div class="settings-content">
                        <div v-if="activeTab === 'providers'">
                            <div class="setting-card" v-if="!editing || !editing.id">
                                <div class="setting-card-title">快速接入</div>
                                <div class="preset-buttons">
                                    <button v-for="preset in providerPresets" :key="preset.id" class="btn-test" @click="useProviderPreset(preset)">{{ preset.name }}</button>
                                    <button class="btn-test" @click="newProvider">自定义接口</button>
                                </div>
                                <p class="pane-note">地址和型号已备好。填上你的 Key，就能接入。</p>
                            </div>
                            <div v-if="editing" class="provider-form">
                                <h3>
                                    <sw-icon :name="editing.id ? 'pencil' : 'plus'" :size="20"></sw-icon>
                                    {{ editing.id ? '编辑提供商' : '添加提供商' }}
                                </h3>
                                <div class="form-grid">
                                    <label class="form-label">显示名称
                                        <input v-model="editing.name" placeholder="如: 本地 Ollama / DeepSeek">
                                    </label>
                                    <label class="form-label">类型
                                        <select v-model="editing.kind" @change="providerKindChanged">
                                            <option value="cloud">云端 API（OpenAI 兼容）</option>
                                            <option value="local">本地 Ollama</option>
                                        </select>
                                    </label>
                                    <label class="form-label">接口地址
                                        <input v-model="editing.base_url" placeholder="https://api.deepseek.com/v1">
                                    </label>
                                    <label class="form-label">默认模型
                                        <input v-model="editing.model" placeholder="如 deepseek-chat / deepseek-r1:8b">
                                    </label>
                                    <label class="form-label">API Key
                                        <input type="password" v-model="editing.api_key"
                                            :placeholder="editing.api_key_placeholder || '输入新 Key；留空则保留原值'">
                                    </label>
                                    <label class="form-label">或引用服务端环境变量<input v-model="editing.api_key_env" placeholder="例如 DEEPSEEK_API_KEY"><small>填写变量名后优先用它，并清除配置文件中的原明文 Key；不填则保留原接入方式。</small></label>
                                    <div class="form-check-group">
                                        <label class="form-label form-check">
                                            <input type="checkbox" v-model="editing.is_default"> 设为默认提供商
                                        </label>
                                        <label v-if="editing.api_key_placeholder" class="form-label form-check">
                                            <input type="checkbox" v-model="editing.clear_key"> 清除已保存的 Key
                                        </label>
                                    </div>
                                </div>

                                <div class="models-manage">
                                    <button v-if="editing.kind === 'local'" class="btn-test" @click="scanOllama" :disabled="scanningModels">{{ scanningModels ? '扫描中…' : '扫描本机 Ollama' }}</button>
                                    <p v-if="scanMessage" class="pane-note">{{ scanMessage }}</p>
                                    <div class="model-chips"><button v-for="m in modelCandidates" :key="m" class="btn-test" @click="pickCandidate(m)"><span>{{ m }}</span><sw-icon name="plus" :size="18"></sw-icon></button></div>
                                    <div class="models-manage-title">模型栏 · 只显示已添加型号</div>
                                    <div v-if="editing.models.length === 0" class="models-empty">
                                        选个型号，或在下方手动添加。
                                    </div>
                                    <div class="model-chips">
                                        <span v-for="m in editing.models" :key="m" class="model-chip">
                                            <span class="chip-name">{{ m }}</span>
                                            <button class="chip-remove" @click="removeModelFromEdit(m)" title="移除">
                                                <sw-icon name="x" :size="20"></sw-icon>
                                            </button>
                                        </span>
                                    </div>
                                    <div class="model-add-row">
                                        <input v-model="newModelName" placeholder="输入模型名，如 deepseek-chat"
                                            @keyup.enter="addModelToEdit">
                                        <button class="btn-confirm" @click="addModelToEdit">添加</button>
                                    </div>
                                </div>

                                <div class="provider-form-actions">
                                    <button class="btn-danger-outline" @click="removeProvider(editing.id)" v-if="editing.id">删除</button>
                                    <span style="flex:1"></span>
                                    <button class="btn-cancel" @click="editing = null">取消</button>
                                    <button class="btn-confirm" @click="saveProviderForm" :disabled="providerSaving">
                                        {{ providerSaving ? '保存中…' : '保存' }}
                                    </button>
                                </div>
                            </div>
                        </div>

                        <div v-else-if="activeTab === 'tasks'" class="task-pane">
                            <h3>任务工作台</h3>
                            <div class="setting-card">
                                <div class="setting-card-title">后台任务</div>
                                <p class="pane-note">任务在后台跑。授权在这里处理；取消会等当前调用结束。</p>
                                <textarea v-model="jobMessage" placeholder="描述一件独立任务…" rows="3"></textarea>
                                <button class="btn-confirm" @click="startJob" :disabled="jobBusy || !chat.activeProviderId">{{jobBusy ? '启动中…' : '开始后台任务'}}</button>
                                <div v-for="job in jobs" :key="job.id" class="task-row">
                                    <button class="btn-test" @click="inspectJob(job.id)">{{job.message.slice(0,60)}} · {{job.status}}</button>
                                    <button v-if="['queued','running'].includes(job.status)" class="btn-cancel" @click="killJob(job.id)">取消</button>
                                </div>
                                <div v-if="selectedJob">
                                    <pre class="task-output">{{selectedJob.text || '还在跑，稍等。'}}</pre>
                                    <div v-if="jobApproval(selectedJob)" class="job-approval">
                                        <p>工具 {{jobApproval(selectedJob).name}} 等待授权：{{JSON.stringify(jobApproval(selectedJob).args)}}</p>
                                        <button class="btn-confirm" @click="approveJob(jobApproval(selectedJob),true)">允许一次</button>
                                        <button class="btn-cancel" @click="approveJob(jobApproval(selectedJob),false)">拒绝</button>
                                    </div>
                                </div>
                            </div>
                            <div class="setting-card">
                                <div class="setting-card-title">工作区修改</div>
                                <p v-if="!workspaceChanges.length" class="pane-note">工作区还没改动。写入后可查看差异、回滚。</p>
                                <div v-for="change in workspaceChanges.slice(0,30)" :key="change.id" class="task-row">
                                    <span>{{change.path}}</span><button class="btn-test" @click="showDiff(change.id)">查看差异</button>
                                    <button class="btn-cancel" @click="rollbackChange(change.id)">回滚</button>
                                </div>
                                <pre v-if="selectedDiff" class="task-output">{{selectedDiff}}</pre>
                            </div>
                        </div>
                        <div v-else-if="activeTab === 'mcp'" class="mcp-pane">
                            <h3><sw-icon name="plug" :size="20"></sw-icon> MCP 连接</h3>
                            <p class="pane-note">连接你保存的本机程序或 HTTP 服务器。连接不等于批准工具调用，每次调用仍需确认。</p>
                            <div class="setting-card">
                                <div class="setting-card-title">已配置的服务器</div>
                                <p v-if="!mcpServers.length" class="pane-note">还没有配置。用下面的表单添加一个；配置保存在 data/mcp_servers.json。</p>
                                <div v-for="s in mcpServers" :key="s.name" class="task-row">
                                    <strong>{{s.name}}</strong>
                                    <code class="command-preview">{{s.transport==='http'?s.url:s.command+' '+(s.args||[]).join(' ')}}</code>
                                    <small>{{s.connected?'已连接':'未连接'}}</small>
                                    <div class="studio-actions">
                                    <button @click="editServer(s)">编辑</button>
                                    <button @click="connectServer(s)">{{s.connected?'断开':'启动并连接'}}</button>
                                    <button v-for="kind in ['tools','resources','prompts']" :disabled="!s.connected" @click="inspectServer(s,kind)">{{kind}}</button>
                                    </div>
                                    <details>
                                    <summary>删除配置</summary>
                                    <button class="btn-danger" @click="removeServer(s)">确认删除 {{s.name}}</button>
                                    </details>
                                </div>
                            </div>
                            <div class="setting-card">
                                <div class="setting-card-title">添加 / 编辑服务器</div>
                                <label class="field-label">服务器标识</label>
                                <input v-model="server.name" placeholder="local_server">
                                <label class="field-label">传输</label>
                                <select v-model="server.transport">
                                    <option value="stdio">stdio · 本机进程</option>
                                    <option value="http">Streamable HTTP</option>
                                </select>
                                <template v-if="server.transport==='http'">
                                    <label class="field-label">服务器 URL</label>
                                    <input v-model="server.url" placeholder="https://example.com/mcp">
                                    <label class="form-label form-check"><input type="checkbox" v-model="server.allow_loopback">允许此配置访问本机回环地址</label>
                                </template>
                                <template v-else>
                                    <label class="field-label">可执行文件</label>
                                    <input v-model="server.command" placeholder="完整可执行路径">
                                    <label class="field-label">参数 JSON 数组</label>
                                    <textarea v-model="server.args" spellcheck="false" rows="3"></textarea>
                                    <label class="field-label">运行目录</label>
                                    <input v-model="server.cwd" placeholder="留空使用项目目录">
                                </template>
                                <label class="field-label">{{server.transport==='http'?'请求头':'进程环境变量'}} → 凭据变量名</label>
                                <textarea v-model="server.env_refs" spellcheck="false" rows="2"></textarea>
                                <label class="field-label">分类</label>
                                <select v-model="server.category">
                                    <option v-for="c in ['core','agent','python','cyberSecurity']">{{c}}</option>
                                </select>
                                <button class="btn-save" :disabled="mcpBusy" @click="saveServer">保存配置</button>
                            </div>
                            <div v-if="queryServer" class="setting-card">
                                <div class="setting-card-title">{{queryServer}} · 读取资源 / 提示词</div>
                                <label class="field-label">读取方式</label>
                                <select v-model="queryMethod">
                                    <option>resources/read</option>
                                    <option>prompts/get</option>
                                </select>
                                <label class="field-label">请求参数 JSON</label>
                                <textarea v-model="queryParams" rows="3" spellcheck="false"></textarea>
                                <label class="form-label form-check"><input type="checkbox" v-model="queryApproved">允许发送上述请求到该服务器</label>
                                <button class="btn-test" :disabled="mcpBusy||!queryApproved" @click="queryMcp">读取</button>
                            </div>
                            <pre v-if="mcpResult" class="task-output" aria-live="polite">{{mcpResult}}</pre>
                        </div>
                        <div v-else-if="activeTab === 'kb'" class="kb-pane">
                            <h3><sw-icon name="book" :size="20"></sw-icon> 知识库 / RAG</h3>
                            <div class="setting-card">
                                <div class="setting-row">
                                    <div>
                                        <div class="setting-row-title">RAG 检索</div>
                                        <div class="setting-row-desc">从勾选的库一起找线索。开关状态也显示在顶栏。</div>
                                    </div>
                                    <label class="switch">
                                        <input type="checkbox" :checked="chat.useRag" @change="chat.setUseRag($event.target.checked)">
                                        <span class="slider"></span>
                                    </label>
                                </div>
                                <div class="setting-row">
                                    <div>
                                        <div class="setting-row-title">同时开启</div>
                                        <div class="setting-row-desc mono">{{ chat.kbSlugs.join(' · ') || '尚未勾选' }}</div>
                                    </div>
                                </div>
                            </div>

                            <div class="setting-card">
                                <div class="setting-card-title">上传资料</div>
                                <label class="field-label">存到哪个库</label>
                                <select :value="chat.kbSlug" @change="chat.setKbSlug($event.target.value)">
                                    <option v-for="slug in chat.kbSlugs" :key="slug" :value="slug">{{ slug }}</option>
                                </select>
                                <p class="pane-note">检索可多选，文件每次存进一个库。</p>
                                <div class="file-upload-area" :class="{ dragging: dragOver }"
                                    @click="$refs.fileInput.click()"
                                    @dragover.prevent="dragOver = true"
                                    @dragleave="dragOver = false"
                                    @drop.prevent="onDrop">
                                    <input ref="fileInput" type="file" multiple style="display:none;" @change="onFileChange">
                                    <sw-icon class="upload-icon" name="folder" :size="30"></sw-icon>
                                    <div class="upload-hint">点击或拖拽文件到此处</div>
                                    <div class="upload-sub">支持: {{ kb.supportedExtensions.join(', ') || '加载中...' }}</div>
                                </div>
                                <div v-if="kb.uploading" class="uploading-hint">上传中...</div>
                                <div v-if="kb.uploadResults.length" class="file-list">
                                    <div v-for="(r, i) in kb.uploadResults" :key="i" class="file-item">
                                        <span>{{ r.filename }}{{ r.chunks > 0 ? ' (' + r.chunks + ' 块)' : '' }}</span>
                                        <span :style="{ color: r.success ? 'var(--neon-green)' : 'var(--danger)' }">
                                            <sw-icon :name="r.success ? 'check' : 'cross'" :size="20"></sw-icon>
                                        </span>
                                    </div>
                                </div>
                            </div>

                            <div class="setting-card">
                                <div class="setting-row">
                                    <div><div class="setting-row-title">积累技术经验</div><div class="setting-row-desc">回答后挑值得留的经验，存进已开启的库。会多一次模型调用，最长等 12 秒。模型笔记标为待核验。</div></div>
                                    <label class="switch"><input type="checkbox" :checked="chat.ragAutoLearn" @change="chat.setAutoLearn($event.target.checked)"><span class="slider"></span></label>
                                </div>
                                <div class="setting-card-title">知识库管理 · 可多选</div>
                                <div class="kb-create-row">
                                    <input type="text" placeholder="知识库标识 (如 my_kb)" v-model="newKbName">
                                    <button class="btn-confirm" @click="createKb">创建</button>
                                </div>
                                <div class="kb-list">
                                    <div v-if="kb.knowledgeBases.length === 0" class="kb-empty">
                                        暂无知识库，请先创建或上传文件
                                    </div>
                                    <div v-for="item in kb.knowledgeBases" :key="item.kb_slug" class="kb-item"
                                        :class="{ active: chat.kbSlugs.includes(item.kb_slug) }">
                                        <div>
                                            <span class="kb-name">{{ item.name || item.kb_slug }}</span>
                                            <div class="kb-count">文档数: {{ item.document_count || 0 }}</div>
                                        </div>
                                        <div class="kb-actions">
                                            <label class="kb-enable"><input type="checkbox" :aria-label="(item.name || item.kb_slug) + '知识库'" :checked="chat.kbSlugs.includes(item.kb_slug)" @change="chat.setKbEnabled(item.kb_slug,$event.target.checked)"> {{ chat.kbSlugs.includes(item.kb_slug) ? '已开启' : '未开启' }}</label>
                                            <button class="kb-del" @click="deleteKb(item.kb_slug)" title="删除">
                                                <sw-icon name="trash" :size="20"></sw-icon>
                                            </button>
                                        </div>
                                    </div>
                                </div>
                            </div>
                            <div class="setting-card">
                                <div class="setting-row"><div class="setting-card-title">经验笔记</div><button class="btn-test" @click="loadLearningNotes">查看 / 刷新</button></div>
                                <p class="pane-note">只看已开启的库。觉得记错了，就撤回。</p>
                                <div v-for="note in learningNotes.filter(n => chat.kbSlugs.includes(n.kb_slug))" :key="note.kb_slug+note.filename" class="kb-item">
                                    <span class="mono">{{note.kb_slug}} · {{note.title || note.filename}}（{{note.status || '待核验'}}）</span>
                                    <button class="icon-btn kb-del" @click="removeLearningNote(note)" title="撤回笔记"><sw-icon name="trash" :size="20"></sw-icon></button>
                                </div>
                            </div>
                        </div>

                        <div v-else-if="activeTab === 'system'" class="system-pane">
                            <h3><sw-icon name="server" :size="20"></sw-icon> 系统状态</h3>
                            <div v-if="chat.health" class="sys-grid">
                                <div class="sys-item">
                                    <span class="sys-label">本地 Ollama</span>
                                    <span class="sys-value" :class="chat.health.ollama_available ? 'ok' : 'bad'">
                                        <span class="px-dot" :class="chat.health.ollama_available ? 'ok' : 'bad'"></span>
                                        {{ chat.health.ollama_available ? '在线' : '离线' }}
                                    </span>
                                </div>
                                <div class="sys-item">
                                    <span class="sys-label">提供商数量</span>
                                    <span class="sys-value">{{ chat.health.provider_count ?? chat.providers.length }}</span>
                                </div>
                                <div class="sys-item">
                                    <span class="sys-label">云端配置</span>
                                    <span class="sys-value" :class="chat.health.cloud_configured ? 'ok' : ''">
                                        <span class="px-dot" :class="chat.health.cloud_configured ? 'ok' : 'off'"></span>
                                        {{ chat.health.cloud_configured ? '已配置' : '未配置' }}
                                    </span>
                                </div>
                                <div class="sys-item">
                                    <span class="sys-label">Embedding 后端</span>
                                    <span class="sys-value">{{ chat.health.embedding_backend }}</span>
                                </div>
                                <div class="sys-item">
                                    <span class="sys-label">版本</span>
                                    <span class="sys-value">v{{ chat.health.version }}</span>
                                </div>
                            </div>
                            <div v-else class="settings-empty">系统状态加载中...</div>
                            <div class="setting-card"><div class="setting-card-title">访问凭证</div><p>退出当前浏览器不会撤销其他客户端。轮换会让旧令牌失效，并使项目授权失效。</p><button class="btn-test" @click="logout">退出当前浏览器</button><details><summary>轮换本机令牌</summary><p>当前浏览器使用新的 HttpOnly Cookie；脚本和其他窗口重新读取 data/auth_token。环境令牌由部署配置维护。</p><button class="btn-save" @click="rotateToken">确认轮换令牌</button></details></div>
                        </div>

                        <div v-else-if="activeTab === 'agent'" class="agent-pane">
                            <h3><sw-icon name="bot" :size="20"></sw-icon> Agent 能力</h3>
                            <div class="setting-card">
                                <div class="setting-card-title">运行方式</div>
                                <div class="setting-row">
                                    <div>
                                        <div class="setting-row-title">Agent 模式</div>
                                        <div class="setting-row-desc">工具交给我调度。写入和外部调用仍需授权。</div>
                                    </div>
                                    <label class="switch">
                                        <input type="checkbox" :checked="chat.agentMode"
                                            @change="chat.setAgentMode($event.target.checked)">
                                        <span class="slider"></span>
                                    </label>
                                </div>
                                <div class="setting-row">
                                    <div>
                                        <div class="setting-row-title">联网搜索</div>
                                        <div class="setting-row-desc">查网页，带上来源。关闭后只查本地。</div>
                                    </div>
                                    <label class="switch">
                                        <input type="checkbox" :checked="chat.enableWeb"
                                            @change="chat.setEnableWeb($event.target.checked)">
                                        <span class="slider"></span>
                                    </label>
                                </div>
                                <div class="setting-row">
                                    <div>
                                        <div class="setting-row-title">最大工具轮数</div>
                                        <div class="setting-row-desc">每次任务最多调用工具的轮数，1–12 轮。</div>
                                    </div>
                                    <input type="number" class="steps-input" min="1" max="12"
                                        :value="chat.maxSteps"
                                        @change="chat.setMaxSteps($event.target.value)">
                                </div>
                            </div>

                            <div class="setting-card">
                                <div class="setting-card-title">当前可用工具（{{ agentTools.length }}）</div>
                                <div class="tool-tags">
                                    <span v-for="t in agentTools" :key="t.name" class="mini-tag"
                                        :title="t.description">{{ t.name }}</span>
                                    <span v-if="!agentTools.length" class="setting-row-desc">加载中或暂不可用…</span>
                                </div>
                                <div class="bg-note">
                                    知识库与自定义工具的修改都需授权。记录见 <code>data/logs/changes.log</code>。
                                </div>
                            </div>

                            <div class="setting-card search-config">
                                <div class="setting-card-title">联网搜索后端</div>
                                <p class="search-config-desc">
                                    <strong>Tavily</strong> 用你的 Key。没配好或不可用，就切到 <strong>Bing</strong>。
                                </p>

                                <div class="config-row">
                                    <label class="config-label" for="cfg-tavily">Tavily Key</label>
                                    <div class="config-control">
                                        <input id="cfg-tavily" type="password" v-model="searchForm.tavilyKey"
                                            :placeholder="searchForm.tavilyConfigured ? '已保存，留空则保持不变' : '粘贴 tvly- 开头的 Key'">
                                        <span class="config-status" :class="searchForm.tavilyConfigured ? 'ok' : 'off'">
                                            {{ searchForm.tavilyConfigured ? '已配置' : '未配置' }}
                                        </span>
                                    </div>
                                </div>

                                <div class="setting-row">
                                    <div>
                                        <div class="setting-row-title">启用 Tavily</div>
                                        <div class="setting-row-desc">
                                            关闭后走 Bing，不用 Tavily 额度。
                                        </div>
                                    </div>
                                    <label class="switch">
                                        <input type="checkbox" :checked="chat.tavilyEnabled"
                                            @change="chat.setTavilyEnabled($event.target.checked)">
                                        <span class="slider"></span>
                                    </label>
                                </div>

                                <div class="config-row">
                                    <label class="config-label" for="cfg-firecrawl">Firecrawl（可选）</label>
                                    <div class="config-control">
                                        <input id="cfg-firecrawl" type="password" v-model="searchForm.firecrawlKey"
                                            :placeholder="searchForm.firecrawlConfigured ? '已保存，留空则保持不变' : '用于抓取 JS 渲染网页的正文'">
                                        <span class="config-status" :class="searchForm.firecrawlConfigured ? 'ok' : 'off'">
                                            {{ searchForm.firecrawlConfigured ? '已配置' : '未配置' }}
                                        </span>
                                    </div>
                                </div>

                                <div class="config-hint">保存后，测一下通道。</div>
                                <div class="config-actions">
                                    <button class="btn-test" @click="testSearch('tavily')">测试 Tavily</button>
                                    <button class="btn-test" @click="testSearch('bing')">测试爬虫 · Bing</button>
                                    <button class="btn-save" @click="saveSearchConfig">保存配置</button>
                                </div>
                                <div v-if="searchTestMsg" class="test-result">{{ searchTestMsg }}</div>
                            </div>

                            <div class="setting-card">
                                <div class="setting-card-title">最近的自我修改记录</div>
                                <div v-if="!agentChanges.length" class="setting-row-desc">暂无记录</div>
                                <ul class="src-list">
                                    <li v-for="(c, i) in agentChanges.slice(-5).reverse()" :key="i">
                                        <span class="mini-tag">{{ c.action }}</span>
                                        <span class="src-url">{{ JSON.stringify(c.detail) }}</span>
                                    </li>
                                </ul>
                            </div>
                        </div>

                        <div v-else-if="activeTab === 'memory'">
                            <h3><sw-icon name="brain" :size="20"></sw-icon> 长期记忆</h3>
                            <p class="pane-note">
                                约定存这里，下次也能查。删除后不会再召回。
                            </p>
                            <div class="setting-card">
                                <div class="setting-card-title">手动存档</div>
                                <div class="config-row">
                                    <label class="config-label" for="cfg-mem-content">内容</label>
                                    <div class="config-control">
                                        <input id="cfg-mem-content" type="text" v-model="memoryForm.content"
                                            placeholder="记一条约定，例如：项目使用 FastAPI"
                                            @keyup.enter="addMemoryEntry">
                                    </div>
                                </div>
                                <div class="config-row">
                                    <label class="config-label" for="cfg-mem-tags">标签</label>
                                    <div class="config-control">
                                        <input id="cfg-mem-tags" type="text" v-model="memoryForm.tags"
                                            placeholder="标签，用逗号隔开" @keyup.enter="addMemoryEntry">
                                    </div>
                                </div>
                                <button class="btn-save" @click="addMemoryEntry">记住它</button>
                            </div>
                            <div class="setting-card">
                                <div class="setting-card-head">
                                    <div class="setting-card-title">已有记忆（{{ memories.length }}）</div>
                                    <button class="btn-test" @click="clearMemories"
                                        :disabled="!memories.length">清空全部</button>
                                </div>
                                <div v-if="!memories.length" class="setting-row-desc">
                                    还没存档。告诉我「记住这件事」，就能留到下次。
                                </div>
                                <ul class="src-list memory-list">
                                    <li v-for="m in memories" :key="m.id">
                                        <div class="memory-item">
                                            <div class="memory-content">{{ m.content }}</div>
                                            <div class="memory-meta">
                                                <span class="mini-tag" v-if="m.tags">{{ m.tags }}</span>
                                                <span class="src-url">{{ fmtMemoryTime(m.ts) }} · {{ m.source }}</span>
                                            </div>
                                        </div>
                                        <button class="btn-test" @click="delMemory(m.id)" title="忘记这条">删除</button>
                                    </li>
                                </ul>
                            </div>
                        </div>

                        <div v-else-if="activeTab === 'context'" class="context-pane">
                            <h3><sw-icon name="chip" :size="20"></sw-icon> 上下文</h3>
                            <p class="pane-note">
                                历史每次都会重读。窗口和用量，在这里控制。
                            </p>

                            <div class="setting-card">
                                <div class="setting-card-head">
                                    <div class="setting-card-title">上下文窗口</div>
                                    <span class="ctx-value">{{ chat.unlimitedTurns ? '∞' : chat.fmtTokens(chat.contextBudget) }}</span>
                                </div>
                                <p class="search-config-desc">
                                    一次请求能带多少内容：提示词、检索、历史和当前消息。
                                    约 {{ (chat.ctxChineseChars() / 10000).toFixed(1) }} 万字中文，仅作估算。
                                </p>
                                <input class="ctx-slider" type="range" min="0"
                                    :max="chat.ctxPresets.length - 1" step="1"
                                    :value="chat.ctxSliderIndex()" :disabled="chat.unlimitedTurns"
                                    @input="chat.setContextBudget(chat.ctxPresets[$event.target.value])"
                                    aria-label="上下文窗口档位">
                                <div class="ctx-ticks" :class="{ disabled: chat.unlimitedTurns }">
                                    <span v-for="(v, i) in chat.ctxPresets" :key="v"
                                        class="ctx-tick" :class="{ active: i === chat.ctxSliderIndex() }"
                                        role="button" :tabindex="chat.unlimitedTurns ? -1 : 0" :aria-pressed="i === chat.ctxSliderIndex()"
                                        @keydown.enter="chat.setContextBudget(v)" @keydown.space.prevent="chat.setContextBudget(v)"
                                        @click="chat.setContextBudget(v)">{{ chat.fmtTokens(v) }}</span>
                                </div>
                                <div class="config-row">
                                    <label class="config-label" for="cfg-budget">手动输入</label>
                                    <div class="config-control">
                                        <input id="cfg-budget" type="number" min="4000" max="1000000"
                                            step="1000" :value="chat.contextBudget" :disabled="chat.unlimitedTurns"
                                            @change="chat.setContextBudget($event.target.value)">
                                        <span class="config-status off">tokens</span>
                                    </div>
                                </div>
                                <div class="config-hint">
                                    4K–1M。按模型窗口设置；历史越多，输入用量越高。
                                </div>
                            </div>

                            <div class="setting-card">
                                <div class="setting-card-title">历史保留</div>
                                <div class="config-row">
                                    <label class="config-label" for="cfg-keep">保底最近轮数</label>
                                    <div class="config-control">
                                        <input id="cfg-keep" type="number" min="1" max="20" step="1"
                                            :value="chat.minKeepTurns" :disabled="chat.unlimitedTurns"
                                            @change="chat.setMinKeepTurns($event.target.value)">
                                        <span class="config-status off">轮</span>
                                    </div>
                                </div>
                                <div class="config-hint">
                                    优先保留最近几轮。一次问答算一轮。
                                </div>
                                <div class="setting-row">
                                    <div>
                                        <div class="setting-row-title">无限轮数</div>
                                        <div class="setting-row-desc">
                                            不主动裁剪历史。模型窗口仍有限，长对话会增加用量。
                                        </div>
                                    </div>
                                    <label class="switch">
                                        <input type="checkbox" :checked="chat.unlimitedTurns"
                                            @change="chat.setUnlimitedTurns($event.target.checked)">
                                        <span class="slider"></span>
                                    </label>
                                </div>
                                <div class="setting-row">
                                    <div>
                                        <div class="setting-row-title">上下文压缩</div>
                                        <div class="setting-row-desc">
                                            旧对话压成摘要。留线索，省上下文。
                                        </div>
                                    </div>
                                    <label class="switch">
                                        <input type="checkbox" :checked="chat.compressContext"
                                            @change="chat.setCompressContext($event.target.checked)">
                                        <span class="slider"></span>
                                    </label>
                                </div>
                            </div>

                            <div class="setting-card">
                                <div class="setting-card-head">
                                    <div class="setting-card-title">长度限制</div>
                                    <span class="ctx-value">{{ chat.replyUnlimited ? '∞' : chat.fmtTokens(chat.replyMaxTokens) }}</span>
                                </div>
                                <div class="config-row">
                                    <label class="config-label" for="cfg-toollimit">工具结果上限</label>
                                    <div class="config-control">
                                        <input id="cfg-toollimit" type="number" min="500" max="50000" step="500"
                                            :value="chat.toolOutputLimit"
                                            @change="chat.setToolOutputLimit($event.target.value)">
                                        <span class="config-status off">字符</span>
                                    </div>
                                </div>
                                <div class="config-hint">
                                    预览保留头尾，原文可按需取回。500–50000 字符。
                                </div>
                                <div class="config-row">
                                    <label class="config-label" for="cfg-maxtokens">单次回复上限</label>
                                    <div class="config-control">
                                        <input id="cfg-maxtokens" type="number" min="256" max="32768" step="256"
                                            :value="chat.replyMaxTokens" :disabled="chat.replyUnlimited"
                                            @change="chat.setReplyMaxTokens($event.target.value)">
                                        <span class="config-status off">tokens</span>
                                    </div>
                                </div>
                                <input class="ctx-slider" type="range" min="0"
                                    :max="chat.replyPresets.length - 1" step="1"
                                    :value="chat.replySliderIndex()" :disabled="chat.replyUnlimited"
                                    @input="chat.setReplyMaxTokens(chat.replyPresets[$event.target.value])"
                                    aria-label="单次回复上限档位">
                                <div class="ctx-ticks" :class="{ disabled: chat.replyUnlimited }">
                                    <span v-for="(v, i) in chat.replyPresets" :key="v"
                                        class="ctx-tick" :class="{ active: i === chat.replySliderIndex() }"
                                        role="button" :tabindex="chat.replyUnlimited ? -1 : 0" :aria-pressed="i === chat.replySliderIndex()"
                                        @keydown.enter="chat.setReplyMaxTokens(v)" @keydown.space.prevent="chat.setReplyMaxTokens(v)"
                                        @click="chat.setReplyMaxTokens(v)">{{ chat.fmtTokens(v) }}</span>
                                </div>
                                <div class="setting-row">
                                    <div>
                                        <div class="setting-row-title">无限长度</div>
                                        <div class="setting-row-desc">
                                            不设应用输出上限。实际长度由模型决定，用量随输出增加。
                                        </div>
                                    </div>
                                    <label class="switch">
                                        <input type="checkbox" :checked="chat.replyUnlimited"
                                            @change="chat.setReplyUnlimited($event.target.checked)">
                                        <span class="slider"></span>
                                    </label>
                                </div>
                            </div>
                        </div>

                        <div v-else-if="activeTab === 'appearance'" class="appearance-pane">
                            <h3><sw-icon name="brush" :size="20"></sw-icon> 外观</h3>
                            <div class="mode-card"><strong>{{chat.modeLabel()}} · 当前外观</strong><p>选 Lv.999 的背景或银狼头像会自动切到 Lv.999，选银狼的则切回来。开拓者头像不跟模式走。</p><button class="btn-test" @click="chat.switchMode(chat.mode === 'normal' ? 'lv999' : 'normal')">{{chat.mode === 'normal' ? '切到 Lv.999' : '切回银狼'}}</button></div>
                            <div class="setting-card">
                                <div class="setting-card-title">主题色</div>
                                <div class="preset-row">
                                    <button v-for="p in chat.themePresets" :key="p.name"
                                        class="preset-swatch" :class="{ active: chat.theme.accent === p.accent }"
                                        :style="{ '--swatch': p.accent }" :title="p.name"
                                        @click="chat.applyThemePreset(p)">
                                        <span class="preset-name">{{ p.name }}</span>
                                    </button>
                                </div>
                                <div class="setting-row">
                                    <div>
                                        <div class="setting-row-title">自定义主色</div>
                                        <div class="setting-row-desc">主色你定，点缀色我配。</div>
                                    </div>
                                    <input type="color" class="color-input"
                                        :value="chat.theme.accent"
                                        @input="chat.setCustomAccent($event.target.value)">
                                </div>
                            </div>
                            <div class="setting-card">
                                <div class="setting-card-title">背景图</div>
                                <div class="asset-grid background-presets">
                                    <button v-for="p in chat.modeAssets('backgrounds')" :key="p.id" class="asset-option" @click="chat.useBackgroundPreset(p.url)" :class="{selected:chat.theme.bgImage === p.url}">
                                        <img :src="p.url" :alt="p.name" loading="lazy"><span>{{p.name}}</span><small v-if="p.origin === 'upload'">我的收藏</small>
                                    </button>
                                </div>
                                <label class="form-label">背景遮罩 · {{Math.round(chat.theme.bgShade * 100)}}%
                                    <input type="range" min="0.1" max="0.65" step="0.05" v-model.number="chat.theme.bgShade" @input="chat.saveTheme()">
                                </label>
                                <div class="bg-actions">
                                    <button class="btn-confirm" @click="$refs.bgInput.click()">
                                        <sw-icon name="folder" :size="20"></sw-icon> 上传本地图片
                                    </button>
                                    <input ref="bgInput" type="file" accept="image/*" style="display:none;"
                                        @change="onBgUpload">
                                    <button class="btn-cancel" @click="chat.clearBgImage()"
                                        :disabled="!chat.theme.bgImage">恢复默认</button>
                                    <button class="btn-cancel" @click="chat.restoreCustomBackground()">用我的上传</button>
                                </div>
                                <div class="bg-note">
                                    上传的图片存本机收藏。切换预设不丢原图，遮罩也能调。
                                </div>
                            </div>
                            <div class="setting-card">
                                <div class="setting-card-title">头像</div>
                                <div v-for="role in ['bot','user']" :key="role" class="avatar-presets">
                                    <p class="pane-note">{{appearancePresets.kind === 'fan_art' ? (role === 'bot' ? '银狼 · 同人头像' : '开拓者 · 队友头像') : (role === 'bot' ? '银狼 · 原创助手形象' : '队友 · 原创头像')}}</p>
                                    <div class="asset-grid">
                                        <button v-for="p in chat.modeAssets(role)" :key="p.id" class="asset-option avatar-option" :class="{selected:chat.avatarSelections[role] === p.id}" @click="chat.useAvatarPreset(role,p.id)">
                                            <img :src="p.url" :alt="p.name" loading="lazy"><span>{{p.name}}</span><small v-if="p.origin === 'upload'">我的收藏</small>
                                        </button>
                                        <button class="btn-test" @click="chat.useAvatarPreset(role,null)">用我的上传</button>
                                    </div>
                                </div>
                                <div class="avatar-settings">
                                    <div class="avatar-setting-item">
                                        <div class="avatar-preview bot-preview">
                                            <img v-if="chat.avatars.bot" :src="chat.avatars.bot" alt="">
                                            <sw-icon v-else name="wolf" :size="28"></sw-icon>
                                        </div>
                                        <div class="avatar-setting-info">
                                            <div class="setting-row-title">银狼头像</div>
                                            <div class="avatar-actions">
                                                <button class="btn-confirm btn-sm" @click="onAvatarUpload('bot')">上传</button>
                                                <button class="btn-cancel btn-sm" @click="chat.removeAvatar('bot')"
                                                    :disabled="!chat.avatars.bot">恢复默认</button>
                                            </div>
                                        </div>
                                    </div>
                                    <div class="avatar-setting-item">
                                        <div class="avatar-preview user-preview">
                                            <img v-if="chat.avatars.user" :src="chat.avatars.user" alt="">
                                            <sw-icon v-else name="user" :size="26"></sw-icon>
                                        </div>
                                        <div class="avatar-setting-info">
                                            <div class="setting-row-title">我的头像</div>
                                            <div class="avatar-actions">
                                                <button class="btn-confirm btn-sm" @click="onAvatarUpload('user')">上传</button>
                                                <button class="btn-cancel btn-sm" @click="chat.removeAvatar('user')"
                                                    :disabled="!chat.avatars.user">恢复默认</button>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                                <input ref="avatarInput" type="file" accept="image/*" class="avatar-file-input"
                                    style="display:none;" @change="onAvatarFile">
                                <div class="bg-note">
                                    {{appearancePresets.kind === 'fan_art' ? '同人预设只存本机。' : '原创预设随项目提供。'}} 原上传保留，随时切回。
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        `,
    };

    // 工作区选择器：纯点击浏览，不用手输路径——起点是「此电脑」（所有磁盘 + 常用位置 + 最近），
    // 点文件夹逐层进入，面包屑可以往回跳，确认即把该文件夹设为工作区（后端要求 approved 标记）。
    // 只看文件夹：文件结构展示交给顶栏「文件」面板，这里只负责挑目录。
    components['workspace-picker'] = {
        props: ['show'],
        data() {
            return {
                chat, loading: false, error: '', listing: null, current: '',
            };
        },
        computed: {
            // 面包屑：把 E:\a\b 拆成 [{name:'E:', path:'E:\\'}, {name:'a', path:'E:\\a'}, ...]
            crumbs() {
                const parts = String(this.current || '').split(/[\\/]/).filter(Boolean);
                return parts.map((name, index) => ({
                    name,
                    path: parts.slice(0, index + 1).join('\\') + (index === 0 ? '\\' : ''),
                }));
            },
        },
        watch: {
            show(value) {
                focusDialog(this, value, 'button');
                if (value) this.loadRoot();
            },
        },
        methods: {
            dialogKey(e) { dialogKey(e, this.$el, () => this.$emit('close')); },
            async loadRoot() {
                this.error = ''; this.current = ''; this.loading = true;
                try {
                    this.listing = await SWApi.developer.workspace.browse('');
                }
                catch (e) { this.error = e.message; this.listing = null; }
                finally { this.loading = false; }
            },
            async enter(path) {
                this.error = ''; this.loading = true;
                try {
                    const data = await SWApi.developer.workspace.browse(path);
                    this.listing = data; this.current = data.path || path;
                } catch (e) { this.error = e.message; }
                finally { this.loading = false; }
            },
            async goUp() {
                if (!this.current) return;
                const parent = this.current.replace(/[\\/][^\\/]*$/, '');
                if (parent && parent !== this.current) await this.enter(parent);
                else await this.loadRoot();
            },
            async use(path) {
                this.error = ''; this.loading = true;
                try { await chat.setWorkspace(path || this.current || undefined); this.$emit('close'); }
                catch (e) { this.error = e.message; }
                finally { this.loading = false; }
            },
            async useDefault() {
                this.error = ''; this.loading = true;
                try { await chat.setWorkspace(); this.$emit('close'); }
                catch (e) { this.error = e.message; }
                finally { this.loading = false; }
            },
        },
        template: `
            <div v-if="show" class="modal-overlay" @keydown="dialogKey">
                <div class="modal modal-wide" role="dialog" aria-modal="true" aria-label="选择工作区">
                    <h3>选择工作区</h3>
                    <p class="usage-dim">工作模式会把项目工具、终端与变更记录都限定在这个文件夹里。切换会关闭已打开的终端。</p>
                    <div class="ws-crumbs">
                        <button class="ws-crumb" @click="loadRoot">此电脑</button>
                        <template v-for="c in crumbs" :key="c.path">
                            <span class="ws-sep">›</span>
                            <button class="ws-crumb" @click="enter(c.path)">{{ c.name }}</button>
                        </template>
                    </div>
                    <p v-if="error" class="error-text">{{ error }}</p>
                    <div v-if="loading" class="usage-dim">读取中…</div>
                    <template v-else-if="listing">
                        <div v-if="listing.drives" class="ws-grid">
                            <button v-for="d in listing.drives" :key="d" class="ws-entry" @click="enter(d)">
                                <sw-icon name="server" :size="18"></sw-icon><span>{{ d }}</span>
                            </button>
                            <button v-if="listing.shortcuts" class="ws-entry" @click="enter(listing.shortcuts.home)">
                                <sw-icon name="user" :size="18"></sw-icon><span>用户目录</span>
                            </button>
                            <button v-if="listing.shortcuts" class="ws-entry" @click="enter(listing.shortcuts.default)">
                                <sw-icon name="star" :size="18"></sw-icon><span>项目默认</span>
                            </button>
                        </div>
                        <div v-if="listing.workspaces && listing.workspaces.length" class="ws-recent-block">
                            <div class="setting-row-title">已添加的工作区（点一下切过去）</div>
                            <div class="ws-grid">
                                <button v-for="w in listing.workspaces" :key="w.path" class="ws-entry"
                                    :class="{ picked: w.active }" :title="w.path" @click="use(w.path)">
                                    <sw-icon name="folder" :size="18"></sw-icon>
                                    <span>{{ w.name }}<small v-if="w.active">当前</small><small v-else-if="w.missing">目录不在了</small></span>
                                </button>
                            </div>
                        </div>
                        <div v-if="listing.entries" class="ws-grid">
                            <button v-if="current" class="ws-entry" @click="goUp">
                                <sw-icon name="caret" :size="18"></sw-icon><span>上一层</span>
                            </button>
                            <button v-for="e in listing.entries" :key="e.path" class="ws-entry" @click="enter(e.path)">
                                <sw-icon name="folder" :size="18"></sw-icon><span>{{ e.name }}</span>
                            </button>
                            <p v-if="!listing.entries.length" class="usage-dim">这个目录是空的。</p>
                        </div>
                    </template>
                    <div class="modal-actions">
                        <button class="btn-cancel" @click="$emit('close')">取消</button>
                        <button class="btn-cancel" @click="useDefault"
                            :disabled="chat.workspace.is_default">回到默认工作区</button>
                        <button class="btn-confirm" @click="use()" :disabled="!current">用这个文件夹</button>
                    </div>
                </div>
            </div>
        `,
    };

    // 重命名 / 删除 弹窗
    components['rename-modal'] = {
        props: ['show', 'session'],
        data() {
            return { input: '' };
        },
        watch: {
            show(value) { focusDialog(this,value,'input'); },
            session(newVal) {
                if (newVal) this.input = newVal.title || '';
            },
        },
        methods: {
            dialogKey(e) { dialogKey(e,this.$el,() => this.$emit('close')); },
            confirm() {
                if (this.input.trim() && this.session) {
                    this.$emit('confirm', this.session.id, this.input.trim());
                }
            },
        },
        template: `
            <div v-if="show" class="modal-overlay" @keydown="dialogKey">
                <div class="modal" role="dialog" aria-modal="true" aria-label="重命名对话">
                    <h3>重命名对话</h3>
                    <input type="text" v-model="input" placeholder="输入新标题..."
                        @keyup.enter="confirm">
                    <div class="modal-actions">
                        <button class="btn-cancel" @click="$emit('close')">取消</button>
                        <button class="btn-confirm" @click="confirm">确认</button>
                    </div>
                </div>
            </div>
        `,
    };

    components['delete-modal'] = {
        props: ['show'],
        watch: { show(value) { focusDialog(this,value,'.btn-cancel'); } },
        methods: { dialogKey(e) { dialogKey(e,this.$el,() => this.$emit('close')); } },
        template: `
            <div v-if="show" class="modal-overlay" @keydown="dialogKey">
                <div class="modal" role="dialog" aria-modal="true" aria-label="删除对话">
                    <h3>删除对话</h3>
                    <p style="margin-bottom:18px;">确定要删除这个对话吗？此操作不可撤销。</p>
                    <div class="modal-actions">
                        <button class="btn-cancel" @click="$emit('close')">取消</button>
                        <button class="btn-danger" @click="$emit('confirm')">删除</button>
                    </div>
                </div>
            </div>
        `,
    };

    components['app-toast'] = {
        data() {
            return { chat };
        },
        template: `
            <div v-if="chat.toast.show" class="toast show" :class="chat.toast.type">{{ chat.toast.message }}</div>
        `,
    };

    // v2.3.0：高危工具审批弹窗（Agent 想执行写/删等操作时弹出来问用户）
    components['approval-modal'] = {
        data() {
            return { chat };
        },
        computed: {
            prettyArgs() {
                const args = this.chat.pendingApproval ? this.chat.pendingApproval.args : null;
                try {
                    return args ? JSON.stringify(args, null, 2) : '';
                } catch (e) {
                    return String(args || '');
                }
            },
        },
        watch: { 'chat.pendingApproval'(value) { focusDialog(this,!!value,'.btn-test'); } },
        methods: {
            dialogKey(e) { dialogKey(e,this.$el,() => this.respond(false)); },
            async respond(allow) {
                const a = this.chat.pendingApproval;
                if (!a || !a.resolve) return;
                try {
                    await window.SWApi.agent.approval(a.requestId, allow);
                } catch (e) {
                    // 后端已断开/超时：静默，用户点完按钮弹窗照常关闭
                }
                a.resolve(allow);
            },
        },
        template: `
            <div v-if="chat.pendingApproval" class="approval-overlay" @click.self="respond(false)" @keydown="dialogKey">
                <div class="approval-box" role="dialog" aria-modal="true" aria-label="工具执行审批">
                    <div class="approval-title">
                        <sw-icon name="warning" :size="20"></sw-icon> Agent 需要授权
                    </div>
                    <p class="approval-desc">
                        银狼想对你的机器执行：
                    </p>
                    <div class="approval-call">
                        <span class="mini-tag">{{ chat.pendingApproval.name }}</span>
                        <pre class="approval-args">{{ prettyArgs }}</pre>
                    </div>
                    <p class="approval-hint">这属于高危操作，批准后真的会执行；60 秒没点默认拒绝。</p>
                    <div class="approval-actions">
                        <button class="btn-test" @click="respond(false)">拒绝</button>
                        <button class="btn-save" @click="respond(true)">允许一次</button>
                    </div>
                </div>
            </div>
        `,
    };

    // 专业面板复用同一份能力配置；试运行和项目授权由用户明确操作。
    // 共用接管卡：终端与文件面板共用一份授权（chat.grant），这里负责开启与撤销。
    components['grant-card'] = {
        data() { return {chat, allowWrite:false, allowCommands:false, allowTerminal:false, grantApproved:false, busy:false, error:''}; },
        methods: {
            async act(fn) {
                if (this.busy) return;
                this.busy = true; this.error = '';
                try { return await fn(); } catch (e) { this.error = e.message; }
                finally { this.busy = false; }
            },
            grant() { this.act(() => chat.authorizeGrant(
                {write:this.allowWrite, commands:this.allowCommands, terminal:this.allowTerminal},
                this.grantApproved)); },
            revoke() { this.act(() => chat.revokeGrant()); },
        },
        template: `
            <div v-if="!chat.grant" class="studio-editor">
                <p>授权有效 30 分钟，重启或轮换令牌失效。文件接口保护用户数据、凭据和 Git 内部目录。</p>
                <p>本次接管：<code>{{chat.workspace.root}}</code></p>
                <label class="check-row">
                <input type="checkbox" v-model="allowWrite">允许编辑工作区源码（保留差异与冲突检查）</label>
                <label class="check-row">
                <input type="checkbox" v-model="allowCommands">允许运行命令</label>
                <label class="check-row"><input type="checkbox" v-model="allowTerminal" :disabled="!allowCommands">允许 Windows 交互终端（30分钟内不逐条询问）</label><p v-if="allowCommands" class="error-text">命令以当前用户身份运行，没有操作系统沙箱，能影响工作区以外的文件；交互终端在授权期内免逐条确认。</p>
                <label class="check-row">
                <input type="checkbox" v-model="grantApproved">我已确认上述范围</label>
                <button class="btn-save" :disabled="busy||!grantApproved" @click="grant">开启本次授权</button>
                <p v-if="error" class="error-text" role="alert">{{error}}</p>
            </div>
            <div v-else class="studio-editor">
                <div class="studio-actions">
                <span class="workspace-path" :title="chat.grant.workspace">{{chat.grant.workspace}} · 授权到 {{new Date(chat.grant.expires*1000).toLocaleTimeString()}}</span>
                <button :disabled="busy" @click="revoke">撤销授权</button>
                </div>
                <p class="usage-dim">本次权限：<template v-if="chat.grant.write">编辑 </template><template v-if="chat.grant.commands">命令 </template><template v-if="chat.grant.terminal">终端 </template><template v-if="!chat.grant.write&&!chat.grant.commands&&!chat.grant.terminal">仅查看</template></p>
            </div>
        `,
    };

    // 文件面板预览的语法高亮：按扩展名映射到 markdown.js 支持的语言，没映射的按纯文本转义。
    const PREVIEW_LANGUAGES = {py:'python', pyw:'python', js:'javascript', mjs:'javascript', cjs:'javascript', ts:'typescript', tsx:'typescript', json:'json', sh:'bash', bash:'bash', bat:'bash', cmd:'bash'};
    function previewLanguage(name) { return PREVIEW_LANGUAGES[String(name).split('.').pop().toLowerCase()] || ''; }

    // 文件树按类型给图标与点缀色：常见代码/文档/图片/脚本各配专属标记，其余用通用文件图标。
    const FILE_ICONS = {py:'file-py', pyw:'file-py', md:'file-md', markdown:'file-md', json:'file-json',
        js:'file-code', mjs:'file-code', cjs:'file-code', jsx:'file-code', ts:'file-code', tsx:'file-code',
        vue:'file-code', html:'file-code', htm:'file-code', css:'file-code',
        png:'file-img', jpg:'file-img', jpeg:'file-img', gif:'file-img', svg:'file-img', webp:'file-img', ico:'file-img', bmp:'file-img',
        sh:'file-sh', bat:'file-sh', cmd:'file-sh', ps1:'file-sh'};
    const FILE_ICON_COLORS = {'file-py':'#ffc784', 'file-md':'#8ce2cf', 'file-json':'#c5a0ff', 'file-code':'#3ED8F0', 'file-img':'#FF70BC', 'file-sh':'#34D399'};
    function fileIcon(name) {
        const icon = FILE_ICONS[String(name).split('.').pop().toLowerCase()] || 'file';
        return {name: icon, color: FILE_ICON_COLORS[icon] || ''};
    }

    // 工作区文件面板（顶栏「文件」按钮）：像代码编辑器一样看工作区的结构树，
    // 逐级展开（点文件夹按需加载下一层）；点文件免授权只读预览，开启接管（write 位）后可直接编辑、看差异、回滚。
    // 树节点 rel 是从工作区根出发的正斜杠相对路径，正好喂给授权操作；预览用绝对路径走免授权接口。
    components['files-panel'] = {
        data() {
            return {
                chat, narrow:true,
                nodes: [], loading: false, error: '', busy: false, result: '',
                preview: null, previewPath: '', previewLoading: false, previewError: '', previewToken: 0,
                selectedAbs: '', editing: false,
                editor: { path: '', content: '', revision: '' }, diff: '',
                changes: [], gitText: '', showGrant: false,
            };
        },
        computed: {
            // 把树按展开状态摊平成可见行（模板不做递归组件，深度用缩进表现）
            rows() {
                const rows = [];
                const walk = (nodes, depth) => {
                    for (const node of nodes) {
                        rows.push({ node, depth, icon: node.dir ? null : fileIcon(node.name) });
                        if (node.dir && node.open) walk(node.children, depth + 1);
                    }
                };
                walk(this.nodes, 0);
                return rows;
            },
            canEdit() {
                return !!(chat.grant && chat.grant.write && this.preview && !this.preview.truncated);
            },
            // 只读预览的 HTML：highlightCode 先整体转义再上色，v-html 是安全的
            previewHtml() {
                if (!this.preview) return '';
                const text = this.preview.content || '';
                return text ? window.SWMarkdown.highlightCode(text, previewLanguage(this.preview.name || this.previewPath)) : '（空文件）';
            },
        },
        watch: {
            'chat.filesOpen'(open) {
                focusDialog(this, open, 'button');
                if (open) this.rebuild();
                else this.previewToken += 1;
            },
            'chat.workspaceEpoch'() { this.resetState(); if (chat.filesOpen) this.rebuild(); },
            'chat.grant'(grant) { if (!grant) { this.editing = false; this.diff = ''; this.showGrant = false; } },
        },
        mounted() {this._viewport=window.matchMedia('(max-width:1100px)');this._resize=()=>{this.narrow=this._viewport.matches;};this._resize();this._viewport.addEventListener('change',this._resize);},
        beforeUnmount() { this._viewport?.removeEventListener('change',this._resize); this.previewToken += 1; },
        methods: {
            async act(fn) {
                if (this.busy) return;
                this.busy = true; this.error = '';
                try { return await fn(); } catch (e) { this.error = e.message; }
                finally { this.busy = false; }
            },
            close() { chat.filesOpen = false; },
            keydown(e) {if(this.narrow)dialogKey(e,this.$el,()=>this.close());else if(e.key==='Escape'){e.preventDefault();this.close();}},
            // 把 browse 的一层结果转成树节点；rel 是从工作区根出发的相对路径（正斜杠）
            buildNodes(data, base) {
                const join = name => (base ? base + '/' + name : name);
                const nodes = [];
                for (const d of data.entries || []) {
                    nodes.push({ name: d.name, path: d.path, rel: join(d.name), dir: true, open: false, loaded: false, loading: false, children: [] });
                }
                for (const f of data.files || []) {
                    nodes.push({ name: f.name, path: f.path, rel: join(f.name), dir: false, size: f.size });
                }
                return nodes;
            },
            async loadChildren(node) {
                if (node.loaded || node.loading) return;
                node.loading = true;
                try {
                    const data = await SWApi.developer.workspace.browse(node.path);
                    node.children = this.buildNodes(data, node.rel);
                    node.loaded = true;
                } catch (e) { this.error = e.message; }
                finally { node.loading = false; }
            },
            async toggleDir(node) {
                if (node.open) { node.open = false; return; }
                if (!node.loaded) {
                    await this.loadChildren(node);
                    if (!node.loaded) return;
                }
                node.open = true;
            },
            // 结构逐级展开：进入面板只铺根一层，点文件夹才加载下一层，
            // 方便在大项目里按需翻找（原自动铺开在大仓库里会把整棵树倒出来）。
            async rebuild() {
                this.error = ''; this.loading = true;
                try {
                    const data = await SWApi.developer.workspace.browse(chat.workspace.root);
                    this.nodes = this.buildNodes(data, '');
                } catch (e) { this.error = e.message; this.nodes = []; }
                finally { this.loading = false; }
                if (chat.grant) this.loadChanges();
            },
            // 工作区换代后的本地清理
            resetState() {
                this.previewToken += 1;
                this.nodes = [];
                this.preview = null; this.previewPath = ''; this.previewError = ''; this.previewLoading = false;
                this.selectedAbs = ''; this.editing = false;
                this.editor = { path: '', content: '', revision: '' };
                this.diff = ''; this.changes = []; this.gitText = ''; this.result = ''; this.showGrant = false;
            },
            async selectFile(node) {
                const token = ++this.previewToken;
                this.previewPath = node.rel; this.selectedAbs = node.path;
                this.preview = null; this.previewError = ''; this.previewLoading = true;
                this.editing = false; this.diff = '';
                try {
                    const data = await SWApi.developer.workspace.preview(node.path);
                    if (token !== this.previewToken) return;
                    this.preview = data;
                } catch (e) {
                    if (token !== this.previewToken) return;
                    this.previewError = e.message;
                } finally {
                    if (token === this.previewToken) this.previewLoading = false;
                }
            },
            clearFile() {
                this.previewToken += 1;
                this.previewPath = ''; this.selectedAbs = '';
                this.preview = null; this.previewError = ''; this.previewLoading = false;
                this.editing = false; this.diff = '';
            },
            startEdit() { this.act(async()=>{ this.editor = await SWApi.developer.action('read', {path: this.previewPath}); this.editing = true; this.diff = ''; }); },
            cancelEdit() { this.editing = false; this.diff = ''; },
            previewDiff() { this.act(async()=>{ this.diff = (await SWApi.developer.action('preview', this.editor)).diff; }); },
            saveFile() { this.act(async()=>{
                this.editor = await SWApi.developer.action('write', this.editor);
                this.diff = ''; this.result = '已保存：' + this.editor.path;
                if (this.selectedAbs) {
                    try { this.preview = await SWApi.developer.workspace.preview(this.selectedAbs); } catch (e) { /* 预览刷新失败不挡保存结果 */ }
                }
                await this.loadChanges();
            }); },
            async loadChanges() {
                try { this.changes = (await SWApi.developer.action('changes')).changes; }
                catch (e) { this.error = e.message; }
            },
            loadGit() { this.act(async()=>{ this.gitText = (await SWApi.developer.action('git')).text || '（git 没有输出）'; }); },
            changeAction(row, action) { this.act(async()=>{
                const data = await SWApi.developer.action(action, {id: row.id});
                if (action === 'diff') this.diff = data.diff;
                else { this.changes = (await SWApi.developer.action('changes')).changes; this.result = '回滚已完成。'; }
            }); },
            fmtSize(bytes) {
                const n = Number(bytes) || 0;
                if (n < 1024) return n + ' B';
                if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
                return (n / 1048576).toFixed(1) + ' MB';
            },
            // 拖左缘调抽屉宽；宽度变量同时喂给 .main 的让位边距
            startDrawerResize(e) {
                const min = 380;
                dragResize(e, ev => window.innerWidth - ev.clientX, min,
                    () => Math.max(window.innerWidth - 300, min),
                    px => document.documentElement.style.setProperty('--files-drawer-w', px + 'px'),
                    () => localStorage.setItem('sw_files_drawer_w', String(varPx('--files-drawer-w', 640))));
            },
            // 拖分栏条调树宽；上限给预览留出至少 200px。
            // 拖拽条压在树右边线上（中心比边线右偏半个条宽），减掉它让边线跟手不跳。
            startTreeResize(e) {
                const layout = this.$refs.filesLayout;
                if (!layout) return;
                const min = 150, half = 7;
                dragResize(e, ev => ev.clientX - layout.getBoundingClientRect().left - half, min,
                    () => Math.max(layout.clientWidth - 200, min),
                    px => document.documentElement.style.setProperty('--files-tree-w', px + 'px'),
                    () => localStorage.setItem('sw_files_tree_w', String(varPx('--files-tree-w', 260))));
            },
        },
        template: `
            <div v-if="chat.filesOpen" class="drawer-backdrop" @click.self="close" @keydown="keydown">
                <aside class="files-drawer" role="dialog" :aria-modal="narrow" aria-labelledby="files-title">
                    <div class="studio-heading">
                        <div>
                        <span class="eyebrow">WORKSPACE // FILES</span>
                        <h2 id="files-title">工作区文件</h2>
                        </div>
                        <button class="icon-btn" @click="close" aria-label="关闭文件面板">
                        <sw-icon name="x" :size="24">
                        </sw-icon>
                        </button>
                        </div>
                    <div class="workspace-bar">
                        <span>当前工作区 <code :title="chat.workspace.root">{{chat.workspace.root}}</code><template v-if="chat.workspace.is_default"> · 默认</template><template v-if="chat.workspace.stale"> · 原目录不存在，已回退默认</template></span>
                        <span class="studio-actions">
                        <button @click="$emit('workspace')">切换工作区</button>
                        <button :aria-pressed="showGrant" @click="showGrant=!showGrant">{{ chat.grant ? '接管详情' : '开启接管' }}</button>
                        </span>
                    </div>
                    <grant-card v-if="showGrant"></grant-card>
                    <p v-if="error" class="error-text" role="alert">{{error}}</p>
                    <p v-if="busy" role="status">处理中…</p>
                    <div ref="filesLayout" class="files-layout">
                        <div class="files-tree">
                            <div class="studio-actions">
                                <button :disabled="loading" @click="rebuild">{{ loading ? '读取中…' : '刷新结构' }}</button>
                            </div>
                            <div class="ws-tree-rows files-tree-rows" role="tree">
                                <template v-for="row in rows" :key="row.node.rel">
                                    <button v-if="row.node.dir" class="ws-node ws-node-dir" role="treeitem"
                                        :aria-expanded="row.node.open"
                                        :style="{ paddingLeft: (6 + row.depth * 15) + 'px' }"
                                        @click="toggleDir(row.node)">
                                        <sw-icon name="caret" :size="16" class="ws-caret" :class="{ open: row.node.open }"></sw-icon>
                                        <sw-icon name="folder" :size="16"></sw-icon>
                                        <span class="ws-node-name">{{ row.node.name }}</span>
                                        <span v-if="row.node.loading" class="ws-node-size">读取中…</span>
                                    </button>
                                    <button v-else class="ws-node ws-node-file" :class="{ picked: row.node.rel === previewPath }"
                                        :style="{ paddingLeft: (26 + row.depth * 15) + 'px' }"
                                        :title="row.node.rel" @click="selectFile(row.node)">
                                        <sw-icon :name="row.icon.name" :size="16" :style="row.icon.color ? { '--icon-detail': row.icon.color } : null"></sw-icon>
                                        <span class="ws-node-name">{{ row.node.name }}</span>
                                        <span class="ws-node-size">{{ fmtSize(row.node.size) }}</span>
                                    </button>
                                </template>
                                <p v-if="!rows.length && !loading" class="usage-dim">空目录，或目录读不出来。</p>
                            </div>
                        </div>
                        <div class="files-split" @pointerdown.prevent="startTreeResize" role="separator" aria-orientation="vertical" aria-label="拖动调整结构树宽度"></div>
                        <div class="files-view">
                            <template v-if="previewPath">
                                <div class="setting-row-title files-view-head">
                                    <span class="files-path" :title="previewPath">{{ previewPath }}</span>
                                    <span class="studio-actions">
                                    <button v-if="!editing && canEdit" :disabled="busy" @click="startEdit">编辑</button>
                                    <template v-if="editing">
                                    <button :disabled="busy" @click="previewDiff">预览差异</button>
                                    <button class="btn-save" :disabled="busy||!diff" @click="saveFile">保存修改</button>
                                    <button :disabled="busy" @click="cancelEdit">退出编辑</button>
                                    </template>
                                    <button v-else @click="clearFile">关闭</button>
                                    </span>
                                </div>
                                <p v-if="previewLoading" class="usage-dim">读取中…</p>
                                <p v-else-if="previewError" class="error-text">{{previewError}}</p>
                                <template v-else-if="preview">
                                    <p class="usage-dim">{{ fmtSize(preview.size) }}<template v-if="preview.truncated"> · 文件较大，只显示前 256 KiB，不能直接编辑</template><template v-else-if="!editing && !canEdit"> · 编辑需先开启接管（含编辑权限）</template></p>
                                    <textarea v-if="editing" v-model="editor.content" rows="18" spellcheck="false">
                                    </textarea>
                                    <pre v-else class="files-preview-body" v-html="previewHtml"></pre>
                                </template>
                            </template>
                            <p v-else class="usage-dim">点左侧文件看内容；开启接管后可直接编辑与回滚。</p>
                        </div>
                    </div>
                    <pre v-if="diff" class="studio-output">{{diff}}</pre>
                    <details v-if="chat.grant" class="studio-editor">
                        <summary>变更与回滚</summary>
                        <div class="studio-actions">
                        <button :disabled="busy" @click="loadChanges">刷新变更</button>
                        <button :disabled="busy" @click="loadGit">Git 状态</button>
                        </div>
                        <p v-if="!changes.length" class="usage-dim">当前工作区还没有登记过的修改。</p>
                        <div v-for="row in changes" :key="row.id" class="studio-item">
                        <span>{{row.path}}</span>
                        <button @click="changeAction(row,'diff')">差异</button>
                        <details v-if="chat.grant.write">
                        <summary>回滚</summary>
                        <button @click="changeAction(row,'rollback')">确认回滚这一项</button>
                        </details>
                        </div>
                        <pre v-if="gitText" class="studio-output">{{gitText}}</pre>
                    </details>
                    <pre v-if="result" class="studio-output" aria-live="polite">{{result}}</pre>
                </aside>
                <div class="drawer-resize files-resize" @pointerdown.prevent="startDrawerResize" role="separator" aria-orientation="vertical" aria-label="拖动调整文件面板宽度"></div>
            </div>
        `,
    };

    components['studio-panel'] = {
        data() { return {chat, narrow:true, catalog:{tools:[],skills:[],mcp:[]}, jobs:[], templates:[], busy:false, error:'', result:'',
            tool:{name:'',description:'',url:'',method:'GET',category:'core',params:'{}',headers_env:'{}'}, editingTool:false,
            selectedTool:null, testArgs:'{}', testApproved:false,
            hooks:'[]',
            skill:{id:'',content:'',revision:null},
            code:'', url:'', headers:'{}', checkPath:'', httpsSite:true, task:'',
            ttyId:null, ttyStatus:'未打开', ttySequence:0}; },
        computed: {
            tab: {get() {return chat.studioTab;}, set(value) {chat.studioTab=value;}},
            domainTools() { return this.catalog.tools.filter(t => t.category === chat.activeDomain || t.category === 'core').sort((a,b)=>(a.category===chat.activeDomain?0:1)-(b.category===chat.activeDomain?0:1)); },
        },
        watch: {
            tab(value) {this.result='';this.error='';if(value==='terminal')this.enterTerminal();},
            'chat.activeDomain'() {this.tab='analyze';this.result='';},
            // 打开抽屉先拉目录再连终端：load 占着 busy 时 connectTTY 会被 act 静默拦下，
            // 首次进终端 tab 会表现为"点开没反应"，所以排到 load 落地之后再连。
            'chat.studioOpen'(open) { focusDialog(this,open,'button'); if(open){this.load().then(()=>{if(this.tab==='terminal')this.enterTerminal();});} else this.detachTTY(); },
            'chat.mode'(mode) { if(mode!=='lv999' && chat.grant)chat.revokeGrant(); },
            'chat.grant'(grant) {if(!grant)this.closeTTY();else if(chat.studioOpen&&this.tab==='terminal')this.enterTerminal();},
            // 工作区换了（侧栏切换、选择器选中、或把当前工作区移出列表）：
            // 后端已吊销授权并关闭终端，前端必须同步清空，否则终端区还显示"已接管"和旧状态，
            // 用户点一下才收到"授权已失效"，看着就像删工作区出了毛病。
            'chat.workspaceEpoch'() { this.resetAfterWorkspaceChange(); },
        },
        mounted() {this._viewport=window.matchMedia('(max-width:1100px)');this._resize=()=>{this.narrow=this._viewport.matches;};this._resize();this._viewport.addEventListener('change',this._resize);},
        beforeUnmount() { this.detachTTY();this._viewport?.removeEventListener('change',this._resize); },
        methods: {
            // 方向定义是模块级的，组件里要各自暴露一次才能在模板里用
            domainInfo(id) { return domainInfo(id); },
            async act(fn) {
                if(this.busy)return;
                this.busy=true;this.error='';
                try { return await fn(); } catch(e) { this.error=e.message; }
                finally {this.busy=false;}
            },
            async load() { return this.act(async()=>{
                const [catalog,skills,jobs,hooks] = await Promise.all([SWApi.workbench.catalog(),SWApi.skills.list(),SWApi.jobs.list(),SWApi.workbench.hooks()]);
                this.catalog=catalog;this.templates=skills.templates||[];this.jobs=jobs.jobs||[];
                this.hooks=JSON.stringify(hooks.rules||[],null,2);
                chat.loadWorkspace();
            }); },
            async refreshCatalog() { this.catalog=await SWApi.workbench.catalog(); },
            saveHooks() {this.act(async()=>{await SWApi.workbench.saveHooks(JSON.parse(this.hooks));this.result='调用守卫已保存。规则只能增加限制或记账，不能放宽审批。';});},
            close() {chat.studioOpen=false;},
            keydown(e) {if(e.target.closest?.('.pty-host')&&!this._ttyTabFocus)return;if(this.narrow)dialogKey(e,this.$el,()=>this.close());else if(e.key==='Escape'){e.preventDefault();this.close();}},
            toggleTool(t) {this.act(async()=>{await SWApi.workbench.enabled(t.name,!t.enabled);await this.refreshCatalog();});},
            selectTool(t) {this.selectedTool=t;this.testArgs='{}';this.testApproved=false;this.result='';},
            runTool() {this.act(async()=>{this.result=renderToolResult(await SWApi.workbench.test(this.selectedTool.name,JSON.parse(this.testArgs),this.testApproved));});},
            editTool(t) {this.act(async()=>{const row=await SWApi.workbench.definition(t.name);this.tool={...row,params:JSON.stringify(row.params||{},null,2),headers_env:JSON.stringify(row.headers_env||{},null,2)};this.editingTool=true;});},
            newTool() {this.editingTool=false;this.tool={name:'',description:'',url:'',method:'GET',category:chat.activeDomain,params:'{}',headers_env:'{}'};},
            saveTool() {this.act(async()=>{const {headers,...row}=this.tool;await SWApi.workbench.save(row.name,{...row,params:JSON.parse(row.params),headers_env:JSON.parse(row.headers_env)});await this.refreshCatalog();this.result='工具已保存。';});},
            removeTool(t) {this.act(async()=>{await SWApi.workbench.remove(t.name);await this.refreshCatalog();this.selectedTool=null;});},
            openSkill(s) {this.act(async()=>{this.skill=await SWApi.skills.get(s.id);});},
            skillTemplate(t) {this.skill={id:t.id,content:t.content,revision:null};},
            saveSkill() {this.act(async()=>{this.skill=await SWApi.skills.save(this.skill.id,{content:this.skill.content,revision:this.skill.revision});await this.refreshCatalog();this.result='技能已保存；在列表中开启后，下次任务按需读取。';});},
            toggleSkill(s) {this.act(async()=>{await SWApi.skills.enabled(s.id,!s.enabled);await this.refreshCatalog();});},
            removeSkill() {this.act(async()=>{await SWApi.skills.remove(this.skill.id,this.skill.revision);this.skill={id:'',content:'',revision:null};await this.refreshCatalog();});},
            importSkill(e) {const file=e.target.files[0];e.target.value='';if(file)this.act(async()=>{this.skill=await SWApi.skills.import(file);await this.refreshCatalog();});},
            exportSkill(s) {this.act(()=>SWApi.skills.export(s.id));},
            probe(name,args) {this.act(async()=>{this.result=renderToolResult(await SWApi.workbench.test(name,args));});},
            headersProbe() {this.act(async()=>{
                let parsed;
                try { parsed=JSON.parse(this.headers); }
                catch(_) { throw new Error('响应头不是合法 JSON，请检查引号、逗号与括号。'); }
                this.result=renderToolResult(await SWApi.workbench.test('security_headers_check',{headers:parsed,https:this.httpsSite}));
            });},
            // 后台任务不跟随输入框芯片：芯片是"这一条消息"的方向，任务该用全量工具，
            // 否则用户点过一次 Python 之后，任务会静默丢掉网络安全工具。
            startTask() {this.act(async()=>{await SWApi.jobs.start({message:this.task,provider_id:chat.activeProviderId,model:chat.activeModel,agent_mode:true,enable_web:chat.enableWeb,use_rag:chat.useRag,kb_slugs:chat.kbSlugs});this.jobs=(await SWApi.jobs.list()).jobs;});},
            taskAction(j,action) {this.act(async()=>{if(action==='output')this.result=JSON.stringify(await SWApi.jobs.output(j.id),null,2);else await SWApi.jobs[action](j.id);this.jobs=(await SWApi.jobs.list()).jobs;});},
            // 工作区换代后的面板本地清理：终端。共享授权与提示由 chat.resetWorkspaceSession 处理。
            resetAfterWorkspaceChange() {
                this.detachTTY();
                this.ttyId = null;
                this.ttyStatus = '未打开';
                this.ttySequence = 0;
                this.result = '';
            },
            // 进终端 tab / 打开面板时：先量好可见尺寸，必要时自动连上（授权里有终端位）。
            // 尺寸为 0 时不连——xterm 提前开在隐藏容器里会算错列宽。
            enterTerminal() { this.$nextTick(()=>{ this.fitTTY(); if(chat.grant?.terminal && !this._term && chat.studioOpen && chat.studioTab==='terminal') this.connectTTY(); }); },
            connectTTY() {this.act(async()=>{
                if(!window.Terminal)throw new Error('缺少本地xterm运行时');
                this.detachTTY();this.ttySequence=0;this.ttyStatus='连接中';
                await this.$nextTick();
                const term=new Terminal({fontFamily:'Consolas, monospace',fontSize:14,rows:18,cols:60,scrollback:2000,screenReaderMode:true,theme:{background:'#0b101b',foreground:'#dce6ff',cursor:'#ff70bc'}});
                this._term=term;term.open(this.$refs.ptyHost);term.parser.registerOscHandler(52,()=>true);
                this._ttyTabFocus=false;
                term.attachCustomKeyEventHandler(e=>{if(e.type==='keydown'&&e.ctrlKey&&e.key.toLowerCase()==='m'){this._ttyTabFocus=!this._ttyTabFocus;chat.showToast(this._ttyTabFocus?'Tab 切换焦点。':'Tab 用于终端补全。','info');return false;}if(e.key==='Tab'&&this._ttyTabFocus)return false;return true;});
                const socket=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/api/terminal');this._ttySocket=socket;
                term.onData(text=>{if(socket.readyState!==WebSocket.OPEN)return;if(socket.bufferedAmount>65536){this.error='终端输入缓冲已满，等连接恢复。';return;}const points=Array.from(text);for(let i=0;i<points.length;i+=4096)socket.send(JSON.stringify({type:'input',text:points.slice(i,i+4096).join('')}));});
                this._ttyResize=new ResizeObserver(()=>this.fitTTY());this._ttyResize.observe(this.$refs.ptyHost);
                await new Promise((resolve,reject)=>{
                    const deadline=setTimeout(()=>{socket.close();reject(new Error('终端连接超时'));},10000);
                    socket.onopen=()=>socket.send(JSON.stringify({grant:SWApi.developer.current(),id:this.ttyId,after:0}));
                    socket.onmessage=e=>{
                        const packet=JSON.parse(e.data);
                        if(packet.type==='ready'){clearTimeout(deadline);this.ttyId=packet.id;this.ttyStatus='运行中';this.fitTTY();term.focus();resolve();}
                        else if(packet.type==='output'&&packet.id>this.ttySequence){this.ttySequence=packet.id;term.write(packet.text);}
                        else if(packet.type==='reset'){term.reset();this.ttySequence=0;}
                        else if(packet.type==='exit'){this.ttyStatus='已退出 · '+(packet.code??'—');this.ttyId=null;}
                        else if(packet.type==='error'){clearTimeout(deadline);this.error=packet.text;this.ttyStatus='连接失败';reject(new Error(packet.text));}
                    };
                    socket.onerror=()=>{clearTimeout(deadline);this.ttyStatus='连接失败';reject(new Error('终端连接失败，检查登录与授权状态。'));};
                    socket.onclose=()=>{clearTimeout(deadline);if(this.ttyStatus==='运行中'||this.ttyStatus==='连接中')this.ttyStatus='已断开';reject(new Error('终端连接已关闭'));};
                });
            });},
            fitTTY() {const node=this.$refs.ptyHost,term=this._term;if(!node||!term)return;const cols=Math.max(20,Math.min(160,Math.floor((node.clientWidth-20)/8.5))),rows=Math.max(8,Math.min(60,Math.floor((node.clientHeight-14)/17)));term.resize(cols,rows);if(this._ttySocket?.readyState===WebSocket.OPEN)this._ttySocket.send(JSON.stringify({type:'resize',cols,rows}));},
            detachTTY() {this._ttyResize?.disconnect();if(this._ttySocket){this._ttySocket.close();this._ttySocket=null;}this._term?.dispose();this._term=null;},
            closeTTY() {if(this._ttySocket?.readyState===WebSocket.OPEN)this._ttySocket.send(JSON.stringify({type:'close'}));this.detachTTY();this.ttyId=null;this.ttyStatus='已关闭';},
            // 拖左缘调创造台抽屉宽；宽度变量同时喂给 .main 的让位边距
            startStudioResize(e) {
                const min = 380;
                dragResize(e, ev => window.innerWidth - ev.clientX, min,
                    () => Math.max(window.innerWidth - 300, min),
                    px => document.documentElement.style.setProperty('--studio-drawer-w', px + 'px'),
                    () => localStorage.setItem('sw_studio_drawer_w', String(varPx('--studio-drawer-w', 560))));
            },
        },
        template: `
            <div v-if="chat.studioOpen" class="drawer-backdrop" @click.self="close" @keydown="keydown">
                <aside class="studio-drawer" role="dialog" :aria-modal="narrow" aria-labelledby="studio-title">
                    <div class="studio-heading">
                        <div>
                        <span class="eyebrow">CAPABILITIES // {{chat.mode === 'lv999' ? 'CREATOR' : 'WORKBENCH'}}</span>
                        <h2 id="studio-title">{{chat.mode === 'lv999' ? '创造工作台' : '专业工作台'}}</h2>
                        </div>
                        <button class="icon-btn" @click="close" aria-label="关闭工作台">
                        <sw-icon name="x" :size="24">
                        </sw-icon>
                        </button>
                        </div>
                    <nav class="studio-tabs" aria-label="专业方向">
                        <button v-for="d in [['agent','Agent'],['python','Python'],['cyberSecurity','网络安全']]" :key="d[0]" :aria-pressed="chat.activeDomain===d[0]" @click="chat.activeDomain=d[0]">{{d[1]}}</button>
                        </nav>
                    <nav class="studio-tabs" aria-label="能力管理">
                        <button v-for="t in [['terminal','终端'],['tools','工具'],['skills','Skills'],['tasks','任务'],['analyze','检查']]" :key="t[0]" :aria-pressed="tab===t[0]" @click="tab=t[0]">{{t[1]}}</button>
                        <button class="icon-btn" @click="load" :disabled="busy" aria-label="刷新能力">
                        <sw-icon name="refresh" :size="20">
                        </sw-icon>
                        </button>
                        </nav>
                    <p v-if="error" class="error-text" role="alert">{{error}}</p>
                        <p v-if="busy" role="status">处理中…</p>
                    <section v-if="tab==='tools'" class="studio-section">
                        <h3>工具链</h3>
                        <p>当前方向：{{ chat.pendingProfiles.length ? chat.pendingProfiles.map(id => domainInfo(id).label).join(' + ') : '全部' }}。方向在输入框左侧的加号里选，可多选，只作用于那一条消息；这里按方向列出工具，先看参数和风险再试运行。</p>
                        <div v-for="t in domainTools" :key="t.name" class="studio-item">
                        <div>
                        <button class="text-button" @click="selectTool(t)">{{t.name}}</button>
                        <p>{{t.description}}</p>
                        <small>{{t.kind}} · {{(t.risks||[]).join(' / ') || '只读'}}</small>
                        </div>
                        <div class="studio-actions">
                        <button :disabled="busy" :aria-pressed="t.enabled" @click="toggleTool(t)">{{t.enabled?'已开启':'已关闭'}}</button>
                        <button v-if="t.kind==='custom'" @click="editTool(t)">编辑</button>
                        </div>
                        </div>
                        <div v-if="selectedTool" class="studio-editor">
                        <h4>{{selectedTool.name}} · 试运行</h4>
                        <details>
                        <summary>参数定义</summary>
                        <pre>{{JSON.stringify(selectedTool.parameters,null,2)}}</pre>
                        </details>
                        <label>参数 JSON<textarea v-model="testArgs" spellcheck="false" rows="4">
                        </textarea>
                        </label>
                        <label v-if="selectedTool.approval_required" class="check-row">
                        <input type="checkbox" v-model="testApproved">允许这一次实际调用：{{(selectedTool.risks||[]).join(' / ')}}</label>
                        <button class="btn-save" :disabled="busy || (selectedTool.approval_required&&!testApproved)" @click="runTool">运行工具</button>
                        <details v-if="selectedTool.kind==='custom'">
                        <summary>删除工具</summary>
                        <button class="btn-danger" @click="removeTool(selectedTool)">确认删除 {{selectedTool.name}}</button>
                        </details>
                        </div>
                        <details class="studio-editor">
                        <summary>调用守卫 / Hooks</summary>
                        <p>只允许禁止或记账，不执行脚本。event 用 before_tool / after_tool / after_response；action 用 deny / log。tool 支持 * 和 ?，回答完成事件匹配 chat。</p>
                        <label>规则 JSON<textarea v-model="hooks" rows="6" spellcheck="false" placeholder='[{"id":"block-example","event":"before_tool","action":"deny","tool":"example_*","enabled":true}]'>
                        </textarea>
                        </label>
                        <button class="btn-save" :disabled="busy" @click="saveHooks">保存守卫规则</button>
                        </details>
                        <details class="studio-editor">
                        <summary>添加 / 编辑 HTTP 工具</summary>
                        <p>固定公网域名，参数仅替换路径和查询。凭据填写服务端环境变量名。</p>
                        <button class="btn-test" @click="newTool">新建工具</button>
                        <label>标识<input v-model="tool.name" :readonly="editingTool">
                        </label>
                        <label>说明<input v-model="tool.description">
                        </label>
                        <label>URL 模板<input v-model="tool.url" placeholder="https://example.com/api?q={query}">
                        </label>
                        <div class="form-grid">
                        <label>方法<select v-model="tool.method">
                        <option>GET</option>
                        <option>POST</option>
                        </select>
                        </label>
                        <label>分类<select v-model="tool.category">
                        <option v-for="c in ['core','agent','python','cyberSecurity']">{{c}}</option>
                        </select>
                        </label>
                        </div>
                        <label>参数定义 JSON<textarea v-model="tool.params" rows="3" spellcheck="false">
                        </textarea>
                        </label>
                        <label>请求头 → 环境变量 JSON<textarea v-model="tool.headers_env" rows="2" spellcheck="false">
                        </textarea>
                        </label>
                        <button class="btn-save" :disabled="busy" @click="saveTool">保存工具</button>
                        </details>
                    </section>
                    <section v-if="tab==='skills'" class="studio-section">
                        <h3>工作方法</h3>
                        <p>开启后我先读摘要，再按需读正文。Skill 不会扩大权限，也不会自动运行附带脚本。</p>
                        <div class="studio-item" v-for="s in catalog.skills" :key="s.id">
                        <div>
                        <button class="text-button" @click="openSkill(s)">{{s.id}}</button>
                        <p>{{s.description || s.error}}</p>
                        </div>
                        <div class="studio-actions">
                        <button :disabled="s.invalid||busy" @click="toggleSkill(s)" :aria-pressed="s.enabled">{{s.enabled?'已开启':'已关闭'}}</button>
                        <button @click="exportSkill(s)">导出</button>
                        </div>
                        </div>
                        <div class="studio-actions">
                        <button v-for="t in templates" :key="t.id" @click="skillTemplate(t)">{{t.description}}模板</button>
                        <label class="btn-test">导入 ZIP<input class="visually-hidden" type="file" accept=".zip" @change="importSkill">
                        </label>
                        </div>
                        <div class="studio-editor">
                        <label>技能标识<input v-model="skill.id" :readonly="!!skill.revision" placeholder="my-skill">
                        </label>
                        <label>SKILL.md<textarea v-model="skill.content" rows="14" spellcheck="false">
                        </textarea>
                        </label>
                        <button class="btn-save" :disabled="busy||!skill.id||!skill.content" @click="saveSkill">保存技能</button>
                        <details v-if="skill.revision">
                        <summary>删除此技能</summary>
                        <button class="btn-danger" @click="removeSkill">确认删除 {{skill.id}}</button>
                        </details>
                        </div>
                        </section>
                    <section v-if="tab==='tasks'" class="studio-section">
                        <h3>后台任务</h3>
                        <p>离开页面继续运行。停止在模型、工具与审批边界生效。</p>
                        <label>任务目标<textarea v-model="task" rows="3">
                        </textarea>
                        </label>
                        <button class="btn-save" :disabled="busy||!task.trim()" @click="startTask">启动 Agent 任务</button>
                        <div class="studio-item" v-for="j in jobs" :key="j.id">
                        <div>
                        <strong>{{j.message}}</strong>
                        <small>{{j.status}} · {{j.id}}</small>
                        <div class="studio-actions">
                        <button @click="taskAction(j,'output')">输出</button>
                        <button v-if="['queued','running'].includes(j.status)" @click="taskAction(j,'kill')">停止</button>
                        <details v-else>
                        <summary>删除记录</summary>
                        <button class="btn-danger" @click="taskAction(j,'remove')">确认删除</button>
                        </details>
                        </div>
                        </div>
                        </div>
                        </section>
                    <section v-if="tab==='analyze'" class="studio-section">
                        <template v-if="chat.activeDomain==='python'">
                        <h3>Python 检查</h3>
                        <p>语法与结构检查不执行代码；依赖核对以 requirements.txt 为准。</p>
                        <button class="btn-test" @click="probe('python_environment',{})">核对解释器与依赖</button>
                        <label>仓库内文件路径<input v-model="checkPath" placeholder="agent/runner.py">
                        </label>
                        <button class="btn-test" :disabled="!checkPath" @click="probe('python_check',{path:checkPath})">检查仓库文件</button>
                        <label>或粘贴 Python 代码<textarea v-model="code" rows="10" spellcheck="false">
                        </textarea>
                        </label>
                        <button class="btn-save" :disabled="!code" @click="probe('python_check',{code})">检查语法与结构</button>
                        </template>
                        <template v-else-if="chat.activeDomain==='cyberSecurity'">
                        <h3>被动安全检查</h3>
                        <p>校验地址和现有响应头，不扫描目标；缺少响应头只是待核查线索，不等于已确认漏洞。</p>
                        <label>URL<input v-model="url" placeholder="https://example.com">
                        </label>
                        <button class="btn-test" :disabled="!url" @click="probe('security_url_check',{url})">检查地址范围</button>
                        <label>响应头 JSON<textarea v-model="headers" rows="8" spellcheck="false">
                        </textarea>
                        </label>
                        <label class="check-row"><input type="checkbox" v-model="httpsSite">目标是 HTTPS 站点（决定是否评估 HSTS）</label>
                        <button class="btn-save" @click="headersProbe">体检响应头</button>
                        </template>
                        <template v-else>
                        <h3>Agent 运行检查</h3>
                        <p>查看已注册能力与风险；运行中的计划、审批和任务在聊天与任务页显示。</p>
                        <button class="btn-test" @click="probe('tools_list',{})">检查自定义工具</button>
                        <button class="btn-test" @click="load">刷新能力与任务</button>
                        </template>
                        </section>
                    <section v-show="tab==='terminal' && chat.mode==='lv999'" class="studio-section">
                        <h3>接管项目 · 终端</h3>
                        <div class="workspace-bar">
                        <span>当前工作区 <code :title="chat.workspace.root">{{chat.workspace.root}}</code><template v-if="chat.workspace.is_default"> · 默认</template><template v-if="chat.workspace.stale"> · 原目录不存在，已回退默认</template></span>
                        <span class="studio-actions">
                        <button :disabled="busy" @click="$emit('workspace')">切换工作区</button>
                        </span>
                        </div>
                        <grant-card></grant-card>
                        <template v-if="chat.grant && chat.grant.terminal">
                        <div class="studio-editor"><h4>交互终端 · ConPTY</h4><p>以当前用户身份运行，不是管理员提权，也没有操作系统沙箱。Ctrl+C 中断前台命令；Ctrl+M 切换 Tab 补全 / 焦点。断开后15秒内重连不会重跑命令。</p><div class="studio-actions"><span>{{ttyStatus}}</span><button :disabled="busy || ttyStatus==='运行中'" @click="connectTTY">{{ttyId?'连接已有终端':'打开终端'}}</button><button @click="closeTTY">关闭终端</button></div><div class="pty-host" ref="ptyHost" aria-label="项目交互终端"></div></div>
                        </template>
                        <p v-else-if="chat.grant" class="usage-dim">本次接管没有开终端权限。撤销后重新接管、勾上「终端」，就能在这里输入命令。</p>
                        </section>
                    <pre v-if="result" class="studio-output" aria-live="polite">{{result}}</pre>
                </aside>
                <div class="drawer-resize studio-resize" @pointerdown.prevent="startStudioResize" role="separator" aria-orientation="vertical" aria-label="拖动调整创造台宽度"></div>
            </div>
        `,
    };

    // ══════════════════════════════════════════════════
    // 根组件
    // ══════════════════════════════════════════════════
    const App = {
        data() {
            return {
                session,
                chat,
                kb,
                showSettings: false,
                showRenameModal: false,
                showDeleteModal: false,
                showWorkspacePicker: false,
                renameTarget: null,
                deleteTarget: null,
                loginToken:'',
            };
        },
        mounted() {
            // 恢复上次拖好的面板宽度（文件抽屉 / 创造台 / 文件树），首访用 CSS 里的默认值
            document.documentElement.style.setProperty('--files-drawer-w', storedPx('sw_files_drawer_w', 640) + 'px');
            document.documentElement.style.setProperty('--studio-drawer-w', storedPx('sw_studio_drawer_w', 560) + 'px');
            document.documentElement.style.setProperty('--files-tree-w', storedPx('sw_files_tree_w', 260) + 'px');
            this.bootstrap();
        },
        watch: {'chat.authRequired'(open){focusDialog(this,open,'.auth-gate button');}},
        methods: {
            async login() {try{await SWApi.system.login(this.loginToken.trim());this.chat.authRequired=false;await this.bootstrap();}catch(e){this.chat.showToast(e.message,'error');}finally{this.loginToken='';}},
            authKeys(e) {dialogKey(e,e.currentTarget,()=>{});},
            async bootstrap() {
                this.chat.loadTheme();
                await this.chat.loadUiState();
                await this.chat.loadAppearances();
                try {
                    const cfg = await SWApi.system.config();
                    this.chat.applyConfig(cfg);
                    this.chat.loadProviders(cfg.default_provider_id);
                } catch (e) {
                    console.error('加载配置失败:', e);
                    this.chat.showToast('工作台没连上。先启动服务，再重试。', 'error');
                }
                try {
                    this.chat.health = await SWApi.system.health();
                } catch (e) { /* 忽略 */ }
                this.session.loadSessions();
                this.kb.loadKnowledgeBases();
                this.kb.loadSupportedExtensions();
                // 工作区状态：聊天模式也读一次，切到工作模式时侧栏立刻有内容
                this.chat.loadWorkspace();
            },

            handleNewChat() {
                this.session.clearCurrent();
                this.chat.clearMessages();
            },
            async handleSelectSession(id) {                this.chat.clearMessages();
                try {
                    await this.session.loadSession(id);
                    if (this.session.currentSession) {
                        this.chat.messages = this.session.currentSession.messages.slice();
                    }
                } catch (e) {
                    this.chat.showToast('加载会话失败: ' + e.message, 'error');
                }
            },
            handleRenameClick(id, title) {
                this.renameTarget = { id, title };
                this.showRenameModal = true;
            },
            // 侧栏点「添加工作区」或某个工作区：有明确路径就直接切，否则打开选择器
            async openWorkspacePicker(path) {
                if (typeof path === 'string' && path) {
                    try {
                        await this.chat.setWorkspace(path);
                        this.chat.showToast('工作区已切换：' + this.chat.wsName(), 'info');
                    } catch (e) { this.chat.showToast(e.message, 'error'); }
                    return;
                }
                this.showWorkspacePicker = true;
            },
            // 从侧栏列表移除一个工作区：只移除记录，不删磁盘目录
            async removeWorkspace(entry) {
                try {
                    await this.chat.removeWorkspace(entry.path);
                    this.chat.showToast('已从列表移除：' + entry.name, 'info');
                } catch (e) { this.chat.showToast(e.message, 'error'); }
            },
            async handleRenameConfirm(id, title) {
                try {
                    await this.session.renameSession(id, title);
                    this.chat.showToast('重命名成功', 'success');
                } catch (e) {
                    this.chat.showToast('重命名失败: ' + e.message, 'error');
                }
                this.showRenameModal = false;
                this.renameTarget = null;
            },
            handleDeleteClick(id) {
                this.deleteTarget = id;
                this.showDeleteModal = true;
            },
            // v2.3.0：导出会话为 Markdown
            async handleExport(id) {
                try {
                    await SWApi.session.exportMd(id);
                } catch (e) {
                    this.chat.showToast('导出失败: ' + e.message, 'error');
                }
            },
            async handleDeleteConfirm() {
                try {
                    await this.session.deleteSession(this.deleteTarget);
                    this.chat.showToast('会话已删除', 'success');
                    this.chat.clearMessages();
                } catch (e) {
                    this.chat.showToast('删除失败: ' + e.message, 'error');
                }
                this.showDeleteModal = false;
                this.deleteTarget = null;
            },
        },
        template: `
            <div class="app-shell">
                <sidebar-component
                    @new-chat="handleNewChat"
                    @select="handleSelectSession"
                    @rename="handleRenameClick"
                    @delete="handleDeleteClick"
                    @export="handleExport"
                    @workspace="openWorkspacePicker"
                    @workspace-remove="removeWorkspace">
                </sidebar-component>

                <div class="main">
                    <header-bar></header-bar>
                    <chat-area></chat-area>
                    <div v-if="chat.commandPanel" class="command-panel" role="region" aria-label="快捷指令结果">
                        <button class="icon-btn command-close" @click="chat.commandPanel = ''" aria-label="关闭指令面板"><sw-icon name="x" :size="20"></sw-icon></button>
                        <template v-if="chat.commandPanel === 'help'"><span class="eyebrow">QUICK COMMANDS</span><h3>指令已就位。</h3><p><code>/help</code> 查看说明 · <code>/status</code> 查看状态 · <code>/model</code> 打开已添加模型</p><p class="usage-dim">这些指令在本机处理，不发给模型。Enter 发送，Shift + Enter 换行。点击或右击顶部银狼切换模式。</p></template>
                        <template v-else><span class="eyebrow">SYSTEM STATUS</span><h3>{{chat.health ? '服务已连接。' : '尚未取得服务状态。'}}</h3><div class="status-grid"><span>模式 <strong>{{chat.modeLabel()}}</strong></span><span>模型 <strong>{{chat.modelLabel()}}</strong></span><span>Agent <strong>{{chat.agentMode ? '开' : '关'}}</strong></span><span>联网 <strong>{{chat.enableWeb ? '开' : '关'}}</strong></span><span>知识库 <strong>{{chat.useRag ? chat.kbSlugs.length + ' 个' : '关'}}</strong></span><span>版本 <strong>{{chat.health?.version || '未读取'}}</strong></span></div><p v-if="chat.mode === 'lv999'" class="usage-dim">Lv.999 的项目权限在顶栏「终端 / 文件」里单独开启，有效30分钟；切换外观不会自动授权。</p></template>
                    </div>
                    <input-area></input-area>
                </div>

                <button class="settings-fab" @click="showSettings = !showSettings" title="设置">
                    <sw-icon name="settings" :size="26"></sw-icon>
                </button>

                <settings-modal ref="settings" :open="showSettings" @close="showSettings = false"></settings-modal>
                <studio-panel @workspace="openWorkspacePicker"></studio-panel>
                <files-panel @workspace="openWorkspacePicker"></files-panel>

                <approval-modal></approval-modal>

                <rename-modal :show="showRenameModal" :session="renameTarget"
                    @close="showRenameModal = false; renameTarget = null"
                    @confirm="handleRenameConfirm"></rename-modal>

                <delete-modal :show="showDeleteModal"
                    @close="showDeleteModal = false; deleteTarget = null"
                    @confirm="handleDeleteConfirm"></delete-modal>

                <workspace-picker :show="showWorkspacePicker"
                    @close="showWorkspacePicker = false"></workspace-picker>

                <app-toast></app-toast>
                <div v-if="chat.authRequired" class="auth-gate" @keydown="authKeys"><section role="dialog" aria-modal="true" aria-labelledby="auth-title"><span class="eyebrow">LINK OFFLINE</span><h2 id="auth-title">连接已锁定。</h2><p>本机直接重新登录；远程填写工作台现有令牌。</p><label>访问令牌<input type="password" v-model="loginToken" autocomplete="off" @keydown.enter="login"></label><button class="btn-save" @click="login">重新登录</button></section></div>
            </div>
        `,
    };

    // ══════════════════════════════════════════════════
    // 创建应用并注册组件后挂载
    // ══════════════════════════════════════════════════
    // v2.2.0：像素图标组件由 icons.js 提供（纯 render 函数，无模板）
    if (window.SWIcon && window.SWIcon.component) {
        components['sw-icon'] = window.SWIcon.component;
    }

    const app = Vue.createApp(App);
    for (const name in components) {
        app.component(name, components[name]);
    }
    app.mount('#app');
})();
