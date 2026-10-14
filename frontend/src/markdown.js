/**
 * markdown.js - 轻量离线 Markdown 渲染器（零依赖，无 CDN）
 * 先整体 HTML 转义再解析，杜绝 XSS；支持标题/粗斜体/行内代码/代码块/
 * 无序有序列表/引用/表格/链接/分割线。
 */
(function () {
    'use strict';

    function escapeHtml(s) {
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function emphasis(s) {
        // 链接
        s = s.replace(
            /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
            (m, label, url) => '<a href="' + url + '" target="_blank" rel="noopener">' + label + '</a>'
        );
        // 删除线
        s = s.replace(/~~([^~]+)~~/g, '<del>$1</del>');
        // 粗体
        s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        // 斜体（避免碰到粗体残留的 **）
        s = s.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>');
        return s;
    }

    function inline(text) {
        // 按反引号切段：奇数段是行内代码，只做展示；其余段才跑链接/删除线/粗斜体，
        // 代码里的 **x**、[a](url) 不会被二次解析
        return text
            .split(/`([^`]+)`/g)
            .map((part, index) => (index % 2 ? '<code>' + part + '</code>' : emphasis(part)))
            .join('');
    }

    function highlight(escaped,language) {
        if(!/^(python|py|javascript|js|typescript|ts|json|bash|sh)$/.test(language))return escaped;
        const words=new Set(('def class return import from as async await if else elif for while try except finally with lambda yield pass raise True False None let const var function new null true false throw export break continue').split(' '));
        return escaped.replace(/&quot;(?:\\.|(?!(?:&quot;))[^\\])*?&quot;|'(?:\\.|[^'\\])*'|#[^\n]*|\/\/[^\n]*|\b\d+(?:\.\d+)?\b|\b[A-Za-z_][\w]*\b/g,token=>{
            const kind=token.startsWith('&quot;')||token.startsWith("'")?'string':token.startsWith('#')||token.startsWith('//')?'comment':/^\d/.test(token)?'number':words.has(token)?'keyword':'';
            return kind?'<span class="syntax-'+kind+'">'+token+'</span>':token;
        });
    }

    function render(text) {
        if (!text) return '';
        const lines = escapeHtml(text).split(/\r?\n/);
        const out = [];
        let i = 0;
        let inCode = false;
        let codeLang = '';
        let codeBuf = [];
        let listType = null;
        let listBuf = [];

        function flushList() {
            if (!listBuf.length) return;
            const tag = listType === 'ul' ? 'ul' : 'ol';
            out.push('<' + tag + '>' + listBuf.join('') + '</' + tag + '>');
            listBuf = [];
            listType = null;
        }

        while (i < lines.length) {
            const line = lines[i];

            // 围栏代码块
            const fence = line.match(/^```([\w+-]*)\s*$/);
            if (fence) {
                flushList();
                if (!inCode) {
                    inCode = true;
                    codeLang = fence[1] || '';
                    codeBuf = [];
                } else {
                    out.push(
                        '<pre><code' + (codeLang ? ' class="lang-' + codeLang + '"' : '') + '>' +
                        highlight(codeBuf.join('\n'),codeLang) + '</code></pre>'
                    );
                    inCode = false;
                }
                i++;
                continue;
            }
            if (inCode) {
                codeBuf.push(line);
                i++;
                continue;
            }

            // 表格：| a | b | 后跟分隔行
            if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length &&
                /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1]) && lines[i + 1].indexOf('-') >= 0) {
                flushList();
                const header = line.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map(c => c.trim());
                const rows = [];
                i += 2;
                while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
                    rows.push(lines[i].replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map(c => c.trim()));
                    i++;
                }
                out.push(
                    '<table><thead><tr>' + header.map(h => '<th>' + inline(h) + '</th>').join('') + '</tr></thead>' +
                    '<tbody>' + rows.map(r => '<tr>' + r.map(c => '<td>' + inline(c) + '</td>').join('') + '</tr>').join('') +
                    '</tbody></table>'
                );
                continue;
            }

            // 标题
            const h = line.match(/^(#{1,4})\s+(.*)$/);
            if (h) {
                flushList();
                out.push('<h' + h[1].length + '>' + inline(h[2]) + '</h' + h[1].length + '>');
                i++;
                continue;
            }

            // 引用
            if (/^\s*&gt;\s?/.test(line)) {
                flushList();
                const buf = [];
                while (i < lines.length && /^\s*&gt;\s?/.test(lines[i])) {
                    buf.push(lines[i].replace(/^\s*&gt;\s?/, ''));
                    i++;
                }
                out.push('<blockquote>' + buf.map(b => inline(b)).join('<br>') + '</blockquote>');
                continue;
            }

            // 无序列表
            const ul = line.match(/^\s*[-*+]\s+(.*)$/);
            if (ul) {
                if (listType !== 'ul') { flushList(); listType = 'ul'; }
                listBuf.push('<li>' + inline(ul[1]) + '</li>');
                i++;
                continue;
            }
            // 有序列表
            const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
            if (ol) {
                if (listType !== 'ol') { flushList(); listType = 'ol'; }
                listBuf.push('<li>' + inline(ol[1]) + '</li>');
                i++;
                continue;
            }

            // 分割线
            if (/^\s*([-*_])\1{2,}\s*$/.test(line)) {
                flushList();
                out.push('<hr>');
                i++;
                continue;
            }

            flushList();
            if (!line.trim()) {
                i++;
                continue;
            }
            // 段落
            const para = [];
            while (i < lines.length && lines[i].trim()) {
                para.push(lines[i]);
                i++;
            }
            out.push('<p>' + para.map(p => inline(p)).join('<br>') + '</p>');
        }

        flushList();
        if (inCode) {
            out.push('<pre><code>' + codeBuf.join('\n') + '</code></pre>');
        }
        return out.join('\n');
    }

    window.SWMarkdown = { render };
})();
