"""特典过滤器：把非正片文件排除在整理之外，规则可在 WebUI 里编辑。

宿主在整理（rename）前调用 ``classify(path)`` 决定每个文件怎么处理：

- ``media``：正片，参与改名；
- ``subtitle``：字幕，跟随正片改名；
- ``ignore``：不处理（既不进改名列表，也不拖累种子的完成判定）。

判定顺序：先按扩展名定类（媒体 / 字幕 / 其它），再用「正片白名单」放行，
最后用「特典目录 + 特典文件名规则」拦下。

特典目录与白名单是短的标签，放在插件的「插件选项」表单里；
特典文件名规则是长正则，改由前端挂载点（设置 → 特典规则）用文本域编辑，
一行一条，存在 ``config/plugin-data/special-filter/rules.json``。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from ab_sdk import Plugin, points, provider
from ab_sdk.rename import MediaFiles, MediaKind

_MEDIA_SUFFIXES = frozenset({".mkv", ".mp4"})
_SUBTITLE_SUFFIXES = frozenset({".ass", ".srt"})
_RULES_FILE = "rules.json"

#: 默认特典目录（路径分段包含匹配，如 ``映像特典``、``特典CD``、``SPs``）
_DEFAULT_DIRS = ["映像特典", "特典", "SPs", "Extras", "Bonus", "Scan"]

#: 默认特典文件名规则（逐条正则，命中即忽略）
_DEFAULT_PATTERNS = [
    r"ncop|nced|creditless",
    r"web\s*preview|(?<![\w])preview(?![\w])",
    r"[\[\(【]\s*(?:nco?p|nced|cm|pv|sp|menu|scans?|op|ed)(?:\s*[\w\-]{1,8})?\s*[\]\)】]",
    r"(?<![\w])(?:nco?p|nced|cm|pv|op|ed)\d{1,2}(?![\w])",
    # Info01 / Menu01 / Digest02 这类「非剧集」标记；不拦会让整个种子卡在解析失败上
    r"(?<![\w])(?:info|menu|digest|recap|trailer)\d{1,2}(?![\w])",
    r"全\d{1,4}[话話集]",
    r"番組宣伝|宣伝映像|予告|振り返り|ダイジェスト|メイキング",
    r"ノンクレ(?:ジット)?|ピクチャードラマ|インタビュー|クリップ",
    r"tv\s*special|tv\s*スペシャル|キャラクターpv|チャラクターpv|cm集|pv\s*・\s*cm",
    # OAD01 / OAD02 这类没有集数的 OVA 单集；不拦会让整个种子卡在解析失败上
    r"(?<![\w])OAD\d{1,2}(?![\w])",
]


def _compile(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.I)


def _clean_patterns(values: list[str]) -> list[str]:
    """去空行、去重、逐条校验；非法正则抛 ``ValueError``。"""
    cleaned: list[str] = []
    for value in values:
        pattern = value.strip()
        if not pattern or pattern in cleaned:
            continue
        try:
            _compile(pattern)
        except re.error as exc:
            raise ValueError(f"无效正则 {pattern!r}：{exc}") from exc
        cleaned.append(pattern)
    return cleaned


class Options(BaseModel):
    """「插件选项」里展示的短标签字段。"""

    dirs: list[str] = Field(
        default=_DEFAULT_DIRS,
        title="特典目录",
        description="路径分段包含这些文字即视为特典（如 映像特典、特典CD、SPs）。点 × 删除即不再匹配它。",
    )
    keep_patterns: list[str] = Field(
        default=[],
        title="正片白名单",
        description="最先判断：命中则强制当正片，用来救回被上面规则误挡的番（如 SPY×FAMILY）。",
    )

    @field_validator("dirs")
    @classmethod
    def _clean_dirs(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item.strip()]

    @field_validator("keep_patterns")
    @classmethod
    def _compiled_patterns(cls, value: list[str]) -> list[str]:
        return _clean_patterns(value)


class RulesPayload(BaseModel):
    rules: list[str] = Field(default_factory=list)


class SpecialMediaFiles:
    """``media_files`` 扩展点的实现：每次判定都读插件里的最新规则。"""

    def __init__(self, plugin: SpecialFilter) -> None:
        self._plugin = plugin

    def classify(self, path: str) -> MediaKind:
        plugin = self._plugin
        posix = path.replace("\\", "/")
        name = posix.rsplit("/", 1)[-1]
        suffix = ""
        if "." in name:
            suffix = "." + name.rsplit(".", 1)[-1].lower()

        if suffix in _SUBTITLE_SUFFIXES:
            kind: MediaKind = "subtitle"
        elif suffix in _MEDIA_SUFFIXES:
            kind = "media"
        else:
            return "ignore"

        if any(pattern.search(name) for pattern in plugin.keep_patterns):
            return kind

        directories = posix.split("/")[:-1]
        if any(
            marker in directory.lower()
            for directory in directories
            for marker in plugin.special_dirs
        ):
            return "ignore"
        if any(pattern.search(name) for pattern in plugin.patterns):
            return "ignore"
        return kind


class SpecialFilter(Plugin[Options]):
    """插件入口。"""

    config_model = Options

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self._rules: list[str] = list(_DEFAULT_PATTERNS)
        self._patterns: tuple[re.Pattern[str], ...] = tuple(
            _compile(pattern) for pattern in _DEFAULT_PATTERNS
        )
        self._special_dirs: tuple[str, ...] = ()
        self._keep_patterns: tuple[re.Pattern[str], ...] = ()

    async def setup(self) -> None:
        self._rules = await self._load_rules()
        self._rebuild_rules()
        self._rebuild_options()

    @property
    def rules(self) -> list[str]:
        return list(self._rules)

    @property
    def patterns(self) -> tuple[re.Pattern[str], ...]:
        return self._patterns

    @property
    def special_dirs(self) -> tuple[str, ...]:
        return self._special_dirs

    @property
    def keep_patterns(self) -> tuple[re.Pattern[str], ...]:
        return self._keep_patterns

    def _rebuild_rules(self) -> None:
        self._patterns = tuple(_compile(pattern) for pattern in self._rules)

    def _rebuild_options(self) -> None:
        raw = self.ctx.config
        if raw is None:
            self._special_dirs = tuple(marker.lower() for marker in _DEFAULT_DIRS)
            self._keep_patterns = ()
            return
        options = cast(Options, raw)
        self._special_dirs = tuple(marker.lower() for marker in options.dirs)
        self._keep_patterns = tuple(
            _compile(pattern) for pattern in options.keep_patterns
        )

    def _rules_path(self) -> Path:
        return self.ctx.data_dir / _RULES_FILE

    async def _load_rules(self) -> list[str]:
        try:
            payload = json.loads(self._rules_path().read_text(encoding="utf-8"))
        except OSError, ValueError:
            return list(_DEFAULT_PATTERNS)
        rules = payload.get("rules") if isinstance(payload, dict) else None
        if not isinstance(rules, list):
            return list(_DEFAULT_PATTERNS)
        try:
            return _clean_patterns([str(item) for item in rules])
        except ValueError:
            return list(_DEFAULT_PATTERNS)

    async def _save_rules(self, rules: list[str]) -> None:
        self._rules = rules
        self._rebuild_rules()
        try:
            self._rules_path().write_text(
                json.dumps({"rules": rules}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:  # 落盘失败不影响本次生效，但要能看到原因
            self.ctx.log.warning("写入特典规则失败：%s", exc)

    @provider(points.MEDIA_FILES, id="special-filter")
    def media_files(self) -> MediaFiles:
        self._rebuild_options()
        return SpecialMediaFiles(self)

    @provider(points.API_ROUTER, id="api")
    def api(self) -> APIRouter:
        router = APIRouter()

        @router.get("/rules")
        async def get_rules() -> dict[str, Any]:
            return {"rules": self.rules, "defaults": list(_DEFAULT_PATTERNS)}

        @router.post("/rules")
        async def set_rules(payload: RulesPayload) -> dict[str, Any]:
            try:
                rules = _clean_patterns(payload.rules)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            await self._save_rules(rules)
            return {"rules": rules}

        return router
