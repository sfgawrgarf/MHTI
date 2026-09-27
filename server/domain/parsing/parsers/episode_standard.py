"""Standard episode pattern parser (S01E01, EP01, etc.) - 标准集数解析器.

只从文件名解析标准集数；目录名不应改变文件本身的集数语义。
"""

import re

from server.domain.parsing.parsers.base import ParseContext, ParserPlugin

# 标准集数模式（按优先级排序）
STANDARD_PATTERNS = [
    # S01E01 或 S01.E01 格式
    (r"[.\s_-]?[Ss](\d{1,2})[.\s_-]?[Ee](\d{1,3})", "season_episode"),
    # EP01 或 E01 格式（仅集数；前面需要文件名分隔符）
    (r"[.\s_-][Ee][Pp]?(\d{1,3})(?:[.\s_-]|$)", "episode_only"),
    # 集数范围（多集文件取首集）：1-2 / 01-02 / 第1-2話
    # 放在单集模式之后、末尾数字之前，避免范围被当成尾集
    (r"第\s*(\d{1,3})\s*[-~〜～ー]\s*\d{1,3}\s*[話话集回章弾幕]", "episode_range"),
    (r"[.\s_\[\(](\d{1,3})\s*[-~〜～ー]\s*\d{1,3}(?=[\s_\]\)]|$|[.．])", "episode_range"),
    # [01] 格式（仅集数）
    (r"\[(\d{1,3})\]", "episode_only"),
    # 末尾数字: - 01. 或 .01.
    (r"[.\s_-](\d{1,3})[.\s_-]?(?:\[|$|\.(?:mp4|mkv|avi))", "episode_only"),
    # 末尾数字 + 尾部元数据块（年份 / [1080p] / ～副标题～）
    # 例：作品名 01 (2023) [1080p].mp4、作品名 1 ～サブ～.mp4
    (
        r"[.\s_-](\d{1,3})"
        r"(?:\s*[\[\(（【][^\]\)）】]*[\]\)）】]"
        r"|\s*(?:19|20)\d{2}"
        r"|\s*[～〜][^～〜]*[～〜]"
        r"|\s*[.\s_-])*"
        r"[.\s_-]*(?:$|\.(?:mp4|mkv|avi|wmv|mov|flv|rmvb|ts|m2ts|webm|iso|m4v)$)",
        "episode_only",
    ),
]

class EpisodeStandardPlugin(ParserPlugin):
    """标准集数格式解析插件.

    解析优先级：20
    只解析文件名，避免把父目录中的编号误当成媒体集数。
    """

    priority = 20
    name = "episode_standard"

    def should_skip(self, ctx: ParseContext) -> bool:
        return ctx.episode is not None

    def parse(self, ctx: ParseContext) -> ParseContext:
        if self.should_skip(ctx):
            return ctx

        # 从文件名解析
        for pattern, pattern_type in STANDARD_PATTERNS:
            match = re.search(pattern, ctx.cleaned_filename, re.I)
            if match:
                if pattern_type == "season_episode":
                    ctx.season = int(match.group(1))
                    ctx.episode = int(match.group(2))
                elif pattern_type in ("episode_only", "episode_range"):
                    # 范围集数（1-2）取首集，与 SxxE01-E02 命名惯例一致
                    ctx.episode = int(match.group(1))

                if ctx.episode:
                    ctx.matched_patterns.append(f"{self.name}:{pattern_type}")
                    break

        return ctx
