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

    // 消息气泡
    components['message-bubble'] = {
        props: ['message'],
        data() {
            return { chat };
        },
        methods: {
            renderedHtml() {
                const m = this.message;
                if (m.role === 'bot' && window.SWMarkdown && m.content) {
                    return window.SWMarkdown.render(m.content);
                }
                return '';
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
                        <img v-if="chat.avatars.bot" :src="chat.avatars.bot" alt="Silver Wolf" class="avatar-img">
                        <sw-icon v-else name="wolf" :size="22" class="bot-avatar-default"></sw-icon>
                    </template>
                </div>
                <div class="bubble" :class="{ streaming: message.streaming }">
                    <details v-if="message.reasoning" class="thinking-block">
                        <summary>
                            <sw-icon name="bolt" :size="13" class="think-bolt"></sw-icon>
                            <span>狼尊时刻</span>
                        </summary>
                        <div class="thinking-content">{{ message.reasoning }}</div>
                    </details>
                    <div v-if="message.attachments && message.attachments.length" class="msg-attachments">
                        <div v-for="a in message.attachments" :key="a.path || a.name" class="msg-attach-chip"
                            :title="'SHA256: ' + (a.sha256 || '')">
                            <sw-icon name="clip" :size="12"></sw-icon>
                            <span class="attach-name">{{ a.name }}</span>
                            <span class="attach-size">{{ a.size_label || a.size || '' }}</span>
                        </div>
                    </div>
                    <div v-if="message.role === 'bot' && message.content" class="markdown-body"
                        v-html="renderedHtml()"></div>
                    <span v-else>{{ message.content || '…' }}</span>
                    <!-- v2.3.0：任务计划进度清单（update_plan 工具） -->
                    <div v-if="message.plan && message.plan.length" class="plan-block">
                        <div class="plan-title">
                            <sw-icon name="check" :size="11"></sw-icon> 任务清单
                        </div>
                        <div v-for="(s, idx) in message.plan" :key="s.id || idx" class="plan-step" :class="s.status">
                            <span class="plan-dot"></span>
                            <span class="plan-text">{{ s.text }}</span>
                            <span v-if="s.status === 'running'" class="plan-badge">进行中</span>
                            <span v-else-if="s.status === 'done'" class="plan-badge done">完成</span>
                        </div>
                    </div>
                    <div v-if="message.fallback_warning" class="fallback-warning">
                        <sw-icon name="warning" :size="11"></sw-icon> {{ message.fallback_warning }}
                    </div>
                    <div v-if="message.rag_sources && message.rag_sources.length" class="rag-sources">
                        <sw-icon name="book" :size="11"></sw-icon> RAG 来源: {{ message.rag_sources.join(', ') }}
                    </div>
                    <div v-if="message.model" class="model-tag">
                        <sw-icon name="chip" :size="11"></sw-icon> {{ message.model }}
                    </div>
                    <!-- v2.0.0：Agent 本轮总结（token / 工具 / 联网来源 / RAG 命中） -->
                    <div v-if="message.usage || (message.tools_used && message.tools_used.length) || (message.web_searches && message.web_searches.length) || (message.rag_hits && message.rag_hits.length)"
                        class="msg-footer">
                        <div v-if="message.usage" class="footer-line usage-line">
                            <sw-icon name="coin" :size="11"></sw-icon> 本次消耗 {{ message.usage.total_tokens }} tokens
                            <span class="usage-dim">（输入 {{ message.usage.prompt_tokens }} / 输出 {{ message.usage.completion_tokens }}，{{ message.usage.source === 'provider' ? '实测' : '估算' }}）</span>
                            <span v-if="message.usage.steps > 1" class="usage-dim">· {{ message.usage.steps }} 轮</span>
                        </div>
                        <div v-if="message.tools_used && message.tools_used.length" class="footer-line">
                            <sw-icon name="wrench" :size="11"></sw-icon> 调用工具 {{ message.tools_used.length }} 次
                            <span v-for="(t, i) in message.tools_used" :key="i" class="mini-tag">{{ t.name }}</span>
                        </div>
                        <div v-if="message.web_searches && message.web_searches.length" class="footer-line">
                            <sw-icon name="globe" :size="11"></sw-icon> 联网搜索 {{ totalWebCount() }} 条
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
                            <sw-icon name="book" :size="11"></sw-icon> RAG 命中
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
            return { session };
        },
        template: `
            <aside class="sidebar">
                <div class="sidebar-header">
                    <button class="new-chat-btn" @click="$emit('new-chat')">
                        <sw-icon name="plus" :size="14"></sw-icon>
                        <span>新开一局</span>
                    </button>
                </div>
                <div class="sidebar-list">
                    <div v-if="session.loading" class="session-empty">读档中…</div>
                    <div v-else-if="session.sessions.length === 0" class="session-empty">
                        还没有存档<br>点上面「新开一局」开个头
                    </div>
                    <div v-for="s in session.sessions" :key="s.id"
                        class="session-item" :class="{ active: s.id === session.currentId }"
                        @click="$emit('select', s.id)">
                        <span class="session-item-title" :title="s.title">{{ s.title }}</span>
                        <div class="session-item-actions">
                            <button title="导出 Markdown" @click.stop="$emit('export', s.id)">
                                <sw-icon name="download" :size="13"></sw-icon>
                            </button>
                            <button title="重命名" @click.stop="$emit('rename', s.id, s.title)">
                                <sw-icon name="pencil" :size="13"></sw-icon>
                            </button>
                            <button class="delete-btn" title="删除" @click.stop="$emit('delete', s.id)">
                                <sw-icon name="trash" :size="13"></sw-icon>
                            </button>
                        </div>
                    </div>
                </div>
            </aside>
        `,
    };

    // 顶栏
    components['header-bar'] = {
        data() {
            return { chat };
        },
        methods: {
            toggleRag() {
                chat.setUseRag(!chat.useRag);
            },
        },
        template: `
            <div class="header">
                <div class="header-left">
                    <span class="logo">银狼</span>
                    <span class="badge">Silver Wolf · 星核猎手</span>
                </div>
                <div class="header-right">
                    <span class="model-badge" :title="chat.modelLabel()">
                        <span class="model-badge-dot"></span>
                        <span class="model-badge-text">{{ chat.modelLabel() }}</span>
                    </span>
                    <button class="icon-btn" :class="{ active: chat.useRag }"
                        @click="toggleRag" title="RAG 知识库检索">
                        <sw-icon name="book" :size="14"></sw-icon>
                        <span>RAG</span>
                    </button>
                </div>
            </div>
        `,
    };

    // 聊天区
    components['chat-area'] = {
        data() {
            return { chat };
        },
        watch: {
            'chat.messages': {
                handler() {
                    this.$nextTick(this.scrollToBottom);
                },
                deep: true,
            },
        },
        methods: {
            scrollToBottom() {
                const el = this.$refs.container;
                if (el) el.scrollTop = el.scrollHeight;
            },
        },
        template: `
            <div class="chat-container" ref="container">
                <div v-if="chat.messages.length === 0" class="welcome">
                    <img v-if="chat.avatars.bot" :src="chat.avatars.bot" class="welcome-avatar" alt="银狼">
                    <sw-icon v-else name="wolf" :size="64" class="welcome-wolf"></sw-icon>
                    <h2>银狼</h2>
                    <p class="welcome-tag">SILVER WOLF // 星核猎手</p>
                    <p class="greeting">啧…又来一个。说吧，要我卡哪个BUG？</p>
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
            return { chat, text: '', modelMenuOpen: false };
        },
        mounted() {
            // 点菜单以外的地方自动收起（否则菜单会"粘"在屏幕上）
            this._onDocClick = (e) => {
                const box = this.$el && this.$el.querySelector('.model-select');
                if (box && !box.contains(e.target)) this.modelMenuOpen = false;
            };
            document.addEventListener('click', this._onDocClick);
        },
        beforeUnmount() {
            document.removeEventListener('click', this._onDocClick);
        },
        methods: {
            onKeydown(e) {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
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
            },
            pickModel(provider, model) {
                const name = String(model || '').trim();
                if (!name) return;
                this.chat.setActiveProvider(provider.id, name);
                this.modelMenuOpen = false;
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
                const content = this.text;
                this.text = '';
                const el = this.$refs.input;
                if (el) el.style.height = 'auto';
                await this.chat.sendMessage(content);
            },
        },
        template: `
            <div class="input-area">
                <div class="input-box">
                    <div class="model-picker">
                        <!-- v2.1.0：原生 <select> 的下拉列表字号由系统控制、没法美化，
                             换成自绘菜单：分组更清楚、字号更大、当前项有勾选标记 -->
                        <div class="model-select" :class="{ open: modelMenuOpen }">
                            <button type="button" class="model-trigger" :disabled="chat.sending"
                                @click="toggleModelMenu" :title="chat.modelLabel()">
                                <sw-icon class="model-trigger-icon"
                                    :name="chat.activeKind() === 'local' ? 'monitor' : (chat.activeKind() ? 'cloud' : 'bot')"
                                    :size="15"></sw-icon>
                                <span class="model-trigger-text">
                                    <span class="model-trigger-name">{{ chat.activeModelName() }}</span>
                                    <span class="model-trigger-provider">{{ chat.activeProvider() ? chat.activeProvider().name : '还没有模型' }}</span>
                                </span>
                                <sw-icon class="model-trigger-caret" name="caret" :size="14"></sw-icon>
                            </button>

                            <div v-if="modelMenuOpen" class="model-menu">
                                <div v-if="!chat.providers.length" class="model-menu-empty">
                                    还没有添加模型 —— 去左下角的设置
                                    <sw-icon name="settings" :size="11"></sw-icon>
                                    里添加提供商和模型。
                                </div>
                                <template v-for="p in chat.providers" :key="p.id">
                                    <div class="model-menu-group">
                                        <sw-icon class="model-menu-group-icon"
                                            :name="p.kind === 'local' ? 'monitor' : 'cloud'" :size="13"></sw-icon>
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
                                            class="model-menu-check" name="check" :size="13"></sw-icon>
                                    </button>
                                </template>
                            </div>
                        </div>
                        <button class="attach-btn" @click="$refs.fileInput.click()"
                            :disabled="chat.sending" title="上传附件（文本/压缩包/代码/文档等）">
                            <sw-icon name="clip" :size="15"></sw-icon>
                        </button>
                        <input ref="fileInput" type="file" multiple style="display:none;" @change="onAttachChange">
                    </div>
                    <div v-if="chat.pendingFiles.length" class="pending-files">
                        <span v-for="(f, i) in chat.pendingFiles" :key="i" class="pending-file-chip">
                            <sw-icon name="clip" :size="12"></sw-icon>
                            <span>{{ f.name }}</span>
                            <span class="pf-size">{{ formatSize(f.size) }}</span>
                            <button class="pf-remove" @click="chat.removePendingFile(i)" title="移除">
                                <sw-icon name="x" :size="10"></sw-icon>
                            </button>
                        </span>
                        <span v-if="chat.uploading" class="pf-uploading">上传中…</span>
                    </div>
                    <textarea ref="input" rows="1"
                        placeholder="输入消息，按 Enter 键发送"
                        v-model="text" @input="autoResize" @keydown="onKeydown"></textarea>
                </div>
                <button class="send-btn" :class="{ sending: chat.sending }"
                    @click="chat.sending ? chat.stopGenerating() : send"
                    :title="chat.sending ? '停止生成' : '发送'">
                    <span v-if="chat.sending" class="stop-square"></span>
                    <sw-icon v-else name="send" :size="17"></sw-icon>
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
            };
        },
        methods: {
            showTab(tab) {
                this.activeTab = tab;
                if (tab === 'agent') this.loadAgentInfo();
                if (tab === 'memory') this.loadMemories();
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
                this.editing = {
                    id: p.id,
                    name: p.name,
                    kind: p.kind,
                    base_url: p.base_url,
                    model: p.model,
                    api_key: '',
                    clear_key: false,
                    is_default: !!p.is_default,
                    api_key_placeholder: p.has_api_key ? ('已保存: ' + (p.api_key_masked || '***')) : null,
                    models: (p.models || []).slice(),
                };
                this.activeTab = 'providers';
            },
            newProvider() {
                this.editing = {
                    id: null,
                    name: '',
                    kind: 'cloud',
                    base_url: '',
                    model: '',
                    api_key: '',
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
            onBgUpload(e) {
                const file = e.target.files && e.target.files[0];
                e.target.value = '';
                if (!file) return;
                if (file.size > 3 * 1024 * 1024) {
                    this.chat.showToast('背景图请控制在 3MB 以内', 'warning');
                    return;
                }
                const reader = new FileReader();
                reader.onload = () => {
                    this.chat.setBgImage(reader.result);
                    this.chat.showToast('背景已更新', 'success');
                };
                reader.readAsDataURL(file);
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
                    .then(() => this.chat.showToast(role === 'bot' ? '银狼头像已更新' : '我的头像已更新', 'success'))
                    .catch(err => this.chat.showToast('头像上传失败: ' + err.message, 'error'));
            },
            async handleUpload(files) {
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
            useKb(kbSlug) {
                this.chat.setKbSlug(kbSlug);
                this.chat.showToast(`已切换到知识库: ${kbSlug}`, 'success');
            },
        },
        template: `
            <div v-if="open" class="settings-overlay" @click.self="$emit('close')">
                <div class="settings-dialog">
                    <div class="settings-nav">
                        <div class="settings-nav-header">
                            <span class="settings-nav-title">
                                <sw-icon name="settings" :size="14"></sw-icon> 设置
                            </span>
                            <button class="settings-close" @click="$emit('close')" title="关闭">
                                <sw-icon name="x" :size="15"></sw-icon>
                            </button>
                        </div>
                        <div class="settings-nav-section">模型提供商</div>
                        <button class="settings-nav-item" :class="{ active: activeTab === 'providers' && !editing }"
                            @click="showTab('providers'); editing = null">
                            <sw-icon name="plug" :size="14"></sw-icon> 所有提供商
                        </button>
                        <button v-for="p in chat.providers" :key="p.id"
                            class="settings-nav-item provider-nav-item"
                            :class="{ active: editing && editing.id === p.id }"
                            @click="openProvider(p)">
                            <span class="kind-dot" :class="p.kind"></span>
                            <span class="nav-provider-name">{{ p.name }}</span>
                            <span class="px-dot" :class="{ on: p.has_api_key }"
                                :title="p.has_api_key ? ('API Key: ' + p.api_key_masked) : '未设置 API Key'"></span>
                            <sw-icon v-if="p.is_default" class="nav-default" name="star" :size="12"
                                title="默认提供商"></sw-icon>
                        </button>
                        <button class="settings-nav-add" @click="newProvider">
                            <sw-icon name="plus" :size="12"></sw-icon> 添加提供商
                        </button>

                        <div class="settings-nav-section">其他</div>
                        <button class="settings-nav-item" :class="{ active: activeTab === 'agent' }"
                            @click="showTab('agent')">
                            <sw-icon name="bot" :size="14"></sw-icon> Agent 能力
                        </button>
                        <button class="settings-nav-item" :class="{ active: activeTab === 'memory' }"
                            @click="showTab('memory')">
                            <sw-icon name="brain" :size="14"></sw-icon> 记忆
                        </button>
                        <button class="settings-nav-item" :class="{ active: activeTab === 'context' }"
                            @click="showTab('context')">
                            <sw-icon name="chip" :size="14"></sw-icon> 上下文
                        </button>
                        <button class="settings-nav-item" :class="{ active: activeTab === 'appearance' }"
                            @click="showTab('appearance')">
                            <sw-icon name="brush" :size="14"></sw-icon> 外观
                        </button>
                        <button class="settings-nav-item" :class="{ active: activeTab === 'kb' }"
                            @click="showTab('kb')">
                            <sw-icon name="book" :size="14"></sw-icon> 知识库 / RAG
                        </button>
                        <button class="settings-nav-item" :class="{ active: activeTab === 'system' }"
                            @click="showTab('system')">
                            <sw-icon name="server" :size="14"></sw-icon> 系统状态
                        </button>
                    </div>

                    <div class="settings-content">
                        <div v-if="activeTab === 'providers'">
                            <div v-if="!editing" class="settings-empty">
                                从左侧选择提供商进行编辑，或点下面的「添加提供商」接入新的模型接口。
                            </div>
                            <div v-else class="provider-form">
                                <h3>
                                    <sw-icon :name="editing.id ? 'pencil' : 'plus'" :size="14"></sw-icon>
                                    {{ editing.id ? '编辑提供商' : '添加提供商' }}
                                </h3>
                                <div class="form-grid">
                                    <label class="form-label">显示名称
                                        <input v-model="editing.name" placeholder="如: 本地 Ollama / DeepSeek">
                                    </label>
                                    <label class="form-label">类型
                                        <select v-model="editing.kind">
                                            <option value="cloud">云端 API（OpenAI 兼容）</option>
                                            <option value="local">本地 Ollama</option>
                                        </select>
                                    </label>
                                    <label class="form-label">接口地址
                                        <input v-model="editing.base_url" placeholder="https://api.deepseek.com/v1">
                                    </label>
                                    <label class="form-label">默认模型（聊天兜底）
                                        <input v-model="editing.model" placeholder="如 deepseek-chat / deepseek-r1:8b">
                                    </label>
                                    <label class="form-label">API Key
                                        <input type="password" v-model="editing.api_key"
                                            :placeholder="editing.api_key_placeholder || '输入新 Key；留空则保留原值'">
                                    </label>
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
                                    <div class="models-manage-title">已添加模型（输入框下拉只显示这些）</div>
                                    <div v-if="editing.models.length === 0" class="models-empty">
                                        还没有模型，在下面输入模型名添加
                                    </div>
                                    <div class="model-chips">
                                        <span v-for="m in editing.models" :key="m" class="model-chip">
                                            <span class="chip-name">{{ m }}</span>
                                            <button class="chip-remove" @click="removeModelFromEdit(m)" title="移除">
                                                <sw-icon name="x" :size="10"></sw-icon>
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

                        <div v-else-if="activeTab === 'kb'" class="kb-pane">
                            <h3><sw-icon name="book" :size="15"></sw-icon> 知识库 / RAG</h3>
                            <div class="setting-card">
                                <div class="setting-row">
                                    <div>
                                        <div class="setting-row-title">RAG 检索</div>
                                        <div class="setting-row-desc">问答时自动检索当前知识库，把相关内容拼进上下文</div>
                                    </div>
                                    <label class="switch">
                                        <input type="checkbox" v-model="chat.useRag">
                                        <span class="slider"></span>
                                    </label>
                                </div>
                                <div class="setting-row">
                                    <div>
                                        <div class="setting-row-title">当前知识库</div>
                                        <div class="setting-row-desc mono">{{ chat.kbSlug }}</div>
                                    </div>
                                </div>
                            </div>

                            <div class="setting-card">
                                <div class="setting-card-title">上传文件到当前知识库</div>
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
                                            <sw-icon :name="r.success ? 'check' : 'cross'" :size="12"></sw-icon>
                                        </span>
                                    </div>
                                </div>
                            </div>

                            <div class="setting-card">
                                <div class="setting-card-title">知识库管理</div>
                                <div class="kb-create-row">
                                    <input type="text" placeholder="知识库标识 (如 my_kb)" v-model="newKbName">
                                    <button class="btn-confirm" @click="createKb">创建</button>
                                </div>
                                <div class="kb-list">
                                    <div v-if="kb.knowledgeBases.length === 0" class="kb-empty">
                                        暂无知识库，请先创建或上传文件
                                    </div>
                                    <div v-for="item in kb.knowledgeBases" :key="item.kb_slug" class="kb-item"
                                        :class="{ active: item.kb_slug === chat.kbSlug }">
                                        <div>
                                            <span class="kb-name">{{ item.kb_slug }}</span>
                                            <div class="kb-count">文档数: {{ item.document_count || 0 }}</div>
                                        </div>
                                        <div class="kb-actions">
                                            <button class="kb-use" @click="useKb(item.kb_slug)"
                                                :disabled="item.kb_slug === chat.kbSlug">
                                                {{ item.kb_slug === chat.kbSlug ? '使用中' : '使用' }}
                                            </button>
                                            <button class="kb-del" @click="deleteKb(item.kb_slug)" title="删除">
                                                <sw-icon name="trash" :size="13"></sw-icon>
                                            </button>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div v-else-if="activeTab === 'system'" class="system-pane">
                            <h3><sw-icon name="server" :size="15"></sw-icon> 系统状态</h3>
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
                        </div>

                        <div v-else-if="activeTab === 'agent'" class="agent-pane">
                            <h3><sw-icon name="bot" :size="15"></sw-icon> Agent 能力</h3>
                            <div class="setting-card">
                                <div class="setting-card-title">运行方式</div>
                                <div class="setting-row">
                                    <div>
                                        <div class="setting-row-title">Agent 模式</div>
                                        <div class="setting-row-desc">开启后模型可自动调用工具（联网、RAG、自我维护）</div>
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
                                        <div class="setting-row-desc">允许 Agent 搜索网页并引用来源（关闭后只用本地资料）</div>
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
                                        <div class="setting-row-desc">一次提问里最多「模型→工具」往返次数（1-8）</div>
                                    </div>
                                    <input type="number" class="steps-input" min="1" max="8"
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
                                    Agent 可以自行新增/修改/删除「RAG 文件」与「自定义工具」（声明式 HTTP 工具，
                                    不执行任意代码）；所有自我修改都会记录在 <code>data/agent_changes.log</code>。
                                </div>
                            </div>

                            <div class="setting-card search-config">
                                <div class="setting-card-title">联网搜索后端</div>
                                <p class="search-config-desc">
                                    主后端是 <strong>Tavily</strong>（填 Key 即可）；没有 Key、额度用完
                                    或把上面的开关关掉时，会自动用内置爬虫兜底（<strong>Bing</strong>，免 Key）。
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
                                            关闭后只用内置爬虫（Bing，免 Key），不消耗 Tavily 额度
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

                                <div class="config-hint">保存后可分别自检两条通道：</div>
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
                            <h3><sw-icon name="brain" :size="15"></sw-icon> 长期记忆</h3>
                            <p class="pane-note">
                                跨会话的记忆仓。她替你记下的约定、你跟她拍板过的事都在这 ——
                                存档跨会话有效，删掉就真的忘了，慎点。
                            </p>
                            <div class="setting-card">
                                <div class="setting-card-title">手动存档</div>
                                <div class="config-row">
                                    <label class="config-label" for="cfg-mem-content">内容</label>
                                    <div class="config-control">
                                        <input id="cfg-mem-content" type="text" v-model="memoryForm.content"
                                            placeholder="想让银狼记住的事，例如：项目用的框架是 FastAPI"
                                            @keyup.enter="addMemoryEntry">
                                    </div>
                                </div>
                                <div class="config-row">
                                    <label class="config-label" for="cfg-mem-tags">标签</label>
                                    <div class="config-control">
                                        <input id="cfg-mem-tags" type="text" v-model="memoryForm.tags"
                                            placeholder="可选，逗号分隔，便于 recall 命中" @keyup.enter="addMemoryEntry">
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
                                    还没有存档。跟银狼说「记住……」她就会自动记在这里。
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
                            <h3><sw-icon name="chip" :size="15"></sw-icon> 记忆与上下文</h3>
                            <p class="pane-note">
                                啧，先讲清楚：她的记性不是白长的。你留下的每段历史她每回合都要重读一遍，
                                账单按遍数算。省着喂，或者干脆把额度烧光 —— 你自己选。
                            </p>

                            <div class="setting-card">
                                <div class="setting-card-head">
                                    <div class="setting-card-title">上下文窗口</div>
                                    <span class="ctx-value">{{ chat.unlimitedTurns ? '∞' : chat.fmtTokens(chat.contextBudget) }}</span>
                                </div>
                                <p class="search-config-desc">
                                    每次请求她要重读的全部底料：人设 + 知识库命中 + 旧账 + 你刚发的这句。
                                    约 {{ (chat.ctxChineseChars() / 10000).toFixed(1) }} 万字中文 —— 多大锅煮多少饭。
                                </p>
                                <input class="ctx-slider" type="range" min="0"
                                    :max="chat.ctxPresets.length - 1" step="1"
                                    :value="chat.ctxSliderIndex()" :disabled="chat.unlimitedTurns"
                                    @input="chat.setContextBudget(chat.ctxPresets[$event.target.value])"
                                    aria-label="上下文窗口档位">
                                <div class="ctx-ticks" :class="{ disabled: chat.unlimitedTurns }">
                                    <span v-for="(v, i) in chat.ctxPresets" :key="v"
                                        class="ctx-tick" :class="{ active: i === chat.ctxSliderIndex() }"
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
                                    4K ~ 1M。开大不等于变聪明 —— 多留一段历史，往后每回合都要再付一次。
                                </div>
                            </div>

                            <div class="setting-card">
                                <div class="setting-card-title">记忆策略</div>
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
                                    刀下留人 —— 窗口再挤，最近这几轮也不裁（1 轮 = 你问一次 + 她答一次）。
                                </div>
                                <div class="setting-row">
                                    <div>
                                        <div class="setting-row-title">无限轮数</div>
                                        <div class="setting-row-desc">
                                            记忆全留，一条不删。她不会断片，断的只会是你的账单。
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
                                            过期的对话压成一页小抄存本地：要点留着，废话扔掉
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
                                    搜回来的网页又长又吵？剪短，只留干的那段（500 ~ 50000 字符）。
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
                                        @click="chat.setReplyMaxTokens(v)">{{ chat.fmtTokens(v) }}</span>
                                </div>
                                <div class="setting-row">
                                    <div>
                                        <div class="setting-row-title">无限长度</div>
                                        <div class="setting-row-desc">
                                            让她说尽兴，不在半句话上被掐断。代价同上 —— 字数也是钱。
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
                            <h3><sw-icon name="brush" :size="15"></sw-icon> 外观</h3>
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
                                        <div class="setting-row-desc">选一个主色调，点缀色会自动搭配</div>
                                    </div>
                                    <input type="color" class="color-input"
                                        :value="chat.theme.accent"
                                        @input="chat.setCustomAccent($event.target.value)">
                                </div>
                            </div>
                            <div class="setting-card">
                                <div class="setting-card-title">背景图</div>
                                <div class="bg-actions">
                                    <button class="btn-confirm" @click="$refs.bgInput.click()">
                                        <sw-icon name="folder" :size="13"></sw-icon> 上传本地图片
                                    </button>
                                    <input ref="bgInput" type="file" accept="image/*" style="display:none;"
                                        @change="onBgUpload">
                                    <button class="btn-cancel" @click="chat.clearBgImage()"
                                        :disabled="!chat.theme.bgImage">恢复默认</button>
                                </div>
                                <div class="bg-note">
                                    背景仅保存在本机浏览器（localStorage），不会上传服务器；
                                    默认无背景图；上传后自动加深色遮罩保证文字可读。
                                </div>
                            </div>
                            <div class="setting-card">
                                <div class="setting-card-title">头像</div>
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
                                    头像仅保存在本机浏览器（localStorage），不上传服务器，也不会进入仓库。
                                </div>
                            </div>
                        </div>
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
            session(newVal) {
                if (newVal) this.input = newVal.title || '';
            },
        },
        methods: {
            confirm() {
                if (this.input.trim() && this.session) {
                    this.$emit('confirm', this.session.id, this.input.trim());
                }
            },
        },
        template: `
            <div v-if="show" class="modal-overlay">
                <div class="modal">
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
        template: `
            <div v-if="show" class="modal-overlay">
                <div class="modal">
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
        methods: {
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
            <div v-if="chat.pendingApproval" class="approval-overlay" @click.self="respond(false)">
                <div class="approval-box">
                    <div class="approval-title">
                        <sw-icon name="warning" :size="15"></sw-icon> Agent 需要授权
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
                renameTarget: null,
                deleteTarget: null,
            };
        },
        mounted() {
            this.bootstrap();
        },
        methods: {
            async bootstrap() {
                this.chat.loadTheme();
                this.chat.loadUiState();
                try {
                    const cfg = await SWApi.system.config();
                    this.chat.applyConfig(cfg);
                    this.chat.loadProviders(cfg.default_provider_id);
                } catch (e) {
                    console.error('加载配置失败:', e);
                    this.chat.showToast('无法连接后端服务，请先启动: python -m uvicorn main:app --reload', 'error');
                }
                try {
                    this.chat.health = await SWApi.system.health();
                } catch (e) { /* 忽略 */ }
                this.session.loadSessions();
                this.kb.loadKnowledgeBases();
                this.kb.loadSupportedExtensions();
            },

            handleNewChat() {
                this.session.clearCurrent();
                this.chat.clearMessages();
            },
            async handleSelectSession(id) {
                this.chat.clearMessages();
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
                    @export="handleExport">
                </sidebar-component>

                <div class="main">
                    <header-bar></header-bar>
                    <chat-area></chat-area>
                    <input-area></input-area>
                </div>

                <button class="settings-fab" @click="showSettings = !showSettings" title="设置">
                    <sw-icon name="settings" :size="24"></sw-icon>
                </button>

                <settings-modal :open="showSettings" @close="showSettings = false"></settings-modal>

                <approval-modal></approval-modal>

                <rename-modal :show="showRenameModal" :session="renameTarget"
                    @close="showRenameModal = false; renameTarget = null"
                    @confirm="handleRenameConfirm"></rename-modal>

                <delete-modal :show="showDeleteModal"
                    @close="showDeleteModal = false; deleteTarget = null"
                    @confirm="handleDeleteConfirm"></delete-modal>

                <app-toast></app-toast>
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
