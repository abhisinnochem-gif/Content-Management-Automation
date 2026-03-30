"""
Generic WordPress Brand Site Image Downloader
==============================================
Works for any WordPress brand site (no WooCommerce store).
Uses product name search to find product page, then scores
and picks the best main product image.

Flow:
  1. Try WordPress search: /?s={product_name}&post_type=product
     Fallback: /?s={product_name}
  2. If search blocked — try to find product listing page
     (/products/, /shop/, /our-products/, etc.)
     Score all product links against product name
  3. Load matched product page
  4. Score all images on page:
     - Inside main content area
     - Alt text / filename contains product/front/package keywords
     - Largest image wins ties
     - Skip: logo, banner, lifestyle, award, icon, bg
  5. Download best image

Usage:
    python generic_wordpress.py --url https://blistex.com --upc 041388016047 --name "Daily Condition SPF 20" --folder output/
    python generic_wordpress.py --url https://zonnic.com --upc 123456789 --name "Pouch Mint 4mg" --folder output/

Install:
    pip install requests beautifulsoup4 pillow numpy rembg onnxruntime
    pip install "crawlee[playwright]"  # fallback for Cloudflare-protected sites
    playwright install chromium
"""

import os, sys, argparse, requests, re, asyncio

# Force CPU-only — prevents CUDA DLL crash on machines without GPU drivers
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
os.environ["ORT_LOGGING_LEVEL"]    = "3"

from io import BytesIO
from urllib.parse import urlparse, urljoin, quote_plus
from bs4 import BeautifulSoup
from PIL import Image, ImageFilter, ImageEnhance
import numpy as np

SIZES = {
    "500x500_MasterDB":   (500,  500),
    "1000x1000_LiveSite": (1000, 1000),
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}

# Image filename/alt keywords to skip
SKIP_KEYWORDS = [
    "logo", "icon", "banner", "badge", "award", "cert", "lifestyle",
    "background", "bg", "texture", "pattern", "footer", "header",
    "nav", "menu", "social", "facebook", "instagram", "twitter",
    "arrow", "button", "sprite", "placeholder", "loading",
]

# Keywords that indicate a main product image
MAIN_KEYWORDS = [
    "front", "main", "hero", "primary", "product", "package",
    "bottle", "box", "tube", "container", "pack",
]

# Common product listing page paths to try
LISTING_PATHS = [
    "/products/", "/product/", "/shop/", "/our-products/",
    "/products-page/", "/all-products/", "/catalog/",
    "/pouches/", "/sprays/", "/items/",
    "/eu-products/", "/us-products/", "/uk-products/",
    "/store/", "/supplements/", "/vitamins/",
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


def get_image_dimensions_from_src(src):
    """Extract width hint from srcset or filename like -1920x1080."""
    m = re.search(r'-(\d{3,4})x\d+\.', src)
    if m:
        return int(m.group(1))
    return 0


# ── Crawlee fallback page loader ──────────────────────────────────────────────
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
    """Fetch page HTML — tries plain requests first, falls back to Playwright."""
    r = safe_get(url)
    if r and r.status_code == 200 and len(r.text) > 500:
        return r.text, r.url
    # Fallback to Playwright
    print(f"    requests failed (HTTP {r.status_code if r else 'timeout'}) — trying Playwright...")
    result = asyncio.run(_playwright_get(url))
    return result.get("html", ""), result.get("url", url)


# ── Step 1: Find product page URL ─────────────────────────────────────────────
def score_link(href, text, product_name):
    """Score a link by how well it matches the product name."""
    name_low = product_name.lower()
    href_low = href.lower()
    text_low = text.lower()
    score    = 0

    words = [w for w in re.split(r'[\s\-_,]+', name_low) if len(w) > 2]
    for word in words:
        if word in href_low: score += 3
        if word in text_low: score += 2

    # Exact name in text
    if name_low in text_low: score += 5

    return score


def find_product_url_via_search(base_url, product_name):
    """Try WordPress search endpoints to find product URL."""
    search_queries = [
        f"{base_url}/?s={quote_plus(product_name)}&post_type=product",
        f"{base_url}/?s={quote_plus(product_name)}",
        f"{base_url}/search-results?q={quote_plus(product_name)}",
        f"{base_url}/search?q={quote_plus(product_name)}",
    ]

    for search_url in search_queries:
        print(f"    Trying search: {search_url}")
        html, final_url = fetch_page(search_url)
        if not html:
            continue

        soup = BeautifulSoup(html, "html.parser")
        candidates = []

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href.startswith("http"):
                href = urljoin(base_url, href)
            # Must be on same domain and look like a product page
            if urlparse(href).netloc != urlparse(base_url).netloc:
                continue
            path = urlparse(href).path.lower()
            if not any(seg in path for seg in [
                "/product/", "/products/", "/shop/", "/pouches/", "/sprays/", "/item/",
                "/eu-products/", "/us-products/", "/uk-products/",
                "/supplements/", "/vitamins/", "/store/",
            ]):
                continue
            if any(bad in path for bad in ["cart", "account", "login", "search", "category", "tag"]):
                continue

            text  = a.get_text(strip=True)
            score = score_link(href, text, product_name)
            if score > 0:
                candidates.append((score, href, text))

        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            print(f"    Search results (top 3):")
            for s, h, t in candidates[:3]:
                print(f"      score={s} '{t[:40]}' → {h}")

            # Filter out generic listing pages
            skip_paths = ["/shop/", "/shop", "/products/", "/store/",
                          "/catalog/", "/collections/"]
            filtered = [
                (s, h, t) for s, h, t in candidates
                if not any(h.rstrip("/").endswith(p.rstrip("/"))
                           for p in skip_paths)
                and h.count("/") >= 4  # must have enough path depth
            ]
            if not filtered:
                filtered = candidates  # fallback if all filtered

            best = filtered[0]
            if best[0] >= 2:
                print(f"    ✓ Selected: {best[1]}")
                return best[1]

    return None


def find_product_url_via_listing(base_url, product_name):
    """Try known product listing pages and score all links."""
    for path in LISTING_PATHS:
        listing_url = base_url + path
        html, _ = fetch_page(listing_url)
        if not html or len(html) < 500:
            continue

        soup       = BeautifulSoup(html, "html.parser")
        candidates = []

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href.startswith("http"):
                href = urljoin(base_url, href)
            if urlparse(href).netloc != urlparse(base_url).netloc:
                continue
            # Skip nav/utility links
            if any(bad in href.lower() for bad in ["cart", "account", "login", "#", "javascript"]):
                continue

            text  = a.get_text(strip=True)
            score = score_link(href, text, product_name)
            if score > 0:
                candidates.append((score, href, text))

        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            print(f"    Listing '{path}' — top matches:")
            for s, h, t in candidates[:3]:
                print(f"      score={s} '{t[:40]}' → {h}")
            best = candidates[0]
            if best[0] >= 4:
                print(f"    ✓ Selected from listing: {best[1]}")
                return best[1]

    return None


def find_product_url(base_url, product_name):
    """Find product page URL — try search first, then listing pages."""
    url = find_product_url_via_search(base_url, product_name)
    if url:
        return url
    print(f"    Search failed — trying product listing pages...")
    url = find_product_url_via_listing(base_url, product_name)
    return url


# ── Step 2: Score and pick best image from product page ───────────────────────
def score_image(src, alt, width, is_in_main_content):
    """
    Score an image for being the main product shot.
    Higher = more likely to be the correct product image.
    """
    score    = 0
    src_low  = src.lower()
    alt_low  = alt.lower()
    fname    = src_low.split("/")[-1].split("?")[0]

    # Skip known non-product images
    if any(k in fname or k in alt_low for k in SKIP_KEYWORDS):
        return -1

    # Skip tiny images
    if width > 0 and width < 200:
        return -1

    # In main content area — strong signal
    if is_in_main_content:
        score += 5

    # Main product keywords in alt or filename
    for kw in MAIN_KEYWORDS:
        if kw in alt_low: score += 3
        if kw in fname:   score += 2

    # Image size — larger = more likely product shot
    if width >= 1000: score += 4
    elif width >= 500: score += 3
    elif width >= 300: score += 1

    # wp-content/uploads — WordPress media library
    if "wp-content/uploads" in src_low:
        score += 2

    # Skip SVG
    if fname.endswith(".svg"):
        return -1

    return score


def get_best_image_from_page(product_url, product_name):
    """
    Load product page and pick the best main product image.
    Returns (image_url, score) or (None, 0).
    """
    print(f"    Loading product page: {product_url}")
    html, _ = fetch_page(product_url)
    if not html:
        return None, 0

    soup = BeautifulSoup(html, "html.parser")

    # Find main content containers
    main_containers = (
        soup.find("main") or
        soup.find(id=re.compile(r"main|content|product", re.I)) or
        soup.find(class_=re.compile(r"product|entry|content|article", re.I)) or
        soup
    )

    # Collect all candidate images
    candidates = []
    seen       = set()

    for img in soup.find_all("img", src=True):
        src = img.get("src", "").strip()
        if not src or src in seen:
            continue
        seen.add(src)

        # Make absolute URL
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = urljoin(product_url, src)
        elif not src.startswith("http"):
            continue

        alt   = img.get("alt", "")
        # Get best width from srcset or data-large_image
        width = 0
        large = img.get("data-large_image", "") or img.get("data-src", "")
        if large:
            src = large  # use full-size URL
        srcset = img.get("srcset", "") or img.get("data-srcset", "")
        if srcset:
            # Pick largest from srcset
            parts = srcset.split(",")
            best_w, best_url = 0, src
            for part in parts:
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

        # Check if in main content
        is_main = img in main_containers.find_all("img") if main_containers != soup else False

        score = score_image(src, alt, width, is_main)
        if score > 0:
            candidates.append((score, src, alt, width))

    if not candidates:
        print(f"    No suitable images found on page")
        return None, 0

    # Sort by score
    candidates.sort(key=lambda x: x[0], reverse=True)

    print(f"    Top image candidates:")
    for s, url, alt, w in candidates[:5]:
        fname = url.split("/")[-1].split("?")[0]
        print(f"      score={s} w={w} alt='{alt[:30]}' → {fname}")

    best_score, best_url, best_alt, best_width = candidates[0]
    print(f"    ✓ Selected: {best_url.split('/')[-1].split('?')[0]} (score={best_score})")
    return best_url, best_score



# ── Process and save (new folder structure) ───────────────────────────────────
def process_and_save_wp(img_bytes, ptid, vendor_name, img_index=0):
    """Save to resized_images/{vendor_name}/500x500/{ptid}.jpg etc."""
    from PIL import Image, ImageFilter, ImageEnhance
    import numpy as np
    from io import BytesIO
    try:
        img = Image.open(BytesIO(img_bytes))
        if img.width < 100 or img.height < 100:
            return False
        print(f"    Downloaded: {img.width}x{img.height}px")
        img = remove_shadow(img)

        arr  = np.array(img)
        mask = arr[:,:,3] > 10
        rows = np.any(mask, axis=1)
        cols = np.any(mask, axis=0)
        if rows.any() and cols.any():
            rmin = int(np.where(rows)[0][0]);  rmax = int(np.where(rows)[0][-1])
            cmin = int(np.where(cols)[0][0]);  cmax = int(np.where(cols)[0][-1])
            ph = max(4, int((rmax - rmin) * 0.02))
            pw = max(4, int((cmax - cmin) * 0.02))
            img = img.crop((max(0,cmin-pw), max(0,rmin-ph),
                            min(img.width,cmax+pw+1), min(img.height,rmax+ph+1)))

        file_name = ptid if img_index == 0 else f"{ptid}_{img_index}"
        base_dir  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resized_images")

        for size_label, (w, h) in SIZES.items():
            save_dir = os.path.join(base_dir, vendor_name, size_label)
            os.makedirs(save_dir, exist_ok=True)
            scale   = min(int(w*0.90)/img.width, int(h*0.90)/img.height)
            new_w   = int(img.width  * scale)
            new_h   = int(img.height * scale)
            resized = img.resize((new_w, new_h), Image.LANCZOS)
            rgb = resized.convert("RGB")
            rgb = rgb.filter(ImageFilter.UnsharpMask(radius=1.0, percent=120, threshold=3))
            rgb = ImageEnhance.Sharpness(rgb).enhance(1.3)
            r2,g2,b2 = rgb.split(); _,_,_,a = resized.split()
            resized = Image.merge("RGBA",(r2,g2,b2,a))
            canvas = Image.new("RGBA",(w,h),(255,255,255,255))
            canvas.paste(resized,((w-new_w)//2,(h-new_h)//2),resized)
            out = os.path.join(save_dir, f"{file_name}.jpg")
            canvas.convert("RGB").save(out, "JPEG", quality=95, dpi=(96,96))
            print(f"      → {size_label}/{file_name}.jpg")
        return True
    except Exception as e:
        print(f"    Error: {e}")
        return False


# ── Background removal ────────────────────────────────────────────────────────
def has_clean_background(img, threshold=0.35):
    """Check if image already has a clean white/light background."""
    arr = np.array(img.convert("RGB"))
    white = (arr[:,:,0] > 230) & (arr[:,:,1] > 230) & (arr[:,:,2] > 230)
    if white.sum() / white.size > threshold:
        return True
    light = (arr[:,:,0] > 210) & (arr[:,:,1] > 210) & (arr[:,:,2] > 210)
    if light.sum() / light.size > 0.50:
        return True
    h, w = arr.shape[:2]
    corners = [arr[0,0], arr[0,w-1], arr[h-1,0], arr[h-1,w-1]]
    light_corners = sum(1 for c in corners if c[0] > 200 and c[1] > 200 and c[2] > 200)
    if light_corners >= 3:
        return True
    return False

def remove_shadow(img):
    if has_clean_background(img):
        print(f"    [BG] Clean background — skipping rembg")
        return img.convert("RGBA")
    print(f"    [BG] Removing background + shadows...")
    try:
        from rembg import remove as rembg_remove, new_session
        import numpy as np
        session = new_session("u2net")
        buf = BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        # alpha_matting can cause Cholesky warnings — use False for stability
        result = rembg_remove(
            buf.getvalue(),
            session=session,
            alpha_matting=False,
        )
        rgba = Image.open(BytesIO(result)).convert("RGBA")
        arr  = np.array(rgba)
        r, g, b, a = arr[:,:,0], arr[:,:,1], arr[:,:,2], arr[:,:,3]
        arr[(a < 200) & (r < 100) & (g < 100) & (b < 100), 3] = 0
        arr[a < 80, 3] = 0
        return Image.fromarray(arr, "RGBA")
    except Exception as e:
        print(f"    [BG] Retrying without alpha matting...")
        try:
            from rembg import remove as rembg_remove, new_session
            session = new_session("u2net")
            buf = BytesIO()
            img.convert("RGB").save(buf, format="PNG")
            result = rembg_remove(buf.getvalue(), session=session, alpha_matting=False)
            return Image.open(BytesIO(result)).convert("RGBA")
        except Exception as e2:
            print(f"    [BG] Failed ({e2}) — using original")
            return img.convert("RGBA")


# ── Process and save image ────────────────────────────────────────────────────
def process_image_bytes(img_bytes, folder, name):
    try:
        img = Image.open(BytesIO(img_bytes))
        if img.width < 100 or img.height < 100:
            print(f"    Too small ({img.width}x{img.height}) — skipping")
            return False

        print(f"    Downloaded: {img.width}x{img.height}px")
        img = remove_shadow(img)

        arr  = np.array(img)
        mask = arr[:,:,3] > 10
        rows = np.any(mask, axis=1)
        cols = np.any(mask, axis=0)
        if rows.any() and cols.any():
            rmin = int(np.where(rows)[0][0]);  rmax = int(np.where(rows)[0][-1])
            cmin = int(np.where(cols)[0][0]);  cmax = int(np.where(cols)[0][-1])
            ph = max(4, int((rmax - rmin) * 0.02))
            pw = max(4, int((cmax - cmin) * 0.02))
            img = img.crop((max(0,cmin-pw), max(0,rmin-ph),
                            min(img.width,cmax+pw+1), min(img.height,rmax+ph+1)))
            print(f"    [Crop] {img.width}x{img.height}")

        os.makedirs(folder, exist_ok=True)
        for label, (w, h) in SIZES.items():
            scale   = min(int(w*0.90)/img.width, int(h*0.90)/img.height)
            new_w   = int(img.width  * scale)
            new_h   = int(img.height * scale)
            resized = img.resize((new_w, new_h), Image.LANCZOS)

            rgb = resized.convert("RGB")
            rgb = rgb.filter(ImageFilter.UnsharpMask(radius=1.0, percent=120, threshold=3))
            rgb = ImageEnhance.Sharpness(rgb).enhance(1.3)
            r2,g2,b2 = rgb.split(); _,_,_,a = resized.split()
            resized = Image.merge("RGBA",(r2,g2,b2,a))

            canvas = Image.new("RGBA",(w,h),(255,255,255,255))
            canvas.paste(resized,((w-new_w)//2,(h-new_h)//2),resized)
            out = os.path.join(folder, f"{name}_{label}.jpg")
            canvas.convert("RGB").save(out, "JPEG", quality=95, dpi=(96,96))
            print(f"      → {os.path.basename(out)}")

        return True
    except Exception as e:
        print(f"    Error processing image: {e}")
        return False


# ── Download image ────────────────────────────────────────────────────────────
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


# ── Main: one product ─────────────────────────────────────────────────────────
def process_one(vendor_url, upc, product_name, folder, vendor_name='Unknown', ptid=None, product_url=None):
    base_url = normalise_base(vendor_url)
    print(f"  Vendor  : {base_url}")
    print(f"  Product : {product_name}")
    print(f"  UPC     : {upc}")

    # Step 1: find product page (skip search if direct URL provided)
    if product_url:
        print(f"  Using direct product URL: {product_url}")
    else:
        product_url = find_product_url(base_url, product_name)
    if not product_url:
        print(f"  Could not find product page for '{product_name}'")
        return 0

    # Step 2: pick best image from product page
    img_url, score = get_best_image_from_page(product_url, product_name)
    if not img_url:
        print(f"  No suitable image found")
        return 0

    if score < 3:
        print(f"  Image score too low ({score}) — may not be correct product image")

    # Step 3: download and process
    ptid = ptid or upc or re.sub(r'[^a-z0-9]', '-', product_name.lower())[:20]
    print(f"  Downloading: {img_url.split('/')[-1].split('?')[0]}")
    img_bytes = download_image(img_url)
    if img_bytes and process_and_save_wp(img_bytes, ptid, vendor_name):
        return 1

    return 0


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generic WordPress Brand Site Image Downloader")
    parser.add_argument("--url",    required=True, help="Vendor website URL")
    parser.add_argument("--upc",    default="",    help="Product UPC")
    parser.add_argument("--name",   required=True, help="Product name")
    parser.add_argument("--folder", required=True, help="Output folder")
    args = parser.parse_args()

    os.makedirs(args.folder, exist_ok=True)
    count = process_one(args.url, args.upc, args.name, args.folder)
    print(f"\n  Result: {count} image(s) downloaded")
    sys.exit(0 if count > 0 else 1)
