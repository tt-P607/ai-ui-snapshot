"""浏览器外壳装饰脚本（站点无关）。

独立渲染并截取 1:1 复刻 Chrome / Chromium 官方标准标签页与导航栏的
横幅（``#mofox_chrome_banner``），供各站点截图时在顶部拼接使用。
横幅为临时挂载节点，截图后立即从 DOM 移除，不遮挡或污染页面本身。
"""

from __future__ import annotations

# 浏览器外壳装饰独立渲染脚本（1:1 复刻 Google Chrome / Chromium 官方标准标签页与导航栏）。
# 包含：
# 1. 第一行 Tab 栏 (40px)：Chromium 官方反向圆角裙边曲线 Active Tab + 真实 Favicon + 真实标题 + 关闭小叉 + 新建标签 (+) + Windows 原生窗口控制三件套 (最小化/最大化/关闭)
# 2. 第二行 工具栏 (44px)：Material Symbols 官方退进刷图标 + 药丸形地址栏 (Chrome 2023 Tune 面板图标 + 域名/脱敏路径 + 收藏星号) + 扩展拼图/侧边栏/Google 账号头像/三点菜单
# 挂载为独立截图容器 #mofox_chrome_banner，截图后立即从 DOM 中移除，绝不遮挡或污染页面本身。
BROWSER_CHROME_SCRIPT = """(payload) => {
    const width = payload && payload.width ? payload.width : 1440;
    const theme = payload && payload.theme ? payload.theme : 'auto';
    const avatarUrl = payload && payload.avatar_url ? payload.avatar_url : '';
    const old = document.getElementById('mofox_chrome_banner');
    if (old) old.remove();

    let dark = false;
    const t = (theme || '').toLowerCase();
    if (t === 'light') dark = false;
    else if (t === 'dark') dark = true;
    else {
        const isDarkClass = document.documentElement.classList.contains('dark') || document.body.classList.contains('dark');
        if (isDarkClass) {
            dark = true;
        } else {
            const bg = (getComputedStyle(document.body).backgroundColor || getComputedStyle(document.documentElement).backgroundColor || '');
            const m = bg.match(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/);
            if (m && (+m[1] !== 0 || +m[2] !== 0 || +m[3] !== 0)) {
                const lum = 0.299 * +m[1] + 0.587 * +m[2] + 0.114 * +m[3];
                dark = lum < 128;
            } else if (window.matchMedia) {
                dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
            }
        }
    }

    // Chromium 官方标准色板 (Chrome 2023 Refresh / Material You)
    const C = dark ? {
        tabStripBg: '#1e1f22',
        toolbarBg: '#2b2a33',
        tabText: '#f2f2f2',
        tabSubText: '#9aa0a6',
        captionColor: '#c4c7c5',
        navIcon: '#c4c7c5',
        omniboxBg: '#1e1f22',
        omniboxBorder: 'rgba(255, 255, 255, 0.08)',
        tuneIcon: '#a8c7fa',
        urlHost: '#ffffff',
        urlPath: '#8e918f',
        bottomBorder: 'rgba(255, 255, 255, 0.07)'
    } : {
        tabStripBg: '#dfe1e5',
        toolbarBg: '#ffffff',
        tabText: '#1f1f1f',
        tabSubText: '#5f6368',
        captionColor: '#444746',
        navIcon: '#444746',
        omniboxBg: '#f1f3f4',
        omniboxBorder: 'transparent',
        tuneIcon: '#0b57d0',
        urlHost: '#1f1f1f',
        urlPath: '#747775',
        bottomBorder: '#dadce0'
    };

    const favicon = (document.querySelector('link[rel~="icon"]') || {}).href || '';
    // 动态提取当前活跃对话的标题
    let chatTitle = '';
    const activeSidebar = document.querySelector('div[class*="_546d736"].active, [class*="_546d736"][data-active="true"]');
    if (activeSidebar) {
        chatTitle = (activeSidebar.innerText || '').split(/\\r?\\n/)[0].trim();
    }
    if (!chatTitle) {
        const headerTitleEl = document.querySelector('.ds-header-title, [class*="header"] h1, [class*="title"]');
        if (headerTitleEl) chatTitle = (headerTitleEl.innerText || '').trim();
    }
    if (!chatTitle) {
        chatTitle = (document.title || '').trim();
    }
    if (!chatTitle) {
        chatTitle = 'DeepSeek - 探索未至之境';
    } else if (!chatTitle.includes('DeepSeek') && !chatTitle.includes('Gemini')) {
        chatTitle = chatTitle + ' - DeepSeek';
    }
    const title = chatTitle;

    let urlHost = 'chat.deepseek.com';
    let urlPath = '';
    try {
        const u = new URL(location.href);
        urlHost = u.host;
        urlPath = u.pathname.length > 1 ? u.pathname : '';
    } catch (e) {
        urlPath = '';
    }

    const esc = (s) => String(s || '').replace(/&/g, '&').replace(/</g, '<').replace(/>/g, '>').replace(/"/g, '"');

    const faviconHtml = favicon
        ? '<img src="' + esc(favicon) + '" width="16" height="16" style="border-radius:3px;flex:none;object-fit:contain;"/>'
        : '<svg width="16" height="16" viewBox="0 0 32 32" fill="none" style="border-radius:3px;flex:none;"><rect width="32" height="32" rx="6" fill="#4D6BFE"/><path d="M7 17.5C7 13.5 10 10 15.5 10C21 10 24.5 13.5 24.5 17.5C24.5 20.5 22.5 23 19 23.5L20.5 26H17.5L16 23.5H15.5C11 23.5 7 21 7 17.5Z" fill="white"/></svg>';

    // Google 账号头像内容
    const avatarContent = avatarUrl
        ? '<img src="' + esc(avatarUrl) + '" width="24" height="24" style="border-radius:50%;object-fit:cover;display:block;"/>'
        : '<div style="width:24px;height:24px;border-radius:50%;background:linear-gradient(135deg,#7c3aed,#2563eb);display:flex;align-items:center;justify-content:center;color:#ffffff;font-size:12px;font-weight:600;">✦</div>';

    const banner = document.createElement('div');
    banner.id = 'mofox_chrome_banner';
    banner.style.cssText = 'width:' + width + 'px;height:84px;box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",Roboto,\"Helvetica Neue\",Arial,sans-serif;user-select:none;background:' + C.tabStripBg + ';display:flex;flex-direction:column;position:fixed;top:0;left:0;z-index:2147483647;overflow:hidden;';

    banner.innerHTML =
        // 第一行：Tab Strip (40px)
        '<div style=\"display:flex;align-items:flex-end;height:40px;padding:0 0 0 8px;box-sizing:border-box;position:relative;\">' +
            // 最左侧 Tab Search 按钮 (∨)
            '<div style=\"width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:' + C.tabSubText + ';margin:0 4px 6px 0;cursor:pointer;flex:none;\">' +
                '<svg width=\"14\" height=\"14\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2.5\"><path d=\"M6 9l6 6 6-6\"/></svg>' +
            '</div>' +
            // Active Tab (采用 Chromium 官方反向圆角裙边曲线)
            '<div style=\"display:flex;align-items:center;gap:8px;height:34px;padding:0 12px;min-width:180px;max-width:240px;background:' + C.toolbarBg + ';border-radius:8px 8px 0 0;position:relative;box-sizing:border-box;\">' +
                // 左下反向圆角裙边
                '<div style=\"position:absolute;left:-8px;bottom:0;width:8px;height:8px;background:radial-gradient(circle at 0 0,transparent 8px,' + C.toolbarBg + ' 8.5px);pointer-events:none;\"></div>' +
                faviconHtml +
                '<span style=\"font-size:12px;line-height:1;font-weight:400;color:' + C.tabText + ';white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;\">' + esc(title) + '</span>' +
                '<div style=\"width:16px;height:16px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:' + C.tabSubText + ';font-size:10px;line-height:1;flex:none;cursor:pointer;\">✕</div>' +
                // 右下反向圆角裙边
                '<div style=\"position:absolute;right:-8px;bottom:0;width:8px;height:8px;background:radial-gradient(circle at 100% 0,transparent 8px,' + C.toolbarBg + ' 8.5px);pointer-events:none;\"></div>' +
            '</div>' +
            // 新建标签按钮 (+)
            '<div style=\"width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:' + C.tabSubText + ';margin:0 0 6px 4px;cursor:pointer;flex:none;\">' +
                '<svg width=\"14\" height=\"14\" viewBox=\"0 0 24 24\" fill=\"currentColor\"><path d=\"M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z\"/></svg>' +
            '</div>' +
            // 右侧区域：Windows 原生窗口控制
            '<div style=\"margin-left:auto;display:flex;height:100%;align-items:center;\">' +
                // 最小化 / 最大化 / 关闭
                '<div style=\"width:46px;height:34px;display:flex;align-items:center;justify-content:center;color:' + C.captionColor + ';cursor:pointer;\">' +
                    '<svg width=\"10\" height=\"1\" viewBox=\"0 0 10 1\" fill=\"currentColor\"><rect width=\"10\" height=\"1\"/></svg>' +
                '</div>' +
                '<div style=\"width:46px;height:34px;display:flex;align-items:center;justify-content:center;color:' + C.captionColor + ';cursor:pointer;\">' +
                    '<svg width=\"10\" height=\"10\" viewBox=\"0 0 10 10\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1\"><rect x=\"0.5\" y=\"0.5\" width=\"9\" height=\"9\"/></svg>' +
                '</div>' +
                '<div style=\"width:46px;height:34px;display:flex;align-items:center;justify-content:center;color:' + C.captionColor + ';cursor:pointer;\">' +
                    '<svg width=\"10\" height=\"10\" viewBox=\"0 0 10 10\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.2\"><path d=\"M0 0L10 10M10 0L0 10\"/></svg>' +
                '</div>' +
            '</div>' +
        '</div>' +
        // 第二行：Toolbar & Omnibox (44px)
        '<div style=\"display:flex;align-items:center;height:44px;background:' + C.toolbarBg + ';padding:0 12px;gap:8px;box-sizing:border-box;border-bottom:1px solid ' + C.bottomBorder + ';\">' +
            // 导航按钮组 (Back, Forward, Reload - Material Symbols 官方路径)
            '<div style=\"display:flex;align-items:center;gap:4px;flex:none;\">' +
                '<div style=\"width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:' + C.navIcon + ';opacity:0.5;\">' +
                    '<svg width=\"16\" height=\"16\" viewBox=\"0 0 24 24\" fill=\"currentColor\"><path d=\"M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z\"/></svg>' +
                '</div>' +
                '<div style=\"width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:' + C.navIcon + ';opacity:0.35;\">' +
                    '<svg width=\"16\" height=\"16\" viewBox=\"0 0 24 24\" fill=\"currentColor\"><path d=\"M12 4l-1.41 1.41L16.17 11H4v2h12.17l-5.58 5.59L12 20l8-8z\"/></svg>' +
                '</div>' +
                '<div style=\"width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:' + C.navIcon + ';\">' +
                    '<svg width=\"16\" height=\"16\" viewBox=\"0 0 24 24\" fill=\"currentColor\"><path d=\"M17.65 6.35C16.2 4.9 14.21 4 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08c-.82 2.33-3.04 4-5.65 4-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z\"/></svg>' +
                '</div>' +
            '</div>' +
            // Omnibox 地址栏 (高度 34px, 圆角 17px 胶囊规范)
            '<div style=\"flex:1;height:34px;background:' + C.omniboxBg + ';border-radius:17px;border:1px solid ' + C.omniboxBorder + ';display:flex;align-items:center;padding:0 14px;gap:8px;min-width:0;box-sizing:border-box;\">' +
                // Chrome 2023 官方 Tune 面板滑块图标
                '<svg width=\"14\" height=\"14\" viewBox=\"0 0 24 24\" fill=\"' + C.tuneIcon + '\" style=\"flex:none;\"><path d=\"M3 17v2h6v-2H3zM3 5v2h10V5H3zm10 16v-2h8v-2h-8v-2h-2v6h2zM7 9v2H3v2h4v2h2V9H7zm14 4v-2H11v2h10zm-6-4h2V7h4V5h-4V3h-2v6z\"/></svg>' +
                // URL 文本
                '<div style=\"font-size:12.5px;line-height:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;display:flex;align-items:center;\">' +
                    '<span style=\"color:' + C.urlHost + ';font-weight:500;\">' + esc(urlHost) + '</span>' +
                    '<span style=\"color:' + C.urlPath + ';font-weight:400;\">' + esc(urlPath) + '</span>' +
                '</div>' +
                // 收藏星号 (Bookmark Star)
                '<svg width=\"14\" height=\"14\" viewBox=\"0 0 24 24\" fill=\"' + C.navIcon + '\" style=\"flex:none;opacity:0.75;\"><path d=\"M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z\"/></svg>' +
            '</div>' +
            // 右侧工具栏 (扩展拼图、侧边栏、Google 账号头像、三点菜单)
            '<div style=\"display:flex;align-items:center;gap:6px;flex:none;\">' +
                // 扩展程序拼图 (Puzzle)
                '<div style=\"width:28px;height:28px;display:flex;align-items:center;justify-content:center;color:' + C.navIcon + ';\">' +
                    '<svg width=\"15\" height=\"15\" viewBox=\"0 0 24 24\" fill=\"currentColor\"><path d=\"M20.5 11H19V7c0-1.1-.9-2-2-2h-4V3.5C13 2.12 11.88 1 10.5 1S8 2.12 8 3.5V5H4c-1.1 0-1.99.9-1.99 2v3.8H3.5c1.49 0 2.7 1.21 2.7 2.7s-1.21 2.7-2.7 2.7H2V20c0 1.1.9 2 2 2h3.8v-1.5c0-1.49 1.21-2.7 2.7-2.7 1.49 0 2.7 1.21 2.7 2.7V22H17c1.1 0 2-.9 2-2v-4h1.5c1.38 0 2.5-1.12 2.5-2.5s-1.12-2.5-2.5-2.5z\"/></svg>' +
                '</div>' +
                // 侧边栏 (Side Panel)
                '<div style=\"width:28px;height:28px;display:flex;align-items:center;justify-content:center;color:' + C.navIcon + ';\">' +
                    '<svg width=\"15\" height=\"15\" viewBox=\"0 0 24 24\" fill=\"currentColor\"><path d=\"M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 16H5V5h14v14zm-5-2h3V7h-3v10z\"/></svg>' +
                '</div>' +
                // Google 账号头像
                '<div style=\"width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;overflow:hidden;flex:none;margin:0 2px;\">' +
                    avatarContent +
                '</div>' +
                // 三点菜单 (More)
                '<div style=\"width:28px;height:28px;display:flex;align-items:center;justify-content:center;color:' + C.navIcon + ';\">' +
                    '<svg width=\"16\" height=\"16\" viewBox=\"0 0 24 24\" fill=\"currentColor\"><path d=\"M12 8c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm0 2c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm0 6c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z\"/></svg>' +
                '</div>' +
            '</div>' +
        '</div>';

    document.body.appendChild(banner);
    return true;
}"""

# 浏览器外壳装饰移除脚本：移除用于独立渲染的临时横幅节点。
BROWSER_CHROME_REMOVE_SCRIPT = """() => {
    const el = document.getElementById('mofox_chrome_banner');
    if (el) { el.remove(); return true; }
    return false;
}"""
