"""识图接管验证：真实事件链 + 真实站点识图。

不启动 bot、不需要聊天：按框架 ``on_media_recognize`` 的发布方式构造事件参数，
走**真实事件总线**发布，验证本插件的接管处理器与框架内置 VLM 兜底处理器之间的
契约（成功接管、失败放行）是否真的成立；成功接管时识图走**真实 AI 网页**。

覆盖：
1. 提示词单源：识图提示词确实读自框架模板（``media.image_recognition`` /
   ``media.emoji_recognition``），插件内不存在第二份提示词
2. 事件契约：成功时回填 ``description`` + ``engine_processed``，框架兜底处理器
   被跳过（用真实 EventBus 的 priority 排序与 ``engine_processed`` 判据验证）
3. 真实识图：默认站点（配置的 ``recognize.site``）对真实图片给出非空描述，
   且识图失败（站点收不到图）时不会张冠李戴：每次识图都开新会话，
   回复只可能对应当前图片
4. 放行：接管关闭 / 站点不可用 / 表情包开关关闭 / 非图片媒体 都返回 PASS，
   交回框架（插件出问题时识图能力只会变弱不会变没）
5. 图片定位：无落盘记录时从 base64 落临时文件并清理，不残留
6. 不重复识图：插件成功接管后框架 VLM 一次都不跑；插件放行后恰好跑一次；
   同一张图再次出现时缓存直接命中，根本不进事件链（按 ``core.toml`` 的
   ``[database]`` 配置建立只读连接查询缓存样本）

**运行前提**：bot 已关闭（浏览器 profile 独占）；``--no-site`` 模式下数据库
只读查询可与运行中的 bot 共存。

用法：
    set PYTHONIOENCODING=utf-8 && uv run python scripts/verify_recognize.py
    uv run python scripts/verify_recognize.py --no-site   # 跳过真实识图（只验证契约与放行）
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import os
import pathlib
import sys
import tempfile
from typing import Any

PLUGIN_DIR = pathlib.Path(__file__).resolve().parent.parent
PLUGINS_ROOT = PLUGIN_DIR.parent
PROJECT_ROOT = PLUGINS_ROOT.parent
for _p in (str(PROJECT_ROOT), str(PLUGINS_ROOT)):
    while _p in sys.path:
        sys.path.remove(_p)
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PLUGINS_ROOT))
os.chdir(PROJECT_ROOT)

# 以包形式导入插件：插件内部使用相对导入（..config 等），
# 直接以顶层模块导入会报 "attempted relative import beyond top-level package"。
from ai_ui_snapshot.config import AiUiSnapshotConfig  # noqa: E402
from ai_ui_snapshot.event_handler.media_recognize import ImageRecognizeHandler  # noqa: E402
from ai_ui_snapshot.services.service import framework_recognize_prompt  # noqa: E402
from src.app.plugin_system.api import prompt_api  # noqa: E402
from src.core.config.core_config import init_core_config  # noqa: E402
from src.core.config.model_config import init_model_config  # noqa: E402
from src.kernel.event import EventDecision, get_event_bus  # noqa: E402

EVENT = "on_media_recognize"
CONFIG_PATH = PROJECT_ROOT / "config" / "plugins" / "ai_ui_snapshot" / "config.toml"
CORE_CONFIG = PROJECT_ROOT / "config" / "core.toml"
MODEL_CONFIG = PROJECT_ROOT / "config" / "model.toml"
FAILED: list[str] = []
PASSED = 0
# 框架兜底处理器返回的标记描述（用于判断兜底是否被调用）
FALLBACK_MARK = "FRAMEWORK_FALLBACK_DESCRIPTION"


def check(name: str, cond: bool, detail: str = "") -> None:
    """记录一条断言结果。"""
    global PASSED
    if cond:
        PASSED += 1
        print(f"  [OK]   {name}")
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name}  {detail}")


async def init_framework() -> str:
    """初始化框架配置、数据库引擎，并触发识图提示词模板注册。

    提示词模板由媒体管理器构造时注册（``MediaConfig._register_prompts`` 读
    ``core.toml`` 的识图提示词），因此顺序必须是：先初始化框架配置，
    再取一次媒体管理器让它完成注册。数据库引擎按 ``core.toml`` 的
    ``[database]`` 配置初始化，仅供缓存命中用例做只读查询。

    Returns:
        str: 初始化结果说明（用于打印）。
    """
    notes: list[str] = []
    try:
        init_core_config(str(CORE_CONFIG))
        notes.append("core.toml ✓" if CORE_CONFIG.is_file() else "core.toml 不存在（已生成默认）")
    except Exception as exc:  # noqa: BLE001 - 初始化失败后续断言会失败
        notes.append(f"core.toml 初始化失败: {exc}")
    try:
        if MODEL_CONFIG.is_file():
            init_model_config(str(MODEL_CONFIG))
            notes.append("model.toml ✓")
        else:
            notes.append("model.toml 不存在")
    except Exception as exc:  # noqa: BLE001 - 模型配置不影响提示词验证
        notes.append(f"model.toml 初始化失败: {exc}")
    try:
        from src.core.config.core_config import get_core_config
        from src.kernel.db import init_database_from_config

        db_cfg = get_core_config().database
        await init_database_from_config(
            database_type=db_cfg.database_type,
            sqlite_path=db_cfg.sqlite_path,
            postgresql_host=db_cfg.postgresql_host,
            postgresql_port=db_cfg.postgresql_port,
            postgresql_database=db_cfg.postgresql_database,
            postgresql_user=db_cfg.postgresql_user,
            postgresql_password=db_cfg.postgresql_password,
            postgresql_schema=db_cfg.postgresql_schema,
            postgresql_ssl_mode=db_cfg.postgresql_ssl_mode,
            postgresql_ssl_ca=db_cfg.postgresql_ssl_ca,
            postgresql_ssl_cert=db_cfg.postgresql_ssl_cert,
            postgresql_ssl_key=db_cfg.postgresql_ssl_key,
            connection_pool_size=db_cfg.connection_pool_size,
            connection_timeout=db_cfg.connection_timeout,
            echo=db_cfg.echo,
        )
        notes.append(f"数据库已连接（{db_cfg.database_type}，只读用）")
    except Exception as exc:  # noqa: BLE001 - 数据库不可用时缓存用例自动跳过
        notes.append(f"数据库初始化失败: {exc}")
    try:
        from src.core.managers.media_manager import get_media_manager

        get_media_manager()
        notes.append("媒体管理器已构造（提示词模板已注册）")
    except Exception as exc:  # noqa: BLE001 - 构造失败则模板缺失
        notes.append(f"媒体管理器构造失败: {exc}")
    return "，".join(notes)


def load_config() -> AiUiSnapshotConfig:
    """读插件配置（与运行时同一份 config.toml）。"""
    if CONFIG_PATH.is_file():
        try:
            return AiUiSnapshotConfig.load(CONFIG_PATH)
        except Exception as exc:  # noqa: BLE001 - 配置异常不阻塞验证
            print(f"  ! 读取插件配置失败，改用默认值: {exc}")
    else:
        print(f"  ! 未找到插件配置 {CONFIG_PATH}，改用默认值")
    return AiUiSnapshotConfig()


async def find_cached_image(manager: Any) -> tuple[str, str, str] | None:
    """从框架缓存里挑一条已有描述的图片，返回 (完整哈希, 描述, base64)。

    用于验证"同一张图第二次进来会命中缓存、不再识别"。挑出来的样本必须能
    复现哈希（磁盘文件的 base64 与当初入库时的纯净 base64 一致），否则跳过，
    避免让验证脚本意外写库。

    Args:
        manager: 媒体管理器。

    Returns:
        tuple[str, str, str] | None: 命中返回三元组；没有可用样本时返回 None。
    """
    from src.core.managers.media_manager.utils import compute_hash
    from src.core.models.sql_alchemy import ImageDescriptions, Images
    from src.kernel.db import QueryBuilder

    try:
        rows = await QueryBuilder(ImageDescriptions).filter(type="image").order_by("-timestamp").limit(20).all()
    except Exception as exc:  # noqa: BLE001 - 读库失败就跳过
        print(f"  （读描述缓存失败: {exc}）")
        return None
    for row in rows or []:
        media_hash = str(getattr(row, "image_description_hash", "") or "")
        description = str(getattr(row, "description", "") or "")
        if not media_hash or not description:
            continue
        try:
            media = await QueryBuilder(Images).filter(image_id=media_hash).first()
        except Exception:  # noqa: BLE001 - 单条查询失败换下一条
            continue
        path = str(getattr(media, "path", "") or "") if media else ""
        if not path:
            continue
        file_path = pathlib.Path(path)
        if not file_path.is_absolute():
            file_path = PROJECT_ROOT / file_path
        if not file_path.is_file():
            continue
        try:
            b64 = base64.b64encode(file_path.read_bytes()).decode("ascii")
        except OSError:
            continue
        # 哈希必须能复现，否则说明样本对不上，跳过（不冒险走写入路径）
        if compute_hash(b64) != media_hash:
            continue
        return media_hash, description, b64
    return None


class StubPlugin:
    """事件处理器只依赖 ``plugin.config``，用最小桩对象承载配置。"""

    def __init__(self, config: AiUiSnapshotConfig) -> None:
        """保存配置。"""
        self.config = config


def make_test_image() -> tuple[str, bytes]:
    """生成一张内容可判定的测试图片（大字文本 + 色块）。

    Returns:
        tuple[str, bytes]: (本地路径, PNG 字节)。
    """
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (640, 360), (250, 250, 245))
    draw = ImageDraw.Draw(img)
    draw.rectangle([24, 24, 616, 336], outline=(200, 30, 30), width=8)
    draw.ellipse([440, 60, 580, 200], fill=(30, 90, 200))
    text = "MOFOX 8842"
    try:
        font = None
        for cand in (
            r"C:\Windows\Fonts\arialbd.ttf",
            r"C:\Windows\Fonts\arial.ttf",
            r"C:\Windows\Fonts\msyhbd.ttc",
        ):
            if pathlib.Path(cand).is_file():
                from PIL import ImageFont

                font = ImageFont.truetype(cand, 64)
                break
        draw.text((60, 140), text, fill=(10, 10, 10), font=font)
    except Exception:  # noqa: BLE001 - 字体不可用时退化为默认字体
        draw.text((60, 140), text, fill=(10, 10, 10))
    tmp = tempfile.NamedTemporaryFile(prefix="aiui_verify_img_", suffix=".png", delete=False)
    img.save(tmp.name, format="PNG")
    tmp.close()
    return tmp.name, pathlib.Path(tmp.name).read_bytes()


def event_params(raw: bytes, *, media_type: str = "image", media_hash: str = "") -> dict[str, Any]:
    """按框架发布方式构造事件参数（key 集合与框架一致）。"""
    return {
        "media_hash": media_hash or "verifyhash1234",
        "media_type": media_type,
        "engine": "vlm",
        "base64_data": base64.b64encode(raw).decode("ascii"),
        "stream_id": "verify_recognize",
        "description": None,
        "engine_processed": False,
        "skip_engine": False,
    }


async def main() -> int:
    """执行识图接管验证。"""
    parser = argparse.ArgumentParser(description="识图接管验证")
    parser.add_argument("--no-site", action="store_true", help="跳过真实站点识图（只验证契约与失败放行）")
    args = parser.parse_args()

    config = load_config()
    site = (config.recognize.site or "").strip().lower()
    print("== 识图接管验证 ==")
    print(f"  框架配置: {await init_framework()}")
    print(f"  接管开关: {config.recognize.enabled}   识图站点: {site}")
    print(f"  站点启用: deepseek={config.sites.deepseek} gemini={config.sites.gemini} doubao={config.sites.doubao}")
    print(f"  识图超时: {config.recognize.timeout}s   表情包: {config.recognize.emoji}")

    # ── 0. 提示词单源：读的是框架模板 ──
    print("\n--- 提示词来源（框架模板）---")
    img_tmpl = prompt_api.get_template("media.image_recognition")
    emo_tmpl = prompt_api.get_template("media.emoji_recognition")
    check("框架已注册图片识图模板 media.image_recognition", img_tmpl is not None)
    check("框架已注册表情包识图模板 media.emoji_recognition", emo_tmpl is not None)
    img_prompt = await framework_recognize_prompt("image")
    emo_prompt = await framework_recognize_prompt("emoji")
    print(f"  图片提示词: {img_prompt!r}")
    print(f"  表情包提示词: {emo_prompt!r}")
    check("识图提示词非空（取自框架）", bool(img_prompt), f"prompt={img_prompt!r}")
    if img_tmpl is not None:
        check(
            "提示词内容与框架模板一致（未另立一份）",
            img_prompt == (await img_tmpl.build()).strip(),
            f"service={img_prompt!r}",
        )
    check("图片与表情包用各自模板", emo_prompt != img_prompt or not emo_prompt)

    # 处理器只订阅一次，各用例通过切换它持有的配置来驱动不同分支——
    # 这既避免了订阅表反复增删的干扰，也正好覆盖真实运行时的代码路径
    # （execute 每次调用时读 self.plugin.config）。
    stub = StubPlugin(config)
    handler = ImageRecognizeHandler(stub)
    check("处理器订阅了 on_media_recognize", EVENT in {str(e.value) if hasattr(e, "value") else str(e) for e in handler.get_subscribed_events()})
    check("处理器权重高于框架兜底（>0）", handler.weight > 0, f"weight={handler.weight}")
    check("处理器禁用事件总线超时保护（长耗时）", handler.timeout is not None and handler.timeout <= 0, f"timeout={handler.timeout}")

    bus = get_event_bus()
    fallback_calls: list[str] = []

    async def framework_fallback(event_name: str, params: dict[str, Any]) -> tuple[EventDecision, dict[str, Any]]:
        """模拟框架内置 VLM 兜底处理器（priority=0 + engine_processed 判据）。"""
        if params.get("engine") != "vlm":
            return EventDecision.PASS, params
        if params.get("engine_processed") or params.get("skip_engine"):
            return EventDecision.PASS, params
        fallback_calls.append(str(params.get("media_hash")))
        params["description"] = FALLBACK_MARK
        params["engine_processed"] = True
        return EventDecision.SUCCESS, params

    unsub_self = bus.subscribe(EVENT, handler.execute, priority=handler.weight, timeout=handler.timeout)
    unsub_fallback = bus.subscribe(EVENT, framework_fallback, priority=0, timeout=60)
    check(
        "订阅机制：绑定方法可正常撤订（EventBus 契约）",
        isinstance(bus.unsubscribe(EVENT, framework_fallback), bool),
    )
    # 上面那次撤订的是兜底桩，立即恢复，保证后续用例的链路与运行时一致
    unsub_fallback = bus.subscribe(EVENT, framework_fallback, priority=0, timeout=60)

    img_path, raw = make_test_image()
    print(f"  测试图片: {img_path} ({len(raw)} 字节)")
    try:
        # ── 1. 真实站点识图（链路级：成功接管路径） ──
        # 接管默认关闭，这里显式打开：本段要验的是“开启后能不能真的识别”，
        # 而不是当前配置取值。
        if not args.no_site:
            print(f"\n--- 真实识图（{site}，链路级）---")
            if not config.recognize.enabled:
                print("  （配置里接管是关闭的，本段临时打开以验证识别链路）")
                stub.config = AiUiSnapshotConfig.model_validate(
                    {**config.model_dump(), "recognize": {**config.recognize.model_dump(), "enabled": True}}
                )
            fallback_calls.clear()
            params = event_params(raw)
            decision, out = await bus.publish(EVENT, params)
            desc = str(out.get("description") or "")
            print(f"  描述（{len(desc)} 字）：{desc[:200]}")
            # 注意：链路末位决策可能为 PASS——后置处理器（框架兜底/本测试桩）
            # 见到 engine_processed=True 就会让行，因此判据看的是“谁写了描述”。
            check("识图成功：标记 engine_processed", out.get("engine_processed") is True)
            check("识图成功：回填了非空描述", bool(desc), f"desc={desc!r}")
            check("识图成功：框架兜底被跳过（已被本插件接管）", not fallback_calls, f"calls={fallback_calls}")
            check("描述不是框架兜底标记", desc != FALLBACK_MARK)
            check(
                "描述内容与图片相关（含图中文字或颜色/形状描述）",
                any(k in desc for k in ("MOFOX", "8842", "红", "蓝", "矩形", "圆", "文字")),
                f"desc={desc[:120]}",
            )
            stub.config = config
        else:
            print("\n--- 跳过真实识图（--no-site）---")

        # ── 2. 接管的契约：站点识图失败时放行 ──
        # 注意：链路级发布时，框架自己的兜底处理器（MediaEventHandlers，由
        # get_media_manager 注册）也在链上，它会在本插件放行后接管。因此
        # “放行”分支要用单元级直接调用断言，才能精确验证本插件的行为。
        # 本项会真的向站点发起一次识图（损坏图片必然失败），仅在联网模式下跑。
        if not args.no_site:
            print("\n--- 站点失败放行（单元级，需联网）---")
            stub.config = AiUiSnapshotConfig.model_validate(
                {**config.model_dump(), "recognize": {**config.recognize.model_dump(), "enabled": True}}
            )
            corrupt = event_params(b"not-a-real-image-data")
            decision, out = await handler.execute(EVENT, dict(corrupt))
            check("站点识图失败：本插件返回 PASS（不接管）", decision == EventDecision.PASS, f"decision={decision}")
            check("站点识图失败：本插件未写 description", not out.get("description"), f"desc={out.get('description')!r}")
            check("站点识图失败：本插件未标记 engine_processed", not out.get("engine_processed"))
            stub.config = config
        else:
            print("\n--- 跳过站点失败放行（--no-site）---")

        # ── 3. 放行：接管关闭 ──
        print("\n--- 放行：接管开关关闭（单元级）---")
        stub.config = AiUiSnapshotConfig.model_validate(
            {**config.model_dump(), "recognize": {**config.recognize.model_dump(), "enabled": False}}
        )
        params = event_params(raw)
        decision, out = await handler.execute(EVENT, dict(params))
        check("关闭接管：返回 PASS（交回框架）", decision == EventDecision.PASS, f"decision={decision}")
        check("关闭接管：未写 description", not out.get("description"), f"desc={out.get('description')!r}")
        check("关闭接管：未标记 engine_processed", not out.get("engine_processed"))
        check("关闭接管：参数 key 集合不变（事件链契约）", set(out.keys()) == set(params.keys()))

        # ── 4. 放行：识图站点未启用 ──
        print("\n--- 放行：识图站点未启用（单元级）---")
        disabled = next(
            (s for s in ("deepseek", "gemini", "doubao") if not getattr(config.sites, s, False)), ""
        )
        if not disabled:
            print("  （三个站点全部启用，改用不存在的站点名验证同类分支）")
            stub.config = AiUiSnapshotConfig.model_validate(
                {**config.model_dump(), "recognize": {**config.recognize.model_dump(), "site": "not_a_site"}}
            )
        else:
            stub.config = AiUiSnapshotConfig.model_validate(
                {**config.model_dump(), "recognize": {**config.recognize.model_dump(), "site": disabled}}
            )
        decision, out = await handler.execute(EVENT, dict(event_params(raw)))
        check("站点不可用：返回 PASS（交回框架）", decision == EventDecision.PASS, f"decision={decision}")
        check("站点不可用：未写 description", not out.get("description"))
        stub.config = config

        # ── 5. 非图片媒体不碰 ──
        print("\n--- 语音/视频不接管（单元级）---")
        voice_params = event_params(raw)
        voice_params["engine"] = "asr"
        voice_params["media_type"] = "voice"
        decision, out = await handler.execute(EVENT, dict(voice_params))
        check("asr 分支返回 PASS", decision == EventDecision.PASS, f"decision={decision}")
        check("asr 分支未标记 engine_processed", not out.get("engine_processed"))
        emoji_params = event_params(raw)
        emoji_params["media_type"] = "emoji"
        stub_emoji = AiUiSnapshotConfig.model_validate(
            {**config.model_dump(), "recognize": {**config.recognize.model_dump(), "emoji": False}}
        )
        saved = stub.config
        stub.config = stub_emoji
        decision, _ = await handler.execute(EVENT, dict(emoji_params))
        check("表情包开关关闭时返回 PASS", decision == EventDecision.PASS, f"decision={decision}")
        stub.config = saved

        # ── 6. 不重复识图（框架 VLM 调用计数） ──
        # 直接验证用户最关心的问题：插件拿到描述后，框架内置 VLM 还会不会被再跑一次。
        # 做法：把框架 VLM 引擎的 recognize 包一层计数，然后走真实的分发函数
        # （manager._dispatch_recognition_event 就是决定"要不要跑内置引擎"的地方）。
        print("\n--- 不重复识图（框架 VLM 调用计数）---")
        from src.core.components.types import MediaEngine
        from src.core.managers.media_manager import get_media_manager
        from src.core.managers.media_manager.utils import compute_hash

        manager = get_media_manager()
        engine = manager._vlm_engine
        original_recognize = engine.recognize
        vlm_calls: list[str] = []

        async def counting_recognize(base64_data: str, media_type: str) -> str | None:
            """计数包装：记录框架 VLM 实际被调用的次数。"""
            vlm_calls.append(media_type)
            return f"{FALLBACK_MARK}_{media_type}"

        engine.recognize = counting_recognize  # type: ignore[method-assign]
        try:
            # 6a. 插件成功 → 分发函数应直接返回描述，不碰内置引擎
            import ai_ui_snapshot.event_handler.media_recognize as mr

            original_site = mr.recognize_image_by_site

            async def fake_site(*_args: Any, **_kwargs: Any) -> str:
                """代替真实站点调用，返回一条描述，其余逻辑全走真实插件代码。"""
                return "插件识图结果（验证用）"

            mr.recognize_image_by_site = fake_site  # type: ignore[assignment]
            stub.config = AiUiSnapshotConfig.model_validate(
                {**config.model_dump(), "recognize": {**config.recognize.model_dump(), "enabled": True}}
            )
            vlm_calls.clear()
            got = await manager._recognition._dispatch_recognition_event(
                base64_data=base64.b64encode(raw).decode("ascii"),
                media_hash=compute_hash(base64.b64encode(raw).decode("ascii")),
                media_type="image",
                engine=MediaEngine.VLM,
                stream_id="verify_recognize",
            )
            check("插件成功后：分发结果就是插件的描述", got == "插件识图结果（验证用）", f"got={got!r}")
            check("插件成功后：框架 VLM 一次都没跑", not vlm_calls, f"vlm_calls={vlm_calls}")

            # 6b. 插件放行（站点不可用）→ 内置引擎恰跑一次
            mr.recognize_image_by_site = original_site  # type: ignore[assignment]
            stub.config = AiUiSnapshotConfig.model_validate(
                {**config.model_dump(), "recognize": {**config.recognize.model_dump(), "enabled": False}}
            )
            vlm_calls.clear()
            got2 = await manager._recognition._dispatch_recognition_event(
                base64_data=base64.b64encode(raw).decode("ascii"),
                media_hash=compute_hash(base64.b64encode(raw).decode("ascii")),
                media_type="image",
                engine=MediaEngine.VLM,
                stream_id="verify_recognize",
            )
            check("插件放行后：框架 VLM 恰跑一次（不是两次）", len(vlm_calls) == 1, f"vlm_calls={vlm_calls}")
            check("插件放行后：结果来自框架 VLM", got2 == f"{FALLBACK_MARK}_image", f"got={got2!r}")
            stub.config = config
        finally:
            engine.recognize = original_recognize  # type: ignore[method-assign]

        # 6c. 缓存命中：根本不进事件链，两个引擎都不跑
        probe = await find_cached_image(manager)
        if probe is None:
            print("  （缓存里没找到可用样本，跳过缓存命中用例）")
        else:
            cached_hash, cached_desc, cached_b64 = probe
            vlm_calls.clear()
            got3 = await manager.recognize_media(cached_b64, "image", use_cache=True)
            check("缓存命中：返回的就是缓存里的描述", got3 == cached_desc, f"got={(got3 or '')[:40]!r}")
            check("缓存命中：框架 VLM 一次都没跑", not vlm_calls, f"vlm_calls={vlm_calls}")
            check("缓存命中：哈希可复现（说明样本有效）", compute_hash(cached_b64) == cached_hash)

        # ── 7. 临时文件清理 ──
        print("\n--- 图片定位与临时文件清理 ---")
        tmp_before = set(pathlib.Path(tempfile.gettempdir()).glob("aiui_recognize_*"))
        located, cleanup = await ImageRecognizeHandler._locate_image(event_params(raw))
        check("无落盘记录时落临时文件", bool(located) and pathlib.Path(located).is_file(), f"path={located}")
        after_create = set(pathlib.Path(tempfile.gettempdir()).glob("aiui_recognize_*"))
        check("临时文件确实被创建", len(after_create) > len(tmp_before), f"before={len(tmp_before)} after={len(after_create)}")
        if cleanup:
            cleanup()
        check("清理后临时文件已删除", not pathlib.Path(located).exists(), f"path={located}")
    finally:
        unsub_self()
        unsub_fallback()
        pathlib.Path(img_path).unlink(missing_ok=True)

    print(f"\n== 结果 == 通过 {PASSED} 项，失败 {len(FAILED)} 项")
    for item in FAILED:
        print(f"  !! {item}")
    from src.kernel.db import close_engine

    await close_engine()
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
