"""Filename cleaner plugin - 里番专用文件名清洗器.

清洗策略：
1. 移除视频扩展名和语言标识
2. 移除日期前缀（如 [251114]）
3. 移除制作组方括号
4. 移除末尾作者方括号
5. 移除 OVA/OAD/THE ANIMATION 标记
6. 移除副标题（～...～、「...」、集数后长文本）
"""

import re

from server.services.parsers.base import ParseContext, ParserPlugin

# ============================================================================
# 视频文件扩展名
# ============================================================================
VIDEO_EXTENSIONS = r"\.(mp4|mkv|avi|wmv|mov|flv|rmvb|ts|m2ts|webm|iso|m4v)$"

# ============================================================================
# 语言标识（扩展名前）
# ============================================================================
LANGUAGE_SUFFIXES = [
    r"\.cht",   # 繁体中文
    r"\.chs",   # 简体中文
    r"\.chi",   # 中文
    r"\.tc",    # Traditional Chinese
    r"\.sc",    # Simplified Chinese
    r"\.jpn",   # 日语
    r"\.jap",   # 日语
    r"\.eng",   # 英语
    r"\.kor",   # 韩语
    r"\.zho",   # 中文 (ISO 639-3)
    r"\.und",   # 未定义
]

# ============================================================================
# 日期前缀模式 [YYMMDD] 或 [YYYYMMDD]
# ============================================================================
DATE_PREFIX_PATTERN = r"^\[(\d{6}|\d{8})\]"

# ============================================================================
# 里番制作组/发布者（扩展列表）
# ============================================================================
KNOWN_GROUPS = [
    # ===== 主要里番制作公司 =====
    r"Queen\s*Bee",
    r"King\s*Bee",
    r"Pink\s*Pineapple",
    r"ピンクパイナップル",
    r"Bunny\s*Walker",
    r"ばにぃうぉ～か～",
    r"Collaboration\s*Works",
    r"Studio\s*Fantasia",
    r"魔人",
    r"Majin",
    r"PoRO",
    r"A1C",
    r"Arms",
    r"Lilith",
    r"BOOTLEG",
    r"T-Rex",
    r"Pixy",
    r"ANIMATED",
    r"Milky",
    r"Pashmina",
    r"GOLD\s*BEAR",
    r"DISCOVERY",
    r"nur",
    r"Suzuki\s*Mirano",
    r"MS\s*Pictures",
    r"Lune",
    r"Seven",
    r"セブン",
    r"ChiChinoya",
    r"ちちのや",
    r"Mary\s*Jane",
    r"メリー・ジェーン",
    r"Celeb",
    r"セレブ",
    r"Breakbottle",
    r"Digital\s*Works",
    r"Green\s*Bunny",
    r"グリーンバニー",
    r"Vanilla",
    r"バニラ",
    r"Animac",
    r"アニマック",
    r"Schoolzone",
    r"スクールゾーン",
    # ===== 字幕组 =====
    r"字幕组",
    r"動畫瘋",
    r"喵萌",
]

# ============================================================================
# 副标题模式（需要移除）
# ============================================================================
SUBTITLE_PATTERNS = [
    r"(?<=\s)～[^～]{2,}～",   # ～副标题～（前面须有空格，避免误删剧名中的波浪号）
    r"(?<=\s)〜[^〜]{2,}〜",   # 〜副标题〜
    r"「[^」]+」",             # 「副标题」
    r"『[^』]+』",             # 『副标题』
]

# ============================================================================
# 集数标记模式（用于定位副标题起始位置）
# ============================================================================
EPISODE_MARKERS_FOR_SUBTITLE = [
    r"第\s*[\d一二三四五六七八九十]+\s*[話话集回章弾幕]",  # 第1話, 第二話
    r"[＃#♯]\s*\d+",                                      # ＃2, #2
    r"[Vv]ol\.?\s*\d+",                                   # Vol.1
    r"前編|後編|前篇|後篇|上巻|下巻",                      # 前編/後編
    r"其[のノ之乃]\s*[\d一二三四五六七八九十弍参肆伍]+",   # 其の弍
]

# ============================================================================
# OVA/动画标记（需要移除）
# ============================================================================
ANIMATION_MARKERS = [
    r"\bOVA\b",
    r"\bOAD\b",
    r"\bONA\b",
    r"\bTHE\s+ANIMATION\b",
    r"\bANIMATION\b",
]


def is_author_bracket(content: str) -> bool:
    """判断方括号内容是否为作者名。

    里番文件名中，作者名通常在末尾方括号内，格式为日文人名（2-8个字符）。
    """
    content = content.strip()

    # 日期格式 -> 不是作者
    if re.fullmatch(r"\d{6}|\d{8}", content):
        return False

    # 制作组 -> 不是作者（由其他逻辑处理）
    for group_pattern in KNOWN_GROUPS:
        if re.search(group_pattern, content, re.I):
            return False

    # 日文人名特征：2-8个字符，包含汉字/平假名/片假名
    if re.fullmatch(r"[\u4e00-\u9fa5\u3040-\u309f\u30a0-\u30ff]{2,8}", content):
        return True

    return False


class CleanerPlugin(ParserPlugin):
    """里番专用文件名清洗插件.

    清洗策略：
    - 移除日期、制作组、作者方括号
    - 移除 OVA/THE ANIMATION 标记
    - 移除副标题（保留集数标记）
    """

    priority = 10
    name = "cleaner"

    def parse(self, ctx: ParseContext) -> ParseContext:
        cleaned = ctx.original_filename

        # 1. 移除视频扩展名
        cleaned = re.sub(VIDEO_EXTENSIONS, "", cleaned, flags=re.I)

        # 2. 移除语言标识
        for pattern in LANGUAGE_SUFFIXES:
            cleaned = re.sub(pattern, "", cleaned, flags=re.I)

        # 3. 移除日期前缀 [251114]
        cleaned = re.sub(DATE_PREFIX_PATTERN, "", cleaned)

        # 4. 移除制作组和作者方括号
        cleaned = self._remove_group_and_author_brackets(cleaned)

        # 5. 移除 OVA/THE ANIMATION 标记
        for pattern in ANIMATION_MARKERS:
            cleaned = re.sub(pattern, " ", cleaned, flags=re.I)

        # 6. 移除副标题（～...～、「...」等）
        for pattern in SUBTITLE_PATTERNS:
            cleaned = re.sub(pattern, " ", cleaned)

        # 7. 移除集数后的副标题文本
        cleaned = self._remove_post_episode_subtitle(cleaned)

        # 8. 规范化空白和分隔符
        cleaned = re.sub(r"[._]+", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned)
        cleaned = cleaned.strip(" -")

        ctx.cleaned_filename = cleaned
        ctx.matched_patterns.append(f"{self.name}:cleaned")

        return ctx

    def _remove_group_and_author_brackets(self, text: str) -> str:
        """移除制作组和作者方括号。"""
        # 移除开头的制作组方括号（日期已移除，第一个方括号是制作组）
        text = re.sub(r"^\[[^\]]+\]", "", text)

        # 移除末尾的作者方括号
        match = re.search(r"\[([^\]]+)\]$", text)
        if match and is_author_bracket(match.group(1)):
            text = text[:match.start()]

        return text

    def _remove_post_episode_subtitle(self, text: str) -> str:
        """移除集数标记后的副标题文本。

        例如：
        - "勇者姫ミリア 第四話 砂漠の町のオークション！" -> "勇者姫ミリア 第四話"
        - "ながちち永井さん Vol.1 むちむちダイエット奮戦記" -> "ながちち永井さん Vol.1"
        """
        # 找到集数标记的位置
        for pattern in EPISODE_MARKERS_FOR_SUBTITLE:
            match = re.search(pattern, text)
            if match:
                # 集数标记结束位置
                ep_end = match.end()
                # 检查集数后是否有副标题（非空白内容）
                remaining = text[ep_end:].strip()
                if remaining:
                    # 有副标题，截断到集数标记结束
                    return text[:ep_end].strip()

        return text
