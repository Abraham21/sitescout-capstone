"""Carry real photos, portfolio links and review sources from research into each concept."""

import base64
import io
import re
import warnings
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from PIL import Image, ImageOps, UnidentifiedImageError


def listing_profile(profile, url, text, evidence_id):
    """Search results can establish a public gallery; its photos stay on the publisher."""
    parts = urlsplit(url)
    domain = (parts.hostname or "").removeprefix("www.")
    platforms = {
        "yelp.com": ("Yelp", r"^/biz/[^/]+/?$"),
        "houzz.com": ("Houzz", r"^/professionals/[^/]+/[^/]+"),
        "instagram.com": ("Instagram", r"^/[^/]+/?$"),
        "facebook.com": ("Facebook", r"^/(?:people/)?[^/]+(?:/\d+)?/?$"),
        "bbb.org": ("BBB", r"/profile/"),
        "angi.com": ("Angi", r"-reviews-\d+"),
    }
    platform = next(
        (
            label
            for h, (label, path) in platforms.items()
            if (domain == h or domain.endswith("." + h)) and re.search(path, parts.path)
        ),
        "",
    )
    if not platform:
        return
    item = {"url": url, "source_url": url, "evidence_id": evidence_id}
    # Never infer photos or completed projects from a site's default “Photos & reviews” heading.
    count = re.search(r"(?:see all\s+)?\b([1-9]\d{0,3})\s+(?:photos|projects)\b", text, re.IGNORECASE)
    portfolio = re.search(
        r"\b(?:our (?:work|portfolio|projects)|portfolio of|recent projects?|before and after)\b",
        text,
        re.IGNORECASE,
    )
    if count or portfolio:
        label = count[0].removeprefix("See all ").removeprefix("see all ") if count else "Work"
        profile["work_links"].append({**item, "label": f"{label.capitalize()} on {platform}"})
    if re.search(
        r"\b[1-9]\d{0,4}\s+(?:recommended\s+)?reviews?\b|customer reviews?:", text, re.IGNORECASE
    ):
        profile["review_links"].append({**item, "label": f"Read reviews on {platform}"})


def page_images(soup, url):
    """Identify only labeled project or logo images on pages later verified as first-party."""
    candidates = []
    gallery_pattern = re.compile(r"gallery|portfolio|our[- ]work|projects", re.IGNORECASE)
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    gallery = gallery_pattern.search(urlsplit(url).path) or gallery_pattern.search(title)
    for image in soup.select("img")[:80]:
        alt = " ".join(str(image.get("alt", "")).split())
        figure = image.find_parent("figure")
        if not alt and figure and (caption := figure.find("figcaption")):
            alt = caption.get_text(" ", strip=True)
        source = image.get("data-src") or image.get("data-lazy-src") or image.get("src")
        if not source or len(alt) > 140:
            continue
        logo = bool(
            re.search(r"\blogo\b", alt, re.IGNORECASE) or re.search(r"/logos?/", source, re.IGNORECASE)
        )
        if logo:
            # A partner badge and the contractor's own masthead are different assets.
            home = image.find_parent("a", href=True)
            if (
                not home
                or urljoin(url, home["href"]).rstrip("/")
                != f"{urlsplit(url).scheme}://{urlsplit(url).netloc}"
            ):
                continue
            alt = alt or "Business logo from the company website"
        else:
            within_gallery = image.find_parent(id=gallery_pattern) or image.find_parent(
                class_=gallery_pattern
            )
            if not (
                gallery
                or within_gallery
                or re.search(r"\b(?:our|completed) project\b", alt, re.IGNORECASE)
            ):
                continue
            if re.fullmatch(
                r"(?:img|image|photo|gallery)[- _]?\d+\.(?:jpe?g|webp|png)", alt, re.IGNORECASE
            ):
                alt = "Photograph from the company’s gallery"
            if not alt:
                continue
        kind = "logo" if logo else "project"
        if re.search(
            r"stock|unsplash|pexels|shutterstock|advertisement|badge", f"{source} {alt}", re.IGNORECASE
        ):
            continue
        candidates.append({"asset_url": urljoin(url, source), "alt": alt, "kind": kind})
    return candidates


def small_raster(body, kind):
    """Decode the original bytes and re-encode a small, offline-safe image without metadata."""
    if not body or len(body) > 3_000_000:
        return ""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(body)) as original:
                width, height = original.size
                if original.format not in {"JPEG", "PNG", "WEBP", "AVIF"} or width * height > 16_000_000:
                    return ""
                if kind == "project" and (width < 250 or height < 140):
                    return ""
                display = ImageOps.exif_transpose(original).convert("RGBA")
                display.thumbnail((300, 160) if kind == "logo" else (1280, 900))
                canvas = Image.new("RGB", display.size, "white")
                canvas.paste(display, mask=display.getchannel("A"))
                output = io.BytesIO()
                canvas.save(output, format="JPEG", quality=82, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ):
        return ""


def fixture_image(mapping, url, kind):
    """Read an explicitly staged, bundled image for a synthetic case. No network access."""
    name = mapping.get(url, "")
    if not name or Path(name).name != name:
        return ""
    file = Path(__file__).parent / "assets" / name
    return small_raster(file.read_bytes(), kind) if file.is_file() else ""
