"""豆包站点专属常量与页面脚本（依据 2026-08 实测 DOM 探测校准）。

四轮探测（scripts/probe_doubao_dom*.py + 报告 doubao_probe_report/）确认：
- 输入框：``div.tiptap.ProseMirror[contenteditable="true"]`` 富文本编辑器
  （公开情报的 textarea.semi-input-textarea 已过时）
- 消息列表：虚拟列表 ``[class*="v_list"]``，行 ``[class*="v_list_row"]``
- 用户消息气泡：``[class*="send-msg-bubble"]``（bg-g-send-msg-bubble-bg）
- AI 回复正文：``[class*="md-box-root"]`` 容器（md- 前缀 markdown 组件）
- 生成中：``[class*="loading-container"]`` 可见；完成后出现
  "AI 生成可能有误 注意核实" 标记（span，文本精确匹配）
- 历史会话：侧栏 ``a.group/conversation-item``（href=/chat/<数字ID>）
- 模型菜单：radix 菜单（role=menuitem），两档「豆包 快速」/「豆包 2.1
  Turbo 专家」（专家即深度思考档）
- 技能按钮：data-skill-id 稳定标识（图像 skill_bar_button_3 / 视频
  skill_bar_button_17），点击后 placeholder 变"描述你想要的图片/视频"
- 生成图片：byteimg CDN http 直链（可直接 fetch 下载）；视频 poster 先现
  （cover-u1rIsU），video 元素出现即完成（约 5 分钟，需后台等待）
- 附件白名单：``input[type=file]`` accept 为 文档+图片 固定集合
- 主题：localStorage ``dbx-web-theme`` = light/dark；html[data-theme]
- URL：会话 ID 为纯数字（/chat/38438418129860098）

站点 DOM 变动时仅需在此同步；供服务层（DoubaoActions）与业务层复用。
"""

from __future__ import annotations

# 豆包官方默认入口（会话管理器据此定位登录态 profile 与默认 URL）
SITE_URL = "https://www.doubao.com/chat/"

# 模型档位（对应网页"豆包 快速"模型菜单的两档；专家档即深度思考）
MODEL_FAST = "快速"
MODEL_PRO = "专家"
SUPPORTED_MODELS: tuple[str, ...] = (MODEL_FAST, MODEL_PRO)

# 输入框选择器（tiptap ProseMirror contenteditable 富文本）
INPUT_SELECTOR = 'div.tiptap.ProseMirror[contenteditable="true"]'

# 发送按钮（仅输入框有文本时渲染，Enter 未生效时用其兜底发送）
SEND_BUTTON_SELECTOR = '.send-btn-wrapper'

# 用户消息气泡（bg-g-send-msg-bubble-bg 气泡链内 content 容器）
USER_BUBBLE_SELECTOR = '[class*="send-msg-bubble"]'

# AI 回复正文容器（md-box-root markdown 根组件）
AI_MESSAGE_SELECTOR = '[class*="md-box-root"]'

# 消息列表虚拟滚动容器（v_list 系 class 含 scroller/行容器）
CONVERSATION_SELECTOR = '[class*="v_list"]'

# 新对话按钮文本（侧栏"新对话"入口）
NEW_CHAT_TEXT = "新对话"

# AI 回复完成标记（正文下方 disclaimer 文本，精确匹配）
DONE_MARKER_TEXT = "AI 生成可能有误 注意核实"

# 登录态就绪判定脚本（双条件：输入框可见 + 正面文案，且无精确"登录"按钮；
# 豆包未登录页也渲染输入区，不能只看输入框——2026-08-22 掉登录事故教训）
READY_CHECK_SCRIPT = """([input_sel, markers, login_text]) => {
    const visible = (el) => el.getBoundingClientRect().width > 0
        && el.getBoundingClientRect().height > 0;
    const input = document.querySelector(input_sel);
    if (!input || !visible(input)) return false;
    const bodyText = (document.body.innerText || '');
    if (!markers.some(m => bodyText.includes(m))) return false;
    const loginBtn = Array.from(document.querySelectorAll('button, a, [role="button"]'))
        .some(el => (el.innerText || '').trim() === login_text && visible(el));
    return !loginBtn;
}"""

# 就绪判定的输入框选择器与正面文案（与 READY_CHECK_SCRIPT 配套）
READY_INPUT_SELECTOR = (
    "div.tiptap.ProseMirror[contenteditable='true'], div[contenteditable='true'][role='textbox'], textarea"
)
READY_TEXT_MARKERS: tuple[str, ...] = ("新对话", "有什么我能帮你的吗", "发消息")
LOGIN_BUTTON_TEXT = "登录"

# 登录后偶发的客户端下载推广弹窗会遮住输入区和侧栏。
DOWNLOAD_PROMO_DISMISS_SCRIPT = """() => {
    const button = Array.from(document.querySelectorAll('button')).find(el =>
        (el.innerText || '').replace(/\\s+/g, ' ').trim() === '下次提醒我');
    if (!button || !button.getClientRects().length) return false;
    for (let parent = button.parentElement, depth = 0;
         parent && depth < 8; parent = parent.parentElement, depth++) {
        if ((parent.innerText || '').includes('下载电脑版')) {
            button.click();
            return true;
        }
    }
    return false;
}"""

# 等待 AI 回复完成的轮询间隔（秒）
POLL_INTERVAL_S = 2.0

# 主题偏好 localStorage 键（dbx-web-theme = light/dark）
THEME_PREF_KEY = "dbx-web-theme"

# 生成中指示器探测脚本：loading-container 可见 或 存在"停止"按钮
# 即视为仍在生成（豆包无稳定的 aria 停止按钮，以 loading 为主信号）。
GENERATING_SCRIPT = """() => {
    const loading = Array.from(document.querySelectorAll('[class*="loading-container"]'))
        .some(el => el.getBoundingClientRect().width > 0);
    if (loading) return true;
    const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
    return btns.some(el => {
        const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
        return t && t.includes('停止');
    });
}"""

# 页面可见性检测脚本（返回消息容器是否已撑开可见）
VISIBILITY_SCRIPT = """(selector) => {
    const el = document.querySelector(selector);
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none';
}"""

# 对话文本提取脚本：
# - last：最新一条 AI 回复（最后一个 md-box-root 纯文本，剔除完成标记）
# - full：整段对话（虚拟行内用户气泡与 AI 回复按序拼接，前缀区分角色）
CONVERSATION_TEXT_SCRIPT = """(scope) => {
    const clean = (el) => (el.innerText || '')
        .split('\\n')
        .filter(line => line.trim() && line.trim() !== 'AI 生成可能有误 注意核实')
        .join('\\n')
        .trim();
    const replies = Array.from(document.querySelectorAll('[class*="md-box-root"]'));
    if (scope === 'full') {
        const rows = Array.from(document.querySelectorAll('[class*="v_list_row"]'));
        const parts = [];
        rows.forEach(row => {
            const user = row.querySelector('[class*="send-msg-bubble"]');
            const ai = row.querySelector('[class*="md-box-root"]');
            if (user) parts.push('用户: ' + clean(user));
            if (ai) parts.push('豆包: ' + clean(ai));
        });
        return parts.join('\\n\\n');
    }
    if (!replies.length) return '';
    return clean(replies[replies.length - 1]);
}"""

# 活跃会话标题提取脚本（侧栏选中态会话项首行文本）
ACTIVE_TITLE_SCRIPT = """() => {
    const item = document.querySelector('a[class*="conversation-item"][aria-current="page"]')
        || document.querySelector('a[class*="conversation-item"][data-state="active"]')
        || document.querySelector('a[class*="conversation-item"][class*="e2e-test-active"]');
    if (item) {
        const t = (item.innerText || '').split('\\n')[0].trim();
        if (t) return t;
    }
    return '';
}"""

# 活跃会话稳定 ID 提取脚本（URL /chat/<数字ID>；新会话为空）
ACTIVE_ID_SCRIPT = """() => {
    const m = location.pathname.match(/\\/chat\\/(\\d+)/);
    return m ? m[1] : '';
}"""

# 历史会话列表脚本（侧栏全部会话项：href 数字 ID + 标题首行）
HISTORY_LIST_SCRIPT = """() => {
    return Array.from(document.querySelectorAll('a[class*="conversation-item"]'))
        .filter(a => /\\/chat\\/\\d+/.test(a.getAttribute('href') || ''))
        .map(a => {
            const href = a.getAttribute('href') || '';
            const m = href.match(/\\/chat\\/(\\d+)/);
            return {id: m ? m[1] : '', title: (a.innerText || '').split('\\n')[0].trim()};
        })
        .filter(x => x.title);
}"""

# 历史会话项匹配脚本：按标题（精确 > 首个包含）定位侧栏项，返回该项的会话 ID
# 与 href（供上层精确定位点击与切换校验；都未命中时 id/href 为空）。
HISTORY_ITEM_HIT_SCRIPT = """(title) => {
    const norm = (s) => (s || '').split('\\n')[0].trim().replace(/\\s+/g, ' ');
    const want = norm(title);
    if (!want) return {id: '', href: ''};
    const items = Array.from(document.querySelectorAll('a[class*="conversation-item"]'))
        .filter(a => /\\/chat\\/\\d+/.test(a.getAttribute('href') || ''));
    const idOf = (a) => {
        const m = (a.getAttribute('href') || '').match(/\\/chat\\/(\\d+)/);
        return m ? m[1] : '';
    };
    let hit = null;
    for (const a of items) {
        if (norm(a.innerText) === want) { hit = a; break; }
    }
    if (!hit) {
        for (const a of items) {
            const t = norm(a.innerText);
            if (t && t.includes(want)) { hit = a; break; }
        }
    }
    if (!hit) return {id: '', href: ''};
    return {id: idOf(hit), href: hit.getAttribute('href') || ''};
}"""

# 当前模型档位读取脚本（返回 快速 / 专家；按输入区模型按钮文本判定）
GET_MODEL_SCRIPT = """() => {
    const btns = Array.from(document.querySelectorAll('button'));
    const b = btns.find(el => {
        const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
        return t === '豆包 快速' || t === '豆包 2.1 Turbo'
            || t === '豆包 2.1 Turbo 专家'
            || /^豆包.+快速$/.test(t) || /^豆包.+专家$/.test(t);
    });
    if (!b) return '';
    const t = (b.innerText || '').replace(/\\s+/g, ' ').trim();
    if (t.includes('专家') || t.includes('Turbo')) return '专家';
    if (t.includes('快速')) return '快速';
    return t;
}"""

# 设置主题脚本（dbx-web-theme 写 light/dark；改后 reload 生效）
SET_THEME_SCRIPT = """(theme) => {
    try {
        localStorage.setItem('dbx-web-theme', theme);
        return true;
    } catch (e) { return false; }
}"""

# 读取主题脚本（返回 light/dark，缺省 light）
GET_THEME_SCRIPT = """() => {
    const v = localStorage.getItem('dbx-web-theme');
    return v === 'dark' ? 'dark' : 'light';
}"""

# ---------------------------------------------------------------------------
# 技能（图像/视频生成，2026-08 probe5 实测）
# ---------------------------------------------------------------------------

# 技能按钮 data-skill-id（稳定标识：图像生成 / 视频生成）
SKILL_IMAGE_BUTTON_ID = "skill_bar_button_3"
SKILL_VIDEO_BUTTON_ID = "skill_bar_button_17"
# 技能受理确认：点击后输入框 placeholder 变为对应提示文案
SKILL_IMAGE_PLACEHOLDER = "描述你想要的图片"
SKILL_VIDEO_PLACEHOLDER = "描述你想要的视频"

# 视频生成免费模型（付费升级模型如 Seedance 2.0 / Seedance 2.5 需 VIP 或高倍额度，不向模型暴露）
FREE_VIDEO_MODELS: tuple[str, ...] = ("Seedance 2.0 Fast", "Seedance 2.0 Mini")
DEFAULT_VIDEO_MODEL: str = "Seedance 2.0 Fast"

# 视频生成原生比例与时长
VIDEO_ASPECT_RATIOS: tuple[str, ...] = ("自动", "16:9", "9:16", "3:4", "4:3", "1:1", "21:9")
DEFAULT_VIDEO_RATIO: str = "自动"
VIDEO_DURATIONS: tuple[str, ...] = ("4s", "10s", "15s")
DEFAULT_VIDEO_DURATION: str = "10s"

# 图像生成免费模型（付费升级模型如 Seedream 5.0 Pro 需 VIP 或 4 倍消耗，不向模型暴露）
FREE_IMAGE_MODELS: tuple[str, ...] = ("Seedream 4.5", "Seedream 4.0")
DEFAULT_IMAGE_MODEL: str = "Seedream 4.5"

# 图像生成原生比例与风格（32 种官方原生风格）
IMAGE_ASPECT_RATIOS: tuple[str, ...] = ("自动", "1:1", "16:9", "9:16", "3:4", "4:3", "2:3", "3:2")
DEFAULT_IMAGE_RATIO: str = "自动"
IMAGE_STYLES: tuple[str, ...] = (
    "自动",
    "人像摄影",
    "电影写真",
    "中国风",
    "动漫",
    "3D渲染",
    "赛博朋克",
    "CG 动画",
    "水墨画",
    "油画",
    "古典",
    "水彩画",
    "卡通",
    "平面插画",
    "风景",
    "港风动漫",
    "像素风格",
    "荧光绘画",
    "彩铅画",
    "手办",
    "儿童绘画",
    "抽象",
    "锐笔插画",
    "二次元",
    "油墨印刷",
    "版画",
    "莫奈",
    "毕加索",
    "伦勃朗",
    "马蒂斯",
    "巴洛克",
    "复古动漫",
    "绘本",
)
DEFAULT_IMAGE_STYLE: str = "自动"

# 技能激活态探测脚本：placeholder 变为目标文案即受理成功。
# 检测方式与 probe5 验证一致：全局 [data-placeholder] 或
# [class*="placeholder"] 元素（tiptap 空编辑器占位由父容器渲染，CSS ::
# before 文案不进 innerText，data-placeholder 属性才可靠）
SKILL_ACTIVE_SCRIPT = """(placeholder) => {
    const el = document.querySelector('[data-placeholder], [class*="placeholder"]');
    if (el) {
        const attr = el.getAttribute('data-placeholder') || '';
        if (attr === placeholder) return true;
    }
    return false;
}"""

# 风控拦截检测脚本（人机验证）：豆包在生图/生视频等高消耗链路上会弹出
# 图片选择验证，自动化无法完成，必须人工介入；提前识别可避免长时间空等超时。
# 验证层由字节验证中心以 iframe 加载（rmc.bytedance.com/verifycenter/captcha），
# 其文案在主文档 innerText 中取不到，故以可见的验证 iframe 为主要判据，
# 主文档文案作为站点改用同文档渲染时的兜底。
# 注意：iframe 判定必须同时校验真实可见性 —— getBoundingClientRect 对
# visibility:hidden / opacity:0 的元素仍返回真实尺寸（只有 display:none 为 0），
# 而豆包会预加载隐藏的 iframe（如 drive-iframe 实测 1440x900 + visibility:hidden），
# 只比尺寸会把隐藏预加载误判成"弹了验证"。
CAPTCHA_SCRIPT = """() => {
    for (const f of document.querySelectorAll('iframe')) {
        const src = f.getAttribute('src') || '';
        if (!/verifycenter\\/captcha|rmc\\.bytedance\\.com/.test(src)) continue;
        const cs = getComputedStyle(f);
        if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') continue;
        const r = f.getBoundingClientRect();
        if (r.width > 50 && r.height > 50) return '图片选择验证';
    }
    const body = document.body ? (document.body.innerText || '') : '';
    if (body.includes('请选择所有符合上文描述的图片')) return '图片选择验证';
    if (body.includes('拖拽到这里') && body.includes('验证')) return '人机验证';
    return '';
}"""

# 生成参数确认检测脚本：豆包在生图/生视频前会先用自然语言列出参数（模型/
# 时长/比例/创作方向）并问"确认后我再开始生成视频"，这是一轮普通对话而非
# 可点按钮（消息下方只有复制/点赞等常规操作栏），自动化必须回复"确认"才会
# 真正开始生成；未出现该提示说明描述已足够明确、直接进入生成。
# 只匹配末尾几行：历史会话里可能残留旧的参数卡片，全页匹配会误判成
# "本次已出现"从而提前发确认。
PARAM_CONFIRM_SCRIPT = """() => {
    const rows = Array.from(document.querySelectorAll('[class*="v_list_row"]'));
    if (!rows.length) return false;
    const tail = rows.slice(-3).map((r) => r.innerText || '').join(' ');
    return tail.includes('确认后我再开始生成') || tail.includes('参数确认');
}"""

# 生成图片检测脚本：返回全部生成图的大图 src 列表（去重后）。
# 判定特征（probe6/7 实测）：一次生成 4 张候选，每张渲染缩略图（384px）与
# 大图（2048px）两个 img（src 主干相同、仅模板参数不同），均在虚拟列表
# 行内、CDN 路径含 ``rc_gen_image``。按 src 主干去重后返回每张大图 URL。
IMAGE_READY_SCRIPT = """() => {
    // 注意：生成结果 img 使用 srcset 响应式加载——im.src 属性仍是 384 缩略
    // 模板 URL，浏览器实际加载的大图来自 srcset；必须用 im.currentSrc
    // （真实加载源）才能拿到 2048 大图 URL（实测 2026-08）
    const realSrc = (im) => im.currentSrc || im.src || '';
    const imgs = Array.from(document.querySelectorAll('img'))
        .filter(im => im.complete && /^https?:/.test(realSrc(im)))
        .filter(im => realSrc(im).includes('rc_gen_image')
            || (im.naturalWidth >= 300 && Boolean(im.closest('[class*="v_list_row"]'))));
    // 按 src 主干去重（去掉模板参数后相同即同一张图），保留最大尺寸版本；
    // 组内最大 < 1000 说明该组只有缩略引用（侧栏/历史引用），整组跳过——
    // 生成结果必然有大图版本（2048），只回收有大图的组
    const byKey = {};
    for (const im of imgs) {
        const key = realSrc(im).split('~')[0];
        const prev = byKey[key];
        if (!prev || im.naturalWidth * im.naturalHeight > prev.w * prev.h) {
            byKey[key] = {src: realSrc(im), w: im.naturalWidth, h: im.naturalHeight};
        }
    }
    return Object.values(byKey)
        .filter(x => x.w >= 1000)
        .sort((a, b) => b.w * b.h - a.w * a.h)
        .map(x => x.src);
}"""

# 生成视频检测脚本：按视频卡片（``block-video``）判定，不是 ``<video>``。
# 实测：生成完成后豆包只渲染卡片容器 + 封面图，``<video>`` 需鼠标悬停
# 卡片才挂载，因此以 ``<video>`` 作完成信号会永远等不到（实测坑）。
# 返回卡片的挂载状态、真实视频地址（若已挂载）、封面地址与中心坐标。
VIDEO_CARD_SCRIPT = """() => {
    const rows = Array.from(document.querySelectorAll('[class*="v_list_row"]'));
    for (let i = rows.length - 1; i >= 0; i--) {
        const card = rows[i].querySelector('[class*="block-video"]');
        if (!card) continue;
        const r = card.getBoundingClientRect();
        if (r.width < 120 || r.height < 80) continue;
        const v = card.querySelector('video');
        const cover = card.querySelector('img');
        const coverReady = !!cover && cover.complete && cover.naturalWidth > 0;
        return {
            found: true,
            coverReady: coverReady,
            mounted: !!v,
            src: v ? (v.currentSrc || v.src || '') : '',
            duration: v ? Math.round(v.duration || 0) : 0,
            x: Math.round(r.x + r.width / 2),
            y: Math.round(r.y + r.height / 2),
        };
    }
    return {found: false};
}"""

# 生成视频卡片选择器：hover 该容器才会挂载 ``<video>`` 与操作栏。
VIDEO_CARD_SELECTOR = '[class*="block-video"]'

# 输入区附件挂载计数脚本（三种模式类名不同，逐项取较大值）：
# - 普通对话/生图模式：``image-wrapper-*`` 卡片（每张图一个）
# - 生视频模式：``thumb-card-*`` 参考图卡片（内含 ``thumb-image-*``）
# 两类都与单次上传数量 1:1；仅认其中一种会在另一模式下漏判（实测坑：
# 生视频模式只挂 thumb-card，用 image-wrapper 计数会把成功上传误判为失败）
ATTACHMENT_COUNT_SCRIPT = """() => {
    const wrappers = document.querySelectorAll('[class*="image-wrapper"]').length;
    const cards = document.querySelectorAll('[class*="thumb-card"]').length;
    const thumbs = document.querySelectorAll('img[class*="thumb-image"]').length;
    return Math.max(wrappers, cards, thumbs);
}"""

# 附件删除脚本：点击首个附件卡片内的删除按钮（逐张清除）。
# 豆包切换"新对话"时会保留输入区草稿（实测），残留附件会跟着下一条消息
# 一起发出，故上传前需先清空。
ATTACHMENT_DELETE_SCRIPT = """() => {
    const btn = document.querySelector(
        '[class*="thumb-card"] [class*="delete-btn"],'
        + ' [class*="image-wrapper"] [class*="delete-btn"]'
    );
    if (!btn) return false;
    btn.click();
    return true;
}"""

# 上传素材安全确认弹窗脚本：带参考图/附件提交生成任务时豆包弹出"安全确认"
# 授权确认（标题"安全确认"，正文含"均已获充分授权"），弹窗会拦住发送，
# 必须点"确认"才能继续。仅当弹窗文本命中该授权文案时才自动确认，其他弹窗
# （额度不足/会员升级等）不代点，交由上层报错。
SAFETY_CONFIRM_SCRIPT = """() => {
    const d = document.querySelector(
        '[data-slot="alert-dialog-content"], [role="alertdialog"], [role="dialog"]'
    );
    if (!d) return '';
    const r = d.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return '';
    const text = d.innerText || '';
    if (!text.includes('安全确认') && !text.includes('均已获充分授权')) return '';
    const btn = Array.from(d.querySelectorAll('button'))
        .find(b => (b.innerText || '').trim() === '确认');
    if (!btn) return '';
    btn.click();
    return '安全确认';
}"""

# 视频下载按钮探测脚本：hover 视频卡片后出现的"下载"图标。
#
# 实测（2026-09）：该按钮是**纯图标**，无 aria-label / title / 文本，按语义
# 文本检索必然落空（表现为日志"未找到视频下载按钮"）；可靠判据是结构位置——
# 卡片内 ``hover-mask-*`` 容器（hover 才显形）下的可点击 ``action-*`` 卡片。
# 返回中心坐标供鼠标点击（图标无文本，无法用文本选择器定位）。
# 侧栏同名按钮由卡片作用域天然排除。
VIDEO_DOWNLOAD_BUTTON_SCRIPT = """() => {
    const card = document.querySelector('[class*="block-video"]');
    if (!card) return '';
    const mask = card.querySelector('[class*="hover-mask"]');
    if (!mask) return '';
    const hit = mask.querySelector('[class*="action-"]') || mask.firstElementChild;
    if (!hit) return '';
    const r = hit.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return '';
    return {
        x: Math.round(r.x + r.width / 2),
        y: Math.round(r.y + r.height / 2),
        w: Math.round(r.width),
        h: Math.round(r.height),
    };
}"""


def normalize_model(model: str) -> str | None:
    """归一化模型档位名（快速/专家）。

    Args:
        model: 用户或上层传入的模型名（支持 快速/专家/深度思考 等变体）。

    Returns:
        str | None: 归一化档位（快速/专家）；不识别时返回 None。
    """
    m = (model or "").strip()
    if not m:
        return None
    if "专家" in m or "深度思考" in m or "pro" in m.lower():
        return MODEL_PRO
    if "快速" in m or "fast" in m.lower():
        return MODEL_FAST
    return None

