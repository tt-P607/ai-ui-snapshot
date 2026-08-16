"""DeepSeek 站点专属常量与归一化工具。

集中管理 DeepSeek 网页的哈希选择器、对话模式、开关名、脚本与归一化映射，
供服务层（BrowserActions）与业务层（snapshot_service / tools / commands）
复用，避免魔法字符串散落各处。选择器依赖站点 DOM，变动时仅需在此同步。
"""

from __future__ import annotations

# DeepSeek 支持的对话模式（与网页模式选择器文案一致）
SUPPORTED_MODES: tuple[str, ...] = ("快速模式", "专家模式", "识图模式")

# 开关名称（深度思考 / 智能搜索），页面与业务层统一引用
THINK_TOGGLE_NAME = "深度思考"
SEARCH_TOGGLE_NAME = "智能搜索"
TOGGLE_NAMES: tuple[str, ...] = (THINK_TOGGLE_NAME, SEARCH_TOGGLE_NAME)

# DeepSeek 对话消息虚拟滚动容器选择器（站点专有，变动时需同步更新）
CONVERSATION_SELECTOR = ".ds-virtual-list"

# 主题偏好 localStorage 键（DeepSeek 用 appKit 存储主题偏好，值为
# {"value":"system"|"light"|"dark"}；改后需 reload 生效）
THEME_PREF_KEY = "__appKit_@deepseek/chat_themePreference"

# 读取主题偏好脚本（返回 system/light/dark，缺省视为 system）
GET_THEME_SCRIPT = """() => {
    try {
        const raw = localStorage.getItem('__appKit_@deepseek/chat_themePreference');
        if (!raw) return 'system';
        const obj = JSON.parse(raw);
        return obj && obj.value ? obj.value : 'system';
    } catch (e) { return 'system'; }
}"""
# 设置主题偏好脚本（写入 localStorage，返回是否成功）
SET_THEME_SCRIPT = """(theme) => {
    try {
        localStorage.setItem('__appKit_@deepseek/chat_themePreference',
            JSON.stringify({value: theme, __version: '0'}));
        return true;
    } catch (e) { return false; }
}"""

# 模式选择器选项容器（带 aria-checked，真正可点的元素）
MODE_OPTION_SELECTOR = "div[class*='_9f2341b']"

# 模式选择器触发按钮（隐藏辅助文本 span）
MODE_TRIGGER_SELECTOR = "span[class*='321831d']"

# 开关容器（深度思考/智能搜索，激活时追加 ds-toggle-button--selected）
TOGGLE_SELECTOR = "div[class*='f79352dc']"

# 侧边栏外框 / 内层 flex 容器 / 历史会话项 class 片段
SIDEBAR_OUTER_CLASS = "dc04ec1d"
SIDEBAR_INNER_CLASS = "b8812f16"
HISTORY_ITEM_CLASS = "_546d736"

# 等待 AI 回复完成的轮询间隔（秒）
POLL_INTERVAL_S = 2.0

# 生成中指示器探测脚本：DeepSeek 生成回复时会显示"停止生成"按钮（含停止图标），
# 该按钮存在即视为仍在生成。用于 wait_reply_done 的强信号判定。
GENERATING_SCRIPT = """() => {
    const btns = Array.from(document.querySelectorAll('button, [role="button"], div'));
    for (const el of btns) {
        const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
        if (t && (t.includes('停止生成') || t === 'Stop')) return true;
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

# 完整 UI 整体同步拉伸脚本（用于长截图）：
# 1. 撑开消息区及其全祖先链（含 html/body/root）。
# 2. 让左侧侧边栏跟随整页拉长到底，避免底部留白。
# 3. 将底部输入框容器脱离吸附/固定定位，改为 relative 流布局，跟随在对话消息最末尾。
#
# 注意：Playwright page.evaluate 的返回值必须 JSON 可序列化，DOM 节点会被序列化为
# 空对象，因此本脚本只记录节点的 CSS 路径（path）与样式值，不直接传递节点引用。
# 移动过的输入框节点加临时 data 标记（restoreTag），还原时按标记找回。
EXPAND_SCRIPT = """(selector) => {
    const el = document.querySelector(selector);
    if (!el) return null;
    const saved = [];
    const recorded = new Set();
    const pathOf = (node) => {
        if (!node || node.nodeType !== 1) return null;
        if (node === document.documentElement) return 'html';
        const parts = [];
        let cur = node;
        while (cur && cur !== document.documentElement) {
            // 按所有元素子节点计数（与 previousElementSibling 遍历一致），
            // 对应 CSS :nth-child 语义；若用 :nth-of-type 会因 tag 分组而匹配失败。
            let idx = 1;
            let sib = cur.previousElementSibling;
            while (sib) { idx += 1; sib = sib.previousElementSibling; }
            parts.unshift(cur.tagName.toLowerCase() + ':nth-child(' + idx + ')');
            cur = cur.parentElement;
        }
        return 'html>' + parts.join('>');
    };
    // 记录节点当前渲染高度（px），还原时用于恢复被 height:auto 撑开的容器。
    // 去重：同一节点仅记录第一次（撑开前）的原始值，避免后续重复 record 读到
    // 已被改为 auto 的值导致还原错误。
    const record = (node, props) => {
        if (recorded.has(node)) return;
        recorded.add(node);
        saved.push({
            path: pathOf(node),
            origH: Math.round(node.getBoundingClientRect().height),
            ...props
        });
    };

    // 0. 先记录侧边栏外框与历史滚动区原始状态（必须在撑开正文之前，否则
    //    origH 会记录成被撑开后的高度，导致还原时无法缩回）。
    const sidebars0 = document.querySelectorAll('[class*="dc04ec1d"], [class*="b8812f16"]');
    for (const sb of sidebars0) {
        record(sb, { h: sb.style.height, minH: sb.style.minHeight });
    }
    const scrollers0 = document.querySelectorAll('[class*="ds-scroll-area"]');
    for (const sc of scrollers0) {
        if (sc.getBoundingClientRect().width >= 500) continue;
        // resetH：还原时清空 inline height（其高度由 flex 布局决定），
        // 避免还原成固定像素后配合 flex:1 1 0% 又被撑满
        record(sc, { h: sc.style.height, flex: sc.style.flex, minH: sc.style.minHeight, maxH: sc.style.maxHeight, resetH: true });
    }

    // 1. 撑开消息容器及其全祖先链（含 c3ecdb44/cb86951c 等公共祖先）
    //    公共祖先 height:auto 后正文才能反映到文档高度；侧边栏对话历史滚动容器
    //    在步骤 3 中被显式固定回原高度，因此不会随 flex 布局展开。
    let cur = el;
    while (cur && cur !== document.body && cur !== document.documentElement) {
        record(cur, {
            h: cur.style.height, o: cur.style.overflow,
            maxH: cur.style.maxHeight, minH: cur.style.minHeight,
            pos: cur.style.position
        });
        cur.style.height = 'auto';
        cur.style.overflow = 'visible';
        cur.style.maxHeight = 'none';
        cur = cur.parentElement;
    }

    // 2. 放开根容器与布局节点限制
    const bodyNodes = [document.documentElement, document.body, document.querySelector('#root')];
    for (const node of bodyNodes) {
        if (!node) continue;
        record(node, { h: node.style.height, o: node.style.overflow });
        node.style.height = 'auto';
        node.style.overflow = 'visible';
    }

    // 3. 让左侧侧边栏外框跟随正文高度贯穿到底，对话历史自适应撑满、账户盒吸底。
    //    目标高度 = 消息区撑开后的实际底部（含输入框），而非整页 scrollHeight，
    //    避免侧边栏被额外内容撑得过长。原始样式已在步骤 0 记录。
    const msgBottom = Math.max(
        document.documentElement.scrollHeight,
        (el.getBoundingClientRect().bottom + window.scrollY)
    );
    const sidebarTarget = Math.ceil(msgBottom);
    const sidebars = document.querySelectorAll('[class*="dc04ec1d"], [class*="b8812f16"]');
    for (const sb of sidebars) {
        sb.style.height = sidebarTarget + 'px';
        sb.style.minHeight = sidebarTarget + 'px';
    }
    // 侧边栏对话历史滚动容器：恢复原生 flex:1 1 0% 让它吃满外框剩余高度，
    // 历史按页面有多长就显示多长；账户设置盒（flex:0 1 auto）随之被 flex
    // 推到外框左下角。不要固定像素高度，否则底部出现空洞且账户盒不吸底。
    // 侧边栏存在两层 ds-scroll-area：外层 overflow:visible（flex 吃满剩余），
    // 内层 overflow:auto（真正滚动容器，内容为完整历史列表）。只放开外层，
    // 内层保持原生滚动限制，否则历史列表会被 height:auto 完整展开、超出对话区。
    const scrollers = document.querySelectorAll('[class*="ds-scroll-area"]');
    for (const sc of scrollers) {
        if (sc.getBoundingClientRect().width >= 500) continue;
        if (window.getComputedStyle(sc).overflow !== 'visible') continue;
        sc.style.flex = '1 1 0%';
        sc.style.height = 'auto';
        sc.style.minHeight = '0px';
        sc.style.maxHeight = 'none';
    }

    // 4. 账户设置盒吸底：margin-top:auto 依赖 flex 剩余空间，但历史滚动区
    //    （flex:1 1 0%）已 grow 吃满，账户盒抢不到空间、停在历史区末尾。
    //    改用绝对定位把账户盒锚定到侧边栏 flex 容器（b8812f16，position:
    //    relative）底部，不依赖 flex 轴；同时给历史滚动区留出底部 padding，
    //    避免账户盒遮挡历史列表末尾。原始样式已在 record 中登记用于还原。
    const sidebarFlex = document.querySelector('[class*="b8812f16"]');
    if (sidebarFlex) {
        const accountBox = Array.from(sidebarFlex.children).find((ch) => {
            const rect = ch.getBoundingClientRect();
            return ch.querySelector('img') && rect.height < 100;
        });
        if (accountBox) {
            const accH = Math.round(accountBox.getBoundingClientRect().height) || 44;
            record(accountBox, {
                pos: accountBox.style.position,
                top: accountBox.style.top,
                bottom: accountBox.style.bottom,
                left: accountBox.style.left,
                right: accountBox.style.right,
                width: accountBox.style.width,
            });
            accountBox.style.position = 'absolute';
            accountBox.style.bottom = '0px';
            accountBox.style.top = 'auto';
            accountBox.style.left = '0px';
            accountBox.style.right = '0px';
            accountBox.style.width = '100%';
            // 历史滚动区底部留出账户盒高度，避免内容被遮挡。该滚动区在步骤
            // 0/3 已被 record 过，会因去重跳过，故直接 saved.push 独立登记。
            const scroller = Array.from(sidebarFlex.querySelectorAll('[class*="ds-scroll-area"]'))
                .find((sc) => window.getComputedStyle(sc).overflow === 'visible'
                    && sc.getBoundingClientRect().width < 500);
            if (scroller) {
                saved.push({
                    path: pathOf(scroller),
                    padB: scroller.style.paddingBottom,
                });
                scroller.style.paddingBottom = accH + 'px';
            }
        }
    }

    // 5. 将底部悬浮输入框脱离吸附定位，跟随在对话消息最末尾
    //    DeepSeek 输入区外壳以 sticky 吸附在滚动容器底部；撑开后若仍 sticky
    //    会悬浮在视口内遮挡消息。这里只改 position 样式、不移动 DOM 节点，
    //    避免触发 React 虚拟列表重渲染把容器高度重置回 auto。
    //    输入框外壳本身就是消息列表的末子节点，改为 relative 后自然位于
    //    消息下方参与流布局。
    const textarea = document.querySelector('textarea');
    if (textarea) {
        let inputBox = textarea;
        let fallback = null;
        while (inputBox && inputBox.parentElement && inputBox.parentElement !== document.body) {
            const style = window.getComputedStyle(inputBox);
            if (style.position === 'sticky') {
                fallback = inputBox;
                break;
            }
            if (!fallback && ['fixed', 'absolute'].includes(style.position)) {
                fallback = inputBox;
            }
            inputBox = inputBox.parentElement;
        }
        inputBox = fallback || inputBox;
        if (inputBox && inputBox !== textarea) {
            record(inputBox, {
                pos: inputBox.style.position,
                bottom: inputBox.style.bottom,
                top: inputBox.style.top,
                zIndex: inputBox.style.zIndex
            });
            // 只改 position 让其脱离吸附、跟随在消息末尾，不引入额外间距
            inputBox.style.position = 'relative';
            inputBox.style.top = 'auto';
            inputBox.style.bottom = 'auto';
            inputBox.style.zIndex = '1';
        }
    }

    return saved;
}"""

# 精确恢复消息容器、侧边栏及底部输入框的原始样式。
# 以 CSS 路径重新定位节点并还原样式值；输入框仅改动 position 样式、未移动 DOM，
# 因此无需额外的节点位置恢复。React 虚拟列表不受 DOM 移动干扰，还原后保持原高度。
# 对高度由 CSS 类控制（inline 为空串）的容器，用记录的原渲染高度 origH 显式还原，
# 否则 height:auto 展开后无法缩回（auto 等于内容高度）。
RESTORE_SCRIPT = """(payload) => {
    const saved = payload ? payload.saved : null;
    if (!Array.isArray(saved)) return;
    const queryPath = (path) => {
        if (!path) return null;
        try { return document.querySelector(path); } catch (e) { return null; }
    };
    for (const item of saved) {
        if (!item) continue;
        // selector 优先（思考块等语义稳定的类选择器），其次 CSS 路径
        const node = item.selector ? queryPath(item.selector) : queryPath(item.path);
        if (!node) continue;
        // 思考块内容节点：还原折叠时隐藏的 display（'' 表示清除 inline 值）
        if (item.origDisplay !== undefined) node.style.display = item.origDisplay;
        // 高度还原：resetH 标记的容器（侧边栏历史滚动区）高度由 flex 布局决定，
        // 直接清空 inline height 让其自然缩回，不能用 origH 固定像素，否则配合
        // flex:1 1 0% 还原后仍被撑满。其余容器优先用原 inline 值或 origH 兜底。
        const restoreHeight = () => {
            if (item.resetH) {
                node.style.height = '';
            } else if (item.h !== undefined && item.h !== '') {
                node.style.height = item.h;
            } else if (item.h === '' && item.origH) {
                node.style.height = item.origH + 'px';
            }
        };
        restoreHeight();
        if (item.o !== undefined) node.style.overflow = item.o;
        if (item.maxH !== undefined) node.style.maxHeight = item.maxH;
        if (item.minH !== undefined) node.style.minHeight = item.minH;
        if (item.pos !== undefined) node.style.position = item.pos;
        if (item.bottom !== undefined) node.style.bottom = item.bottom;
        if (item.top !== undefined) node.style.top = item.top;
        if (item.left !== undefined) node.style.left = item.left;
        if (item.right !== undefined) node.style.right = item.right;
        if (item.width !== undefined) node.style.width = item.width;
        if (item.marginTop !== undefined) node.style.marginTop = item.marginTop;
        if (item.padB !== undefined) node.style.paddingBottom = item.padB;
        if (item.zIndex !== undefined) node.style.zIndex = item.zIndex;
        if (item.flex !== undefined) node.style.flex = item.flex;
    }
    // 强制一次同步重排，让虚拟滚动容器恢复到原渲染高度
    void document.body.offsetHeight;
}"""

# 思考过程块操作脚本（深度思考回复中"已思考"折叠块）：
# - collapse：隐藏思考内容（内容仍在 DOM，还原时可直接恢复）
# - expand：点击"已思考"标题让 React 展开（内容挂载进 DOM，截完需还原）
# - reveal：仅当内容被折叠（无 ds-think-content 节点）时点击标题展开，
#   已展开则保持现状，还原时优先恢复其初始展开状态。
# 返回已记录节点的恢复项数组（供 RESTORE_SCRIPT 还原）。
THINK_SCRIPT = """(mode) => {
    const saved = [];
    // 思考内容节点（语义稳定的类名），折叠时从 DOM 卸载
    const contentNodes = () => Array.from(document.querySelectorAll('.ds-think-content'));
    // 思考块标题（"已思考（用时 X 秒）"），点击可展开/折叠
    const headerNodes = () => Array.from(document.querySelectorAll('*')).filter(el => {
        const t = (el.innerText || '').trim();
        return el.children.length === 0 && /已思考/.test(t);
    });
    const applyCollapse = () => {
        for (const c of contentNodes()) {
            saved.push({ selector: '.ds-think-content', origDisplay: c.style.display });
            c.style.display = 'none';
        }
    };
    const expandOne = () => {
        for (const h of headerNodes()) {
            if (contentNodes().length > 0) return;
            h.click();
            return;
        }
    };
    if (mode === 'collapse') {
        applyCollapse();
    } else if (mode === 'expand') {
        expandOne();
    } else if (mode === 'reveal') {
        if (contentNodes().length === 0) expandOne();
        // 记录展开后的内容节点，还原时把点击展开导致的隐藏清掉（display:''）
        for (const c of contentNodes()) {
            saved.push({ selector: '.ds-think-content', origDisplay: c.style.display });
        }
    }
    return saved;
}"""

# 侧边栏收起/展开脚本：
# - hide：隐藏左侧边栏外框（display:none），主内容自动左移占满整宽
# - show：确保侧边栏可见（还原为原始 display 值）
# 记录每个外框的原始 display，还原时按 origDisplay 恢复。
SIDEBAR_SCRIPT = """(mode) => {
    const saved = [];
    const bars = document.querySelectorAll('[class*="dc04ec1d"]');
    for (const bar of bars) {
        if (bar.getBoundingClientRect().left !== 0) continue; // 只处理最左侧外框
        saved.push({ selector: 'html [class*="dc04ec1d"]', origDisplay: bar.style.display });
        if (mode === 'hide') {
            bar.style.display = 'none';
        } else if (mode === 'show') {
            bar.style.display = '';
        }
    }
    return saved;
}"""

# 消息文本提取脚本：
# - last：取最后一个 .ds-message 的 .ds-markdown 正文（纯 AI 回复，不含思考块）
# - full：汇总全部 .ds-message 的正文（用户消息取 innerText，AI 消息取
#   .ds-markdown；思考块文字自然被排除）。
# 依据探测：DeepSeek 所有消息均在 .ds-message 中可枚举，虚拟列表不卸载历史。
CONVERSATION_TEXT_SCRIPT = """(scope) => {
    const msgs = Array.from(document.querySelectorAll('.ds-message'));
    if (msgs.length === 0) return '';
    if (scope === 'last') {
        // 最新一条 AI 回复：取最后一个含 .ds-markdown 的消息（用户消息不计入）
        for (let i = msgs.length - 1; i >= 0; i--) {
            const md = msgs[i].querySelector('.ds-markdown');
            if (md) return (md.innerText || '').trim();
        }
        // 无 AI 回复（如思考中）时返回空，等待轮询继续
        return '';
    }
    const parts = [];
    for (const m of msgs) {
        const md = m.querySelector('.ds-markdown');
        const txt = md ? (md.innerText || '') : (m.innerText || '');
        const clean = txt.trim();
        if (clean) parts.push(clean);
    }
    return parts.join('\\n\\n');
}"""

# 会话指纹脚本：消息数 + 页面标题，用于校验 open_conversation 是否真正切换会话。
# 消息数比首条消息文本更可靠（不同会话可能首条相似），标题反映当前会话主题。
FINGERPRINT_SCRIPT = """() => {
    const msgs = document.querySelectorAll('.ds-message');
    const title = (document.title || '').trim();
    return msgs.length + ':' + title;
}"""

# 侧边栏历史会话列表提取：返回去重后的会话标题（A 标签 _546d736 为可点会话项）。
# 对匹配到的可点元素向上找"真正承载 onClick 的容器"（优先带 aria/已知会话项哈希
# 类的祖先），点击该容器而非 <a> 本身，避免点错元素。
HISTORY_LIST_SCRIPT = """() => {
    const seen = new Set();
    const out = [];
    const links = document.querySelectorAll('[class*="dc04ec1d"] a, [class*="b8812f16"] a');
    for (const a of links) {
        const r = a.getBoundingClientRect();
        const txt = (a.innerText || '').trim().replace(/\\n+/g, ' ');
        if (!txt || r.width < 50 || r.height < 10) continue;
        if (seen.has(txt)) continue;
        seen.add(txt);
        // 向上找真正可点的容器（带 aria 或已知会话项哈希类）
        let clickable = a;
        let cur = a;
        for (let i = 0; i < 5 && cur && cur !== document.body; i++) {
            if (cur.getAttribute('role') === 'button'
                    || cur.getAttribute('role') === 'link'
                    || cur.getAttribute('aria-label')
                    || /_546d736/.test(cur.className || '')) {
                clickable = cur;
                break;
            }
            cur = cur.parentElement;
        }
        out.push({ title: txt, clickable: !!clickable });
    }
    return out.slice(0, 50);
}"""

# 历史会话列表滚动加载脚本：滚动侧边栏历史滚动容器到底，触发虚拟列表加载更多项。
# 返回是否发生了滚动（用于 open_conversation 判断是否需继续加载）。
HISTORY_SCROLL_SCRIPT = """() => {
    const scrollers = document.querySelectorAll('[class*="ds-scroll-area"]');
    for (const sc of scrollers) {
        const r = sc.getBoundingClientRect();
        if (r.width >= 500 || r.height < 50) continue;
        const before = sc.scrollTop;
        sc.scrollTop = sc.scrollHeight;
        if (sc.scrollTop !== before) return true;
    }
    return false;
}"""

# 点击指定标题的历史会话：在侧边栏按多级文本匹配（精确 > 前缀 > 首个包含）选
# 候选，找到后向上定位可点容器并点击，返回是否命中。
HISTORY_OPEN_SCRIPT = """(title) => {
    const norm = (s) => (s || '').trim().replace(/\\n+/g, ' ').replace(/\\s+/g, ' ');
    const want = norm(title);
    if (!want) return false;
    const links = Array.from(document.querySelectorAll('[class*="dc04ec1d"] a, [class*="b8812f16"] a'));
    const seen = new Set();
    const candidates = [];
    for (const a of links) {
        const txt = norm(a.innerText);
        const r = a.getBoundingClientRect();
        if (!txt || r.width < 50 || r.height < 10 || seen.has(txt)) continue;
        seen.add(txt);
        candidates.push({ a, txt });
    }
    // 匹配优先级：精确 > 前缀 > 首个包含
    let pick = null;
    pick = candidates.find(c => c.txt === want) || null;
    if (!pick) pick = candidates.find(c => c.txt.startsWith(want)) || null;
    if (!pick) pick = candidates.find(c => c.txt.includes(want)) || null;
    if (!pick) return false;
    let clickable = pick.a;
    let cur = pick.a;
    for (let i = 0; i < 5 && cur && cur !== document.body; i++) {
        if (cur.getAttribute('role') === 'button'
                || cur.getAttribute('role') === 'link'
                || cur.getAttribute('aria-label')
                || /_546d736/.test(cur.className || '')) {
            clickable = cur;
            break;
        }
        cur = cur.parentElement;
    }
    clickable.click();
    return true;
}"""

# 当前活跃对话标题提取：从侧边栏历史会话项中找 active 项取标题，与
# BROWSER_CHROME_SCRIPT 的标题提取逻辑一致（复用 _546d736.active 选择器）。
# 无 active 项时回退到侧边栏 aria-current/aria-selected 高亮项。
ACTIVE_CONVERSATION_TITLE_SCRIPT = """() => {
    const el = document.querySelector('div[class*="_546d736"].active, [class*="_546d736"][data-active="true"]');
    if (el) {
        const t = (el.innerText || '').split(/\\r?\\n/)[0].trim();
        if (t) return t;
    }
    const active = document.querySelector('[class*="b8812f16"] [aria-current="page"], [class*="b8812f16"] [aria-selected="true"]');
    if (active) {
        const t = (active.innerText || '').trim();
        if (t) return t;
    }
    return '';
}"""

# 当前活跃对话稳定 ID 提取：从 URL 中提取会话 UUID 段（pathname/hash 中
# 首个连续 8 位以上十六进制段），作为模式锁的稳定 key。会话切换时 URL 的
# UUID 段随之变化，标题会变而 ID 稳定，故锁以 ID 为准、标题仅作展示。
ACTIVE_CONVERSATION_ID_SCRIPT = """() => {
    const m = (location.href || '').match(/[0-9a-f]{8,}/i);
    return m ? m[0].toLowerCase() : '';
}"""


def normalize_mode(mode: str) -> str | None:
    """将模式别名归一化为标准模式名。

    Args:
        mode: 原始模式输入（如 快速 / 快速模式 / 专家）。

    Returns:
        str | None: 标准模式名；无法识别时返回 None。
    """
    alias = {
        "快速": "快速模式",
        "快速模式": "快速模式",
        "专家": "专家模式",
        "专家模式": "专家模式",
        "识图": "识图模式",
        "识图模式": "识图模式",
    }
    return alias.get((mode or "").strip())


def normalize_think(think: str) -> str | None:
    """将思考过程块展开方式归一化为脚本模式。

    Args:
        think: 原始输入（auto/expand/collapse/reveal，大小写不敏感）。

    Returns:
        str | None: 脚本模式；auto 或无法识别时返回 None（保持现状）。
    """
    mapping = {
        "expand": "expand",
        "展开": "expand",
        "collapse": "collapse",
        "折叠": "collapse",
        "收起": "collapse",
        "reveal": "reveal",
        "展开思考": "expand",
        "收起思考": "collapse",
    }
    return mapping.get((think or "").strip().lower())


def normalize_sidebar(sidebar: str) -> str | None:
    """将侧边栏显示方式归一化为脚本模式。

    Args:
        sidebar: 原始输入（auto/show/hide，大小写不敏感）。

    Returns:
        str | None: 脚本模式；auto 或无法识别时返回 None（保持现状）。
    """
    mapping = {
        "show": "show",
        "展开": "show",
        "打开": "show",
        "显示": "show",
        "hide": "hide",
        "收起": "hide",
        "折叠": "hide",
        "隐藏": "hide",
        "关闭": "hide",
    }
    return mapping.get((sidebar or "").strip().lower())


def normalize_decoration_theme(theme: str) -> str:
    """将浏览器外壳配色归一化为脚本可用值。

    Args:
        theme: 原始输入（auto/light/dark，大小写不敏感）。

    Returns:
        str: auto/light/dark 之一；无法识别时回退为 auto（跟随页面）。
    """
    value = (theme or "").strip().lower()
    return value if value in ("auto", "light", "dark") else "auto"


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
