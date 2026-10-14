/**
 * 银狼界面图标：app.js 使用 sw-icon，stores.js 复用狼头生成标签图标。
 * 原生 SVG 几何路径，24px 网格、双配色；不引入图标库、字体或远程资源。
 * 图标隐藏于辅助阅读器，按钮本身提供名称，避免重复读出。
 */
(function () {
    'use strict';
    const paths = {
        settings: ['M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1z', 'M9 12a3 3 0 1 0 6 0 3 3 0 1 0-6 0'],
        plug: ['M8 3v5m8-5v5M6 8h12v4l-3 4H9l-3-4z', 'M12 16v5'],
        shield: ['M12 2l9 4v6l-3 6-6 4-6-4-3-6V6z', 'M8 12l3 3 5-6'],
        bot: ['M5 7h14l2 3v8l-3 3H6l-3-3v-8zM12 3v4', 'M7 12h2m6 0h2m-8 5h6'],
        chip: ['M6 6h12v12H6zM9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4', 'M9 9h6v6H9z'],
        brush: ['M14 3l7 7-9 9-7-7zM5 12l-2 9 9-2', 'M12 5l7 7M3 21l5-5'],
        book: ['M12 6L4 3H2v15h3l7 3 7-3h3V3h-2zM12 6v15', 'M5 8l4 1m6 0l4-1M5 12l4 1m6 0l4-1'],
        brain: ['M8 3h8l4 4v10l-4 4H8l-4-4V7z', 'M8 8h8v8H8zM12 3v5m0 8v5M4 12h4m8 0h4'],
        server: ['M3 3h18v6H3zM3 15h18v6H3z', 'M6 6h1m3 0h8M6 18h1m3 0h8M7 9v6m10-6v6'],
        trash: ['M4 6h16M9 3h6M6 6l1 15h10l1-15', 'M10 10v7m4-7v7'],
        download: ['M4 16v5h16v-5', 'M12 3v13m-5-5l5 5 5-5'],
        folder: ['M3 6h7l2 3h9v11H3z', 'M8 14l4-4 4 4m-4-4v7'],
        file: ['M5 2h9l5 5v15H5zM14 2v6h5','M8 12h8m-8 4h8'],
        // 文件类型图标：同一张文件底稿 + 类型标记（标记走 --icon-detail 上色）
        'file-py': ['M5 2h9l5 5v15H5zM14 2v6h5','M15.5 11.5c-3.2 0-3.2 2.8-3.2 4s0 4-3.2 4M13.8 13h.01'],
        'file-md': ['M5 2h9l5 5v15H5zM14 2v6h5','M8 18.5v-6l2.5 3 2.5-3v6M15.5 12.5v6m-2-2l2 2 2-2'],
        'file-json': ['M5 2h9l5 5v15H5zM14 2v6h5','M10.5 11.5q-1.5 0-1.5 1.6v1.4q0 1.6-1.5 1.6M13.5 11.5q1.5 0 1.5 1.6v1.4q0 1.6 1.5 1.6'],
        'file-code': ['M5 2h9l5 5v15H5zM14 2v6h5','M10 12l-2.5 3.5 2.5 3.5M13.2 11.8l-2.4 7.4M14 12l2.5 3.5-2.5 3.5'],
        'file-img': ['M5 2h9l5 5v15H5zM14 2v6h5','M8.5 12h7v6.5h-7zM10.5 14h.01M9.5 17.4l1.7-2.1 1.3 1.1 1.7-2.2 1.2 1.5'],
        'file-sh': ['M5 2h9l5 5v15H5zM14 2v6h5','M9.5 12.5l2.8 3-2.8 3M13.5 18.5h4'],
        refresh: ['M20 9a8 8 0 0 0-14-4L3 8m0-5v5h5','M4 15a8 8 0 0 0 14 4l3-3m0 5v-5h-5'],
        monitor: ['M3 3h18v14H3zM8 21h8M12 17v4', 'M7 8l3 3-3 3m6 0h4'],
        cloud: ['M7 18H5a4 4 0 0 1-1-8 7 7 0 0 1 13-3 5 5 0 0 1 2 11h-2', 'M12 13v8m-3-5l3-3 3 3'],
        star: ['M12 2l3 6 7 1-5 5 1 7-6-3-6 3 1-7-5-5 7-1z'],
        check: ['M4 12l5 5L20 6'],
        cross: ['M6 6l12 12M18 6L6 18'],
        x: ['M6 6l12 12M18 6L6 18'],
        plus: ['M12 4v16M4 12h16'],
        pencil: ['M3 17L16 4l4 4L7 21H3z', 'M13 7l4 4'],
        coin: ['M12 2l8 5v10l-8 5-8-5V7z', 'M15 7H9v5h6v5H9m3-12v14'],
        wrench: ['M20 3l-5 2-2 5-9 9 2 2 9-9 5-2 2-5-4 4-3-3z'],
        globe: ['M3 12a9 9 0 1 0 18 0 9 9 0 1 0-18 0M3 12h18', 'M12 3c-6 6-6 12 0 18 6-6 6-12 0-18'],
        warning: ['M12 3l10 18H2z', 'M12 9v5m0 3v1'],
        clip: ['M8 16l8-8a3 3 0 0 0-4-4L4 12a5 5 0 0 0 7 7l9-9', 'M8 16l7-7'],
        send: ['M3 3l18 9-18 9 4-9z', 'M7 12h14'],
        user: ['M4 21v-3l4-4h8l4 4v3', 'M8 6a4 4 0 1 0 8 0 4 4 0 1 0-8 0'],
        bolt: ['M13 2L4 14h7l-1 8 10-12h-7z'],
        caret: ['M6 9l6 6 6-6'],
        wolf: ['M3 3l6 4h6l6-4v12l-5 6H8l-5-6zM3 10l9 6 9-6M9 18h6', 'M7 10h2m6 0h2M12 16v3']
    };

    function attributes(size) {
        return {class: 'sw-icon', viewBox: '0 0 24 24', width: size, height: size,
            fill: 'none', 'stroke-width': 1.8, 'stroke-linecap': 'round',
            'stroke-linejoin': 'round', 'aria-hidden': 'true', focusable: 'false'};
    }

    const component = typeof Vue === 'undefined' ? null : {
        name: 'sw-icon',
        props: {name: {type: String, required: true}, size: {type: [Number, String], default: 20}},
        render() {
            const icon = paths[this.name];
            if (!icon) console.warn('[SWIcon] 未定义图标：' + this.name);
            return Vue.h('svg', attributes(this.size), (icon || []).map((d, index) =>
                Vue.h('path', {d, stroke: index ? 'var(--icon-detail, var(--neon-cyan))' : 'currentColor'})));
        }
    };

    function wolfFaviconURI() {
        const svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linejoin="round">' +
            paths.wolf.map((d, index) => '<path d="' + d + '" stroke="' + (index ? '#58E3F1' : '#B7AEFF') + '"/>').join('') + '</svg>';
        return 'data:image/svg+xml,' + encodeURIComponent(svg);
    }

    window.SWIcon = {component, wolfFaviconURI};
})();
