"""Square covers from pillarboxed thumbnails (YouTube: 16:9 frame, square art, empty sides).

Crops only when everything outside the square is empty background — a real 16:9 picture
is never cut. Used for covers noaap saved itself; user-supplied covers are left alone.
"""

from __future__ import annotations

import io

from PIL import Image, ImageChops

BACKGROUND_TOLERANCE = 40  # per channel; JPEG noise and near-black vignettes count as background
CORNER_SPREAD = 30  # the four corners must agree on the background colour


def square_if_padded(data: bytes) -> bytes | None:
    """A square crop of `data` if its non-square part is empty background, else None."""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        return None
    w, h = img.size
    if w == h or min(w, h) < 64:
        return None
    rgb = img.convert("RGB")

    corners = [rgb.getpixel(p) for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
    if any(max(c[i] for c in corners) - min(c[i] for c in corners) > CORNER_SPREAD for i in range(3)):
        return None  # no uniform border: nothing is padding
    background = tuple(sorted(c[i] for c in corners)[1] for i in range(3))

    diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, background)).convert("L")
    content = diff.point(lambda v: 255 if v > BACKGROUND_TOLERANCE else 0).getbbox()
    if content is None:
        return None  # a blank image
    side = min(w, h)
    left, top, right, bottom = content
    # centred (YouTube centres the art), shifted only as far as needed to keep all content:
    # art often fades into the background, so its detected box is a little too small
    if w > h:
        if right - left > side:
            return None  # the picture really is wider than a square
        x = _fit((w - side) // 2, left, right, side, w)
        box = (x, 0, x + side, h)
    else:
        if bottom - top > side:
            return None
        y = _fit((h - side) // 2, top, bottom, side, h)
        box = (0, y, w, y + side)

    out = io.BytesIO()
    rgb.crop(box).save(out, "JPEG", quality=95, optimize=True)
    return out.getvalue()


def _fit(start: int, lo: int, hi: int, side: int, total: int) -> int:
    """Move a window [start, start+side) the least amount so that it covers [lo, hi)."""
    start = min(start, lo)
    start = max(start, hi - side)
    return min(max(start, 0), total - side)


# -- thumbnails for the grid (§9, slice 140) ------------------------------------------------------
#
# Measured on the user's library over NFS, 1,221 covers beside their albums: a card is **10.5rem
# wide, about 168 px**, and it was sent the cover as it stands — **65 KiB on average, 220 KiB at
# worst**, 2.67 MB for one view of forty and about **82 MB** for the whole grid. Not a stall (27 ms
# each) but bytes nobody can see: the file crosses NFS, crosses the HTTP connection, and is then
# scaled down to a thumb's size by the browser.

THUMB_SIDE = 336        # about twice the card's width, so it is still sharp on a 2× screen
THUMB_QUALITY = 78


def thumbnail(data: bytes, side: int = THUMB_SIDE) -> tuple[bytes, str] | None:
    """`data` scaled to fit `side`, as JPEG — or None where it is no bigger than that already.

    None rather than a re-encode: a cover that is already small gains nothing from being passed
    through an encoder, and a JPEG round trip is never free.
    """
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        return None
    if max(img.size) <= side:
        return None
    img.thumbnail((side, side), Image.LANCZOS)
    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=THUMB_QUALITY, optimize=True)
    return out.getvalue(), "image/jpeg"
