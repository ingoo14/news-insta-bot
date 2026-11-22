"""매거진 커버 스타일 카드 (1080x1350, 인스타 4:5)."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from . import config

W, H = 1080, 1350
BG = (15, 15, 20)
WHITE = (255, 255, 255)
FONT_DIR = "/usr/share/fonts/opentype/noto"
KR = 1  # NotoSansCJK .ttc 안에서 한국어(KR) 서체 인덱스

# 서버에 따라 일부 굵기만 설치돼 있음 → 없으면 비슷한 굵기로 대체
FALLBACK = {"Black": ["Black", "Bold"], "Bold": ["Bold", "Black", "Regular"],
            "Medium": ["Medium", "Regular", "Bold"], "Regular": ["Regular", "Medium", "Bold"]}
_cache = {}


SERIF_FALLBACK = {"Black": ["Black", "Bold", "SemiBold"], "Bold": ["Bold", "SemiBold", "Black"]}


def font(weight: str, size: int, family: str = "Sans"):
    """family: Sans(고딕) / Serif(명조). 서버에 없는 굵기는 비슷한 굵기로 대체."""
    key = (weight, size, family)
    if key in _cache:
        return _cache[key]
    local = Path(__file__).resolve().parent.parent / "fonts"  # apt 설치 실패 시 받아둔 폰트
    tries = []  # (경로, ttc 인덱스)
    if family == "Serif":
        for w in SERIF_FALLBACK.get(weight, [weight, "Bold"]):
            tries += [(f"{FONT_DIR}/NotoSerifCJK-{w}.ttc", KR), (str(local / f"NotoSerifKR-{w}.otf"), 0)]
    for w in FALLBACK.get(weight, [weight, "Regular", "Bold"]):
        tries += [(f"{FONT_DIR}/NotoSansCJK-{w}.ttc", KR), (str(local / f"NotoSansKR-{w}.otf"), 0)]
    for path, idx in tries:
        try:
            _cache[key] = ImageFont.truetype(path, size, index=idx)
            return _cache[key]
        except OSError:
            continue
    raise OSError("한글 폰트(Noto CJK)를 찾을 수 없음 — 워크플로의 '한글 폰트 설치' 단계 확인")


def accent():
    from PIL import ImageColor
    try:
        return ImageColor.getrgb(config.ACCENT_COLOR)
    except Exception:
        return (255, 212, 59)


def wrap(draw, text, fnt, max_w):
    lines, line = [], ""
    for word in text.split(" "):
        trial = (line + " " + word).strip()
        if draw.textlength(trial, font=fnt) <= max_w:
            line = trial
            continue
        if line:
            lines.append(line)
        line = ""
        for ch in word:
            if draw.textlength(line + ch, font=fnt) <= max_w:
                line += ch
            else:
                lines.append(line)
                line = ch
    if line:
        lines.append(line)
    return lines


def fit(draw, text, weight, sizes, max_w, max_lines, family="Sans"):
    """1줄에 들어가는 가장 큰 크기 우선 → 안 되면 2줄을 균형 있게 나눔."""
    text = text.strip()
    for s in sizes:
        f = font(weight, s, family)
        if draw.textlength(text, font=f) <= max_w:
            return f, [text]
    if max_lines >= 2:
        words = text.split(" ")
        for s in sizes:
            f = font(weight, s, family)
            best = None
            for i in range(1, len(words)):
                a, b = " ".join(words[:i]), " ".join(words[i:])
                wa, wb = draw.textlength(a, font=f), draw.textlength(b, font=f)
                if wa <= max_w and wb <= max_w and (best is None or max(wa, wb) < best[0]):
                    best = (max(wa, wb), [a, b])
            if best:
                return f, best[1]
    f = font(weight, sizes[-1], family)
    return f, wrap(draw, text, f, max_w)[:max_lines]


def center_text(img, y, lines, fnt, fill, line_gap=1.18, shadow=True):
    """가운데 정렬 + 그림자(사진 위 가독성)."""
    d = ImageDraw.Draw(img)
    for ln in lines:
        w = d.textlength(ln, font=fnt)
        x = (W - w) / 2
        if shadow:
            sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(sh).text((x + 3, y + 4), ln, font=fnt, fill=(0, 0, 0, 200))
            img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(5)))
            d = ImageDraw.Draw(img)
        d.text((x, y), ln, font=fnt, fill=fill)
        y += int(fnt.size * line_gap)
    return y


def _grad(h, top_alpha, bottom_alpha, power=1.0):
    g = Image.new("L", (1, h))
    for y in range(h):
        t = (y / max(h - 1, 1)) ** power
        g.putpixel((0, y), int(top_alpha + (bottom_alpha - top_alpha) * t))
    return g.resize((W, h))


MAST_BOTTOM = 90  # 얼굴 윗부분이 이 아래로 오게 (상단 여백)


def _faces(im: Image.Image):
    """얼굴 위치 찾기 (OpenCV). 실패하면 빈 리스트."""
    try:
        import cv2
        import numpy as np
        g = cv2.cvtColor(np.array(im), cv2.COLOR_RGB2GRAY)
        cc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        min_side = max(40, int(min(im.size) * 0.06))
        fs = cc.detectMultiScale(g, scaleFactor=1.1, minNeighbors=6, minSize=(min_side, min_side))
        return [tuple(map(int, f)) for f in fs]
    except Exception as e:
        print(f"[얼굴 인식 건너뜀] {e}")
        return []


def contain_photo(im: Image.Image) -> Image.Image:
    """자르지 않고 사진 전체를 넣기: 폭에 맞춰 위쪽에 배치, 남는 공간은 같은 사진을 흐리게."""
    bg_sc = max(W / im.width, H / im.height)
    bg = im.resize((int(im.width * bg_sc) + 1, int(im.height * bg_sc) + 1))
    bx, by = (bg.width - W) // 2, (bg.height - H) // 2
    canvas = bg.crop((bx, by, bx + W, by + H)).filter(ImageFilter.GaussianBlur(45)).convert("RGBA")
    canvas.alpha_composite(Image.new("RGBA", (W, H), (0, 0, 0, 120)))
    sc = min(W / im.width, (H * 0.72) / im.height)
    ph = ImageEnhance_safe(im.resize((int(im.width * sc), int(im.height * sc)), Image.LANCZOS)).convert("RGBA")
    m = Image.new("L", ph.size, 0)
    ImageDraw.Draw(m).rectangle([18, 18, ph.width - 18, ph.height - 18], fill=255)
    ph.putalpha(m.filter(ImageFilter.GaussianBlur(14)))
    x = (W - ph.width) // 2
    y = max(0, int(H * 0.40 - ph.height / 2))
    canvas.alpha_composite(ph, (x, y))
    bh = int(H * 0.5)
    bot = Image.new("RGBA", (W, bh), (0, 0, 0, 255)); bot.putalpha(_grad(bh, 0, 200, 1.4))
    canvas.alpha_composite(bot, (0, H - bh))
    return canvas


def fill_photo(im: Image.Image):
    """화면을 꽉 채우되, 얼굴이 하나도 잘리지 않을 때만. 잘릴 것 같으면 None (→ 여백 방식)."""
    # 가로 사진(방송 캡처·단체 사진 등)은 꽉 채우면 양옆이 40% 넘게 잘림 → 얼굴 인식이 놓친 사람이
    # 잘릴 수 있으니 무조건 전체가 보이게 (여백 방식). 세로·정사각형에 가까운 사진만 채움.
    if im.width / im.height > 1.05:
        return None
    sc = max(W / im.width, H / im.height)
    faces = _faces(im)
    if not faces:
        if im.width / im.height > 0.9:  # 얼굴 못 찾은 정사각형 사진도 안전하게 안 채움
            return None
        x0, x1, y0 = im.width * 0.5, im.width * 0.5, im.height * 0.2
    else:
        x0 = min(f[0] for f in faces); x1 = max(f[0] + f[2] for f in faces)
        y0 = min(f[1] for f in faces)
        pad = max(f[2] for f in faces) * 0.9  # 얼굴 양옆으로 머리·어깨 여유
        x0, x1 = max(0, x0 - pad), min(im.width, x1 + pad)
        if (x1 - x0) * sc > W:  # 사람들이 가로로 넓게 퍼져 있음 → 잘리니까 안 채움
            return None
    im2 = im.resize((max(W, int(im.width * sc + 0.5)), max(H, int(im.height * sc + 0.5))), Image.LANCZOS)
    left = int((x0 + x1) / 2 * sc - W / 2)
    left = min(max(left, 0), im2.width - W)
    top = int(y0 * sc - H * 0.18)  # 얼굴이 화면 위쪽에 오되 머리 위 여유
    top = min(max(top, 0), im2.height - H)
    canvas = ImageEnhance_safe(im2.crop((left, top, left + W, top + H))).convert("RGBA")
    bh = int(H * 0.5)
    bot = Image.new("RGBA", (W, bh), (0, 0, 0, 255)); bot.putalpha(_grad(bh, 0, 200, 1.4))
    canvas.alpha_composite(bot, (0, H - bh))
    return canvas


def base_photo(path: str, keep_all: bool = False) -> Image.Image:
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    # 직접 보낸 사진, 또는 가로가 긴 사진에 여러 명이 나오면 → 자르지 않음
    if keep_all:
        filled = fill_photo(im)  # 사람이 다 들어오면 여백 없이 꽉 채움
        if filled is not None:
            return filled
        return contain_photo(im)
    if im.width / im.height > 0.95 and len(_faces(im)) >= 2:
        return contain_photo(im)
    sc = max(W / im.width, H / im.height)  # 화면을 꽉 채우는 배율
    faces = _faces(im)
    if faces:
        x0 = min(f[0] for f in faces); y0 = min(f[1] for f in faces)
        x1 = max(f[0] + f[2] for f in faces); y1 = max(f[1] + f[3] for f in faces)
        span = (x1 - x0) * 1.5  # 어깨까지 여유
        if span * sc > W:  # 단체 사진: 전원이 들어오도록 축소 (빈 곳은 흐린 배경)
            sc = max(W / span, H * 0.66 / im.height)  # 너무 작아지지 않게 (화면 높이 66% 이상)
        elif (y1 - y0) * sc < H * 0.12:  # 멀리 찍힌 사진: 살짝 확대
            sc *= min(1.6, (H * 0.14) / max((y1 - y0) * sc, 1))
    im2 = im.resize((max(1, int(im.width * sc + 0.5)), max(1, int(im.height * sc + 0.5))), Image.LANCZOS)

    # 배경: 같은 사진을 크게 흐리게 (사진이 화면을 다 못 채울 때 대비)
    bg_sc = max(W / im.width, H / im.height)
    bg = im.resize((int(im.width * bg_sc) + 1, int(im.height * bg_sc) + 1)).crop((0, 0, W, H))
    canvas = bg.filter(ImageFilter.GaussianBlur(40)).convert("RGBA")
    canvas.alpha_composite(Image.new("RGBA", (W, H), (0, 0, 0, 90)))

    if faces:
        cx = (x0 + x1) / 2 * sc
        left = int(cx - W / 2)
        top = int(y0 * sc - (MAST_BOTTOM + 30))  # 얼굴 윗부분이 로고 아래에 오도록
    else:
        left = (im2.width - W) // 2
        top = int((im2.height - H) * 0.28)
    left = min(max(left, 0), max(im2.width - W, 0)) if im2.width >= W else -(W - im2.width) // 2
    top = min(top, max(im2.height - H, 0))
    photo = ImageEnhance_safe(im2).convert("RGBA")
    if im2.width < W or im2.height < H:  # 배경과 경계가 자연스럽게 섞이도록 가장자리 페더
        m = Image.new("L", photo.size, 0)
        ImageDraw.Draw(m).rectangle([30, 30, photo.width - 30, photo.height - 30], fill=255)
        photo.putalpha(m.filter(ImageFilter.GaussianBlur(28)))
    canvas.paste(photo, (-left, -top), photo)
    # 위: 로고 가독성용 어둡게 / 아래: 헤드라인용 진하게
    bh = int(H * 0.5)
    bot = Image.new("RGBA", (W, bh), (0, 0, 0, 255)); bot.putalpha(_grad(bh, 0, 190, 1.4))
    canvas.alpha_composite(bot, (0, H - bh))
    return canvas


def ImageEnhance_safe(im):
    from PIL import ImageEnhance
    im = ImageEnhance.Contrast(im).enhance(1.06)
    return ImageEnhance.Color(im).enhance(1.05)


def _spaced(d, xy, text, fnt, fill, spacing):
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=fnt, fill=fill)
        x += d.textlength(ch, font=fnt) + spacing
    return x


def _spaced_len(d, text, fnt, spacing):
    return sum(d.textlength(ch, font=fnt) for ch in text) + spacing * (len(text) - 1)


def _shadow_text(img, xy, text, fnt, fill, blur=6, off=(0, 4), alpha=170):
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).text((xy[0] + off[0], xy[1] + off[1]), text, font=fnt, fill=(0, 0, 0, alpha))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(blur)))
    ImageDraw.Draw(img).text(xy, text, font=fnt, fill=fill)


def masthead(img, text):
    """상단 큰 영문 로고 — 폭에 맞춰 최대 크기."""
    d = ImageDraw.Draw(img)
    text = (text or "NEWS").upper()[:10]
    max_w = W - 120
    size = 260
    while size > 80:
        f = font("Black", size)
        sp = -size * 0.02
        if _spaced_len(d, text, f, sp) <= max_w:
            break
        size -= 6
    tw = _spaced_len(d, text, f, sp)
    x = (W - tw) / 2
    bb = d.textbbox((0, 0), text, font=f)
    y = 40 - bb[1]
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    _spaced(ImageDraw.Draw(sh), (x + 2, y + 5), text, f, (0, 0, 0, 120), sp)
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(8)))
    _spaced(ImageDraw.Draw(img), (x, y), text, f, WHITE, sp)
    return y + bb[3] + 18


def barcode(img, x, y, w=150, h=58, seed=7):
    d = ImageDraw.Draw(img)
    d.rectangle([x - 8, y - 8, x + w + 8, y + h + 30], fill=(255, 255, 255, 235))
    import random
    rnd = random.Random(seed)
    cx = x
    while cx < x + w:
        bw = rnd.choice([2, 2, 3, 4, 6])
        if rnd.random() > 0.35:
            d.rectangle([cx, y, cx + bw - 1, y + h], fill=(10, 10, 10))
        cx += bw + rnd.choice([1, 2, 3])
    nf = font("Bold", 18)
    num = f"8 801{seed:04d} {seed * 37 % 100000:05d}"
    d.text((x + (w - d.textlength(num, font=nf)) / 2, y + h + 4), num, font=nf, fill=(10, 10, 10))


# 포인트 컬러 (게시물마다 돌아가며 사용) — 하늘·핑크·라임·블루·옐로
PALETTE = [(170, 205, 255), (255, 150, 185), (205, 245, 110), (90, 170, 255), (255, 225, 110)]


def _glow_text(img, xy, text, fnt, fill, glow):
    """은은한 번짐(글로우) + 그림자 — 사진 위에서도 잘 읽히게."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((xy[0], xy[1] + 3), text, font=fnt, fill=(0, 0, 0, 150))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(9)))
    if glow:
        g = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(g).text(xy, text, font=fnt, fill=glow + (150,))
        img.alpha_composite(g.filter(ImageFilter.GaussianBlur(14)))
    ImageDraw.Draw(img).text(xy, text, font=fnt, fill=fill)


def photo_color(path: str):
    """사진에서 가장 눈에 띄는 색을 뽑아 밝은 파스텔톤 포인트 컬러로 변환.
    흑백·무채색 사진이면 레퍼런스처럼 연하늘색."""
    import colorsys
    im = Image.open(path).convert("RGB").resize((80, 80))
    buckets = {}
    for r, g, b in im.getdata():
        h, sat, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if sat < 0.25 or v < 0.25:  # 무채색·어두운 부분 제외
            continue
        k = int(h * 18)  # 색상 18구간
        w = sat * v
        acc = buckets.setdefault(k, [0.0, 0.0, 0.0])
        acc[0] += w; acc[1] += h * w; acc[2] += 1
    total = sum(1 for _ in im.getdata())
    if not buckets or max(a[2] for a in buckets.values()) < total * 0.04:
        return (170, 205, 255)
    k, acc = max(buckets.items(), key=lambda kv: kv[1][0])
    hue = acc[1] / acc[0]
    r, g, b = colorsys.hsv_to_rgb(hue, 0.45, 1.0)  # 파스텔 (사진 위에서 잘 읽히는 밝기)
    return (int(r * 255), int(g * 255), int(b * 255))


def render_story(story: dict, photo: dict | None, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    img = base_photo(photo["path"], keep_all=photo.get("kind") in ("직접 전송", "기사 사진")) if photo else text_layer()
    d = ImageDraw.Draw(img)
    color = photo_color(photo["path"]) if photo else PALETTE[int(story.get("issue_no", 0)) % len(PALETTE)]

    # 아래 왼쪽: 흰색 타이틀 + 포인트 컬러 타이틀 (각 최대 2줄)
    x, max_w = 80, W - 160
    fam = "Serif" if config.TITLE_FONT == "serif" else "Sans"
    f1, l1 = fit(d, story.get("headline_top", ""), "Black", [120, 112, 104, 96, 88, 80], max_w, 2, fam)
    f2, l2 = fit(d, story.get("headline_bottom", ""), "Black", [120, 112, 104, 96, 88, 80], max_w, 2, fam)
    lh1, lh2 = int(f1.size * 1.2), int(f2.size * 1.2)
    y = H - 150 - (len(l1) * lh1 + len(l2) * lh2)
    for ln in l1:
        _glow_text(img, (x, y), ln, f1, WHITE, None)
        y += lh1
    for ln in l2:
        _glow_text(img, (x, y), ln, f2, color, color)
        y += lh2

    # 사진 출처 (CC 라이선스 표기 — 오른쪽 아래 작게)
    credit_txt = " / ".join(x for x in ((photo or {}).get("credit"), (photo or {}).get("license")) if x)
    if photo and credit_txt:
        credit = f"Photo: {credit_txt}"[:70]
        cf = font("Regular", 18)
        d = ImageDraw.Draw(img)
        d.text((W - 40 - d.textlength(credit, font=cf), H - 44), credit, font=cf, fill=(255, 255, 255, 150))

    p = out_dir / "01.jpg"
    img.convert("RGB").save(p, "JPEG", quality=92, optimize=True)
    paths = [p]
    if story.get("body") or story.get("card_summary"):
        p2 = out_dir / "02.jpg"
        render_summary(story, photo, color).convert("RGB").save(p2, "JPEG", quality=92, optimize=True)
        paths.append(p2)
    return paths


def render_summary(story: dict, photo: dict | None, color) -> Image.Image:
    """2장째: 같은 사진을 흐리게 깔고 기사 요약."""
    fam = "Serif" if config.TITLE_FONT == "serif" else "Sans"
    if photo:
        im = ImageOps.fit(Image.open(photo["path"]).convert("RGB"), (W, H), method=Image.LANCZOS)
        img = im.filter(ImageFilter.GaussianBlur(38)).convert("RGBA")
        img.alpha_composite(Image.new("RGBA", (W, H), (10, 10, 14, 205)))
    else:
        img = text_layer()
    d = ImageDraw.Draw(img)
    x, max_w = 90, W - 180

    # 캡션 요약 본문을 그대로 사용 (이모지는 폰트에 없어서 제거)
    import re as _re
    body = _re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]", "", story.get("body") or "")
    paras = [p.strip() for p in body.split("\n") if p.strip()]
    if not paras:
        paras = story.get("card_summary") or []
    lf = font("Bold", 32, "Sans")
    title = (story.get("title") or f"{story.get('headline_top', '')} {story.get('headline_bottom', '')}").strip()
    tf, tl = fit(d, title, "Black", [64, 58, 54, 50, 46], max_w, 3, fam)
    top, bottom = 110, H - 200
    # 본문 글자 크기: 영역에 다 들어가는 가장 큰 크기
    for size in (44, 42, 40, 38, 36, 34, 32, 30, 28):
        bf = font("Regular", size, "Sans")
        lh, gap = int(size * 1.55), int(size * 0.9)
        lines = [wrap(d, p, bf, max_w) for p in paras]
        block = 76 + len(tl) * int(tf.size * 1.3) + 50 + sum(len(l) * lh for l in lines) + gap * (len(lines) - 1)
        if top + block <= bottom:
            break
    y = max(top, (bottom - block) // 2 + 30)

    d.text((x, y), story.get("tag") or "NEWS", font=lf, fill=color)
    d.line([x, y + 48, x + 64, y + 48], fill=color, width=4)
    y += 76
    for ln in tl:
        d.text((x, y), ln, font=tf, fill=WHITE)
        y += int(tf.size * 1.3)
    y += 50
    for li, lines_ in enumerate(lines):
        for ln in lines_:
            if y + lh > bottom + 20:
                break
            d.text((x, y), ln, font=bf, fill=(232, 232, 238))
            y += lh
        y += gap

    # 하단: 출처
    sf = font("Regular", 24, "Sans")
    d.line([x, H - 170, W - x, H - 170], fill=(255, 255, 255, 60), width=2)
    pc = " / ".join(x for x in ((photo or {}).get("credit"), (photo or {}).get("license")) if x)
    if pc:
        d.text((x, H - 135), f"사진 · {pc}"[:60], font=sf, fill=(190, 190, 198))
    return img


def text_layer() -> Image.Image:
    canvas = Image.new("RGBA", (W, H), BG + (255,))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([W * 0.1, 200, W * 0.9, 200 + W * 0.8], fill=accent() + (60,))
    canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(140)))
    return canvas


def crop_capture(path: str) -> bool:
    """휴대폰 캡처에서 앱 화면(상단 바·버튼·글자)을 잘라내고 게시물 사진 부분만 남김.
    배경색(흰/검정)이 아닌 줄이 가장 길게 이어지는 구간 = 사진. 못 찾으면 그대로 둠."""
    import numpy as np
    im = Image.open(path).convert("RGB")
    a = np.asarray(im).astype(int)
    h, w, _ = a.shape
    # 배경색 = 맨 위·맨 아래 줄(상태바·하단바 영역)에서 가장 많이 나오는 색
    k = max(3, h // 40)
    edge = np.concatenate([a[:k].reshape(-1, 3), a[-k:].reshape(-1, 3)]) // 16
    vals, cnt = np.unique(edge, axis=0, return_counts=True)
    bg = vals[cnt.argmax()] * 16 + 8
    # 진짜 '앱 화면 캡처'일 때만 자름: 위·아래 가장자리가 흰색/검정 단색이어야 함
    # (그냥 사진을 보낸 경우 벽·배경색을 앱 화면으로 착각해서 사람을 잘라먹는 것 방지)
    plain = bool((bg > 225).all() or (bg < 40).all())
    if not plain or cnt.max() / cnt.sum() < 0.6:
        print("[캡처 자르기] 앱 화면이 아닌 일반 사진 → 그대로 사용")
        return False
    near = (np.abs(a - bg).sum(axis=2) < 40).mean(axis=1)  # 줄마다 배경색 비율
    photo_row = near < 0.6
    best, start, run = (0, 0), None, 0
    for y, v in enumerate(list(photo_row) + [False]):
        if v and start is None:
            start = y
        elif not v and start is not None:
            if y - start > best[1] - best[0]:
                best = (start, y)
            start = None
    top, bot = best
    if bot - top < h * 0.25:
        return False
    band = a[top:bot]
    col_near = (np.abs(band - bg).sum(axis=2) < 40).mean(axis=0)
    # 가로도 '사진 열'이 가장 길게 이어지는 구간만 (PC 캡처의 오른쪽 댓글창 제거)
    left, right, st = 0, w, None
    bestc = (0, 0)
    for x, v in enumerate(list(col_near < 0.6) + [False]):
        if v and st is None:
            st = x
        elif not v and st is not None:
            if x - st > bestc[1] - bestc[0]:
                bestc = (st, x)
            st = None
    if bestc[1] - bestc[0] > w * 0.3:
        left, right = bestc
    im.crop((left, top, right, bot)).save(path, "JPEG", quality=95)
    print(f"[캡처 자르기] {w}x{h} → {right - left}x{bot - top}")
    return True


def photo_slide(path: str, out: Path) -> Path:
    """글자 없이 사진만 있는 장 (자르지 않고 전체, 남는 곳은 같은 사진을 흐리게)."""
    im = Image.open(path).convert("RGB")
    bg_sc = max(W / im.width, H / im.height)
    bg = im.resize((int(im.width * bg_sc) + 1, int(im.height * bg_sc) + 1))
    bx, by = (bg.width - W) // 2, (bg.height - H) // 2
    canvas = bg.crop((bx, by, bx + W, by + H)).filter(ImageFilter.GaussianBlur(45))
    canvas = Image.blend(canvas, Image.new("RGB", (W, H), (0, 0, 0)), 0.35)
    sc = min(W / im.width, H / im.height)
    ph = im.resize((int(im.width * sc), int(im.height * sc)), Image.LANCZOS)
    canvas.paste(ph, ((W - ph.width) // 2, (H - ph.height) // 2))
    canvas.save(out, "JPEG", quality=92, optimize=True)
    return out
