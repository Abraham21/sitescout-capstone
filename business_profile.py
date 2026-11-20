"""Carry real photos, portfolio links and review sources from research into each concept."""

import base64
import io
import json
import re
import warnings
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from PIL import Image, ImageOps, UnidentifiedImageError


BUSINESS_TYPES = {
    "LocalBusiness",
    "ProfessionalService",
    "HousePainter",
    "HomeAndConstructionBusiness",
    "GeneralContractor",
    "Organization",
}


def business_nodes(soup):
    """Read a publisher's business records before scripts are removed from its page."""
    records = []
    for tag in soup.select('script[type="application/ld+json"]')[:20]:
        try:
            obj = json.loads(tag.string or "")
        except (ValueError, RecursionError):
            continue
        nodes = obj.get("@graph", [obj]) if isinstance(obj, dict) else obj
        if not isinstance(nodes, list):
            continue
        for node in nodes:
            if not isinstance(node, dict) or not isinstance(node.get("name"), str):
                continue
            kinds = node.get("@type", [])
            if isinstance(kinds, str):
                kinds = [kinds]
            if isinstance(kinds, list) and any(
                isinstance(k, str) and k in BUSINESS_TYPES for k in kinds
            ):
                records.append(node)
                if len(records) == 12:
                    return records
    return records


def reviews_from(node):
    """Keep complete, short, attributed reviews attached to this particular business."""
    reviews = node.get("review", [])
    rows = []
    for review in (reviews if isinstance(reviews, list) else [reviews])[:12]:
        if not isinstance(review, dict) or review.get("@type") != "Review":
            continue
        subject = review.get("itemReviewed")
        if subject and (not isinstance(subject, dict) or subject.get("name") != node["name"]):
            continue
        author = review.get("author", "")
        author = author.get("name") if isinstance(author, dict) else author
        quote = review.get("reviewBody")
        if isinstance(quote, str) and isinstance(author, str):
            rows.append({"quote": " ".join(quote.split()), "author": " ".join(author.split())})
    return rows


def named_service_areas(text):
    """Read place names from explicit service-area statements, without inferring a radius."""
    places = []
    for clause in re.finditer(
        r"(?:^|[.!?;]\s+)(?:we (?:(?:only|proudly) )?serve|(?:proudly )?serving|"
        r"(?:our )?service areas?(?: (?:includes?|covers?))?)"
        r"\s*:?\s+([^.!?;:\n]{2,160})(?=[.!?;]|$)",
        text,
        re.IGNORECASE,
    ):
        for name in re.split(r"\s*(?:,|\band\b|&)\s*", clause[1]):
            name = name.removeprefix("the ").strip()
            if (
                re.fullmatch(r"[A-ZÀ-ÖØ-Þ][\w'-]*(?: [A-ZÀ-ÖØ-Þ][\w'-]*){0,4}", name)
                and name.casefold() not in {"ca", "more", "homeowners", "nearby areas", "surrounding areas"}
                and name not in places
            ):
                places.append(name)
    return places


def quoted_customers(soup):
    rows = []
    region = re.compile(r"testimonial|customer.review", re.I)
    for quote in soup.find_all("blockquote", limit=20):
        section = quote.find_parent(id=region) or quote.find_parent(class_=region)
        credit = quote.find("cite") or (
            quote.parent.find("figcaption") if quote.parent.name == "figure" else None
        )
        if section and credit:
            author_text = set(credit.stripped_strings)
            words = " ".join(s for s in quote.stripped_strings if s not in author_text)
            rows.append({"quote": words, "author": credit.get_text(" ", strip=True)})
    return rows[:6]


def listing_profile(profile, url, text, evidence_id):
    """Keep observed links to a business’s gallery or review profile for source attribution."""
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
    """Find masthead logos and project photos in gallery context on pages later verified as first-party."""
    candidates = []
    gallery_pattern = re.compile(r"gallery|portfolio|our[- ]work|projects", re.IGNORECASE)
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    gallery = gallery_pattern.search(urlsplit(url).path) or gallery_pattern.search(title)
    for image_number, image in enumerate(soup.select("img")[:80], 1):
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
            generic_label = re.fullmatch(
                r"(?:img|image|photo|gallery)[- _]?\d+\.(?:jpe?g|webp|png)", alt, re.IGNORECASE
            )
            if not alt or generic_label:
                if not alt and not within_gallery and not (gallery and figure):
                    continue
                alt = f"Company gallery photo {image_number}"
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
                if (
                    original.format not in {"JPEG", "PNG", "WEBP", "AVIF"}
                    or width * height > 16_000_000
                ):
                    return ""
                if kind == "project" and (width < 250 or height < 140):
                    return ""
                display = ImageOps.exif_transpose(original).convert("RGBA")
                display.thumbnail((300, 160) if kind == "logo" else (1280, 900))
                canvas = Image.new("RGB", display.size, "white")
                canvas.paste(display, mask=display.getchannel("A"))
                output = io.BytesIO()
                canvas.save(output, format="JPEG", quality=82, optimize=True)
                # Keep high-noise photos within the renderer’s 900k-character data URI limit.
                if output.tell() > 660_000:
                    canvas.thumbnail((900, 675))
                    output = io.BytesIO()
                    canvas.save(output, format="JPEG", quality=75, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ):
        return ""


def hero_fit(data_uri):
    """Prefer photos that fill a landscape hero with enough resolution and visible detail."""
    try:
        body = base64.b64decode(data_uri.partition(",")[2], validate=True)
        with Image.open(io.BytesIO(body)) as image:
            width, height = image.size
            aspect = width / height
            crop = min(aspect / 1.5, 1.5 / aspect)
            size = min(width / 1000, 1)
            sample = image.convert("L").resize((64, 48))
        levels = sample.histogram()
        visible = sum(levels[12:244]) / sum(levels)
        return crop * (0.5 + 0.5 * size) * visible
    except (OSError, ValueError):
        return 0


def fixture_image(mapping, url, kind):
    """Read an explicitly staged, bundled image for a synthetic case. No network access."""
    name = mapping.get(url, "")
    if not name or Path(name).name != name:
        return ""
    file = Path(__file__).parent / "assets" / name
    return small_raster(file.read_bytes(), kind) if file.is_file() else ""
