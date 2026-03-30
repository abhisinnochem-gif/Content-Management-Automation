"""
Generic Sitemap Image Downloader
=================================
Universal product image downloader that works on ANY platform
(Shopify, WordPress, WooCommerce, Magento, BigCommerce, Squarespace, custom).

Strategy (no platform detection needed):
  1. Fetch sitemap.xml → follow sitemap index → collect all product URLs
  2. Score URLs against product name + UPC → find best match
  3. Load matched product page (requests → Playwright fallback)
  4. Score all images on page → pick best product image
  5. Download, remove background, resize and save

Sitemap formats handled:
  - Simple sitemap:       <urlset><url><loc>...</loc></url></urlset>
  - Sitemap index:        <sitemapindex><sitemap><loc>...</loc></sitemap></sitemapindex>
  - Shopify sitemaps:     sitemap_products_1.xml etc.
  - Compressed:           .xml.gz files
  - Nested indexes:       sitemap index pointing to another index

Usage:
    python generic_sitemap.py --url https://synevit.com --upc 860011318019 --ptid 223140 --name "Neurocomplex-B Slow-Release" --vendor "Synevit"
    python generic_sitemap.py --url https://incrediwear.com --upc 123456789 --name "Elbow Sleeve" --folder output/

Install:
    pip install requests beautifulsoup4 openpyxl pillow numpy rembg onnxruntime lxml
    pip install "crawlee[playwright]"
    playwright install chromium
"""

import os, sys, re, asyncio, argparse, gzip

# Force CPU-only — prevents CUDA DLL crash on machines without GPU drivers
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
os.environ["ORT_LOGGING_LEVEL"]    = "3"

import requests
from io import BytesIO
from urllib.parse import urlparse, urljoin, quote_plus
from bs4 import BeautifulSoup
from PIL import Image, ImageFilter, ImageEnhance
import numpy as np

# ── Output sizes ──────────────────────────────────────────────────────────────
SIZES = {
    "500x500_MasterDB":   (500,  500),
    "1000x1000_LiveSite": (1000, 1000),
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}

# Image keywords to skip (non-product images)
SKIP_KEYWORDS = [
    "logo", "icon", "banner", "badge", "award", "cert", "lifestyle",
    "background", "bg", "texture", "pattern", "footer", "header",
    "nav", "menu", "social", "facebook", "instagram", "twitter",
    "arrow", "button", "sprite", "placeholder", "loading", "404",
]

# Keywords that suggest a main product image
MAIN_KEYWORDS = [
    "front", "main", "hero", "primary", "product", "package",
    "bottle", "box", "tube", "container", "pack", "label",
]

# URL path segments that indicate product pages (covers all platforms)
PRODUCT_PATH_SIGNALS = [
    "/products/", "/product/", "/shop/", "/store/",
    "/item/", "/items/", "/catalog/", "/p/",
    # WordPress custom slugs
    "/eu-products/", "/us-products/", "/uk-products/",
    "/supplements/", "/vitamins/", "/nutrition/",
    # Magento
    ".html",
    # WooCommerce
    "/wc/",
]

# URL path segments to skip (listing/utility pages)
SKIP_PATH_SIGNALS = [
    "/cart", "/account", "/login", "/register", "/checkout",
    "/search", "/category", "/categories", "/tag", "/tags",
    "/blog", "/news", "/about", "/contact", "/faq",
    "/press/", "/articles/", "/journal/", "/post/", "/posts/",
    "/collections/all", "/collections/frontpage",
    "#",
]


# ── Helpers ───────────────────────────────────────────────────────────────────
def normalise_base(url):
    if not url.startswith("http"):
        url = "https://" + url
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def safe_get(url, timeout=15):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        return r
    except Exception:
        return None


def slugify(text):
    """Convert product name to URL-friendly slug for matching."""
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')


def name_tokens(product_name):
    """Extract meaningful words from product name (3+ chars)."""
    return [w for w in re.split(r'[\s\-_,/]+', product_name.lower()) if len(w) >= 3]


# ── Playwright fallback ───────────────────────────────────────────────────────
async def _playwright_get(url):
    from playwright.async_api import async_playwright
    result = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await browser.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)
            result["html"] = await page.content()
            result["url"]  = page.url
        except Exception as e:
            print(f"    Playwright error: {e}")
        finally:
            await browser.close()
    return result


def fetch_page(url):
    """Fetch page HTML — requests first, Playwright fallback."""
    r = safe_get(url)
    if r and r.status_code == 200 and len(r.text) > 500:
        return r.text, r.url
    print(f"    requests failed ({r.status_code if r else 'timeout'}) — trying Playwright...")
    result = asyncio.run(_playwright_get(url))
    return result.get("html", ""), result.get("url", url)


# ── Step 1: Sitemap crawl ─────────────────────────────────────────────────────
def fetch_xml(url):
    """Fetch XML content, handling gzip compression."""
    r = safe_get(url, timeout=20)
    if not r or r.status_code != 200:
        return None
    content = r.content
    # Handle gzip
    if url.endswith(".gz") or r.headers.get("Content-Encoding") == "gzip":
        try:
            content = gzip.decompress(content)
        except Exception:
            pass
    try:
        return BeautifulSoup(content, "lxml-xml")
    except Exception:
        return BeautifulSoup(content, "html.parser")


def get_sitemap_urls(base_url, max_product_urls=2000):
    """
    Crawl sitemap and return list of product page URLs.
    Handles sitemap indexes, nested indexes, and .xml.gz files.
    """
    print(f"    Fetching sitemap...")

    # Common sitemap locations
    sitemap_candidates = [
        f"{base_url}/sitemap.xml",
        f"{base_url}/sitemap_index.xml",
        f"{base_url}/sitemap/",
        f"{base_url}/sitemap.xml.gz",
    ]

    root_sitemap = None
    for candidate in sitemap_candidates:
        soup = fetch_xml(candidate)
        if soup:
            root_sitemap = (candidate, soup)
            print(f"    Found sitemap: {candidate}")
            break

    if not root_sitemap:
        print(f"    No sitemap found")
        return []

    sitemap_url, soup = root_sitemap
    product_urls = []
    visited_sitemaps = set()

    def process_sitemap(url, soup, depth=0):
        if url in visited_sitemaps or depth > 4:
            return
        visited_sitemaps.add(url)

        # Is this a sitemap index?
        index_locs = soup.find_all("loc")
        child_sitemaps = []
        direct_urls    = []

        for loc in index_locs:
            loc_url = loc.get_text(strip=True)
            if not loc_url:
                continue
            # Child sitemap reference
            loc_path = loc_url.split("?")[0]  # strip query params for extension check
            if loc_path.endswith(".xml") or loc_path.endswith(".xml.gz"):
                if loc_url != url:
                    child_sitemaps.append(loc_url)
            else:
                direct_urls.append(loc_url)

        # If we have child sitemaps — recurse (prioritise product sitemaps)
        if child_sitemaps:
            # Sort: product sitemaps first
            child_sitemaps.sort(key=lambda u: (
                0 if any(k in u.lower() for k in ["product", "shop", "item", "catalog"]) else 1
            ))
            for child_url in child_sitemaps:
                if len(product_urls) >= max_product_urls:
                    break
                child_soup = fetch_xml(child_url)
                if child_soup:
                    process_sitemap(child_url, child_soup, depth + 1)

        # Process direct URLs
        for page_url in direct_urls:
            if len(product_urls) >= max_product_urls:
                break
            product_urls.append(page_url)

    process_sitemap(sitemap_url, soup)
    print(f"    Collected {len(product_urls)} URLs from sitemap")
    return product_urls


# ── Step 2: Score URLs against product ───────────────────────────────────────
def score_url(url, product_name, upc=""):
    """
    Score a URL for how likely it is to be the correct product page.
    Returns int score (higher = better match).
    """
    url_low  = url.lower()
    path     = urlparse(url).path.lower()
    score    = 0

    # Hard skip — utility/nav pages
    if any(skip in url_low for skip in SKIP_PATH_SIGNALS):
        return -1

    # Must look like a product page
    is_product_path = any(sig in path for sig in PRODUCT_PATH_SIGNALS)
    if not is_product_path:
        # Still allow if it has enough path depth (custom slugs)
        depth = path.strip("/").count("/")
        if depth < 1:
            return -1

    # UPC in URL — very strong signal
    if upc and upc in url_low:
        score += 20

    # Product name tokens in URL
    tokens = name_tokens(product_name)
    matched_tokens = sum(1 for t in tokens if t in url_low)
    score += matched_tokens * 4

    # Slug match — full slugified name
    slug = slugify(product_name)
    slug_parts = [p for p in slug.split("-") if len(p) >= 3]
    for part in slug_parts:
        if part in path:
            score += 2

    # Product path signal bonus
    if is_product_path:
        score += 4  # strong boost for known product paths

    # Extra boost for most reliable product URL patterns
    if any(sig in path for sig in ["/products/", "/eu-products/", "/us-products/", "/product/"]):
        score += 3

    return score


def find_product_url_in_sitemap(sitemap_urls, product_name, upc=""):
    """Score all sitemap URLs and return the best match."""
    if not sitemap_urls:
        return None, 0

    scored = []
    for url in sitemap_urls:
        s = score_url(url, product_name, upc)
        if s > 0:
            scored.append((s, url))

    if not scored:
        return None, 0

    scored.sort(key=lambda x: x[0], reverse=True)

    print(f"    Top URL matches from sitemap:")
    for s, u in scored[:5]:
        print(f"      score={s} → {u}")

    best_score, best_url = scored[0]
    if best_score >= 3:
        return best_url, best_score

    return None, 0


# ── Step 3: Score and pick best image from product page ──────────────────────
def get_image_dimensions_from_src(src):
    m = re.search(r'-(\d{3,4})x\d+\.', src)
    return int(m.group(1)) if m else 0


def score_image(src, alt, width, is_in_main_content):
    score   = 0
    src_low = src.lower()
    alt_low = alt.lower()
    fname   = src_low.split("/")[-1].split("?")[0]

    if any(k in fname or k in alt_low for k in SKIP_KEYWORDS):
        return -1
    if width > 0 and width < 150:
        return -1
    if fname.endswith(".svg") or fname.endswith(".gif"):
        return -1

    if is_in_main_content:
        score += 5

    for kw in MAIN_KEYWORDS:
        if kw in alt_low: score += 3
        if kw in fname:   score += 2

    if width >= 1000: score += 4
    elif width >= 500: score += 3
    elif width >= 300: score += 1

    # Common product image CDN paths
    for cdn_signal in ["wp-content/uploads", "cdn.shopify", "files/", "media/catalog", "pub/media"]:
        if cdn_signal in src_low:
            score += 2
            break

    return score


def get_best_image_from_page(product_url, product_name):
    """Load product page and pick the best main product image."""
    print(f"    Loading: {product_url}")
    html, _ = fetch_page(product_url)
    if not html:
        return None, 0

    soup = BeautifulSoup(html, "html.parser")

    # Find main content container
    main_container = (
        soup.find("main") or
        soup.find(id=re.compile(r"main|content|product", re.I)) or
        soup.find(class_=re.compile(r"product|entry|content|article", re.I)) or
        soup
    )

    candidates = []
    seen       = set()

    for img in soup.find_all("img", src=True):
        src = img.get("src", "").strip()
        if not src or src in seen:
            continue
        seen.add(src)

        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = urljoin(product_url, src)
        elif not src.startswith("http"):
            continue

        alt   = img.get("alt", "")
        width = 0

        # Prefer high-res variants
        for attr in ["data-large_image", "data-src", "data-zoom-image", "data-full"]:
            large = img.get(attr, "")
            if large and large.startswith("http"):
                src = large
                break

        # Parse srcset for largest image
        srcset = img.get("srcset", "") or img.get("data-srcset", "")
        if srcset:
            best_w, best_url = 0, src
            for part in srcset.split(","):
                tokens = part.strip().split()
                if len(tokens) >= 2:
                    try:
                        w = int(tokens[1].replace("w", ""))
                        if w > best_w:
                            best_w, best_url = w, tokens[0]
                    except Exception:
                        pass
            if best_w > 0:
                src   = best_url
                width = best_w

        if width == 0:
            width = int(img.get("width", 0) or 0)
        if width == 0:
            width = get_image_dimensions_from_src(src)

        is_main = (main_container != soup) and (img in main_container.find_all("img"))
        score   = score_image(src, alt, width, is_main)

        if score > 0:
            candidates.append((score, src, alt, width))

    if not candidates:
        # Try og:image as last resort
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            print(f"    Falling back to og:image")
            return og["content"], 2

        print(f"    No suitable images found on page")
        return None, 0

    candidates.sort(key=lambda x: x[0], reverse=True)
    print(f"    Top image candidates:")
    for s, u, a, w in candidates[:4]:
        fname = u.split("/")[-1].split("?")[0]
        print(f"      score={s} w={w} alt='{a[:30]}' → {fname}")

    best = candidates[0]
    print(f"    ✓ Selected: {best[1].split('/')[-1].split('?')[0]} (score={best[0]})")
    return best[1], best[0]


# ── Step 4: Download image ────────────────────────────────────────────────────
def download_image(img_url):
    try:
        r = requests.get(img_url, headers=HEADERS, timeout=20)
        if r.status_code == 200 and len(r.content) > 1000:
            return r.content
        print(f"    HTTP {r.status_code}")
        return None
    except Exception as e:
        print(f"    Download error: {e}")
        return None


# ── Step 5: Background removal ────────────────────────────────────────────────
def has_clean_background(img, threshold=0.35):
    """Check if image already has a clean white/light background."""
    arr = np.array(img.convert("RGB"))
    # Check for white background
    white = (arr[:,:,0] > 230) & (arr[:,:,1] > 230) & (arr[:,:,2] > 230)
    if white.sum() / white.size > threshold:
        return True
    # Check for near-white / light grey background
    light = (arr[:,:,0] > 210) & (arr[:,:,1] > 210) & (arr[:,:,2] > 210)
    if light.sum() / light.size > 0.50:
        return True
    # Check corners — if all 4 corners are light, background is likely clean
    h, w = arr.shape[:2]
    corners = [arr[0,0], arr[0,w-1], arr[h-1,0], arr[h-1,w-1]]
    light_corners = sum(1 for c in corners if c[0] > 200 and c[1] > 200 and c[2] > 200)
    if light_corners >= 3:
        return True
    return False


def remove_background(img):
    if has_clean_background(img):
        print(f"    [BG] Clean background — skipping rembg")
        return img.convert("RGBA")
    print(f"    [BG] Removing background...")
    try:
        from rembg import remove as rembg_remove, new_session
        session = new_session("u2net")
        buf = BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        result = rembg_remove(buf.getvalue(), session=session, alpha_matting=False)
        rgba = Image.open(BytesIO(result)).convert("RGBA")
        arr  = np.array(rgba)
        # Clean up dark shadow pixels
        r, g, b, a = arr[:,:,0], arr[:,:,1], arr[:,:,2], arr[:,:,3]
        arr[(a < 200) & (r < 100) & (g < 100) & (b < 100), 3] = 0
        arr[a < 80, 3] = 0
        return Image.fromarray(arr, "RGBA")
    except Exception as e:
        print(f"    [BG] Failed ({e}) — using original")
        return img.convert("RGBA")


# ── Step 6: Process and save ──────────────────────────────────────────────────
def process_and_save(img_bytes, ptid, vendor_name):
    """Resize and save image to resized_images/{vendor}/{size}/{ptid}.jpg"""
    try:
        img = Image.open(BytesIO(img_bytes))
        if img.width < 100 or img.height < 100:
            print(f"    Too small ({img.width}x{img.height}) — skipping")
            return False

        print(f"    Downloaded: {img.width}x{img.height}px")
        img = remove_background(img)

        # Auto-crop to content
        arr  = np.array(img)
        mask = arr[:,:,3] > 10
        rows = np.any(mask, axis=1)
        cols = np.any(mask, axis=0)
        if rows.any() and cols.any():
            rmin = int(np.where(rows)[0][0]);  rmax = int(np.where(rows)[0][-1])
            cmin = int(np.where(cols)[0][0]);  cmax = int(np.where(cols)[0][-1])
            ph = max(4, int((rmax - rmin) * 0.02))
            pw = max(4, int((cmax - cmin) * 0.02))
            img = img.crop((max(0, cmin-pw), max(0, rmin-ph),
                            min(img.width, cmax+pw+1), min(img.height, rmax+ph+1)))
            print(f"    [Crop] {img.width}x{img.height}")

        base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resized_images")

        for size_label, (w, h) in SIZES.items():
            save_dir = os.path.join(base_dir, vendor_name, size_label)
            os.makedirs(save_dir, exist_ok=True)

            scale   = min(int(w * 0.90) / img.width, int(h * 0.90) / img.height)
            new_w   = int(img.width  * scale)
            new_h   = int(img.height * scale)
            resized = img.resize((new_w, new_h), Image.LANCZOS)

            # Sharpen
            rgb = resized.convert("RGB")
            rgb = rgb.filter(ImageFilter.UnsharpMask(radius=1.0, percent=120, threshold=3))
            rgb = ImageEnhance.Sharpness(rgb).enhance(1.3)
            r2, g2, b2 = rgb.split()
            _, _, _, a = resized.split()
            resized = Image.merge("RGBA", (r2, g2, b2, a))

            # Place on white canvas
            canvas = Image.new("RGBA", (w, h), (255, 255, 255, 255))
            canvas.paste(resized, ((w - new_w) // 2, (h - new_h) // 2), resized)

            out = os.path.join(save_dir, f"{ptid}.jpg")
            canvas.convert("RGB").save(out, "JPEG", quality=95, dpi=(96, 96))
            print(f"      → {size_label}/{ptid}.jpg")

        return True

    except Exception as e:
        print(f"    Error: {e}")
        return False


# ── Main entry point ──────────────────────────────────────────────────────────
def process_one(vendor_url, upc, product_name, folder=None, vendor_name="Unknown",
                ptid=None, product_url=None):
    """
    Universal sitemap-based image downloader.
    Works on any platform — no platform detection needed.

    Args:
      vendor_url   : vendor website base URL
      upc          : product UPC (used for URL matching)
      product_name : product name (used for URL + image matching)
      folder       : (unused — kept for API compatibility)
      vendor_name  : output subfolder name
      ptid         : output filename stem
      product_url  : skip sitemap, go directly to this URL
    """
    base_url = normalise_base(vendor_url)
    ptid     = ptid or upc or re.sub(r'[^a-z0-9]', '-', product_name.lower())[:30]

    print(f"  Vendor  : {base_url}")
    print(f"  PTID    : {ptid}  UPC: {upc}")

    # ── Step 1: Get product page URL ──────────────────────────────────────────
    if product_url:
        print(f"  Direct URL provided — skipping sitemap")
    else:
        sitemap_urls = get_sitemap_urls(base_url)

        if sitemap_urls:
            product_url, match_score = find_product_url_in_sitemap(sitemap_urls, product_name, upc)
            if product_url:
                print(f"  ✓ Matched: {product_url} (score={match_score})")
            else:
                print(f"  No sitemap match found (best score too low)")
        else:
            print(f"  Sitemap unavailable")

    if not product_url:
        print(f"  Could not locate product page")
        return 0

    # ── Step 2: Pick best image from product page ─────────────────────────────
    img_url, img_score = get_best_image_from_page(product_url, product_name)
    if not img_url:
        print(f"  No suitable image found on page")
        return 0

    if img_score < 2:
        print(f"  Warning: low image confidence (score={img_score})")

    # ── Step 3: Download + process + save ─────────────────────────────────────
    print(f"  Downloading: {img_url.split('/')[-1].split('?')[0]}")
    img_bytes = download_image(img_url)
    if img_bytes and process_and_save(img_bytes, ptid, vendor_name):
        return 1

    return 0


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Universal Sitemap Image Downloader — works on any platform",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python generic_sitemap.py --url https://synevit.com --upc 860011318019 --ptid 223140 --name "Neurocomplex-B Slow-Release" --vendor "Synevit"
  python generic_sitemap.py --url https://incrediwear.com --upc 123456 --name "Elbow Sleeve" --vendor "Incrediwear"
  python generic_sitemap.py --url https://someshop.com --upc 987654 --name "Product X" --product-url https://someshop.com/products/product-x
        """
    )
    parser.add_argument("--url",         required=True, help="Vendor website URL")
    parser.add_argument("--upc",         default="",    help="Product UPC")
    parser.add_argument("--ptid",        default="",    help="Output filename stem")
    parser.add_argument("--name",        default="",    help="Product name")
    parser.add_argument("--vendor",      default="Unknown", help="Vendor name (output subfolder)")
    parser.add_argument("--product-url", default="",    help="Direct product page URL — skips sitemap")
    args = parser.parse_args()

    count = process_one(
        vendor_url   = args.url,
        upc          = args.upc,
        product_name = args.name,
        vendor_name  = args.vendor,
        ptid         = args.ptid or args.upc,
        product_url  = args.product_url or None,
    )
    print(f"\nResult: {count} image(s) downloaded")
    sys.exit(0 if count > 0 else 1)
