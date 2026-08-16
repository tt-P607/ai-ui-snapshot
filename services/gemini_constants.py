"""Gemini (Google AI) 站点专属常量、选择器与页面脚本。

依据对已登录 gemini.google.com 网页的真实 DOM 探测（免费账号旧版 UI）整理：
- 输入框：``div.ql-editor[contenteditable="true"]``（aria-label="为 Gemini 输入提示"）
- 发送按钮：``button[aria-label="发送"]``（输入后出现）
- 停止按钮：``button[aria-label="停止回答"]``（生成中出现）
- 模式选择器：``button[aria-label="打开模式选择器，当前模式为..."]``
- 模型菜单项：``[role="menuitem"]``（当前选中 class 含 ``selected``）
- 用户消息：``.user-query-container`` → ``.query-text.gds-body-l``
- 模型消息：``.response-container`` → ``.response-content`` → ``.model-response-text`` → ``.markdown.markdown-main-panel``

站点 DOM 变动时仅需在此同步；供服务层（GeminiActions）与业务层复用。
"""

from __future__ import annotations

# Gemini 官方默认入口
GEMINI_APP_URL = "https://gemini.google.com/app"

# 免费账号可见的模型列表（旧版 UI；订阅账号会更多，如 3.7 Flash）
SUPPORTED_MODELS: tuple[str, ...] = (
    "3.5 Flash-Lite",
    "3.6 Flash",
    "3.1 Pro",
    "扩展思考",
)

# 输入框选择器（contenteditable 富文本框）
INPUT_SELECTOR = (
    "div.ql-editor[contenteditable='true'][role='textbox']"
    ", div[role='textbox'][aria-label*='提示']"
    ", div[contenteditable='true'][aria-label*='Gemini']"
)

# 发送按钮选择器（输入后出现；aria-label="发送"）
SEND_BUTTON_SELECTOR = "button[aria-label='发送'], button[aria-label*='发送']"

# 停止生成按钮选择器（生成中出现；aria-label="停止回答"）
STOP_BUTTON_SELECTOR = (
    "button[aria-label='停止回答'], button[aria-label*='停止'], button[aria-label*='Stop']"
)

# 模式选择器触发按钮（aria-label 含"打开模式选择器，当前模式为..."）
MODE_SELECTOR_BUTTON = "button[aria-label*='打开模式选择器'], .input-area-switch"

# 模型菜单项（打开模式选择器后出现）
MODEL_MENU_ITEM_SELECTOR = "[role='menuitem']"

# 用户消息容器与正文
USER_QUERY_SELECTOR = ".user-query-container"
USER_QUERY_TEXT_SELECTOR = ".query-text.gds-body-l, .query-text"

# 模型回复容器与正文
RESPONSE_SELECTOR = ".response-container"
RESPONSE_CONTENT_SELECTOR = ".response-content"
MODEL_RESPONSE_TEXT_SELECTOR = ".model-response-text"
MODEL_MARKDOWN_SELECTOR = ".markdown.markdown-main-panel"

# 对话内容容器选择器（撑开长截图时定位）
CONVERSATION_SELECTOR = "main.chat-app, main[class*='chat-app'], main"

# 右侧主对话区滚动容器（Gemini 为固定视口布局，消息区在此容器内滚动；
# 整页 docH 被 html overflow:hidden 锁死，故截图改为对该容器分片截取）
CONVERSATION_SCROLL_SELECTOR = ".content-wrapper"

# 对话内容真实容器（消息列表在此容器内完整渲染，内容高度 = 完整对话高度；
# 外层 infinite-scroller 是虚拟滚动，scrollHeight 不承载真实内容，故长截图
# 改为将该容器临时设为可滚动后逐段元素截图拼接）
CONVERSATION_CONTENT_SELECTOR = ".conversation-container"

# 文件上传：Gemini 输入区旁"上传和工具"按钮（打开文件选择器后存在隐藏 input[type=file]）
UPLOAD_BUTTON_SELECTOR = "button[aria-label='上传和工具'], button[aria-label*='上传']"
FILE_INPUT_SELECTOR = "input[type='file']"

# 图片生成：加号菜单中的"制作图片"按钮（强制图片生成模式）
MAKE_IMAGE_BUTTON_TEXT = "制作图片"
# 图片生成完成信号：检测对话区（main 内）的生成图或"下载完整尺寸"按钮。
# 不检测输入区附件图（gem-attachment/preview class），避免参考图被误判为生成结果。
IMAGE_GENERATED_SCRIPT = """() => {
    const main = document.querySelector('main') || document.body;
    const dlBtn = Array.from(document.querySelectorAll('button')).find(b =>
        (b.getAttribute('aria-label') || '').includes('下载完整尺寸')
        || (b.getAttribute('aria-label') || '').includes('下载图片'));
    // 对话区内的生成图：animate class / AI 生成 alt / 非输入区的 blob 图
    const genImg = Array.from(main.querySelectorAll('img[class*="animate"], img[alt*="AI 生成"], img[src^="blob:"]'))
        .find(im => !/(gem-attachment|preview-image)/.test(im.className || '') && im.complete);
    return Boolean(genImg || dlBtn);
}"""
# 下载完整尺寸图片按钮
DOWNLOAD_IMAGE_BUTTON = "button[aria-label*='下载完整尺寸'], button[aria-label*='Download full']"
# 点击加号菜单中"制作图片"按钮的脚本
CLICK_MAKE_IMAGE_SCRIPT = """() => {
    const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
    const b = btns.find(el => (el.innerText || '').includes('制作图片'));
    if (b) { b.click(); return true; }
    return false;
}"""
# 打开当前对话操作菜单（分享链接入口）：优先选最靠上的"更多/菜单"按钮
# （侧边栏历史会话项的更多按钮 top 较大，当前对话顶部按钮 top 最小）
CLICK_SHARE_MENU_SCRIPT = """() => {
    const btns = Array.from(document.querySelectorAll('button[aria-label*="更多"], button[aria-label*="菜单"], button[aria-label*="对话操作"], button[aria-label*="分享"]'));
    if (btns.length === 0) return false;
    const open = btns.reduce((a, b) => {
        const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
        return (rb.top < ra.top || (rb.top === ra.top && rb.left < ra.left)) ? b : a;
    });
    open.click();
    return true;
}"""
CLICK_SHARE_ITEM_SCRIPT = """() => {
    const items = Array.from(document.querySelectorAll('[role="menuitem"], [class*="menu-item"]'));
    const it = items.find(el => (el.innerText || '').includes('分享对话内容'));
    if (it) { it.click(); return true; }
    return false;
}"""
# 分享链接生成完成检测：弹窗内出现公开链接（share.gemini.google 链接元素）
SHARE_LINK_READY_SCRIPT = """() => {
    const a = document.querySelector('[role="dialog"] a.link-url, a[href*="share.gemini.google"], a[href*="g.co/gemini"]');
    return a ? (a.getAttribute('href') || a.href || '') : '';
}"""

# 侧边栏历史会话项（mat-mdc-list-item，标题为 innerText 首行；选中项含 is-active）
HISTORY_ITEM_SELECTOR = (
    "mat-nav-list mat-mdc-list-item a, [class*='gem-nav-list-item'] a"
    ", [class*='sidenav-with-history-container'] a"
)
# 当前活跃会话标题提取（选中项 aria-current="page" 或 class 含 is-active）
ACTIVE_CONVERSATION_TITLE_SCRIPT = """() => {
    const el = document.querySelector(
        '[class*="mat-mdc-list-item"].is-active, [class*="mat-mdc-list-item"][aria-current="page"], [aria-current="page"]'
    );
    if (el) {
        const t = (el.innerText || '').split(/\\r?\\n/)[0].trim();
        if (t) return t;
    }
    return '';
}"""
# 当前活跃会话稳定 ID 提取（URL 中 /app/<id> 的会话 UUID）
ACTIVE_CONVERSATION_ID_SCRIPT = """() => {
    const m = (location.href || '').match(/\\/app\\/([0-9a-f]+)/i);
    return m ? m[1].toLowerCase() : '';
}"""
# 历史会话列表提取（去重，返回标题数组；跳过侧边栏固定操作项）
HISTORY_LIST_SCRIPT = """() => {
    const seen = new Set();
    const out = [];
    const skip = new Set(['发起新对话', '搜索对话内容', '库', '笔记本', '新建笔记本', '最近', 'Gemini']);
    const links = document.querySelectorAll("mat-nav-list mat-mdc-list-item a, [class*='gem-nav-list-item'] a, [class*='sidenav-with-history-container'] a");
    for (const a of links) {
        const r = a.getBoundingClientRect();
        const txt = (a.innerText || '').trim().replace(/\\n+/g, ' ').replace(/\\s+/g, ' ').trim();
        if (!txt || r.width < 40 || r.height < 8) continue;
        if (skip.has(txt)) continue;
        if (seen.has(txt)) continue;
        seen.add(txt);
        out.push(txt);
    }
    return out.slice(0, 50);
}"""
# 打开指定标题的历史会话（在侧边栏按文本匹配并点击，返回是否命中）
HISTORY_OPEN_SCRIPT = """(title) => {
    const norm = (s) => (s || '').trim().replace(/\\s+/g, ' ').replace(/\\n+/g, ' ');
    const want = norm(title);
    if (!want) return false;
    const links = Array.from(document.querySelectorAll("mat-nav-list mat-mdc-list-item a, [class*='gem-nav-list-item'] a, [class*='sidenav-with-history-container'] a"));
    let pick = null;
    pick = links.find(a => norm(a.innerText) === want) || null;
    if (!pick) pick = links.find(a => norm(a.innerText).startsWith(want)) || null;
    if (!pick) pick = links.find(a => norm(a.innerText).includes(want)) || null;
    if (!pick) return false;
    pick.click();
    return true;
}"""
# 新对话入口（点击"发起新对话"）
NEW_CHAT_SCRIPT = """() => {
    const links = Array.from(document.querySelectorAll('a[aria-label="发起新对话"], a[aria-label*="新对话"]'));
    if (links.length > 0) { links[0].click(); return true; }
    return false;
}"""
# 会话指纹脚本（消息数 + 当前 URL，用于校验 open_conversation 是否真正切换）
FINGERPRINT_SCRIPT = """() => {
    const msgs = document.querySelectorAll('.model-response-text, .query-text');
    return msgs.length + ':' + (location.href || '');
}"""

# 等待 AI 回复完成的轮询间隔（秒）
POLL_INTERVAL_S = 2.0

# 生成中指示器探测脚本：生成回复时出现"停止回答"按钮，存在即视为仍在生成。
GENERATING_SCRIPT = """() => {
    const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
    for (const el of btns) {
        const aria = (el.getAttribute('aria-label') || '').trim();
        const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
        if (aria.includes('停止回答') || aria === 'Stop generating'
                || aria.includes('停止') || t === '停止' || t === 'Stop') {
            return true;
        }
    }
    return false;
}"""

# 页面可见性检测脚本（返回消息容器是否已撑开可见）
VISIBILITY_SCRIPT = """(selector) => {
    const el = document.querySelector(selector);
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none'
        && style.visibility !== 'hidden';
}"""

# 消息文本提取脚本：
# - last：取最后一个 .model-response-text 的 .markdown.markdown-main-panel 正文
#   （纯模型回复，不含用户消息与思考块）
# - full：汇总全部模型回复正文（每个响应取 markdown 正文，去重相邻）。
CONVERSATION_TEXT_SCRIPT = """(scope) => {
    const responses = Array.from(document.querySelectorAll('.model-response-text'));
    const pick = (el) => {
        const md = el.querySelector('.markdown.markdown-main-panel, .markdown');
        return md ? (md.innerText || '').trim() : (el.innerText || '').trim();
    };
    if (scope === 'last') {
        for (let i = responses.length - 1; i >= 0; i--) {
            const t = pick(responses[i]);
            if (t) return t;
        }
        return '';
    }
    const parts = [];
    for (const r of responses) {
        const t = pick(r);
        if (t) parts.push(t);
    }
    return parts.join('\\n\\n');
}"""

# 当前模式（模型）提取脚本：从模式选择器按钮 aria-label 中取"当前模式为"引号内文案。
GET_MODEL_SCRIPT = """() => {
    const btn = document.querySelector("button[aria-label*='打开模式选择器'], .input-area-switch");
    if (!btn) return null;
    const aria = btn.getAttribute('aria-label') || '';
    const m = aria.match(/当前模式为[“\"']?([^”\"']+)/);
    return m ? m[1].trim() : (btn.innerText || '').trim();
}"""

# 打开模式选择器菜单脚本：点击触发按钮。
OPEN_MODEL_MENU_SCRIPT = """() => {
    const btn = document.querySelector("button[aria-label*='打开模式选择器'], .input-area-switch");
    if (!btn) return false;
    btn.click();
    return true;
}"""

# 选择指定模型：在菜单项中按文本匹配并点击（返回是否命中可点项）。
SET_MODEL_SCRIPT = """(model) => {
    const want = (model || '').trim();
    if (!want) return false;
    const items = Array.from(document.querySelectorAll("[role='menuitem'], [role='menuitemradio']"));
    const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
    let pick = null;
    pick = items.find(el => norm(el.innerText).includes(want)) || null;
    if (!pick) pick = items.find(el => (el.getAttribute('aria-label') || '').includes(want)) || null;
    if (!pick) return false;
    pick.click();
    return true;
}"""
# 主题切换脚本：移除旧主题 class、加目标主题 class（light-theme/dark-theme）。
# 依据探测：Gemini 页面主题由 body 的 light-theme / dark-theme class 控制，
# 改 class 会真实切换渲染（浅色背景 rgb(253,252,252) / 深色 rgb(15,15,15)）。
SET_THEME_SCRIPT = """(theme) => {
    const body = document.body;
    if (!body) return false;
    body.classList.remove('light-theme', 'dark-theme');
    if (theme === 'dark') {
        body.classList.add('dark-theme');
    } else if (theme === 'light') {
        body.classList.add('light-theme');
    }
    return true;
}"""
# 读取当前页面主题（body class 含 dark-theme 视为深色，否则浅色）
GET_THEME_SCRIPT = """() => {
    const body = document.body;
    if (!body) return 'light';
    return body.classList.contains('dark-theme') ? 'dark' : 'light';
}"""

# 查询指定菜单项是否被选中（class 含 selected / active，返回是否命中+选中态）
MODEL_ITEM_SELECTED_SCRIPT = """(model) => {
    const want = (model || '').trim();
    if (!want) return {hit: false, selected: false};
    const items = Array.from(document.querySelectorAll("[role='menuitem'], [role='menuitemradio']"));
    const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
    const pick = items.find(el => norm(el.innerText).includes(want));
    if (!pick) return {hit: false, selected: false};
    const cls = (pick.className && typeof pick.className === 'string') ? pick.className : '';
    return {hit: true, selected: cls.includes('selected') || cls.includes('active')};
}"""
# 扩展思考开关：菜单项"扩展思考"当前是否选中
THINKING_SELECTED_SCRIPT = """() => {
    const items = Array.from(document.querySelectorAll("[role='menuitem'], [role='menuitemradio']"));
    const pick = items.find(el => (el.innerText || '').includes('扩展思考'));
    if (!pick) return false;
    const cls = (pick.className && typeof pick.className === 'string') ? pick.className : '';
    return cls.includes('selected') || cls.includes('active');
}"""
# 扩展思考开关名（叠加在模型上，独立于模型切换）
THINKING_ITEM = "扩展思考"

# Gemini 完整整页长截图撑开脚本：把中间滚动链高度赋为完整内容高度，并放开
# html/body 的 overflow（html overflow:hidden 会锁死 docH），使整页高度跟随
# 对话内容增长，从而 full_page 截出含侧边栏的单张真长截图（无重复拼接）。
# 返回 { saved, contentH }，saved 供 RESTORE_SCRIPT 还原。
GEMINI_FULLPAGE_EXPAND_SCRIPT = """() => {
    const cc = document.querySelector('.conversation-container');
    if (!cc) return null;
    const contentH = cc.scrollHeight || cc.getBoundingClientRect().height || 0;
    if (contentH <= 0) return null;
    const saved = [];
    const sels = [
        '.content-wrapper', '.chat-history-scroll-container', 'infinite-scroller',
        '.xap-uploader-dropzone.chat-container', 'chat-window', 'main'
    ];
    for (const sel of sels) {
        const el = document.querySelector(sel);
        if (!el) continue;
        saved.push({ sel, h: el.style.height, minH: el.style.minHeight, oy: el.style.overflowY, o: el.style.overflow });
        el.style.setProperty('height', contentH + 'px', 'important');
        el.style.setProperty('min-height', contentH + 'px', 'important');
        el.style.setProperty('overflow-y', 'visible', 'important');
        el.style.setProperty('overflow', 'visible', 'important');
    }
    saved.push({ sel: '.conversation-container', h: cc.style.height, oy: cc.style.overflowY, o: cc.style.overflow });
    cc.style.setProperty('height', 'auto', 'important');
    cc.style.setProperty('overflow-y', 'visible', 'important');
    cc.style.setProperty('overflow', 'visible', 'important');
    for (const sel of ['html', 'body']) {
        const el = document.querySelector(sel);
        if (!el) continue;
        saved.push({ sel, h: el.style.height, oy: el.style.overflowY, o: el.style.overflow });
        el.style.setProperty('height', 'auto', 'important');
        el.style.setProperty('overflow-y', 'visible', 'important');
        el.style.setProperty('overflow', 'visible', 'important');
    }
    return { saved, contentH };
}"""

# 完整整页长截图拉伸脚本：撑开消息容器及其全祖先链，使 Gemini 对话区可无损
# 滚动并完整渲染（参考 DeepSeek EXPAND_SCRIPT 思路，适配 Gemini 结构）。
# 只记录 CSS 路径与样式值（DOM 节点无法经 evaluate 返回）。
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
RESTORE_SCRIPT = """(payload) => {
    const saved = payload ? payload.saved : null;
    if (!Array.isArray(saved)) return;
    const queryPath = (path) => {
        if (!path) return null;
        try { return document.querySelector(path); } catch (e) { return null; }
    };
    for (const item of saved) {
        if (!item) continue;
        const node = queryPath(item.path);
        if (!node) continue;
        if (item.h !== undefined && item.h !== '') {
            node.style.height = item.h;
        } else if (item.h === '' && item.origH) {
            node.style.height = item.origH + 'px';
        }
        if (item.minH !== undefined) node.style.minHeight = item.minH;
        if (item.maxH !== undefined) node.style.maxHeight = item.maxH;
        if (item.overflow !== undefined) node.style.overflow = item.overflow;
        if (item.overflowY !== undefined) node.style.overflowY = item.overflowY;
    }
    void document.body.offsetHeight;
}"""
