# frontend/vendor —— 前端本地依赖（Vue 3）

本目录存放**本地化**的前端运行时文件，供 `frontend/index.html` 引用，**无任何 CDN 外网请求**。

| 文件 | 用途 | 是否必需 |
|------|------|----------|
| `vue.global.prod.js` | Vue 3 完整版运行时（含模板编译器） | ✅ 必需 |

> 说明：`vue.global.prod.js` 体积约 130KB。本项目开发环境无外网，
> 因此该文件**未随仓库内置**。请在任意**有网络的机器**上运行项目根目录的
> 运行项目根目录的一键安装脚本 `install.bat` / `install.sh`（或按下述手动方式下载），
> 把文件放到本目录后即可正常使用。
> 依赖就绪后，本项目运行期依旧完全离线。

## 获取方式

### 方式一：下载脚本（推荐）
在有网络的机器上，运行项目根目录的 `install.bat` / `install.sh`（会自动下载本文件）。

### 方式二：手动下载
在浏览器/命令行打开以下任一地址，将文件另存为 `frontend/vendor/vue.global.prod.js`：

- jsDelivr：`https://cdn.jsdelivr.net/npm/vue@3.4.27/dist/vue.global.prod.js`
- unpkg：`https://unpkg.com/vue@3.4.27/dist/vue.global.prod.js`
- 国内镜像：`https://registry.npmmirror.com/vue/3.4.27/files/dist/vue.global.prod.js`

### 方式三：npm（有 Node 环境）
```bash
npm pack vue@3.4.27
# 解压后取 dist/vue.global.prod.js
```

## 校验
文件下载完成后，可用文本编辑器打开文件首行，应看到
`/*! Vue.js v3.4.27 ... */`。若打开页面仍提示“缺少 Vue 3 运行时”，
说明文件未放到正确位置（应为 `frontend/vendor/vue.global.prod.js`）。
