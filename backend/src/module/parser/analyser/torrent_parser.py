import logging
import re
from collections import OrderedDict
from pathlib import Path

from module.models import EpisodeFile, SubtitleFile

logger = logging.getLogger(__name__)

# LRU cache for torrent_parser results to avoid repeated regex parsing
_PARSER_CACHE_MAX_SIZE = 512
_parser_cache: OrderedDict[tuple, EpisodeFile | SubtitleFile | None] = OrderedDict()

PLATFORM = "Unix"

RULES = [
    r"(.*) - (\d{1,4}(?:\.\d{1,2})?(?!\d|p))(?:v\d{1,2})?(?: )?(?:END)?(.*)",
    r"(.*)[\[\ E](\d{1,4}(?:\.\d{1,2})?)(?:v\d{1,2})?(?: )?(?:END)?[\]\ ](.*)",
    r"(.*)\[(?:第)?(\d{1,4}(?:\.\d{1,2})?)[话集話](?:END)?\](.*)",
    r"(.*)第?(\d{1,4}(?:\.\d{1,2})?)[话話集](?:END)?(.*)",
    r"(.*)(?:S\d{2})?EP?(\d{1,4}(?:\.\d{1,2})?)(.*)",
]

COMPILED_RULES = [re.compile(rule, re.I) for rule in RULES]

SUBTITLE_LANG = {
    "zh-tw": ["tc", "cht", "繁", "zh-tw"],
    "zh": ["sc", "chs", "简", "zh"],
}


def get_path_basename(torrent_path: str) -> str:
    """
    Returns the basename of a path string.

    :param torrent_path: A string representing a path to a file.
    :type torrent_path: str
    :return: A string representing the basename of the given path.
    :rtype: str
    """
    return Path(torrent_path).name


_GROUP_SPLIT_RE = re.compile(r"[\[\]()【】（）]")


def get_group(group_and_title) -> tuple[str | None, str]:
    n = [x for x in _GROUP_SPLIT_RE.split(group_and_title) if x]
    if len(n) > 1:
        if re.match(r"\d+", n[1]):
            return None, group_and_title
        return n[0], n[1]
    else:
        return None, n[0]


def get_season_and_title(season_and_title) -> tuple[str, int]:
    title = re.sub(r"([Ss]|Season )\d{1,3}", "", season_and_title).strip()
    match = re.search(r"([Ss]|Season )(\d{1,3})", season_and_title, re.I)
    season = int(match.group(2)) if match else 1
    return title, season


def get_subtitle_lang(subtitle_name: str) -> str | None:
    for key, value in SUBTITLE_LANG.items():
        for v in value:
            if v in subtitle_name.lower():
                return key
    return None


# 电影文件名通常没有集数标记，用于剥离方括号标签（分辨率/来源/字幕组等），
# 剩余文本视为标题
_BRACKET_RE = re.compile(r"[\[\(（【].*?[\]\)）】]")


def _parse_movie_file(
    torrent_path: str, torrent_name: str | None, file_type: str
) -> EpisodeFile | SubtitleFile | None:
    """解析电影/剧场版文件：不要求集数标记，Title (Year)/Title (Year).ext 布局
    下文件名本身即完整标题。"""
    media_path = get_path_basename(torrent_path)
    match_name = torrent_name if torrent_name is not None else media_path
    group, _ = get_group(match_name)
    stem = Path(match_name).stem
    title = _BRACKET_RE.sub(" ", stem)
    title = re.sub(r"\s+", " ", title).strip(" -/")
    if not title:
        title = Path(media_path).stem
    suffix = Path(torrent_path).suffix
    if file_type == "media":
        return EpisodeFile(
            media_path=torrent_path,
            group=group,
            title=title,
            season=1,
            episode=1,
            suffix=suffix,
            episode_type="movie",
        )
    elif file_type == "subtitle":
        language = get_subtitle_lang(media_path)
        return SubtitleFile(
            media_path=torrent_path,
            group=group,
            title=title,
            season=1,
            episode=1,
            language=language,  # type: ignore[arg-type]
            suffix=suffix,
            episode_type="movie",
        )
    return None


def torrent_parser(
    torrent_path: str,
    torrent_name: str | None = None,
    season: int | None = None,
    file_type: str = "media",
    episode_type: str = "episode",
) -> EpisodeFile | SubtitleFile | None:
    # Check cache first to avoid repeated regex parsing
    cache_key = (torrent_path, torrent_name, season, file_type, episode_type)
    if cache_key in _parser_cache:
        # Move to end to mark as recently used
        _parser_cache.move_to_end(cache_key)
        return _parser_cache[cache_key]

    result = _torrent_parser_impl(
        torrent_path, torrent_name, season, file_type, episode_type
    )

    # Store in cache with LRU eviction
    _parser_cache[cache_key] = result
    if len(_parser_cache) > _PARSER_CACHE_MAX_SIZE:
        _parser_cache.popitem(last=False)  # Remove oldest item

    return result


# 集数只采纳下面这些「带标记」的规则：第N話 / #N / SxxEyy / Season x Episode y。
# episode.trailing（结尾数字）与 episode.bare（孤立数字）会把标题里的数字当成
# 集数（`[VCB-Studio] Hibike! Euphonium 2 [01]` 会被读成 2），因此不作为依据。
_STRONG_EPISODE_RULES = frozenset(
    {
        "episode.chinese",
        "episode.explicit",
        "episode.season-compact",
        "episode.season-words",
        "episode.season-range-compact",
        "episode.season-range-words",
    }
)


_SUBTITLE_BRACKET = re.compile(r"[\[(【]([^\])】]{1,48})[\])】]")
# 只把「整段括号都是这些词」的括号当作字幕/语言标签，避免把 [1080p AVC AAC] 当成标签。
_SUBTITLE_TOKENS = frozenset(
    {
        "SC",
        "TC",
        "JPSC",
        "JPTC",
        "CHS",
        "CHT",
        "CHS_JP",
        "CHT_JP",
        "GB",
        "BIG5",
        "简",
        "繁",
        "简繁",
        "简繁内封",
        "内封",
        "字幕",
    }
)


def _subtitle_tail_tags(name: str) -> tuple[str, ...]:
    """收集纯标签括号里的词，例如 ``[SC_TC]`` -> ``("SC", "TC")``。"""
    tags: list[str] = []
    for raw in _SUBTITLE_BRACKET.findall(name):
        parts = [part for part in re.split(r"[\s_\-/&+,]+", raw.strip()) if part]
        if not parts:
            continue
        if all(
            part.upper() in _SUBTITLE_TOKENS or part in _SUBTITLE_TOKENS
            for part in parts
        ):
            tags.extend(parts)
    return tuple(tags)


def _strip_subtitle_tail(title: str, tags: tuple[str, ...]) -> str:
    """标题结尾与标签词逐词一致时才剥离，其余情况原样返回。"""
    if not tags:
        return title
    words = title.split()
    if len(tags) > len(words):
        return title
    tail = [word.strip("[]()【】").upper() for word in words[-len(tags) :]]
    if tail != [tag.upper() for tag in tags]:
        return title
    return " ".join(words[: -len(tags)])


def _plausible_episode(episode) -> bool:
    """集数只接受整数或半集（``12.5``），避免把 ``S01E01.2026`` 读成 1.2026。"""
    if isinstance(episode, float) and episode != int(episode):
        return abs(episode * 2 - round(episode * 2)) < 1e-9
    return 0 <= episode <= 9999


def _episode_rule(trace) -> str:
    """胜出的集数候选来自哪条规则（``candidate_id`` 形如 ``episode.chinese:1:8:12``）。"""
    for decision in getattr(trace, "decisions", ()) or ():
        status = getattr(decision, "status", None)
        if getattr(status, "value", status) != "selected":
            continue
        fields = getattr(decision, "won_fields", ()) or ()
        if any(getattr(field, "value", field) == "episode" for field in fields):
            return str(getattr(decision, "candidate_id", "")).split(":", 1)[0]
    return ""


def _parsed_episode(
    torrent_path: str,
    match_names: list[str],
    season: int | None,
    file_type: str = "media",
) -> EpisodeFile | SubtitleFile | None:
    """用配置的解析器（tokenizer/classic）取集数，取不到就交回旧的五条正则。

    正片与字幕共用这一条路径，两者的标题/季/集因此不会各说各话。
    """
    from module.parser.analyser.selector import (
        parse_configured_release_title_with_trace,
    )
    from module.parser.analyser.tokenizer import MediaType, ReleaseKind

    suffix = Path(torrent_path).suffix
    basename = get_path_basename(torrent_path)
    for candidate in match_names:
        if not candidate:
            continue
        outcome = parse_configured_release_title_with_trace(candidate)
        release = outcome.result
        if release is None or release.episode is None:
            continue
        if not _plausible_episode(release.episode):
            continue
        if not (release.title_zh or release.title_jp or release.title_en):
            continue
        if release.is_mixed_collection:
            continue
        if release.release_kind is not ReleaseKind.SINGLE:
            continue
        if release.media_type not in {MediaType.EPISODE, MediaType.UNKNOWN}:
            continue
        if _episode_rule(outcome.trace) not in _STRONG_EPISODE_RULES:
            continue
        # 标题语言优先级：中 > 日 > 英（自用偏好，默认的 primary_title 是 英 > 中 > 日）
        title: str = release.title_zh or release.title_jp or release.title_en or ""
        tail_tags = _subtitle_tail_tags(candidate) or _subtitle_tail_tags(basename)
        title = _strip_subtitle_tail(title, tail_tags) or title
        parsed_season = season or release.season or 1
        if file_type == "subtitle":
            language = get_subtitle_lang(basename)
            if language is None:
                return None
            return SubtitleFile(
                media_path=torrent_path,
                group=release.group,
                title=title,
                season=parsed_season,
                episode=release.episode,
                language=language,  # type: ignore[arg-type]
                suffix=suffix,
            )
        return EpisodeFile(
            media_path=torrent_path,
            group=release.group,
            title=title,
            season=parsed_season,
            episode=release.episode,
            suffix=suffix,
        )
    return None


def _torrent_parser_impl(
    torrent_path: str,
    torrent_name: str | None = None,
    season: int | None = None,
    file_type: str = "media",
    episode_type: str = "episode",
) -> EpisodeFile | SubtitleFile | None:
    """Internal implementation of torrent_parser without caching."""
    if episode_type == "movie":
        return _parse_movie_file(torrent_path, torrent_name, file_type)
    media_path = get_path_basename(torrent_path)
    match_names = (
        [torrent_name, media_path] if torrent_name is not None else [media_path]
    )
    if file_type in {"media", "subtitle"} and episode_type == "episode":
        parsed = _parsed_episode(torrent_path, match_names, season, file_type)
        if parsed is not None:
            return parsed
    for match_name in match_names:
        for compiled_rule in COMPILED_RULES:
            match_obj = compiled_rule.match(match_name)
            if match_obj:
                group, title = get_group(match_obj.group(1))
                if not season:
                    title, season = get_season_and_title(title)
                else:
                    title, _ = get_season_and_title(title)
                # regex group(2) is always the numeric episode string here (no
                # optional groups); pydantic coerces it to int/float on
                # construction (e.g. "01" -> 1, "48.5" -> 48.5).
                episode: str = match_obj.group(2)
                suffix = Path(torrent_path).suffix
                if file_type == "media":
                    return EpisodeFile(
                        media_path=torrent_path,
                        group=group,
                        title=title,
                        season=season,
                        episode=episode,  # type: ignore[arg-type]
                        suffix=suffix,
                    )
                elif file_type == "subtitle":
                    # `language` may be None for an unrecognized subtitle code;
                    # SubtitleFile requires it, so construction intentionally
                    # raises pydantic.ValidationError in that case.
                    language = get_subtitle_lang(media_path)
                    return SubtitleFile(
                        media_path=torrent_path,
                        group=group,
                        title=title,
                        season=season,
                        language=language,  # type: ignore[arg-type]
                        episode=episode,  # type: ignore[arg-type]
                        suffix=suffix,
                    )
    return None


if __name__ == "__main__":
    ep = torrent_parser(
        "/不时用俄语小声说真心话的邻桌艾莉同学/Season 1/不时用俄语小声说真心话的邻桌艾莉同学 S01E02.mp4"
    )
    print(ep)

    ep = torrent_parser(
        "/downloads/Bangumi/关于我转生变成史莱姆这档事 (2018)/Season 3/[ANi] 關於我轉生變成史萊姆這檔事 第三季 - 48.5 [1080P][Baha][WEB-DL][AAC AVC][CHT].mp4"
    )
    print(ep)

    ep = torrent_parser(
        "/downloads/Bangumi/关于我转生变成史莱姆这档事 (2018)/Season 3/[ANi] 關於我轉生變成史萊姆這檔事 第三季 - 48.5 [1080P][Baha][WEB-DL][AAC AVC][CHT].srt",
        file_type="subtitle",
    )
    print(ep)
