"""
Generic Magento Image Downloader
==================================
Works for ANY Magento 2 store — no custom code needed per vendor.
Mirrors the approach of generic_shopify.py: downloads the full product
catalog via the public REST API, matches by UPC/name, then pulls images.

Magento REST API used:
  GET /rest/V1/products?searchCriteria[pageSize]=100&searchCriteria[currentPage]=N

UPC is stored in custom_attributes — common field names tried:
  upc, barcode, ean, upc_code, barcode_value, gtin

Matching strategy (in order):
  1. UPC match in custom_attributes across catalog pages
  2. Product name title scoring (fallback)

Image saving:
  resized_images/{Vendor Name}/500x500/{PTID}.jpg
  resized_images/{Vendor Name}/500x500/{PTID}_1.jpg ...
  resized_images/{Vendor Name}/1000x1000/{PTID}.jpg
  resized_images/{Vendor Name}/1000x1000/{PTID}_1.jpg ...

  PTID = value from Excel PTID column — used as output filename.
  Main image = {PTID}.jpg, additional = {PTID}_1.jpg, {PTID}_2.jpg etc.

Image classification (same as Shopify scraper):
  main : front, _1, 1_, main, hero, primary
  back : back, rear, _2, 2_
  left : left, _3, 3_
  right: right, side, _4, 4_
  sfp  : supplement, fact, sfp, nutrition, label, ingredient
  SKIP : lifestyle, model, person, wearing, benefit, icon, badge, cert, award, logo, banner

Usage:
    python generic_magento.py --url https://synevit.com --upc 860011318019 --ptid 223140 --name "Neurocomplex-B Slow-Release" --vendor "Synevit"
"""

import os, sys, argparse, requests, asyncio
from io import BytesIO
from urllib.parse import urlparse, quote, urlencode
from PIL import Image, ImageFilter, ImageEnhance
import numpy as np

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resized_images")

SIZES = {
    "500x500":   (500,  500),
    "1000x1000": (1000, 1000),
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

# Common Magento custom_attribute codes that store UPC/barcode
UPC_ATTRIBUTE_CODES = [
    "upc", "barcode", "ean", "upc_code", "barcode_value", "gtin",
    "upc_ean", "product_barcode", "ean_upc",
]

# Image skip keywords
SKIP_KEYWORDS = [
    "lifestyle", "model", "person", "people", "wearing", "benefit",
    "icon", "badge", "cert", "award", "logo", "banner", "background",
    "bg", "texture", "pattern", "social", "infographic", "claim",
]

# Image type classification
TYPE_KEYWORDS = {
    "main":  ["front", "_1.", "-1.", "main", "hero", "primary", "_f.", "-f.",
              "carousel-1", "carousel_1", "-01.", "_01.", "image-1", "img-1", "photo-1"],
    "back":  ["back", "rear", "_2.", "-2.", "_b.", "-b.",
              "carousel-2", "carousel_2", "-02.", "_02."],
    "left":  ["left", "_3.", "-3.", "_l.", "-l.",
              "carousel-3", "carousel_3", "-03.", "_03."],
    "right": ["right", "side", "_4.", "-4.", "_r.", "-r.", "_side.", "-side.",
              "carousel-4", "carousel_4", "-04.", "_04."],
    "sfp":   ["supplement", "fact", "sfp", "nutrition", "label",
              "ingredient", "panel", "supfact", "sup-fact"],
}


# ── Helpers ───────────────────────────────────────────────────────────────────
def get_attr(custom_attributes, code):
    """Extract a value from Magento custom_attributes list by attribute_code."""
    for attr in (custom_attributes or []):
        if attr.get("attribute_code") == code:
            return str(attr.get("value", "")).strip()
    return ""


def get_upc_from_attrs(custom_attributes):
    """Try all known UPC attribute codes and return the first non-empty value."""
    for code in UPC_ATTRIBUTE_CODES:
        val = get_attr(custom_attributes, code)
        if val:
            return val
    return ""


# ── Classify image ────────────────────────────────────────────────────────────
def classify_image(src, label=""):
    """Return image type string or None if the image should be skipped."""
    fname    = src.split("/")[-1].split("?")[0].lower()
    lbl_low  = label.lower()
    combined = fname + " " + lbl_low

    if any(k in combined for k in SKIP_KEYWORDS):
        return None

    for img_type, keywords in TYPE_KEYWORDS.items():
        if any(k in combined for k in keywords):
            return img_type

    return "unknown"


# ── Background removal ────────────────────────────────────────────────────────
def remove_shadow(img):
    """
    Solid background → keep as-is.
    Complex/gradient background → run rembg.
    """
    arr = np.array(img.convert("RGB")).astype(int)
    h, w = arr.shape[:2]

    margin  = max(10, min(h, w) // 20)
    corners = [
        arr[:margin, :margin],
        arr[:margin, -margin:],
        arr[-margin:, :margin],
        arr[-margin:, -margin:],
    ]
    bg_r = int(np.median([c[:,:,0].mean() for c in corners]))
    bg_g = int(np.median([c[:,:,1].mean() for c in corners]))
    bg_b = int(np.median([c[:,:,2].mean() for c in corners]))
    print(f"    [BG] Corner color: R={bg_r} G={bg_g} B={bg_b}")

    r, g, b  = arr[:,:,0], arr[:,:,1], arr[:,:,2]
    tol      = 30
    is_solid = (
        (np.abs(r - bg_r) < tol) &
        (np.abs(g - bg_g) < tol) &
        (np.abs(b - bg_b) < tol)
    )
    solid_ratio = is_solid.sum() / is_solid.size
    print(f"    [BG] Solid bg ratio: {solid_ratio:.1%}")

    if solid_ratio > 0.65:
        print(f"    [BG] Solid background — keeping as-is")
        return img.convert("RGBA")

    print(f"    [BG] Complex background — running rembg...")
    try:
        from rembg import remove as rembg_remove, new_session
        session = new_session("u2net")
        buf     = BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        result  = rembg_remove(buf.getvalue(), session=session, alpha_matting=False)
        return Image.open(BytesIO(result)).convert("RGBA")
    except Exception as e:
        print(f"    [BG] rembg failed ({e}) — keeping as-is")
        return img.convert("RGBA")


# ── Process and save image ────────────────────────────────────────────────────
def process_and_save(img_bytes, ptid, vendor_name, img_index):
    """
    Process image and save to:
      resized_images/{vendor_name}/500x500/{ptid}.jpg       (index=0)
      resized_images/{vendor_name}/500x500/{ptid}_1.jpg     (index=1)
      resized_images/{vendor_name}/1000x1000/{ptid}.jpg
      resized_images/{vendor_name}/1000x1000/{ptid}_1.jpg
    """
    try:
        img = Image.open(BytesIO(img_bytes))
        if img.width < 100 or img.height < 100:
            print(f"    Too small ({img.width}x{img.height}) — skipping")
            return False

        print(f"    Downloaded: {img.width}x{img.height}px")
        img = remove_shadow(img)

        arr              = np.array(img)
        has_transparency = (arr[:,:,3] < 255).any()

        if has_transparency:
            mask = arr[:,:,3] > 10
            rows = np.any(mask, axis=1)
            cols = np.any(mask, axis=0)
            if rows.any() and cols.any():
                rmin = int(np.where(rows)[0][0]);  rmax = int(np.where(rows)[0][-1])
                cmin = int(np.where(cols)[0][0]);  cmax = int(np.where(cols)[0][-1])
                ph   = max(10, int((rmax - rmin) * 0.04))
                pw   = max(10, int((cmax - cmin) * 0.04))
                img  = img.crop((max(0, cmin-pw), max(0, rmin-ph),
                                 min(img.width, cmax+pw+1), min(img.height, rmax+ph+1)))
                print(f"    [Crop] {img.width}x{img.height}")

        file_name = ptid if img_index == 0 else f"{ptid}_{img_index}"

        for size_label, (w, h) in SIZES.items():
            save_dir = os.path.join(OUTPUT_DIR, vendor_name, size_label)
            os.makedirs(save_dir, exist_ok=True)

            scale   = min(int(w*0.90)/img.width, int(h*0.90)/img.height)
            new_w   = int(img.width  * scale)
            new_h   = int(img.height * scale)
            resized = img.resize((new_w, new_h), Image.LANCZOS)

            rgb = resized.convert("RGB")
            rgb = rgb.filter(ImageFilter.UnsharpMask(radius=1.0, percent=120, threshold=3))
            rgb = ImageEnhance.Sharpness(rgb).enhance(1.3)
            r2, g2, b2 = rgb.split(); _, _, _, a = resized.split()
            resized = Image.merge("RGBA", (r2, g2, b2, a))

            canvas = Image.new("RGBA", (w, h), (255, 255, 255, 255))
            canvas.paste(resized, ((w-new_w)//2, (h-new_h)//2), resized)

            out = os.path.join(save_dir, f"{file_name}.jpg")
            canvas.convert("RGB").save(out, "JPEG", quality=95, dpi=(96, 96))
            print(f"      → {size_label}/{file_name}.jpg")

        return True
    except Exception as e:
        print(f"    Error processing image: {e}")
        return False


# ── Magento API fetch helpers ─────────────────────────────────────────────────
def api_get(base_url, path, params=None):
    """GET from Magento REST API — returns parsed JSON or None."""
    url = f"{base_url}/rest/V1/{path}"
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=20)
        if r.status_code == 200:
            return r.json()
        print(f"    API {r.status_code}: {url}")
        return None
    except Exception as e:
        print(f"    API error ({e}): {url}")
        return None


async def _playwright_api_get(url):
    """Playwright fallback for bot-protected Magento stores."""
    from playwright.async_api import async_playwright
    import json as json_mod
    result = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await browser.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)
            content = await page.inner_text("body")
            content = content.strip()
            if content.startswith("{") or content.startswith("["):
                result["data"] = json_mod.loads(content)
        except Exception as e:
            print(f"    Playwright error: {e}")
        finally:
            await browser.close()
    return result.get("data")


def api_get_with_fallback(base_url, path, params=None):
    """Try plain requests first; fall back to Playwright if blocked."""
    data = api_get(base_url, path, params)
    if data is not None:
        return data
    url = f"{base_url}/rest/V1/{path}"
    if params:
        url += "?" + urlencode(params)
    print(f"    Blocked — switching to Playwright...")
    return asyncio.run(_playwright_api_get(url))


# ── Fetch one catalog page ────────────────────────────────────────────────────
def fetch_products_page(base_url, page, page_size=100):
    """
    Fetch one page of the Magento product catalog.
    Returns list of product dicts, or None if end of catalog / error.
    """
    params = {
        "searchCriteria[pageSize]":    page_size,
        "searchCriteria[currentPage]": page,
        "fields": "items[id,sku,name,custom_attributes,media_gallery_entries]",
    }
    data = api_get_with_fallback(base_url, "products", params)
    if not data:
        return None
    items = data.get("items", [])
    return items if items else None


# ── Score product name ────────────────────────────────────────────────────────
def score_product_name(product_title, search_name):
    """Score how well a catalog product title matches the search name."""
    title_low  = product_title.lower()
    name_words = [w for w in search_name.lower().split() if len(w) > 2]
    score = sum(2 for w in name_words if w in title_low)
    if search_name.lower() in title_low:
        score += 5
    return score


# ── Find product ──────────────────────────────────────────────────────────────
def find_product(base_url, upc, product_name=""):
    """
    Find a Magento product by paginating the REST API catalog.

    Match priority:
      1. UPC match in custom_attributes
      2. Product name title scoring (fallback)

    Returns product dict or None.
    """
    upc_clean  = str(upc).strip()
    name_words = [w for w in product_name.lower().split() if len(w) > 2] if product_name else []

    print(f"    Scanning {base_url} Magento catalog...")
    page         = 1
    best_score   = 0
    best_product = None

    while True:
        products = fetch_products_page(base_url, page)
        if not products:
            print(f"    Scanned {page-1} page(s)")
            break

        print(f"    Page {page}: {len(products)} products")

        for product in products:
            custom_attrs = product.get("custom_attributes", [])
            p_upc        = get_upc_from_attrs(custom_attrs)
            p_name       = product.get("name", "")

            # Priority 1: UPC exact match — return immediately
            if upc_clean and p_upc == upc_clean:
                print(f"    ✓ UPC match: '{p_name}'")
                return product

            # Priority 2: accumulate best name match
            if name_words:
                score = score_product_name(p_name, product_name)
                if score > best_score:
                    best_score   = score
                    best_product = product

        page += 1

    if best_product and best_score >= 4:
        print(f"    ✓ Best name match (score={best_score}): '{best_product.get('name', '')}'")
        return best_product

    print(f"    Product not found (best score={best_score})")
    return None


# ── Get all product images ────────────────────────────────────────────────────
def get_all_images(product, base_url):
    """
    Build ordered image list from media_gallery_entries.

    Magento image entry fields:
      file     : e.g. /w/h/whatever.jpg  (relative to /pub/media/catalog/product)
      label    : alt text set by merchant
      position : display order (1 = main)
      types    : list like ["image", "small_image", "thumbnail"]
      disabled : bool

    Full image URL = {base_url}/pub/media/catalog/product{file}
    """
    entries = product.get("media_gallery_entries", [])
    if not entries:
        print(f"    No media_gallery_entries found")
        return []

    type_priority  = {"main": 0, "back": 1, "left": 2, "right": 3, "sfp": 4, "unknown": 5}
    collected      = []
    seen_urls      = set()
    seen_types     = set()

    for entry in sorted(entries, key=lambda e: e.get("position", 99)):
        if entry.get("disabled", False):
            continue

        file_path = entry.get("file", "")
        if not file_path:
            continue

        label    = entry.get("label", "") or ""
        types    = entry.get("types", [])
        position = entry.get("position", 99)
        img_url  = f"{base_url}/pub/media/catalog/product{file_path}"

        if img_url in seen_urls:
            continue

        img_type = classify_image(file_path, label)

        if img_type is None:
            fname = file_path.split("/")[-1]
            print(f"    Skipping (lifestyle/promo): {fname}")
            continue

        # Magento 'types' list is the authoritative main image signal
        if "image" in types and "main" not in seen_types:
            img_type = "main"

        # Assign by position if still unclassified
        if img_type == "unknown":
            pos_map  = {1: "main", 2: "back", 3: "left", 4: "right"}
            img_type = pos_map.get(position, "unknown")

        if img_type == "unknown":
            continue

        if img_type in seen_types:
            continue

        seen_urls.add(img_url)
        seen_types.add(img_type)
        fname = file_path.split("/")[-1]
        collected.append((img_url, fname, img_type))
        print(f"    Found [{img_type}] pos={position}: {fname}")

    collected.sort(key=lambda x: type_priority.get(x[2], 99))
    return collected


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


# ── Main ──────────────────────────────────────────────────────────────────────
def process_one(vendor_url, upc, product_name, folder=None, vendor_name="Unknown", ptid=None):
    parsed   = urlparse(vendor_url if vendor_url.startswith("http") else "https://" + vendor_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    ptid     = ptid or upc or "product"

    print(f"  Vendor  : {base_url}")
    print(f"  PTID    : {ptid}  UPC: {upc}")

    product = find_product(base_url, upc, product_name)
    if not product:
        print(f"  Not found in catalog")
        return 0

    images = get_all_images(product, base_url)
    if not images:
        print(f"  No images found")
        return 0

    count = 0
    for img_index, (img_url, fname, img_type) in enumerate(images):
        print(f"  Downloading [{img_type}] {fname}...")
        img_bytes = download_image(img_url)
        if img_bytes and len(img_bytes) > 1000:
            if process_and_save(img_bytes, ptid, vendor_name, img_index):
                count += 1
            else:
                print(f"  [WARN] Failed to process [{img_type}] {fname}")
        else:
            print(f"  [WARN] Empty/invalid image bytes for [{img_type}] {fname}")

    if count == 0:
        print(f"  ✗ No images successfully saved — marking as Failed")
    return count


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generic Magento Image Downloader")
    parser.add_argument("--url",    required=True,     help="Vendor website URL")
    parser.add_argument("--upc",    default="",        help="Product UPC/barcode")
    parser.add_argument("--ptid",   default="",        help="Output filename (PTID from Excel)")
    parser.add_argument("--name",   default="",        help="Product name (fallback if no UPC match)")
    parser.add_argument("--vendor", default="Unknown", help="Vendor name (used as output subfolder)")
    args = parser.parse_args()

    count = process_one(args.url, args.upc, args.name, vendor_name=args.vendor, ptid=args.ptid or args.upc)
    print(f"\n  Result: {count} image(s) downloaded")
    sys.exit(0 if count > 0 else 1)
