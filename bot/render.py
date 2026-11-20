"""카드 이미지 생성 (1080x1350, 인스타 4:5): 인물 사진 + 하단 그라데이션 + 큰 헤드라인."""
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


def font(weight: str, size: int):
    key = (weight, size)
    if key in _cache:
        return _cache[key]
    for w in FALLBACK.get(weight, [weight, "Regular", "Bold"]):
        try:
            _cache[key] = ImageFont.truetype(f"{FONT_DIR}/NotoSansCJK-{w}.ttc", size, index=KR)
            return _cache[key]
        except OSError:
            continue
    raise OSError("한글 폰트(Noto Sans CJK)를 찾을 수 없음 — 워크플로의 '한글 폰트 설치' 단계 확인")


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


def fit(draw, text, weight, sizes, max_w, max_lines):
    """1줄에 들어가는 가장 큰 크기 우선 → 안 되면 2줄을 균형 있게 나눔."""
    text = text.strip()
    for s in sizes:
        f = font(weight, s)
        if draw.textlength(text, font=f) <= max_w:
            return f, [text]
    if max_lines >= 2:
        words = text.split(" ")
        for s in sizes:
            f = font(weight, s)
            best = None
            for i in range(1, len(words)):
                a, b = " ".join(words[:i]), " ".join(words[i:])
                wa, wb = draw.textlength(a, font=f), draw.textlength(b, font=f)
                if wa <= max_w and wb <= max_w and (best is None or max(wa, wb) < best[0]):
                    best = (max(wa, wb), [a, b])
            if best:
                return f, best[1]
    f = font(weight, sizes[-1])
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


def photo_layer(path: str) -> Image.Image:
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    # 얼굴이 보통 위쪽에 있으므로 위쪽 기준으로 자르기
    im = ImageOps.fit(im, (W, int(H * 0.78)), method=Image.LANCZOS, centering=(0.5, 0.2))
    canvas = Image.new("RGBA", (W, H), BG + (255,))
    canvas.paste(im, (0, 0))
    # 아래로 갈수록 어두워지는 그라데이션
    grad = Image.new("L", (1, H), 0)
    start, end = int(H * 0.38), int(H * 0.74)
    for y in range(H):
        a = 0 if y < start else 255 if y > end else int(255 * ((y - start) / (end - start)) ** 1.3)
        grad.putpixel((0, y), a)
    shade = Image.new("RGBA", (W, H), BG + (255,))
    shade.putalpha(grad.resize((W, H)))
    canvas.alpha_composite(shade)
    return canvas


def text_layer() -> Image.Image:
    """사진 없을 때: 어두운 배경 + 큰 포인트 원."""
    canvas = Image.new("RGBA", (W, H), BG + (255,))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    a = accent()
    ImageDraw.Draw(glow).ellipse([W * 0.15, 80, W * 0.85, 80 + W * 0.7], fill=a + (70,))
    canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(120)))
    return canvas


def render_story(story: dict, photo: dict | None, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    img = photo_layer(photo["path"]) if photo else text_layer()
    d = ImageDraw.Draw(img)
    a = accent()

    # 왼쪽 위 분야 태그
    tag = (story.get("tag") or "연예")[:4]
    tf = font("Black", 52)
    tw = d.textlength(tag, font=tf)
    box = [60, 60, 60 + tw + 56, 60 + 92]
    d.rectangle(box, fill=(0, 0, 0, 150), outline=a, width=6)
    d.text((60 + 28, 60 + 12), tag, font=tf, fill=WHITE)
    # 오른쪽 위 계정명
    hf = font("Bold", 30)
    hw = d.textlength(config.BRAND_HANDLE, font=hf)
    d.text((W - 60 - hw, 88), config.BRAND_HANDLE, font=hf, fill=(255, 255, 255, 220))

    # 아래쪽 헤드라인
    max_w = W - 140
    f1, l1 = fit(d, story.get("headline_top", ""), "Black", [104, 96, 88, 80, 72], max_w, 2)
    f2, l2 = fit(d, story.get("headline_bottom", ""), "Black", [104, 96, 88, 80, 72], max_w, 2)
    f3, l3 = fit(d, story.get("subline", ""), "Bold", [48, 44, 40], max_w, 1)
    block = (len(l1) * f1.size + len(l2) * f2.size) * 1.18 + f3.size * 1.18 + 40
    y = H - 110 - block
    y = center_text(img, y, l1, f1, WHITE)
    y = center_text(img, y, l2, f2, a)
    center_text(img, y + 40, l3, f3, a)

    # 사진 출처 (CC 라이선스 표기)
    if photo:
        cf = font("Regular", 22)
        credit = f"Photo: {photo['credit']} / {photo['license']} / {photo.get('source', 'Wikimedia Commons')}"[:95]
        ImageDraw.Draw(img).text((40, H - 46), credit, font=cf, fill=(200, 200, 200))

    p = out_dir / "01.jpg"
    img.convert("RGB").save(p, "JPEG", quality=90, optimize=True)
    return [p]
