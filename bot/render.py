"""카드뉴스 이미지 생성 (1080x1350, 인스타 4:5). 보도사진 없이 텍스트 디자인만 사용."""
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import config

W, H = 1080, 1350
PAD = 90
BG = "#0F0F14"
FG = "#F5F5F7"
SUB = "#A1A1AA"
FONT_DIR = "/usr/share/fonts/opentype/noto"
KR = 1  # NotoSansCJK .ttc 안에서 한국어(KR) 서체 인덱스


def font(weight: str, size: int):
    return ImageFont.truetype(f"{FONT_DIR}/NotoSansCJK-{weight}.ttc", size, index=KR)


def wrap(draw, text, fnt, max_w):
    """한글은 글자 단위, 가능하면 공백에서 줄바꿈."""
    lines = []
    for para in text.split("\n"):
        line = ""
        for word in para.split(" "):
            trial = (line + " " + word).strip()
            if draw.textlength(trial, font=fnt) <= max_w:
                line = trial
                continue
            if line:
                lines.append(line)
            line = ""
            for ch in word:  # 단어 자체가 길면 글자 단위로 자름
                if draw.textlength(line + ch, font=fnt) <= max_w:
                    line += ch
                else:
                    lines.append(line)
                    line = ch
        lines.append(line)
    return lines


def fit(draw, text, weight, sizes, max_w, max_lines):
    for s in sizes:
        f = font(weight, s)
        lines = wrap(draw, text, f, max_w)
        if len(lines) <= max_lines:
            return f, lines
    return f, lines[:max_lines]


def _frame(draw, page, total):
    a = config.ACCENT_COLOR
    draw.rectangle([0, 0, W, 14], fill=a)
    draw.text((PAD, 70), config.BRAND_NAME, font=font("Bold", 34), fill=a)
    pg = f"{page:02d} / {total:02d}"
    pf = font("Medium", 28)
    draw.text((W - PAD - draw.textlength(pg, font=pf), 74), pg, font=pf, fill=SUB)
    hf = font("Medium", 30)
    draw.text((PAD, H - 110), config.BRAND_HANDLE, font=hf, fill=SUB)


def cover(post: dict, when: datetime, total: int) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    _frame(d, 1, total)
    a = config.ACCENT_COLOR
    stamp = when.strftime("%m.%d") + " " + ["오전", "오후"][when.hour >= 12] + f" {((when.hour - 1) % 12) + 1}시 브리핑"
    d.text((PAD, 250), stamp, font=font("Bold", 40), fill=a)
    f, lines = fit(d, post.get("cover_title") or "지금 연예계 소식", "Black", [96, 86, 76], W - 2 * PAD, 3)
    y = 320
    for ln in lines:
        d.text((PAD, y), ln, font=f, fill=FG)
        y += int(f.size * 1.25)
    y += 50
    d.rectangle([PAD, y, PAD + 120, y + 8], fill=a)
    y += 60
    lf = font("Medium", 40)
    for i, it in enumerate(post["items"], 1):
        nf = font("Black", 40)
        d.text((PAD, y), f"{i:02d}", font=nf, fill=a)
        txt = wrap(d, it["headline"], lf, W - 2 * PAD - 90)
        for j, ln in enumerate(txt[:2]):
            d.text((PAD + 90, y + j * 54), ln, font=lf, fill=FG)
        y += 54 * min(len(txt), 2) + 34
    nf = font("Medium", 28)
    d.text((PAD, H - 170), "넘겨서 자세히 보기 →", font=nf, fill=SUB)
    return img


def item_card(it: dict, n: int, page: int, total: int) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    _frame(d, page, total)
    a = config.ACCENT_COLOR
    d.text((PAD, 200), f"{n:02d}", font=font("Black", 150), fill=a)
    f, lines = fit(d, it["headline"], "Black", [84, 76, 68, 60], W - 2 * PAD, 3)
    y = 420
    for ln in lines:
        d.text((PAD, y), ln, font=f, fill=FG)
        y += int(f.size * 1.28)
    y += 40
    d.line([PAD, y, W - PAD, y], fill="#2A2A33", width=3)
    y += 60
    sf = font("Regular", 42)
    for s in it["summary"]:
        sl = wrap(d, s, sf, W - 2 * PAD - 50)
        d.ellipse([PAD, y + 20, PAD + 14, y + 34], fill=a)
        for ln in sl[:3]:
            d.text((PAD + 50, y), ln, font=sf, fill=FG)
            y += 62
        y += 28
    srcf = font("Medium", 28)
    d.text((PAD, H - 170), f"출처 · {it['press']}", font=srcf, fill=SUB)
    return img


def render_post(post: dict, post_id: str, when: datetime) -> list[Path]:
    out = config.POSTS_DIR / post_id
    out.mkdir(parents=True, exist_ok=True)
    total = 1 + len(post["items"])
    imgs = [cover(post, when, total)] + [item_card(it, i, i + 1, total) for i, it in enumerate(post["items"], 1)]
    paths = []
    for i, im in enumerate(imgs, 1):
        p = out / f"{i:02d}.jpg"
        im.save(p, "JPEG", quality=88, optimize=True)
        paths.append(p)
    return paths
