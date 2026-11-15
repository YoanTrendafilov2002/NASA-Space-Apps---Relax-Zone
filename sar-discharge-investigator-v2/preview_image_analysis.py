"""Compare whole-frame browse appearance without interpreting radar measurements."""

from io import BytesIO


MAX_PREVIEW_BYTES = 8 * 1024 * 1024
MAX_PREVIEW_PIXELS = 8_000_000
_DISPLAY_TONE_GAP = 32  # Heuristic on the 0–255 image scale, with no physical units.


def _percentiles(histogram, count):
    cumulative = 0
    targets = iter((0.10, 0.50, 0.90))
    target = next(targets)
    values = []
    for level, hits in enumerate(histogram):
        cumulative += hits
        while target is not None and cumulative >= target * count:
            values.append(level)
            target = next(targets, None)
    return tuple(values)


def describe_preview(data: bytes) -> dict | None:
    """Summarize rendered luminance in opaque pixels of a bounded browse image."""
    if not data or len(data) > MAX_PREVIEW_BYTES:
        return None
    try:
        from PIL import Image
    except ImportError:
        return None

    try:
        with Image.open(BytesIO(data), formats=("PNG", "JPEG", "WEBP")) as image:
            width, height = image.size
            frame_pixels = width * height
            if (min(width, height) < 32 or frame_pixels > MAX_PREVIEW_PIXELS
                    or getattr(image, "is_animated", False)
                    or image.mode not in {"L", "LA", "P", "RGB", "RGBA"}):
                return None
            if "A" in image.getbands() or "transparency" in image.info:
                # Provider grayscale PNGs can carry transparency through a tRNS chunk.
                rgba = image.convert("RGBA")
                opaque = rgba.getchannel("A").point([0] * 255 + [255])
                histogram = rgba.convert("L").histogram(mask=opaque)
            else:
                histogram = image.convert("L").histogram()
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return None

    count = sum(histogram)
    if count < frame_pixels / 4:
        return None
    return {"size": (width, height), "opaque_fraction": count / frame_pixels,
            "tone": _percentiles(histogram, count)}


def compare_previews(anchor: dict, neighbor: dict) -> dict:
    """Compare whole-frame tone. The caller checks matching acquisition metadata."""
    aw, ah = anchor["size"]
    nw, nh = neighbor["size"]
    ratios = (aw / ah, nw / nh)
    if max(ratios) / min(ratios) > 1.10 or abs(anchor["opaque_fraction"] - neighbor["opaque_fraction"]) > 0.10:
        return {"status": "not_comparable", "summary": "The preview frames or visible coverage differ too much for a tone comparison."}

    a_low, a_mid, a_high = anchor["tone"]
    n_low, n_mid, n_high = neighbor["tone"]
    median_gap = n_mid - a_mid
    spread_gap = (n_high - n_low) - (a_high - a_low)
    if abs(median_gap) >= _DISPLAY_TONE_GAP:
        tone = "lighter" if median_gap > 0 else "darker"
        return {"status": "different", "summary": f"This preview looks {tone} overall than the selected preview. Image rendering can affect this."}
    if abs(spread_gap) >= _DISPLAY_TONE_GAP:
        contrast = "more" if spread_gap > 0 else "less"
        return {"status": "different", "summary": f"This preview has {contrast} contrast than the selected preview. Image rendering can affect this."}
    return {"status": "similar", "summary": "The previews have similar overall tone. Smaller or local differences can still be present."}
