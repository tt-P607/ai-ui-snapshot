"""Gemini (Google AI) 站点专属常量与选择器。

集中管理 Google Gemini 网页的 DOM 选择器、状态检测脚本与页面提纯逻辑，
供服务层（GeminiActions）与业务层复用。
"""

from __future__ import annotations

# Gemini 官方默认入口
GEMINI_APP_URL = "https://gemini.google.com/app"

# 支持的模型/模式文案
SUPPORTED_MODELS: tuple[str, ...] = (
    "Gemini 2.0 Flash",
    "Gemini 2.0 Pro",
    "Gemini Advanced",
    "Gemini 1.5 Pro",
    "Gemini 1.5 Flash",
)

# 对话内容容器选择器
CONVERSATION_SELECTOR = "main, .conversation-container, ms-conversation, [role='main']"

# 输入框选择器列表（按优先级尝试）
INPUT_SELECTORS: tuple[str, ...] = (
    "rich-textarea p",
    "rich-textarea div[contenteditable='true']",
    "div.ql-editor",
    "textarea[aria-label*='提示']",
    "textarea[aria-label*='Prompt']",
    "[contenteditable='true']",
)

# 发送按钮选择器列表
SEND_BUTTON_SELECTORS: tuple[str, ...] = (
    "button.send-button",
    "button[aria-label*='发送']",
    "button[aria-label*='Send']",
    "button[mattooltip*='发送']",
    "button[mattooltip*='Send']",
    "button.send-button-container",
)

# 轮询间隔（秒）
POLL_INTERVAL_S = 2.0

# 生成中检测脚本：检测停止生成按钮或流式渲染动画
GENERATING_SCRIPT = """() => {
    const btns = Array.from(document.querySelectorAll('button, [role="button"], div'));
    for (const el of btns) {
        const aria = (el.getAttribute('aria-label') || '').toLowerCase();
        const text = (el.innerText || '').trim().toLowerCase();
        if (aria.includes('停止') || aria.includes('stop') || text === '停止' || text === 'stop') {
            return true;
        }
    }
    const indicator = document.querySelector('.loading-dots, .sparkle-loading, [data-is-generating="true"], .streaming-dots');
    return Boolean(indicator);
}"""

# 展开思考过程与折叠项脚本
EXPAND_THOUGHTS_SCRIPT = """() => {
    const toggles = document.querySelectorAll('button[aria-expanded="false"], mat-expansion-panel-header[aria-expanded="false"], [data-test-id*="thought"]');
    for (const el of toggles) {
        try {
            el.click();
        } catch (e) {}
    }
}"""

# 页面提纯与视觉去噪脚本（隐藏多余遮罩、升级横幅等）
PURGE_NOISE_SCRIPT = """() => {
    const selectors = [
        '.cdk-overlay-backdrop',
        '.gemini-upsell-banner',
        'button[aria-label*="Feedback"]',
        'button[aria-label*="反馈"]',
        '#onetrust-consent-sdk',
        '.cookie-banner'
    ];
    selectors.forEach(sel => {
        document.querySelectorAll(sel).forEach(el => {
            el.style.setProperty('display', 'none', 'important');
        });
    });
}"""

# 完整整页长截图拉伸脚本（使 Gemini 对话区可无损滚动并完整渲染）
EXPAND_SCRIPT = """(selector) => {
    const el = document.querySelector(selector) || document.querySelector('main') || document.body;
    if (!el) return null;
    const saved = [];
    const recorded = new Set();
    const pathOf = (node) => {
        if (!node || node.nodeType !== 1) return null;
        if (node === document.documentElement) return 'html';
        const parts = [];
        let cur = node;
        while (cur && cur !== document.documentElement) {
            let idx = 1;
            let sib = cur.previousElementSibling;
            while (sib) { idx += 1; sib = sib.previousElementSibling; }
            parts.unshift(cur.tagName.toLowerCase() + ':nth-child(' + idx + ')');
            cur = cur.parentElement;
        }
        return 'html>' + parts.join('>');
    };
    const record = (node, props) => {
        if (recorded.has(node)) return;
        recorded.add(node);
        saved.push({
            path: pathOf(node),
            origH: Math.round(node.getBoundingClientRect().height),
            ...props
        });
    };

    let cur = el;
    while (cur) {
        record(cur, {
            h: cur.style.height || '',
            minH: cur.style.minHeight || '',
            maxH: cur.style.maxHeight || '',
            overflow: cur.style.overflow || '',
            overflowY: cur.style.overflowY || ''
        });
        cur.style.setProperty('height', 'auto', 'important');
        cur.style.setProperty('min-height', 'auto', 'important');
        cur.style.setProperty('max-height', 'none', 'important');
        cur.style.setProperty('overflow', 'visible', 'important');
        cur.style.setProperty('overflow-y', 'visible', 'important');
        cur = cur.parentElement;
    }

    record(document.documentElement, {
        h: document.documentElement.style.height || '',
        overflow: document.documentElement.style.overflow || ''
    });
    document.documentElement.style.setProperty('height', 'auto', 'important');
    document.documentElement.style.setProperty('overflow', 'visible', 'important');

    record(document.body, {
        h: document.body.style.height || '',
        overflow: document.body.style.overflow || ''
    });
    document.body.style.setProperty('height', 'auto', 'important');
    document.body.style.setProperty('overflow', 'visible', 'important');

    return { saved };
}"""

# 还原整页拉伸脚本
RESTORE_SCRIPT = """(state) => {
    if (!state || !state.saved) return;
    for (const item of state.saved) {
        const node = document.querySelector(item.path);
        if (!node) continue;
        if ('h' in item) node.style.height = item.h;
        if ('minH' in item) node.style.minHeight = item.minH;
        if ('maxH' in item) node.style.maxHeight = item.maxH;
        if ('overflow' in item) node.style.overflow = item.overflow;
        if ('overflowY' in item) node.style.overflowY = item.overflowY;
    }
}"""
