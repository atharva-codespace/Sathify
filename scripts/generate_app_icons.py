"""Regenerate every Android launcher and splash asset from the brand logo.

    python scripts/generate_app_icons.py [path/to/logo.png]

Rerun this whenever the logo changes; it overwrites the committed PNGs in
`mobile/android/app/src/main/res/` and `mobile/assets/images/`. Requires
Pillow, which the backend virtualenv already has:

    backend/.venv/Scripts/python scripts/generate_app_icons.py

Written as a script rather than the `flutter_launcher_icons` package because
it also produces the adaptive-icon foreground and the per-density bitmaps the
native launch screen needs, and because it adds no dependency to the app.
"""

import sys
from pathlib import Path

from PIL import Image

_REPO = Path(__file__).resolve().parent.parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else _REPO / "mobile/assets/images/sathify_logo.png"
MOBILE = _REPO / "mobile"
RES = MOBILE / "android/app/src/main/res"

# Launcher icon densities: (folder suffix, legacy px, adaptive-foreground px)
# Legacy icons are 48dp; adaptive layers are 108dp.
DENSITIES = [
    ("mdpi", 48, 108),
    ("hdpi", 72, 162),
    ("xhdpi", 96, 216),
    ("xxhdpi", 144, 324),
    ("xxxhdpi", 192, 432),
]

# The native launch screen draws the bitmap at its natural size, so it needs a
# per-density file. ~96dp reads well without dominating a small phone.
SPLASH_DP = 96


def white_to_alpha(image: Image.Image) -> Image.Image:
    """Turn a white-backed logo into a transparent one, edges intact.

    A hard threshold would leave jagged edges on the anti-aliased curves. This
    is the standard un-multiply instead: alpha comes from how far the pixel is
    from white, and the colour is then divided back out, so a half-blended edge
    pixel becomes half-opaque *full-strength* green rather than opaque pale
    green.
    """
    # An already-transparent source is left alone. Deriving alpha from
    # whiteness would read a transparent pixel as (0,0,0,0) -> min 0 ->
    # fully opaque black, so re-running this over its own output would fill
    # the background in. That matters because the default source below *is*
    # the output of a previous run.
    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        return image.convert("RGBA")

    image = image.convert("RGBA")
    pixels = image.load()
    width, height = image.size

    # The source background is 254, not 255, so a bare `255 - min(...)` leaves
    # alpha=1 across the whole canvas: invisible to the eye, fatal to the trim
    # below, because the bounding box then covers every pixel. Anything this
    # close to white is background.
    NEAR_WHITE = 6

    for y in range(height):
        for x in range(width):
            r, g, b, _ = pixels[x, y]
            alpha = 255 - min(r, g, b)
            if alpha <= NEAR_WHITE:
                pixels[x, y] = (0, 0, 0, 0)
                continue
            scale = 255 / alpha
            pixels[x, y] = (
                max(0, min(255, int((r - (255 - alpha)) * scale))),
                max(0, min(255, int((g - (255 - alpha)) * scale))),
                max(0, min(255, int((b - (255 - alpha)) * scale))),
                alpha,
            )
    return image


def fitted(logo: Image.Image, canvas: int, coverage: float, background=None):
    """The logo centred on a square canvas, occupying `coverage` of its width."""
    target = max(1, int(canvas * coverage))
    scaled = logo.copy()
    scaled.thumbnail((target, target), Image.LANCZOS)

    out = Image.new("RGBA", (canvas, canvas), background or (0, 0, 0, 0))
    out.paste(
        scaled,
        ((canvas - scaled.width) // 2, (canvas - scaled.height) // 2),
        scaled,
    )
    return out


def main():
    logo = white_to_alpha(Image.open(SRC))
    logo = logo.crop(logo.split()[-1].getbbox())  # trim the source margin
    print(f"trimmed logo: {logo.size}")

    # --- in-app asset ----------------------------------------------------
    assets = MOBILE / "assets/images"
    assets.mkdir(parents=True, exist_ok=True)
    master = fitted(logo, 512, 1.0)
    master.save(assets / "sathify_logo.png")
    print(f"wrote {assets / 'sathify_logo.png'}")

    for suffix, legacy_px, adaptive_px in DENSITIES:
        folder = RES / f"mipmap-{suffix}"
        folder.mkdir(parents=True, exist_ok=True)

        # Legacy square icon: opaque white, generous logo.
        fitted(logo, legacy_px, 0.78, background=(255, 255, 255, 255)).convert(
            "RGB"
        ).save(folder / "ic_launcher.png")

        # Adaptive foreground: transparent, and small enough to survive the
        # circular/squircle masks, which crop to the inner ~66% of the layer.
        fitted(logo, adaptive_px, 0.52).save(folder / "ic_launcher_foreground.png")

        # Native launch screen bitmap, one per density.
        drawable = RES / f"drawable-{suffix}"
        drawable.mkdir(parents=True, exist_ok=True)
        scale = legacy_px / 48  # dp -> px for this bucket
        fitted(logo, int(SPLASH_DP * scale), 1.0).save(drawable / "splash_logo.png")

        print(f"  {suffix}: icon {legacy_px}px, adaptive {adaptive_px}px")


if __name__ == "__main__":
    main()
