"""
Image collection for Ink Drop.

Finds the real images in an extracted article, downloads them, and rewrites the
markup to point at local files that get packaged into the EPUB.

Uses only the standard library for fetching so the service picks up no new
runtime dependencies.
"""

import os
import re
import base64
import hashlib
import mimetypes
import urllib.parse
import urllib.request
from dataclasses import dataclass

# Kindle screens top out around 1860px wide (Scribe), so anything much larger is
# wasted bytes. We aim for the largest candidate at or under this width.
TARGET_WIDTH = 1600

# Guard rails so one pathological page can't produce a 200MB email.
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 25 * 1024 * 1024
MAX_IMAGES = 40

# Below this, it's a spacer, tracking pixel, or icon - not article content.
MIN_IMAGE_BYTES = 3 * 1024

# Site furniture that shows up inside article bodies: avatars, emoji, logos,
# share buttons, tracking pixels. Matched against the image URL.
_CHROME_PATTERNS = re.compile(
    r"(profile_images|profile_banners|/emoji/|/abs\.twimg\.com/|avatar|gravatar"
    r"|/icons?/|/logos?/|sprite|spacer|/badges?/|pixel\.|/tracking/|1x1\.|blank\.gif"
    r"|favicon|button|/ads?/|doubleclick)",
    re.IGNORECASE,
)

FETCH_TIMEOUT = 20

# Deliberately omits image/webp: many CDNs (including Datadog's) honour Accept
# and hand back a format the Kindle converter is happier with.
_ACCEPT = "image/jpeg,image/png,image/gif;q=0.9,*/*;q=0.1"
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Extensions Kindle's converter handles reliably.
_SNIFF = [
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
]


@dataclass
class Image:
    """An image downloaded and ready to be packaged."""
    filename: str      # e.g. "img001.png", relative to the images/ dir
    media_type: str    # e.g. "image/png"
    data: bytes
    alt: str = ""


class _Collector:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.images: list[Image] = []
        self.total_bytes = 0
        self._by_identity: dict[str, str] = {}  # identity -> filename
        self._failed: set[str] = set()

    # -- URL selection ----------------------------------------------------

    def _identity(self, url: str) -> str:
        """Strip sizing params so the same asset at different widths dedups."""
        parts = urllib.parse.urlsplit(url)
        keep = [
            (k, v)
            for k, v in urllib.parse.parse_qsl(parts.query)
            if k.lower() not in {"w", "h", "width", "height", "dpr", "quality", "format", "fit"}
        ]
        return urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(keep), "")
        )

    def _upgrade(self, url: str) -> str:
        """Ask CDNs with known size parameters for a resolution worth reading."""
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()

        # Twitter serves name=small (680px) by default; name=large is ~2048px.
        if host == "pbs.twimg.com":
            params = urllib.parse.parse_qsl(parts.query)
            if any(k == "name" for k, _ in params):
                params = [(k, "large" if k == "name" else v) for k, v in params]
                return urllib.parse.urlunsplit(
                    (parts.scheme, parts.netloc, parts.path,
                     urllib.parse.urlencode(params), parts.fragment)
                )
        return url

    def _width_hint(self, url: str, descriptor: str) -> float:
        """
        Estimate the rendered pixel width of a candidate.

        Combines the srcset descriptor ("800w" / "2x") with any width param in
        the URL itself, which is how most image CDNs actually size things.
        """
        base = 0.0
        m = re.search(r"[?&](?:w|width)=(\d+)", url)
        if m:
            base = float(m.group(1))

        descriptor = (descriptor or "").strip().lower()
        if descriptor.endswith("w"):
            try:
                return float(descriptor[:-1])
            except ValueError:
                pass
        elif descriptor.endswith("x"):
            try:
                mult = float(descriptor[:-1])
            except ValueError:
                mult = 1.0
            # A dpr descriptor scales whatever width the URL asks for.
            return (base or 800.0) * mult

        m = re.search(r"[?&]dpr=([\d.]+)", url)
        if m and base:
            try:
                return base * float(m.group(1))
            except ValueError:
                pass
        return base

    def _parse_srcset(self, srcset: str) -> list[tuple[str, str]]:
        """Parse a srcset attribute into (url, descriptor) pairs."""
        out = []
        for entry in srcset.split(","):
            entry = entry.strip()
            if not entry:
                continue
            bits = entry.split()
            if not bits:
                continue
            out.append((bits[0], bits[1] if len(bits) > 1 else ""))
        return out

    def _candidates(self, node) -> list[tuple[str, str]]:
        """Gather every candidate URL for an <img> or <picture> node."""
        cands: list[tuple[str, str]] = []

        sources = node.find_all("source") if node.name in ("picture", "figure") else []
        for src in sources:
            for attr in ("srcset", "data-srcset"):
                if src.get(attr):
                    cands.extend(self._parse_srcset(src[attr]))

        imgs = [node] if node.name == "img" else node.find_all("img")
        for img in imgs:
            for attr in ("srcset", "data-srcset"):
                if img.get(attr):
                    cands.extend(self._parse_srcset(img[attr]))
            # Lazy-loading attributes, in rough order of how common they are.
            for attr in ("src", "data-src", "data-original", "data-lazy-src", "data-hi-res-src"):
                if img.get(attr):
                    cands.append((img[attr], ""))

        return cands

    def _best_url(self, node) -> str | None:
        """Pick the highest-resolution candidate that isn't overkill."""
        scored = []
        for url, descriptor in self._candidates(node):
            url = (url or "").strip()
            if not url or url.startswith("data:"):
                if url.startswith("data:"):
                    scored.append((0.0, url))
                continue
            absolute = urllib.parse.urljoin(self.base_url, url)
            if not absolute.startswith(("http://", "https://")):
                continue
            if _CHROME_PATTERNS.search(absolute):
                continue
            absolute = self._upgrade(absolute)
            scored.append((self._width_hint(absolute, descriptor), absolute))

        if not scored:
            return None

        # Prefer the largest at or under target; otherwise the smallest above it.
        under = [s for s in scored if 0 < s[0] <= TARGET_WIDTH]
        if under:
            return max(under, key=lambda s: s[0])[1]
        over = [s for s in scored if s[0] > TARGET_WIDTH]
        if over:
            return min(over, key=lambda s: s[0])[1]
        return scored[0][1]

    # -- fetching ---------------------------------------------------------

    def _sniff(self, data: bytes, declared: str, url: str) -> tuple[str, str] | None:
        """Identify the image from its magic bytes, ignoring the declared type."""
        for magic, media_type, ext in _SNIFF:
            if data.startswith(magic):
                return media_type, ext
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "image/webp", ".webp"
        if declared in ("image/jpeg", "image/png", "image/gif"):
            return declared, mimetypes.guess_extension(declared) or ".img"
        return None

    def _get(self, url: str) -> tuple[bytes, str] | None:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": _UA, "Accept": _ACCEPT, "Referer": self.base_url},
        )
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            declared = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            data = resp.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            return None
        return data, declared

    def _decode_data_uri(self, url: str) -> tuple[bytes, str] | None:
        m = re.match(r"data:([^;,]+)(;base64)?,(.*)$", url, re.DOTALL)
        if not m:
            return None
        media_type, is_b64, payload = m.group(1), bool(m.group(2)), m.group(3)
        try:
            data = base64.b64decode(payload) if is_b64 else urllib.parse.unquote_to_bytes(payload)
        except Exception:
            return None
        return data, media_type.lower()

    def fetch(self, url: str, alt: str) -> str | None:
        """Download one image, returning its local filename."""
        identity = url if url.startswith("data:") else self._identity(url)
        if identity in self._by_identity:
            return self._by_identity[identity]
        if identity in self._failed:
            return None
        if len(self.images) >= MAX_IMAGES or self.total_bytes >= MAX_TOTAL_BYTES:
            return None

        try:
            if url.startswith("data:"):
                got = self._decode_data_uri(url)
            else:
                got = self._get(url)
        except Exception:
            got = None

        if not got:
            self._failed.add(identity)
            return None

        data, declared = got
        sniffed = self._sniff(data, declared, url)

        # Some CDNs return WebP regardless of Accept. Kindle's converter is
        # unreliable with it, so ask the CDN once more for something else.
        if sniffed and sniffed[0] == "image/webp" and not url.startswith("data:"):
            retry = self._force_format(url)
            if retry:
                try:
                    got2 = self._get(retry)
                except Exception:
                    got2 = None
                if got2:
                    sniffed2 = self._sniff(got2[0], got2[1], retry)
                    if sniffed2 and sniffed2[0] != "image/webp":
                        data, sniffed = got2[0], sniffed2

        if not sniffed or len(data) < MIN_IMAGE_BYTES:
            self._failed.add(identity)
            return None

        media_type, ext = sniffed
        self.total_bytes += len(data)
        filename = f"img{len(self.images) + 1:03d}{ext}"
        self.images.append(Image(filename=filename, media_type=media_type, data=data, alt=alt))
        self._by_identity[identity] = filename
        return filename

    def _force_format(self, url: str) -> str | None:
        """Rewrite a CDN URL to explicitly request PNG instead of auto/WebP."""
        parts = urllib.parse.urlsplit(url)
        params = urllib.parse.parse_qsl(parts.query)
        if not any(k.lower() == "format" for k, _ in params):
            return None
        params = [(k, "png" if k.lower() == "format" else v) for k, v in params]
        return urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(params), parts.fragment)
        )


def process_images(soup, base_url: str) -> list[Image]:
    """
    Download the article's images and rewrite the soup in place.

    Every <picture>/<figure>/<img> that yields a usable image is replaced with a
    plain <img src="images/..."> (kept inside its <figure> so captions survive).
    Anything that fails to download is removed so the EPUB has no broken links.
    """
    collector = _Collector(base_url)

    # <picture> first: it contains an <img> that we'd otherwise process twice.
    nodes = soup.find_all("picture") + soup.find_all("img")

    for node in nodes:
        # Skip nodes already detached by an earlier replacement.
        if node.find_parent() is None and node is not soup:
            continue

        alt = ""
        img_tag = node if node.name == "img" else node.find("img")
        if img_tag is not None:
            alt = (img_tag.get("alt") or "").strip()

        url = collector._best_url(node)
        filename = collector.fetch(url, alt) if url else None

        if not filename:
            node.decompose()
            continue

        new_img = soup.new_tag("img", src=f"images/{filename}")
        if alt:
            new_img["alt"] = alt
        node.replace_with(new_img)

    # A page often references the same asset more than once - responsive
    # variants, or markup that survived extraction alongside a restored copy.
    # Keep the first placement of each image and drop the rest.
    seen_files: set[str] = set()
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if not src.startswith("images/"):
            continue
        if src not in seen_files:
            seen_files.add(src)
            continue
        figure = img.find_parent("figure")
        # Only take the figure with it when the image is all it holds.
        if figure is not None and len(figure.find_all("img")) == 1:
            figure.decompose()
        else:
            img.decompose()

    # Drop figures left empty once their images were removed.
    for figure in soup.find_all("figure"):
        if not figure.find("img"):
            figure.decompose()

    return collector.images
