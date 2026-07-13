#!/usr/bin/env python3
"""
Build self-contained all-in-one config files from the original templates plus
channels recovered from the production unmatched-channel log:

    config/demo.txt  + curated stations  -> config/all.txt        (template)
    config/alias.txt + curated aliases   -> config/all_alias.txt  (alias map)

Rationale
---------
unmatch.log contains source channels that matched no entry in the template
(config/demo.txt). Many are real, popular channels that are *already* targets of
alias.txt (e.g. 湖南金鹰卡通 -> 金鹰卡通) but have no template entry, so they fall
through to "unmatched". This script promotes the real canonical channels into the
template (grouped by genre, like demo.txt) and folds the English-name / variant
aliases observed in the log into the alias map.

Both outputs are produced by *merging* the originals with the curated additions
and de-duplicating, so they fully supersede demo.txt / alias.txt:
* template: every channel name appears at most once across all categories;
* alias map: every primary appears on exactly one row (alias tokens from the
  original and the additions are unioned, order-preserving, deduped).

Curation rules
--------------
* Only emit a station/alias-target that actually appears in unmatch.log (validated
  against source counts) so nothing is invented.
* Exclude noise: drama/episode titles (第N集, named dramas), 测试/未知/视频/Unknown,
  "X（B）" pseudo channels, and "X卫视 (1080p)/HD/FHD/高清/+" duplicates of demo
  satellites (format_name already strips resolution/HD/4K, so those self-match).
"""
import collections
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(ROOT, "output/log/unmatch.log")
PFX = "[output/log/unmatch.log] "


# ---- parse the production log: name -> set(distinct source urls) ----------
def load_counts():
    name_urls = collections.defaultdict(set)
    if not os.path.exists(LOG):
        return {}
    with open(LOG, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            i = line.find(PFX)
            if i < 0:
                continue
            rest = line[i + len(PFX):]
            j = rest.rfind(",")  # split on last comma; urls carry no comma
            if j < 0:
                continue
            name, url = rest[:j].strip(), rest[j + 1:].strip()
            if name:
                name_urls[name].add(url)
    return {k: len(v) for k, v in name_urls.items()}


COUNTS = load_counts()


def n(name):
    """Return source count for a name (0 if absent)."""
    return COUNTS.get(name, 0)


# ---- demo.txt names already in template (do not duplicate) ----------------
DEMO = set()
with open(os.path.join(ROOT, "config/demo.txt"), encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line or "#genre#" in line:
            continue
        DEMO.add(line.split(",")[0].strip())

# ---- existing alias.txt primaries + aliases (do not duplicate) ------------
ALIAS_PRIMARIES = set()
ALIAS_TOKENS = set()
with open(os.path.join(ROOT, "config/alias.txt"), encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#") or "," not in line:
            continue
        parts = [p.strip() for p in line.split(",")]
        ALIAS_PRIMARIES.add(parts[0])
        ALIAS_TOKENS.update(parts)

# ---------------------------------------------------------------------------
# Curated template: genre header -> list of canonical channel names.
# Every name below was observed in unmatch.log with a meaningful source count.
# ---------------------------------------------------------------------------
STATIONS = [
    ("📡卫视补充", [
        "兵团卫视", "延边卫视", "康巴卫视", "安多卫视", "农林卫视",
        "旅游卫视", "大湾区卫视", "海峡卫视", "南方卫视", "厦门卫视",
        "珠江卫视",
    ]),
    ("📰资讯·财经", [
        "凤凰资讯", "凤凰中文", "凤凰香港", "凤凰卫视",
        "第一财经", "上海第一财经", "东方财经", "北京财经",
        "中国天气",
    ]),
    ("🎬央视数字付费", [
        "第一剧场", "风云剧场", "怀旧剧场", "风云音乐", "风云足球",
        "世界地理", "兵器科技", "央视台球", "高尔夫网球", "女性时尚",
        "发现之旅", "电视指南", "文物宝库", "文化精品", "央视文化精品",
        "老故事", "中学生",
    ]),
    ("🎞️影视剧场", [
        "CHC家庭影院", "CHC动作电影", "CHC影迷电影", "CHC高清电影",
        "都市剧场", "欢笑剧场", "经典电影", "重温经典", "东方影视",
        "上视东方影视", "谍战剧场", "军旅剧场", "古装剧场", "武侠剧场",
        "家庭剧场", "喜剧影院", "动作影院", "可乐影院", "黑莓电影",
        "惊悚悬疑", "精品大剧", "热播剧场", "新片放映厅", "黑莓动画",
        "纬来电影", "美亚电影", "龙祥电影", "天映经典", "天映",
        "经典香港电影", "淘电影", "淘剧场",
    ]),
    ("📺生活·纪实·文教", [
        "生活时尚", "纪实人文", "上海纪实", "之江纪录", "求索纪录",
        "求索科学", "求索生活", "求索动物", "游戏风云", "动漫秀场",
        "乐游", "新视觉", "金鹰纪实", "梨园", "七彩戏剧", "戏曲台",
        "相声小品", "金色学堂", "法治天地", "武术世界", "中华特产",
        "环球旅游", "亚洲旅游", "财富天下",
    ]),
    ("🧒少儿·动漫", [
        "金鹰卡通", "优漫卡通", "嘉佳卡通", "哈哈炫动", "卡酷少儿",
        "新动漫", "爱动漫", "优优宝贝",
        "CN卡通", "i-Fun动漫", "MOMO亲子", "少儿动画",
        "经典动画大集合", "精品萌宠",
    ]),
    ("⚽体育", [
        "五星体育", "广东体育", "山东体育", "劲爆体育", "魅力足球",
        "精品体育", "智林体育", "天津体育", "辽宁体育", "江苏体育",
        "先锋乒羽", "哒啵赛事", "超级体育", "爱体育", "体育休闲",
        "SPOTV", "SPOTV2", "博斯网球", "博斯无限", "博斯魅力",
        "爱尔达体育2", "睛彩篮球", "睛彩竞技",
    ]),
    ("🎣休闲·钓鱼·生活", [
        "四海钓鱼", "快乐垂钓", "睛彩广场舞", "潮妈辣婆", "家庭理财",
        "茶友频道", "怡伴健康", "卫生健康", "生态环境",
    ]),
    ("🌏港澳台", [
        "翡翠台", "明珠台", "J2", "无线新闻", "TVB星河", "TVB千禧经典",
        "ViuTV", "HOY TV", "HOY资讯", "港台电视31", "港台电视32",
        "华视", "中视", "台视", "民视", "公视",
        "三立新闻", "三立戏剧", "年代新闻", "中天新闻", "镜新闻",
        "寰宇新闻", "寰宇新闻台湾", "人间卫视", "大爱", "客家电视",
        "东森新闻", "东森电影", "东森综合", "东森洋片", "东森超视",
        "东森财经", "东森幼幼", "东森亚洲卫视",
        "纬来体育", "纬来育乐", "纬来日本", "纬来戏剧", "纬来综合",
        "八大戏剧", "八度空间", "星空卫视", "莲花卫视",
        "澳视澳门", "采昌影剧", "黄金翡翠", "华丽翡翠", "翡翠剧集",
        "龙华电影", "龙华卡通", "龙华经典", "龙华偶像", "龙华洋片",
        "龙华日韩", "靖天电影", "靖天卡通", "靖天国际", "靖天资讯",
        "爱尔达娱乐",
    ]),
    ("🌐国际频道", [
        "CGTN", "HBO", "CINEMAX", "CNN", "BBC", "AXN", "Animax",
        "Nickelodeon", "Nick Jr.", "Cartoon Network", "Discovery",
        "Discovery Asia", "Love Nature", "BBC Earth", "tvN", "KIX",
        "HITS", "Celestial Movies", "DW", "France 24", "Al Jazeera",
        "CNBC", "CNA", "Arirang", "Sky News", "ESPN", "NBA TV",
        "EUROSPORT", "Golf Channel", "Fight Sports", "Astro AEC", "Astro AOD",
    ]),
    ("🏙️广东·广州·佛山", [
        "广东珠江", "广东新闻", "广东少儿", "广东民生", "广东影视",
        "广东公共", "广东科教", "广东经济科教", "广东综艺",
        "广州综合", "广州新闻", "广州影视", "广州竞赛", "广州法治",
        "广州台", "广州南国都市", "南国都市",
        "佛山综合", "佛山影视", "佛山公共", "佛山顺德",
        "汕头经济", "汕头综合",
    ]),
    ("🏙️浙江·杭州", [
        "浙江国际", "浙江新闻", "浙江少儿", "浙江钱江", "浙江民生休闲",
        "浙江经济生活", "浙江教科", "浙江公共新闻", "浙江经视",
        "钱江都市", "浙江钱江都市",
        "杭州综合", "杭州生活", "杭州影视", "杭州明珠",
    ]),
    ("🏙️湖南·湖北", [
        "湖南都市", "湖南娱乐", "湖南经视", "湖南电视剧", "湖南电影",
        "湖南国际", "湖南公共", "湖南爱晚", "金鹰纪实",
        "湖北经视", "湖北影视", "湖北综合", "湖北公共", "湖北垄上",
        "湖北教育", "湖北生活",
    ]),
    ("🏙️北京·天津·河北", [
        "北京新闻", "北京财经", "北京生活", "北京影视", "北京卡通",
        "北京科教", "北京文艺", "北京青年", "北京纪实科教", "北京纪实",
        "河北农民", "河北都市", "河北影视", "河北经济", "河北公共",
        "河北少儿科教",
    ]),
    ("🏙️黑龙江·辽宁·吉林", [
        "黑龙江都市", "黑龙江少儿", "黑龙江文体", "黑龙江新闻法治",
        "黑龙江影视",
        "辽宁都市", "辽宁影视剧", "辽宁经济", "辽宁公共", "辽宁北方",
        "吉林都市", "吉林生活", "吉林乡村", "吉林教育",
        "哈尔滨生活", "哈尔滨新闻综合", "哈尔滨影视", "哈尔滨娱乐",
    ]),
    ("🏙️江苏·上海·安徽", [
        "江苏教育", "江苏影视", "江苏城市", "江苏综艺", "江苏国际",
        "江苏公共新闻", "江苏体育休闲",
        "上海都市", "上海新闻综合", "上海外语", "上海教育",
        "安徽综艺体育", "安徽影视", "安徽经济生活", "安徽公共",
        "安徽国际",
    ]),
    ("🏙️山东·河南·山西", [
        "山东教育", "山东教育卫视", "山东少儿", "山东生活", "山东齐鲁",
        "河南都市", "河南民生", "河南新闻", "河南电视剧", "河南公共",
        "河南法治", "河南新农村",
        "山西影视",
    ]),
    ("🏙️福建·海南·广西", [
        "福建新闻", "福建综合", "福建旅游", "福建东南", "福建少儿",
        "福建经济", "福建公共", "福建电视剧",
        "海南新闻", "海南文旅", "海南少儿", "海南公共", "海南自贸",
        "广西新闻", "广西国际", "广西都市", "广西影视", "广西综艺",
    ]),
    ("🏙️西部·西南·其他", [
        "陕西新闻资讯", "陕西都市青春", "陕西体育休闲", "陕西秦腔",
        "甘肃经济", "甘肃文化影视",
        "四川科教", "四川新闻", "重庆新闻", "重庆汽摩", "重庆少儿",
        "云南都市", "云南娱乐",
        "内蒙古", "宁夏经济",
        "南宁新闻综合", "南宁都市生活", "南宁公共", "南宁影视娱乐",
        "南京新闻综合", "南京教科", "南京十八",
        "温州经济科教",
    ]),
]

# ---------------------------------------------------------------------------
# Curated extended aliases: canonical -> [observed variants].
# English names from the log are the main payload. Resolution/HD tags are
# stripped by format_name() so they are not listed here.
# Targets may map to demo.txt names (湖南卫视) or to extended_station names.
# ---------------------------------------------------------------------------
ALIASES = [
    # --- English names of mainland satellite channels (target = demo.txt) ---
    ("湖南卫视", ["Hunan TV", "Hunan Satellite TV"]),
    ("浙江卫视", ["Zhejiang TV", "Zhejiang Satellite TV"]),
    ("江苏卫视", ["Jiangsu TV", "Jiangsu Satellite TV"]),
    ("东方卫视", ["Dragon TV", "Dongfang TV", "Oriental TV"]),
    ("北京卫视", ["Beijing TV", "BRTV 北京卫视", "BTV"]),
    ("广东卫视", ["Guangdong TV", "Guangdong Satellite TV"]),
    ("深圳卫视", ["Shenzhen TV", "Shenzhen Satellite TV"]),
    ("山东卫视", ["Shandong TV"]),
    ("河北卫视", ["Hebei TV"]),
    ("河南卫视", ["Henan TV"]),
    ("黑龙江卫视", ["Heilongjiang TV"]),
    ("辽宁卫视", ["Liaoning TV"]),
    ("江西卫视", ["Jiangxi TV"]),
    ("安徽卫视", ["Anhui TV"]),
    ("四川卫视", ["Sichuan TV"]),
    ("天津卫视", ["Tianjin TV"]),
    ("贵州卫视", ["Guizhou TV"]),
    ("云南卫视", ["Yunnan TV"]),
    ("新疆卫视", ["Xinjiang TV", "Xinjiang TV 3"]),
    ("重庆卫视", ["Chongqing TV"]),
    ("湖北卫视", ["Hubei TV"]),
    # --- English names of extended channels -------------------------------
    ("金鹰卡通", ["Golden Eagle Cartoon"]),
    ("CGTN", ["cgtn", "CGTN News", "CCTVNEWS", "CGTN英语", "CGTN英文"]),
    ("翡翠台", ["TVB Jade", "TVBJ1", "TVB J1", "TVB1"]),
    ("明珠台", ["TVB Pearl", "TVBPearl"]),
    ("无线新闻", ["TVB News", "TVBNews"]),
    ("TVB星河", ["TVB Plus", "TVBPlus", "TVB PLUS"]),
    ("J2", ["TVB J2", "J2台"]),
    ("ViuTV", ["Viutv", "VIUTV", "ViuTV6", "ViuTVsix"]),
    ("HOY TV", ["HOY78", "HOY 77", "HOY77"]),
    ("旅游卫视", ["Travel TV"]),
    ("厦门卫视", ["Xiamen TV"]),
    ("大湾区卫视", ["GBA Satellite TV", "Greater Bay Area TV"]),
    # --- city/regional English names (target = extended_station names) -----
    ("广州综合", ["Guangzhou TV"]),
    ("佛山综合", ["Foshan TV", "Foshan News TV"]),
    ("哈尔滨新闻综合", ["Harbin Comprehensive News Channel"]),
    ("哈尔滨影视", ["Harbin Movie Channel"]),
    ("CHC动作电影", ["CHC Action Movies"]),
    ("CHC家庭影院", ["CHC Home Theater", "CHC Home Cinema"]),
]


# ---------------------------------------------------------------------------
# Canonical-name normalization.
# alias.txt uses brand-prefixed primaries (NewTV黑莓电影, 北京卡酷少儿, 无线新闻台)
# while the template prefers clean display names. For an all-in-one we unify on
# the clean name: the primary is renamed and the brand-prefixed string is kept as
# an alias. This guarantees template-entry == alias-primary for every channel.
# ---------------------------------------------------------------------------
BRAND_PREFIXES = ("NewTV", "NEWTV")
EXPLICIT_RENAME = {
    "北京卡酷少儿": "卡酷少儿",
    "无线新闻台": "无线新闻",
    "CGTN英语": "CGTN",
    "武搏世界": "武术世界",
    "FZTV1": "福州综合",
    "FZTV3": "福州生活",
}


def canonical(primary):
    name = primary.lstrip("*").strip()          # *HOY TV -> HOY TV
    name = EXPLICIT_RENAME.get(name, name)
    for p in BRAND_PREFIXES:                     # NewTV武搏世界 -> 武搏世界
        if name.startswith(p) and len(name) > len(p):
            name = name[len(p):]
            break
    return EXPLICIT_RENAME.get(name, name)       # 武搏世界 -> 武术世界


# routing for auto-added primaries that have no curated category;
# labels reuse existing category names so blocks merge rather than duplicate.
ROUTES = [
    (r"体育|赛事|足球|篮球|网球|乒|竞技|电竞|搏|功夫|武", "⚽体育"),
    (r"剧场|电影|影院|大剧|影视|放映|综艺|大片|剧$", "🎞️影视剧场"),
    (r"卡通|动画|动漫|亲子|幼|宝贝|萌", "🧒少儿·动漫"),
    (r"CGTN", "🌐国际频道"),
    (r"CCTV|CETV", "📺央视频道"),
    (r"HOY|港台|RTHK|台视|民视|中视|TVB", "🌏港澳台"),
    (r"新闻|资讯|财经", "📰资讯·财经"),
]


def route(name):
    for pat, cat in ROUTES:
        if re.search(pat, name):
            return cat
    return "📺其他频道"


# ---------------------------------------------------------------------------
def read_template(fn):
    """Return [(genre, [names]), ...] preserving file order."""
    blocks = []
    current = None
    for line in open(fn, encoding="utf-8"):
        s = line.strip()
        if not s:
            continue
        if "#genre#" in s:
            current = (re.split(r"[，,]", s, maxsplit=1)[0], [])
            blocks.append(current)
        elif current is not None:
            current[1].append(s.split(",")[0].strip())
    return blocks


def read_alias(fn):
    """Return ([(primary, [tokens]), ...], leading_comment_lines). Renames
    primaries to their canonical names and merges rows that collapse together."""
    rows = []
    header = []
    index = {}

    def add(primary, tokens):
        if primary in index:
            rows[index[primary]][1].extend(tokens)
        else:
            index[primary] = len(rows)
            rows.append([primary, list(tokens)])

    for line in open(fn, encoding="utf-8"):
        s = line.rstrip("\n")
        st = s.strip()
        if st.startswith("#"):
            if not rows:
                header.append(s)
            continue
        if not st or "," not in st:
            continue
        parts = [p.strip() for p in st.split(",")]
        raw_primary, tokens = parts[0], parts[1:]
        primary = canonical(raw_primary)
        if primary != raw_primary:
            tokens = [raw_primary] + tokens  # keep old primary as an alias
        add(primary, tokens)
    return rows, header, index


def build_alias():
    rows, header, index = read_alias(os.path.join(ROOT, "config/alias.txt"))
    added = merged = 0
    for primary, variants in ALIASES:
        primary = canonical(primary)
        if primary in index:
            row = rows[index[primary]]
            existing = set(row[1]) | {primary}
            new = [v for v in variants if v not in existing]
            if new:
                row[1].extend(new)
                merged += 1
        else:
            index[primary] = len(rows)
            rows.append([primary, list(variants)])
            added += 1

    # clean token lists
    clean_rows = []
    for primary, tokens in rows:
        seen_t, clean = set(), []
        for t in tokens:
            if t and t != primary and t not in seen_t:
                seen_t.add(t)
                clean.append(t)
        clean_rows.append((primary, clean))
    print(f"all_alias.txt: {len(clean_rows)} primaries "
          f"({added} new rows, {merged} existing rows extended)")
    return clean_rows, header


def build_template(alias_rows):
    blocks = read_template(os.path.join(ROOT, "config/demo.txt"))
    seen = set()
    for _, names in blocks:
        seen.update(names)

    dropped = []
    for genre, names in STATIONS:
        body = []
        for name in names:
            if name in seen:
                dropped.append((name, "duplicate"))
                continue
            if n(name) == 0 and not re.search(r"[A-Za-z]", name):
                dropped.append((name, "not in unmatch.log"))
                continue
            seen.add(name)
            body.append(name)
        if body:
            blocks.append((genre, body))

    # auto-add any alias primary with real sources but no template entry, so
    # that every primary can actually be emitted (otherwise it stays unmatched);
    # merge into the routed category block, creating it only if it doesn't exist.
    genre_index = {g: i for i, (g, _) in enumerate(blocks)}
    n_extra = 0
    for primary, tokens in alias_rows:
        if primary in seen or primary.startswith("re:"):
            continue
        best = max([n(primary)] + [n(t) for t in tokens if not t.startswith("re:")] or [0])
        if best >= 3:
            seen.add(primary)
            n_extra += 1
            cat = route(primary)
            if cat not in genre_index:
                genre_index[cat] = len(blocks)
                blocks.append((cat, []))
            blocks[genre_index[cat]][1].append(primary)

    lines = []
    for genre, body in blocks:
        if not body:
            continue
        lines.append(f"{genre},#genre#")
        lines.extend(body)
        lines.append("")
    total = sum(len(b) for _, b in blocks if b)
    print(f"all.txt: {total} channels across {sum(1 for _, b in blocks if b)} categories"
          f" (+{n_extra} auto-added alias primaries)")
    for name, why in dropped:
        print(f"  dropped {name}: {why}")
    return blocks, seen


def write_template(blocks):
    lines = []
    for genre, body in blocks:
        if not body:
            continue
        lines.append(f"{genre},#genre#")
        lines.extend(body)
        lines.append("")
    with open(os.path.join(ROOT, "config/all.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")


def write_alias(rows, header):
    out = list(header) if header else []
    for primary, tokens in rows:
        out.append(",".join([primary] + tokens))
    with open(os.path.join(ROOT, "config/all_alias.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(out).rstrip() + "\n")


def validate(blocks, alias_rows):
    template = [name for _, body in blocks for name in body]
    tset = set(template)
    problems = 0
    dup = [k for k, v in collections.Counter(template).items() if v > 1]
    if dup:
        problems += 1
        print("  ! duplicate template channels:", dup)
    prim = [p for p, _ in alias_rows]
    dp = [k for k, v in collections.Counter(prim).items() if v > 1]
    if dp:
        problems += 1
        print("  ! duplicate alias primaries:", dp)
    no_entry = [p for p in prim if p not in tset and not p.startswith("re:")]
    if no_entry:
        print(f"  · {len(no_entry)} alias-only primaries (no template entry, <3 sources, preserved):")
        print("    ", ", ".join(no_entry))
    # a literal token that is itself a template entry under a different primary
    conflicts = []
    for primary, tokens in alias_rows:
        for t in tokens:
            if t.startswith("re:"):
                continue
            if t in tset and t != primary:
                conflicts.append((t, primary))
    if conflicts:
        problems += 1
        print("  ! token-equals-other-template-entry:", conflicts)
    print(f"  validation: {'OK' if problems == 0 else str(problems)+' PROBLEM(S)'}")


def prune_conflicts(alias_rows, tset):
    """Drop any alias token that is itself a template entry under a *different*
    primary (a name is either a canonical channel or an alias, never both)."""
    pruned = []
    cleaned = []
    for primary, tokens in alias_rows:
        keep = []
        for t in tokens:
            if not t.startswith("re:") and t in tset and t != primary:
                pruned.append((t, primary))
            else:
                keep.append(t)
        cleaned.append((primary, keep))
    if pruned:
        print(f"  pruned {len(pruned)} misrouting alias tokens "
              f"(each owns a template entry): "
              + ", ".join(f"{t}↛{p}" for t, p in pruned))
    return cleaned


if __name__ == "__main__":
    alias_rows, header = build_alias()
    blocks, tset = build_template(alias_rows)
    alias_rows = prune_conflicts(alias_rows, tset)
    write_template(blocks)
    write_alias(alias_rows, header)
    validate(blocks, alias_rows)
