/**
 * icons.js - SilverWolf 像素图标系统（v2.2.0 新增）
 *
 * 作用：
 *   整个前端不再使用 emoji/颜文字当图标，全部改为自绘的 16×16 像素风 SVG。
 *   「像素」是银狼角色设定的核心视觉母题（官方设计以像素为核心形象），
 *   因此图标走像素网格而不是线性描边。
 *
 * 被谁用：
 *   - frontend/src/app.js：注册 sw-icon 组件，全部模板里的图标都走它；
 *   - frontend/src/stores.js：用 SWIcon.wolfFaviconURI() 生成浏览器标签页图标；
 *   - frontend/index.html：favicon 内联了同一只像素狼头（静态字符串）。
 *
 * 核心原理：
 *   图标用「字符网格」定义（'.'=空 / 'X'=主色 / 'o'=点缀色），
 *   逐个字符转成 SVG path（同一行连续格合并成一个矩形 M..h..v1h-..z），
 *   16×16 网格 + shape-rendering:crispEdges 保证放大也保持像素颗粒感。
 *   主色用 currentColor 跟随文字颜色（因而跟随主题），点缀色固定为主题青蓝。
 *
 * 注意：
 *   sw-icon 组件用 render 函数（Vue.h）实现而不是模板字符串 —— 不用经过
 *   模板编译器，SVG 属性名（viewBox / shape-rendering 大小写敏感）最稳。
 *   图标网格必须每个恰好 16 行、每行 16 列，加载时会自校验并报错。
 */
(function () {
    'use strict';

    var ICON_SIZE = 16;

    // ══════════════════════════════════════════════════
    // 图标定义：每个图标 = 16 行 × 16 列的字符网格
    //   '.' 透明    'X' 主色（currentColor）    'o' 点缀色（青蓝）
    // ══════════════════════════════════════════════════
    var ICONS = {
        // 设置（齿轮）
        settings: [
            '......XXXX......',
            '......XXXX......',
            '....XXXXXXXX....',
            '...XXXXXXXXXX...',
            '..XXXXXXXXXXXX..',
            '..XXXXXXXXXXXX..',
            'XXXXXX....XXXXXX',
            'XXXXXX....XXXXXX',
            'XXXXXX....XXXXXX',
            'XXXXXX....XXXXXX',
            '..XXXXXXXXXXXX..',
            '..XXXXXXXXXXXX..',
            '...XXXXXXXXXX...',
            '....XXXXXXXX....',
            '......XXXX......',
            '......XXXX......',
        ],
        // 模型提供商（电源插头 / 通电符号）
        plug: [
            '.......XX.......',
            '.......XX.......',
            '.......XX.......',
            '.......XX.......',
            '....XX....XX....',
            '...XX......XX...',
            '..XX........XX..',
            '..XX........XX..',
            '..XX........XX..',
            '..XX........XX..',
            '..XX........XX..',
            '...XX......XX...',
            '....XX....XX....',
            '.....XXXXXX.....',
            '................',
            '................',
        ],
        // Agent（机器人头）
        bot: [
            '.......XX.......',
            '.......XX.......',
            '.....XXXXXX.....',
            '..XXXXXXXXXXXX..',
            '..XXXXXXXXXXXX..',
            '..XX..XXXX..XX..',
            '..XX..XXXX..XX..',
            '..XXXXXXXXXXXX..',
            '..XX.XXXXXX.XX..',
            '..XXXXXXXXXXXX..',
            '..XXXXXXXXXXXX..',
            '...XXXXXXXXXX...',
            '................',
            '................',
            '................',
            '................',
        ],
        // 上下文 / 记忆（芯片）
        chip: [
            '...XX..XX..XX...',
            '...XX..XX..XX...',
            '..XXXXXXXXXXXX..',
            '..XXXXXXXXXXXX..',
            '..XXX......XXX..',
            '..XX........XX..',
            '..XX........XX..',
            '..XX........XX..',
            '..XX........XX..',
            '..XXX......XXX..',
            '..XXXXXXXXXXXX..',
            '..XXXXXXXXXXXX..',
            '...XX..XX..XX...',
            '...XX..XX..XX...',
            '................',
            '................',
        ],
        // 外观（画笔）
        brush: [
            '................',
            '..........XXX...',
            '.........XXX....',
            '........XXX.....',
            '.......XXX......',
            '......XXX.......',
            '.....XXX........',
            '....XXX.........',
            '...XXX..........',
            '..XXXX..........',
            '..XXXXXX........',
            '..XXX.XX........',
            '..XX...X........',
            '................',
            '................',
            '................',
        ],
        // 知识库（打开的书）
        book: [
            '................',
            '................',
            '...XXXX..XXXX...',
            '..XXXXX..XXXXX..',
            '..XXXXX..XXXXX..',
            '..XXXXX..XXXXX..',
            '..XXXXX..XXXXX..',
            '..XXXXX..XXXXX..',
            '..XXXXX..XXXXX..',
            '..XXXXX..XXXXX..',
            '..XXXXX..XXXXX..',
            '...XXXX..XXXX...',
            '................',
            '................',
            '................',
            '................',
            '................',
        ],
        // 长期记忆（记忆块 / 存档点）
        brain: [
            '................',
            '................',
            '....XXXXXXXX....',
            '...XXXXXXXXXX...',
            '..XX........XX..',
            '..XX..XXXX..XX..',
            '..XX.XX..XX.XX..',
            '..XX.XX..XX.XX..',
            '..XX..XXXX..XX..',
            '..XX........XX..',
            '..XXXXXXXXXXXX..',
            '...XX.XXXX.XX...',
            '....XX.XX.XX....',
            '....XXXXXXXX....',
            '................',
            '................',
        ],
        // 系统状态（服务器机架）
        server: [
            '................',
            '.XXXXXXXXXXXXXX.',
            '.XX.XXXXXXXXXXX.',
            '.XXXXXXXXXXXXXX.',
            '.XXXXXXXXXXXXXX.',
            '.XX.XXXXXXXXXXX.',
            '.XXXXXXXXXXXXXX.',
            '.XXXXXXXXXXXXXX.',
            '.XX.XXXXXXXXXXX.',
            '.XXXXXXXXXXXXXX.',
            '.XXXXXXXXXXXXXX.',
            '................',
            '................',
            '................',
            '................',
            '................',
        ],
        // 删除（垃圾桶）
        trash: [
            '................',
            '................',
            '....XXXXXXXX....',
            '.XXXXXXXXXXXXXX.',
            '.XXXXXXXXXXXXXX.',
            '..XXXXXXXXXXXX..',
            '..XXX.XX.XX.XX..',
            '..XXX.XX.XX.XX..',
            '..XXX.XX.XX.XX..',
            '..XXX.XX.XX.XX..',
            '..XXX.XX.XX.XX..',
            '..XXX.XX.XX.XX..',
            '..XXXXXXXXXXXX..',
            '...XXXXXXXXXX...',
            '................',
            '................',
        ],
        // 下载（箭头进托盘）
        download: [
            '................',
            '.......XX.......',
            '.......XX.......',
            '.......XX.......',
            '.......XX.......',
            '.......XX.......',
            '......XXXX......',
            '......XXXX......',
            '.....XXXXXX.....',
            '....XXXXXXXX....',
            '..XXXXXXXXXXXX..',
            '................',
            '................',
            '................',
            '................',
            '................',
        ],
        // 上传（带箭头的文件夹）
        folder: [
            '................',
            '................',
            '................',
            '..XXX...........',
            '.XXXXX..........',
            '.XXXXXXXXXXXXXX.',
            '.XXXXXX..XXXXXX.',
            '.XXXXX....XXXXX.',
            '.XXXX......XXXX.',
            '.XXXXXX..XXXXXX.',
            '.XXXXXX..XXXXXX.',
            '.XXXXXX..XXXXXX.',
            '.XXXXXXXXXXXXXX.',
            '................',
            '................',
            '................',
        ],
        // 本地模型（显示器）
        monitor: [
            '................',
            '................',
            '.XXXXXXXXXXXXXX.',
            '.XXXXXXXXXXXXXX.',
            '.XX.XX.......XX.',
            '.XX.XX.......XX.',
            '.XX..........XX.',
            '.XX..........XX.',
            '.XX..........XX.',
            '.XXXXXXXXXXXXXX.',
            '.XXXXXXXXXXXXXX.',
            '......XXXX......',
            '....XXXXXXXX....',
            '................',
            '................',
            '................',
        ],
        // 云端模型（云）
        cloud: [
            '................',
            '................',
            '......XXXX......',
            '....XXXXXXXX....',
            '...XXXXXXXXXX...',
            '..XXXXXXXXXXXX..',
            '.XXXXXXXXXXXXXX.',
            '.XXXXXXXXXXXXXX.',
            '.XXXXXXXXXXXXXX.',
            '..XXXXXXXXXXXX..',
            '................',
            '................',
            '................',
            '................',
            '................',
            '................',
        ],
        // 默认提供商（星标）
        star: [
            '................',
            '................',
            '.......XX.......',
            '.......XX.......',
            '......XXXX......',
            '......XXXX......',
            'XXXXXXXXXXXXXXXX',
            '.XXXXXXXXXXXXXX.',
            '..XXXXXXXXXXXX..',
            '...XXXXXXXXXX...',
            '...XXXX..XXXX...',
            '...XXX....XXX...',
            '..XX........XX..',
            '..X..........X..',
            '................',
            '................',
        ],
        // 勾选
        check: [
            '................',
            '................',
            '................',
            '................',
            '................',
            '................',
            '...........XX...',
            '..........XXX...',
            '.........XXX....',
            '........XXX.....',
            'XX.....XXX......',
            'XXX...XXX.......',
            '.XXX.XXX........',
            '..XXXXX.........',
            '...XXX..........',
            '................',
        ],
        // 叉号（实心，用于状态）
        cross: [
            '................',
            '................',
            '................',
            '.XX..........XX.',
            '.XXX........XXX.',
            '..XXX......XXX..',
            '...XXX....XXX...',
            '....XXX..XXX....',
            '.....XXXXXX.....',
            '......XXXX......',
            '......XXXX......',
            '.....XXXXXX.....',
            '....XXX..XXX....',
            '...XXX....XXX...',
            '..XXX......XXX..',
            '.XXX........XXX.',
        ],
        // 关闭（细叉）
        x: [
            '................',
            '................',
            '................',
            '................',
            '..XX........XX..',
            '...XX......XX...',
            '....XX....XX....',
            '.....XX..XX.....',
            '......XXXX......',
            '......XXXX......',
            '.....XX..XX.....',
            '....XX....XX....',
            '...XX......XX...',
            '..XX........XX..',
            '................',
            '................',
        ],
        // 加号
        plus: [
            '................',
            '................',
            '................',
            '................',
            '................',
            '................',
            '.......XX.......',
            '.......XX.......',
            'XXXXXXXXXXXXXXXX',
            'XXXXXXXXXXXXXXXX',
            '.......XX.......',
            '.......XX.......',
            '................',
            '................',
            '................',
            '................',
        ],
        // 编辑（铅笔）
        pencil: [
            '................',
            '..........XXX...',
            '.........XXX....',
            '........XXX.....',
            '.......XXX......',
            '......XXX.......',
            '.....XXX........',
            '....XXX.........',
            '...XXX..........',
            '..XXX...........',
            '.XXX............',
            '.XX.............',
            '.X..............',
            '................',
            '................',
            '................',
        ],
        // 消耗（金币）
        coin: [
            '................',
            '................',
            '.....XXXXXX.....',
            '...XX......XX...',
            '..XX.XXXXXX.XX..',
            '..X..X....X..X..',
            '.XX..X....X..XX.',
            '.XX..X....X..XX.',
            '.XX..X....X..XX.',
            '.XX..X....X..XX.',
            '..X..X....X..X..',
            '..XX.XXXXXX.XX..',
            '...XX......XX...',
            '.....XXXXXX.....',
            '................',
            '................',
        ],
        // 工具（扳手）
        wrench: [
            '................',
            '..........XXXX..',
            '.........XX..XX.',
            '.........XX..XX.',
            '..........XXXX..',
            '.........XXX....',
            '........XXX.....',
            '.......XXX......',
            '......XXX.......',
            '.....XXX........',
            '....XXX.........',
            '...XXX..........',
            '...XX...........',
            '................',
            '................',
            '................',
        ],
        // 联网（地球）
        globe: [
            '................',
            '................',
            '.....XXXXXX.....',
            '...XX..XX..XX...',
            '..XX...XX...XX..',
            '..X....XX....X..',
            '.XX....XX....XX.',
            '.XXXXXXXXXXXXXX.',
            '.XX....XX....XX.',
            '.XX....XX....XX.',
            '..X....XX....X..',
            '..XX...XX...XX..',
            '...XX..XX..XX...',
            '.....XXXXXX.....',
            '................',
            '................',
        ],
        // 警告
        warning: [
            '................',
            '................',
            '.......XX.......',
            '......XXXX......',
            '.....XX..XX.....',
            '....XX.XX.XX....',
            '...XX..XX..XX...',
            '..XX...XX...XX..',
            '..XX........XX..',
            '.XX....XX....XX.',
            '.XX....XX....XX.',
            'XX............XX',
            'XXXXXXXXXXXXXXXX',
            '................',
            '................',
            '................',
        ],
        // 附件（回形针）
        clip: [
            '................',
            '.......XXXX.....',
            '......XX..XX....',
            '......XX..XX....',
            '......XX..XX....',
            '..XXXXXX..XX....',
            '..XX..XX..XX....',
            '..XX..XX..XX....',
            '..XX..XX..XX....',
            '..XXXXXX..XX....',
            '......XX..XX....',
            '......XXXXXX....',
            '................',
            '................',
            '................',
            '................',
        ],
        // 发送（向上箭头）
        send: [
            '................',
            '................',
            '.......XX.......',
            '......XXXX......',
            '.....XXXXXX.....',
            '....XXXXXXXX....',
            '...XXXXXXXXXX...',
            '..XXXXXXXXXXXX..',
            '......XXXX......',
            '......XXXX......',
            '......XXXX......',
            '......XXXX......',
            '......XXXX......',
            '................',
            '................',
            '................',
        ],
        // 用户（人像）
        user: [
            '................',
            '................',
            '......XXXX......',
            '.....XXXXXX.....',
            '.....XXXXXX.....',
            '.....XXXXXX.....',
            '......XXXX......',
            '...XXXXXXXXXX...',
            '..XXXXXXXXXXXX..',
            '.XXXXXXXXXXXXXX.',
            '.XXXXXXXXXXXXXX.',
            '.XXXXXXXXXXXXXX.',
            '.XXXXXXXXXXXXXX.',
            '................',
            '................',
            '................',
        ],
        // 思考（闪电）
        bolt: [
            '................',
            '.......XXXX.....',
            '......XXXX......',
            '.....XXXX.......',
            '....XXXX........',
            '...XXXXXXXXX....',
            '........XXXX....',
            '.......XXXX.....',
            '......XXXX......',
            '.....XXXX.......',
            '....XXXX........',
            '...XX...........',
            '................',
            '................',
            '................',
            '................',
        ],
        // 下拉箭头
        caret: [
            '................',
            '................',
            '................',
            '................',
            '................',
            '................',
            'XXXXXXXXXXXXXXXX',
            '.XXXXXXXXXXXXXX.',
            '..XXXXXXXXXXXX..',
            '...XXXXXXXXXX...',
            '....XXXXXXXX....',
            '.....XXXXXX.....',
            '......XXXX......',
            '.......XX.......',
            '................',
            '................',
        ],
        // 银狼标记：像素狼头（青蓝眼睛，品牌 / 默认头像 / favicon）
        wolf: [
            '................',
            '.XX..........XX.',
            '.XXX........XXX.',
            '.XXXX......XXXX.',
            '.XXXXX....XXXXX.',
            '.XXXXXXXXXXXXXX.',
            '.XXXooXXXXooXXX.',
            '.XXXooXXXXooXXX.',
            '.XXXXXXXXXXXXXX.',
            '.XXXXXX..XXXXXX.',
            '.XXXXXX..XXXXXX.',
            '.XXXXX.XX.XXXXX.',
            '..XXXXXXXXXXXX..',
            '...XXXXXXXXXX...',
            '................',
            '................',
        ],
    };

    // ══════════════════════════════════════════════════
    // 网格 → SVG path
    // 把同一行连续的同类像素合并成一条矩形子路径，压缩体积
    // ══════════════════════════════════════════════════
    function gridToPath(grid, pick) {
        var parts = [];
        for (var y = 0; y < grid.length; y++) {
            var row = grid[y];
            var x = 0;
            while (x < row.length) {
                if (!pick(row[x])) { x++; continue; }
                var run = 0;
                while (x + run < row.length && pick(row[x + run])) run++;
                parts.push('M' + x + ' ' + y + 'h' + run + 'v1h-' + run + 'z');
                x += run;
            }
        }
        return parts.join('');
    }

    // 加载时自校验：每个图标必须 16 行 × 每行 16 列，防止手绘网格写错
    var PATHS = {};
    for (var name in ICONS) {
        var grid = ICONS[name];
        var ok = grid.length === ICON_SIZE;
        if (ok) {
            for (var i = 0; i < grid.length; i++) {
                if (grid[i].length !== ICON_SIZE) { ok = false; break; }
            }
        }
        if (!ok) {
            // 开发期错误要显眼：宁可控制台报错，也不要页面悄悄少个图标
            console.error('[SWIcon] 图标网格尺寸错误: ' + name +
                '（需要 ' + ICON_SIZE + '×' + ICON_SIZE + '）');
            continue;
        }
        PATHS[name] = {
            main: gridToPath(grid, function (c) { return c === 'X'; }),
            accent: gridToPath(grid, function (c) { return c === 'o'; }),
        };
    }

    // ══════════════════════════════════════════════════
    // Vue 组件 sw-icon（render 函数实现，不经过模板编译器）
    // 用法：<sw-icon name="book" :size="15"></sw-icon>
    // ══════════════════════════════════════════════════
    var component = null;
    if (typeof Vue !== 'undefined' && typeof Vue.h === 'function') {
        component = {
            name: 'sw-icon',
            props: {
                name: { type: String, required: true },
                size: { type: [Number, String], default: 14 },
            },
            render: function () {
                var icon = PATHS[this.name];
                if (!icon) {
                    console.warn('[SWIcon] 未定义的图标: ' + this.name);
                    icon = { main: '', accent: '' };
                }
                var h = Vue.h;
                var kids = [];
                // 主色跟随文字颜色（currentColor），因而自动跟随主题
                if (icon.main) kids.push(h('path', { d: icon.main, fill: 'currentColor' }));
                // 点缀色（狼眼睛等）跟随主题青蓝变量
                if (icon.accent) kids.push(h('path', { d: icon.accent, fill: 'var(--neon-cyan)' }));
                return h('svg', {
                    'class': 'sw-icon',
                    'viewBox': '0 0 ' + ICON_SIZE + ' ' + ICON_SIZE,
                    'width': this.size,
                    'height': this.size,
                    'shape-rendering': 'crispEdges',
                    'aria-hidden': 'true',
                }, kids);
            },
        };
    }

    // ══════════════════════════════════════════════════
    // 工具函数：favicon（浏览器标签图标 = 像素狼头）
    // 标签页图标是静态 SVG 字符串，颜色必须写死，不能用主题变量
    // ══════════════════════════════════════════════════
    function wolfFaviconURI() {
        var wolf = PATHS.wolf || { main: '', accent: '' };
        var svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">' +
            '<path d="' + wolf.main + '" fill="#C9C3E8"/>' +
            '<path d="' + wolf.accent + '" fill="#3ED8F0"/>' +
            '</svg>';
        return 'data:image/svg+xml,' + encodeURIComponent(svg);
    }

    window.SWIcon = {
        component: component,
        paths: PATHS,
        pixelPath: function (name) { return PATHS[name] || null; },
        wolfFaviconURI: wolfFaviconURI,
    };
})();
