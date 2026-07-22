"""Japanese episode pattern parser - 日语集数解析器（增强版）.

支持功能：
- 完整的汉字数字映射（含所有变体字形）
- 多种日语集数标记形式
- 特别篇/番外篇识别
- 前篇/后篇固定模式
"""

import re

from server.services.parsers.base import ParseContext, ParserPlugin

# ============================================================================
# 日语/汉字数字映射（完整版，覆盖所有已知变体）
# ============================================================================
KANJI_NUMBERS = {
    # ===== 标准汉字数字 =====
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "百": 100, "千": 1000,

    # ===== 日语大写数字（法定/正式文书用）=====
    "壱": 1, "弐": 2, "参": 3, "肆": 4, "伍": 5,
    "陸": 6, "漆": 7, "捌": 8, "玖": 9, "拾": 10,
    "佰": 100, "仟": 1000,

    # ===== 中国繁体大写数字 =====
    "壹": 1, "貳": 2, "參": 3, "叁": 3, "肆": 4, "伍": 5,
    "陆": 6, "柒": 7, "捌": 8, "玖": 9, "拾": 10,

    # ===== 日语古文/变体字形 =====
    "弍": 2,   # 弐的变体
    "弎": 3,   # 参的变体
    "貮": 2,   # 贰的变体
    "贰": 2,   # 简化变体
    "叄": 3,   # 参的变体
    "仨": 3,   # 口语三
    "陆": 6,   # 陸的简化
    "柒": 7,   # 漆的变体（更常用）

    # ===== 全角数字 =====
    "０": 0, "１": 1, "２": 2, "３": 3, "４": 4,
    "５": 5, "６": 6, "７": 7, "８": 8, "９": 9,

    # ===== 罗马数字（常见于动画标题）=====
    "Ⅰ": 1, "Ⅱ": 2, "Ⅲ": 3, "Ⅳ": 4, "Ⅴ": 5,
    "Ⅵ": 6, "Ⅶ": 7, "Ⅷ": 8, "Ⅸ": 9, "Ⅹ": 10,
    "Ⅺ": 11, "Ⅻ": 12,
    # 小写罗马数字
    "ⅰ": 1, "ⅱ": 2, "ⅲ": 3, "ⅳ": 4, "ⅴ": 5,
    "ⅵ": 6, "ⅶ": 7, "ⅷ": 8, "ⅸ": 9, "ⅹ": 10,

    # ===== 带圈数字（用于列表编号）=====
    "①": 1, "②": 2, "③": 3, "④": 4, "⑤": 5,
    "⑥": 6, "⑦": 7, "⑧": 8, "⑨": 9, "⑩": 10,
    "⑪": 11, "⑫": 12, "⑬": 13, "⑭": 14, "⑮": 15,
}

# 所有汉字数字字符（用于正则，自动生成）
KANJI_CHARS = "".join(KANJI_NUMBERS.keys())

# ============================================================================
# 特别篇模式（season=0）
# ============================================================================
SPECIAL_PATTERNS = [
    # 日语特别篇
    r"特別編|特別篇|特别编|特别篇",
    r"番外編|番外篇|番外编|番外篇",
    r"SP編|SP篇|SP",
    # OVA/OAD
    r"OVA|OAD|ONA",
    # 剧场版/总集篇
    r"劇場版|剧场版|総集編|总集编",
]

# ============================================================================
# 固定集数模式（season=1，不需要解析数字）
# ============================================================================
FIXED_EPISODE_PATTERNS = [
    # 前編/後編 系列（日语）
    (r"前編|前篇|前编|上巻|上編|上卷|上集", 1),
    (r"後編|後篇|后编|下巻|下編|下卷|下集", 2),
    (r"中編|中篇|中编|中巻|中卷|中集", 2),
    # 完結編
    (r"完結編|完结编|最終編|最终编", 99),
]

# ============================================================================
# 动态集数模式（需要提取数字）
# ============================================================================
# 构建汉字字符集的正则
_KANJI_CHAR_CLASS = f"[{re.escape(KANJI_CHARS)}]"

DYNAMIC_EPISODE_PATTERNS = [
    # ===== 第X話/集/回/章/弾/幕 =====
    (r"第\s*(\d+)\s*[話话集回章弾幕]", "digit"),
    (rf"第\s*({_KANJI_CHAR_CLASS}+)\s*[話话集回章弾幕]", "kanji"),

    # ===== 其の/其ノ/其之/其乃 + 数字（日语古风）=====
    (r"其[のノ之乃]\s*(\d+)", "digit"),
    (rf"其[のノ之乃]\s*({_KANJI_CHAR_CLASS}+)", "kanji"),

    # ===== ＃N / #N / ♯N（全角/半角/音乐符号）=====
    (r"[＃#♯]\s*(\d+)", "digit"),
    (rf"[＃#♯]\s*({_KANJI_CHAR_CLASS}+)", "kanji"),

    # ===== Vol.N / Volume N / 巻N =====
    (r"[Vv]ol\.?\s*(\d+)", "digit"),
    (r"[Vv]olume\s*(\d+)", "digit"),
    (r"巻\s*(\d+)", "digit"),
    (rf"巻\s*({_KANJI_CHAR_CLASS}+)", "kanji"),

    # ===== 里番常见的「お家賃6突き目」形式 =====
    (r"(?:お家賃\s*)?(\d{1,3})\s*突き目", "digit"),

    # ===== Episode N / Ep.N / ep N =====
    (r"[Ee]pisode\s*(\d+)", "digit"),
    (r"[Ee]p\.?\s*(\d+)", "digit"),

    # ===== Act N / Scene N（舞台剧风格）=====
    (r"[Aa]ct\.?\s*(\d+)", "digit"),
    (r"[Ss]cene\.?\s*(\d+)", "digit"),

    # ===== 話/集/回/章 + 数字（后置形式）=====
    (r"(\d+)\s*[話话集回章](?:\s|$|[^\d])", "digit"),

    # ===== 纯数字形式（谨慎匹配，仅限特定上下文）=====
    # 末尾带括号的数字 (1) (2)
    (r"\((\d{1,2})\)\s*$", "digit"),
    # 中括号数字 [01] [02]
    (r"\[(\d{1,3})\]", "digit"),
    # 发行名里的「标题 1［副标题］」；必须有空格和全角括号以避免误匹配标题数字。
    (r"\s+(\d{1,3})\s*［", "digit"),
]


def kanji_to_number(kanji_str: str) -> int | None:
    """将汉字数字转换为阿拉伯数字（增强版）.

    支持格式：
    - 单字符：一 → 1, 壱 → 1, ① → 1, Ⅰ → 1
    - 组合数字：十二 → 12, 二十三 → 23
    - 全角数字串：１２ → 12
    """
    if not kanji_str:
        return None

    kanji_str = kanji_str.strip()
    if not kanji_str:
        return None

    # 单字符直接查表
    if len(kanji_str) == 1:
        return KANJI_NUMBERS.get(kanji_str)

    # 检查是否为全角数字串（如 １２３）
    if all(c in "０１２３４５６７８９" for c in kanji_str):
        result = ""
        for c in kanji_str:
            result += str(KANJI_NUMBERS.get(c, 0))
        return int(result) if result else None

    # 检查是否为罗马数字或带圈数字（单字符已处理，多字符组合不支持）
    if len(kanji_str) == 1 and kanji_str in KANJI_NUMBERS:
        return KANJI_NUMBERS[kanji_str]

    # 组合汉字数字（如 十二 = 12, 二十三 = 23, 一百二十三 = 123）
    result = 0
    temp = 0
    last_unit = 1

    for char in kanji_str:
        num = KANJI_NUMBERS.get(char)
        if num is None:
            return None

        if num == 10 or num == 100 or num == 1000:
            # 遇到单位（十/百/千）
            if temp == 0:
                temp = 1
            result += temp * num
            temp = 0
            last_unit = num
        else:
            temp = num

    result += temp
    return result if result > 0 else None


def fullwidth_to_halfwidth(text: str) -> str:
    """全角字符转半角（数字和字母）。"""
    result = []
    for char in text:
        code = ord(char)
        # 全角数字 ０-９ (0xFF10-0xFF19) -> 半角 0-9
        if 0xFF10 <= code <= 0xFF19:
            result.append(chr(code - 0xFF10 + ord("0")))
        # 全角大写字母 Ａ-Ｚ (0xFF21-0xFF3A) -> 半角 A-Z
        elif 0xFF21 <= code <= 0xFF3A:
            result.append(chr(code - 0xFF21 + ord("A")))
        # 全角小写字母 ａ-ｚ (0xFF41-0xFF5A) -> 半角 a-z
        elif 0xFF41 <= code <= 0xFF5A:
            result.append(chr(code - 0xFF41 + ord("a")))
        else:
            result.append(char)
    return "".join(result)


class EpisodeJapanesePlugin(ParserPlugin):
    """日语集数格式解析插件（增强版）.

    解析优先级：30
    主要处理日语/日本动画的命名习惯。
    """

    priority = 30
    name = "episode_japanese"

    def should_skip(self, ctx: ParseContext) -> bool:
        return ctx.episode is not None

    def parse(self, ctx: ParseContext) -> ParseContext:
        if self.should_skip(ctx):
            return ctx

        # 使用原始文件名（保留完整信息）
        text = ctx.original_filename
        # 同时准备一个全角转半角的版本用于数字匹配
        text_normalized = fullwidth_to_halfwidth(text)

        # 1. 先检查固定模式（前篇/后篇等）- 优先级最高
        # 这些模式明确指定了集数，应该优先处理
        for pattern, episode in FIXED_EPISODE_PATTERNS:
            if re.search(pattern, text):
                ctx.episode = episode
                # 固定模式默认 season=1（除非已经被设置）
                if ctx.season is None:
                    ctx.season = 1
                ctx.matched_patterns.append(f"{self.name}:fixed:{pattern}")
                return ctx

        # 2. 检查特别篇模式（season=0）
        is_special = False
        # 里番发行名经常以“OVA作品名”开头，OVA 在这里是介质标记而不是
        # TMDB 的 Special 季。仅匹配 OVA 后直接跟日文标题的收敛形式。
        is_hentai_ova_release = bool(
            re.search(r"(?:OVA|OAD|ONA)(?=[\u3040-\u30ff\u4e00-\u9fff])", text, re.I)
        )
        for pattern in SPECIAL_PATTERNS:
            if re.search(pattern, text):
                is_special = True
                ctx.matched_patterns.append(f"{self.name}:special")
                # 不直接设置 season=0，等后续判断
                break

        # 3. 检查动态模式（在原始和标准化文本上都尝试）
        for search_text in [text, text_normalized]:
            for pattern, num_type in DYNAMIC_EPISODE_PATTERNS:
                match = re.search(pattern, search_text)
                if match:
                    if num_type == "digit":
                        ctx.episode = int(match.group(1))
                    elif num_type == "kanji":
                        ctx.episode = kanji_to_number(match.group(1))
                    if ctx.episode:
                        ctx.matched_patterns.append(f"{self.name}:dynamic:{pattern}")
                        # 成人动画文件中的 OVA 通常是发行格式，而 TMDB 通常将其
                        # 编在 Season 1；当文件同时含明确集数时优先按正片处理。
                        if is_special and ctx.season is None:
                            ctx.season = 1 if is_hentai_ova_release else 0
                        return ctx

        # 4. 如果是特别篇但没有找到集数，设置默认值
        if is_special:
            if ctx.season is None:
                ctx.season = 1 if is_hentai_ova_release else 0
            if ctx.episode is None:
                ctx.episode = 1

        return ctx
