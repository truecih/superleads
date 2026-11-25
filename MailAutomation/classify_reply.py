# -*- coding: utf-8 -*-
r"""Generic reply classifier for outreach (multilingual: EN / VI / ZH).

Given an inbound reply's body + subject, returns:
  primary     主分类 (询价 / 索样 / 约会议 / 索资料 / 积极意向 / 转介绍 / 婉拒 / 其他)
  level       意向等级 (高意向 / 中意向 / 低意向 / 无意向 / 待判断)
  tags        命中的全部类别
  signals     命中的关键词（可回溯）
  confidence  命中关键词数
  needs_review 是否建议人工复核

Rule-based (transparent, no model call), so every judgment can be traced
to the exact matched phrase.  Use as a module or run directly for a self-test.
"""
import re
import sys

# ---------------------------------------------------------------- keyword sets
KW = {
    "询价": [
        "price", "price list", "pricelist", "quotation", "quote", "pricing",
        "how much", "moq", "minimum order", "fob", "cif", "discount",
        "payment terms", "terms of payment", "offer", "cost",
        "báo giá", "bảng giá", "giá cả", "giá", "chi phí", "đặt hàng",
        "đơn hàng", "thanh toán", "chiết khấu", "giá sỉ",
        "报价", "价格", "多少钱", "价目表", "订货",
    ],
    "索样": [
        "sample", "samples", "free sample", "send sample", "specimen",
        "mẫu thử", "gửi mẫu", "lấy mẫu", "mẫu sản phẩm", "mẫu",
        "样品", "寄样", "拿样",
    ],
    "约会议": [
        "phone call", "give me a call", "let's call", "meeting", "zoom",
        "microsoft teams", "schedule a call", "appointment", "visit your factory",
        "when can we", "discuss further",
        "gọi điện", "cuộc họp", "họp", "trao đổi", "lịch hẹn", "gặp mặt",
        "sang Trung Quốc", "đến nhà máy",
        "开会", "电话会议", "见面", "来访",
    ],
    "索资料": [
        "catalogue", "catalog", "brochure", "specification", "datasheet",
        "technical data", "certificate", "more information", "more details",
        "send details", "product list",
        "tài liệu", "thông số", "chứng nhận", "thêm thông tin", "hồ sơ",
        "danh mục sản phẩm",
        "目录", "规格", "证书", "资料",
    ],
    "积极意向": [
        "interested", "would like", "looking forward", "please send",
        "can you send", "we need", "we are looking for", "keep in touch",
        "cooperate", "partnership", "long term", "next order",
        "quan tâm", "chúng tôi cần", "vui lòng gửi", "mong muốn", "hợp tác",
        "rất muốn", "cần tư vấn", "mối quan hệ lâu dài",
        "感兴趣", "合作", "长期", "需要",
    ],
    "转介绍": [
        "forwarded", "forwarded this", "refer you to", "referral",
        "introduce you", "my colleague", "another department", "please contact",
        "cc'd", "wrong person",
        "chuyển tiếp", "giới thiệu", "liên hệ với", "bộ phận khác",
        "đối tác khác", "không phải người phụ trách",
        "介绍", "转给", "转交", "请联系",
    ],
    "婉拒": [
        "not interested", "no thanks", "no thank you", "unsubscribe",
        "remove me", "we already have", "already have a supplier",
        "do not need", "don't need", "stop sending", "not looking for",
        "no need", "not at this time",
        "không quan tâm", "cảm ơn nhưng", "không cần", "đã có nhà cung cấp",
        "đã có đối tác", "không có nhu cầu", "vui lòng ngừng", "ngừng gửi",
        "不需要", "不感兴趣", "已有供应商", "别再发",
    ],
}

PRIORITY = ["询价", "索样", "约会议", "索资料", "积极意向", "转介绍", "婉拒"]
LEVEL_OF = {
    "询价": "高意向", "索样": "高意向", "约会议": "高意向",
    "索资料": "中意向", "积极意向": "中意向",
    "转介绍": "低意向", "婉拒": "无意向",
}

# Strong negative phrases are removed before positive matching so that
# "not interested" does not also register as "interested".
NEGATIVE_STRIP = [
    "not interested", "no thanks", "no thank you", "no need",
    "don't need", "do not need", "not looking for", "stop sending",
    "không quan tâm", "không cần", "không có nhu cầu", "vui lòng ngừng",
    "不感兴趣", "不需要",
]


def _hits(text, keywords):
    """Match keywords with word boundaries for ASCII terms (avoid e.g. 'cif'
    matching inside 'specification'); plain substring for CJK/Vietnamese."""
    found = []
    for k in keywords:
        if re.fullmatch(r"[a-z0-9 ]+", k):
            pat = r"\b" + r"\s+".join(re.escape(w) for w in k.split()) + r"\b"
            if re.search(pat, text):
                found.append(k)
        elif k in text:
            found.append(k)
    return found


def classify(body="", subject=""):
    text = ((subject or "") + "\n" + (body or "")).lower()
    text = re.sub(r"\s+", " ", text).strip()

    decline_hits = _hits(text, KW["婉拒"])
    cleaned = text
    for phrase in NEGATIVE_STRIP:
        cleaned = cleaned.replace(phrase, " ")

    tags, signals = [], []
    for cat in PRIORITY:
        hits = _hits(cleaned if cat != "婉拒" else text, KW[cat])
        if hits:
            tags.append(cat)
            signals.extend(hits[:4])

    if not tags:
        return {
            "primary": "其他", "level": "待判断", "tags": [],
            "signals": [], "confidence": 0, "needs_review": True,
        }

    # An explicit decline outranks any incidental positive keyword.
    if decline_hits:
        primary, level = "婉拒", "无意向"
        needs_review = len(tags) > 1  # mixed signals -> worth a human look
    else:
        primary = tags[0]
        level = max((LEVEL_OF[t] for t in tags),
                    key=lambda x: ["待判断", "无意向", "低意向", "中意向", "高意向"].index(x))
        needs_review = (len(set(signals)) <= 1 and level in ("待判断", "低意向", "无意向"))

    return {
        "primary": primary,
        "level": level,
        "tags": tags,
        "signals": sorted(set(signals)),
        "confidence": len(set(signals)),
        "needs_review": needs_review,
    }


def format_brief(c):
    """Short human-readable form for a spreadsheet cell."""
    return "%s / %s (%s)" % (c["primary"], c["level"], "+".join(c["signals"][:3]))


if __name__ == "__main__":
    import io
    sys_out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    samples = [
        ("Please send us your price list for 0.6/1kV XLPE cable, FOB price and MOQ.", "Re: Cáp XLPE"),
        ("We are interested. Could you send free samples and specification?", "Re: cooperation"),
        ("Kindly forward your catalogue and certificate to our purchasing dept.", "Request"),
        ("Thanks, but we already have a supplier. Not interested.", "Re: offer"),
        ("Please contact my colleague in the purchasing team, she handles this.", "Forwarded"),
        ("Let's schedule a call next week to discuss further.", "Re: meeting"),
        ("Cảm ơn, chúng tôi cần báo giá và mẫu thử cho dây cáp XLPE.", "Re: hợp tác"),
        ("Không quan tâm. Vui lòng ngừng gửi email.", "Re: cáp"),
        ("We would like to visit your factory and discuss a long term partnership.", "Visit"),
        ("Just to say hello.", "Hi"),
    ]
    for body, subj in samples:
        r = classify(body, subj)
        sys_out.write("[%s|%s] %s -> %s\n" % (r["primary"], r["level"], subj, format_brief(r)))
    sys_out.flush()
