"""
EPUB builder for Ink Drop.

Kindle's converter does not fetch remote images out of an emailed HTML file, so
articles with pictures have to arrive as a package that carries the image bytes
with it. EPUB is the format Send to Kindle officially supports, and it is just a
zip with a manifest - no extra dependencies needed.
"""

import re
import hashlib
import zipfile
from datetime import datetime, timezone
from io import BytesIO

from lxml import etree, html as lxml_html

# Tags worth keeping in a reading view. Everything else is unwrapped (its text
# survives, the tag does not) so no layout cruft reaches the Kindle.
ALLOWED_TAGS = {
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr",
    "ul", "ol", "li",
    "blockquote", "pre", "code",
    "em", "strong", "b", "i", "sup", "sub",
    "figure", "figcaption", "img", "a",
    "table", "thead", "tbody", "tr", "td", "th",
}

# Attributes kept per tag; everything else is dropped.
ALLOWED_ATTRS = {
    "img": {"src", "alt"},
    "a": {"href"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}

# Tags removed entirely, contents and all.
DROP_TAGS = {"script", "style", "noscript", "iframe", "form", "input", "button", "svg", "video", "audio"}

STYLESHEET = """\
body { font-family: Georgia, serif; line-height: 1.6; margin: 0 1em; }
h1 { font-size: 1.5em; margin: 1em 0 0.75em; }
h2 { font-size: 1.25em; margin: 1.5em 0 0.5em; }
h3 { font-size: 1.1em; margin: 1.25em 0 0.5em; }
p { margin: 0 0 1em; text-align: justify; }
figure { margin: 1.5em 0; text-align: center; page-break-inside: avoid; }
figure img { max-width: 100%; }
img { max-width: 100%; height: auto; }
figcaption { font-size: 0.85em; font-style: italic; margin-top: 0.5em; text-align: center; }
blockquote { margin: 1em 1.5em; font-style: italic; }
pre { font-family: monospace; font-size: 0.85em; white-space: pre-wrap; margin: 1em 0; }
code { font-family: monospace; font-size: 0.9em; }
.source { font-size: 0.85em; color: #555; margin-bottom: 1.5em; word-wrap: break-word; }
"""

CONTAINER_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def _esc(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def sanitize_to_xhtml(body_html: str) -> str:
    """
    Turn arbitrary article HTML into a well-formed XHTML body fragment.

    EPUB readers reject anything that isn't valid XML, so this both filters the
    markup down to the allowed set and re-serializes it through an XML writer.
    """
    if not body_html or not body_html.strip():
        return ""

    root = lxml_html.fragment_fromstring(body_html, create_parent="div")

    for el in root.xpath("//*"):
        tag = el.tag
        if not isinstance(tag, str):  # comments, processing instructions
            el.getparent().remove(el)
            continue
        if tag in DROP_TAGS:
            el.drop_tree()
            continue
        if tag not in ALLOWED_TAGS and el is not root:
            el.drop_tag()  # keep the text, lose the element
            continue
        allowed = ALLOWED_ATTRS.get(tag, set())
        for name in list(el.attrib):
            if name not in allowed:
                del el.attrib[name]

    # Drop images that lost their source during rewriting.
    for img in root.xpath("//img"):
        if not img.get("src"):
            img.drop_tree()

    xml = etree.tostring(root, method="xml", encoding="unicode")
    # Unwrap the synthetic <div> wrapper.
    xml = re.sub(r"^<div>", "", xml)
    xml = re.sub(r"</div>$", "", xml)
    return xml


def _xhtml_page(title: str, body: str) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en">
<head>
  <meta charset="utf-8"/>
  <title>{_esc(title)}</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
{body}
</body>
</html>
"""


def _content_opf(title: str, author: str, book_id: str, images, modified: str) -> str:
    manifest = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '<item id="css" href="style.css" media-type="text/css"/>',
        '<item id="article" href="article.xhtml" media-type="application/xhtml+xml"/>',
    ]
    for i, img in enumerate(images):
        manifest.append(
            f'<item id="img{i + 1}" href="images/{img.filename}" media-type="{img.media_type}"/>'
        )

    manifest_xml = "\n    ".join(manifest)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{_esc(book_id)}</dc:identifier>
    <dc:title>{_esc(title)}</dc:title>
    <dc:creator>{_esc(author)}</dc:creator>
    <dc:language>en</dc:language>
    <meta property="dcterms:modified">{modified}</meta>
  </metadata>
  <manifest>
    {manifest_xml}
  </manifest>
  <spine toc="ncx">
    <itemref idref="article"/>
  </spine>
</package>
"""


def _nav_xhtml(title: str) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="en" lang="en">
<head><meta charset="utf-8"/><title>Contents</title></head>
<body>
  <nav epub:type="toc" id="toc">
    <h1>Contents</h1>
    <ol><li><a href="article.xhtml">{_esc(title)}</a></li></ol>
  </nav>
</body>
</html>
"""


def _toc_ncx(title: str, book_id: str) -> str:
    """EPUB 2 table of contents - older Kindle converters still look for it."""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="{_esc(book_id)}"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>{_esc(title)}</text></docTitle>
  <navMap>
    <navPoint id="navpoint-1" playOrder="1">
      <navLabel><text>{_esc(title)}</text></navLabel>
      <content src="article.xhtml"/>
    </navPoint>
  </navMap>
</ncx>
"""


def build_epub(title: str, body_html: str, images=None, source_url: str = "", author: str = "Ink Drop") -> bytes:
    """
    Package an article and its images into an EPUB file.

    Args:
        title: Article title, shown in the Kindle library
        body_html: Article markup, with images already pointing at "images/..."
        images: Image objects from images.process_images()
        source_url: Original URL, shown as a small line under the title
        author: Byline for the Kindle library

    Returns:
        The complete .epub file as bytes.
    """
    images = images or []
    book_id = "urn:ink-drop:" + hashlib.sha1((source_url or title).encode("utf-8")).hexdigest()
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    parts = [f"<h1>{_esc(title)}</h1>"]
    if source_url:
        parts.append(f'<p class="source">{_esc(source_url)}</p>')
    parts.append(sanitize_to_xhtml(body_html))
    article = _xhtml_page(title, "\n".join(parts))

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # The mimetype entry must be first and stored uncompressed.
        zf.writestr(
            zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED
        )
        zf.writestr("META-INF/container.xml", CONTAINER_XML)
        zf.writestr("OEBPS/content.opf", _content_opf(title, author, book_id, images, modified))
        zf.writestr("OEBPS/nav.xhtml", _nav_xhtml(title))
        zf.writestr("OEBPS/toc.ncx", _toc_ncx(title, book_id))
        zf.writestr("OEBPS/style.css", STYLESHEET)
        zf.writestr("OEBPS/article.xhtml", article)
        for img in images:
            zf.writestr(f"OEBPS/images/{img.filename}", img.data)

    return buf.getvalue()
