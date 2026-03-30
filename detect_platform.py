"""
Platform Detector
=================
Detects the ecommerce/CMS platform of any vendor website.

Supported platforms:
  - shopify        : Shopify stores (products.json available)
  - woocommerce    : WordPress + WooCommerce
  - wordpress      : WordPress brand site (no store)
  - magento        : Magento/Adobe Commerce
  - drupal         : Drupal CMS
  - bigcommerce    : BigCommerce
  - squarespace    : Squarespace
  - custom         : Unknown/custom platform

Usage:
    python detect_platform.py --url https://incrediwear.com
    python detect_platform.py --url https://blistex.com
    python detect_platform.py --batch urls.txt
"""

import sys
import argparse
import requests
import re
from urllib.parse import urlparse

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

TIMEOUT = 15


def normalise_url(url):
    """Ensure URL has scheme."""
    if not url.startswith("http"):
        url = "https://" + url
    return url.rstrip("/")


def get_domain(url):
    return urlparse(url).netloc.replace("www.", "")


def safe_get(url, timeout=TIMEOUT):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        return r
    except Exception:
        return None


# ── Detection tests ───────────────────────────────────────────────────────────

def test_shopify(base_url):
    """
    Test 1: Try /products.json — definitive Shopify signal.
    Test 2: Check HTML for cdn.shopify.com.
    """
    # Test 1: products.json endpoint
    r = safe_get(f"{base_url}/products.json?limit=1")
    if r and r.status_code == 200:
        try:
            data = r.json()
            if "products" in data:
                return True, "products.json returned valid JSON"
        except Exception:
            pass

    # Test 2: HTML fingerprint
    r = safe_get(base_url)
    if r and r.status_code == 200:
        html = r.text.lower()
        if "cdn.shopify.com" in html:
            return True, "cdn.shopify.com found in HTML"
        if "shopify.com/s/files" in html:
            return True, "shopify CDN files found in HTML"
        if '"shop_id"' in html or "shopify_analytics" in html.lower():
            return True, "Shopify analytics found in HTML"

    return False, None


def test_woocommerce(html):
    """Check HTML for WooCommerce signals."""
    signals = [
        "woocommerce",
        "wc-ajax",
        "wc_add_to_cart",
        "/wp-json/wc/",
        "woocommerce-cart",
        "woocommerce-checkout",
    ]
    for signal in signals:
        if signal in html.lower():
            return True, f"WooCommerce signal: '{signal}'"
    return False, None


def test_wordpress(html):
    """Check HTML for WordPress signals (without WooCommerce)."""
    signals = [
        "wp-content",
        "wp-includes",
        "wordpress",
        "/wp-json/",
    ]
    for signal in signals:
        if signal in html.lower():
            return True, f"WordPress signal: '{signal}'"
    return False, None


def test_magento(html):
    """Check HTML for Magento signals."""
    signals = [
        "mage/",
        "magento",
        "requirejs/require.js",
        "/static/version",
        "Magento_",
    ]
    for signal in signals:
        if signal in html:
            return True, f"Magento signal: '{signal}'"
    return False, None


def test_drupal(html):
    """Check HTML for Drupal signals."""
    signals = [
        "drupal",
        "/sites/default/files",
        "drupal.js",
        "Drupal.settings",
    ]
    for signal in signals:
        if signal in html.lower():
            return True, f"Drupal signal: '{signal}'"
    return False, None


def test_bigcommerce(html):
    """Check HTML for BigCommerce signals."""
    signals = [
        "bigcommerce",
        "cdn11.bigcommerce.com",
        "bigcommerce.com/s/",
    ]
    for signal in signals:
        if signal in html.lower():
            return True, f"BigCommerce signal: '{signal}'"
    return False, None


def test_squarespace(html):
    """Check HTML for Squarespace signals."""
    signals = [
        "squarespace",
        "static1.squarespace.com",
        "sqspcdn.com",
    ]
    for signal in signals:
        if signal in html.lower():
            return True, f"Squarespace signal: '{signal}'"
    return False, None


def test_weebly(html):
    """Check HTML for Weebly signals."""
    signals = [
        "weebly",
        "weeblycloud.com",
        "wsite-",
    ]
    for signal in signals:
        if signal in html.lower():
            return True, f"Weebly signal: '{signal}'"
    return False, None


# ── Main detector ─────────────────────────────────────────────────────────────

def detect_platform(url):
    """
    Detect the platform of a vendor website.
    Returns dict with keys: platform, confidence, signal, url
    """
    url = normalise_url(url)
    domain = get_domain(url)

    result = {
        "url":        url,
        "domain":     domain,
        "platform":   "unknown",
        "confidence": "low",
        "signal":     "No platform detected",
        "shopify_products_json": False,
    }

    # ── Step 1: Shopify (check before fetching HTML — fastest) ────────────────
    is_shopify, signal = test_shopify(url)
    if is_shopify:
        result["platform"]   = "shopify"
        result["confidence"] = "high"
        result["signal"]     = signal
        result["shopify_products_json"] = "products.json" in signal
        return result

    # ── Step 2: Fetch homepage HTML for other checks ──────────────────────────
    r = safe_get(url)
    if not r or r.status_code != 200:
        result["signal"] = f"Could not fetch homepage (HTTP {r.status_code if r else 'timeout'})"
        return result

    html = r.text

    # ── Step 3: WooCommerce (must check before WordPress) ─────────────────────
    is_woo, signal = test_woocommerce(html)
    if is_woo:
        result["platform"]   = "woocommerce"
        result["confidence"] = "high"
        result["signal"]     = signal
        return result

    # ── Step 4: WordPress brand site ──────────────────────────────────────────
    is_wp, signal = test_wordpress(html)
    if is_wp:
        result["platform"]   = "wordpress"
        result["confidence"] = "high"
        result["signal"]     = signal
        return result

    # ── Step 5: Magento — validate with REST API (not just HTML signal) ───────
    is_magento, signal = test_magento(html)
    if is_magento:
        # Confirm Magento by checking REST API is actually reachable
        rest_check = safe_get(f"{url}/rest/V1/products?searchCriteria[pageSize]=1", timeout=8)
        if rest_check and rest_check.status_code == 200:
            result["platform"]   = "magento"
            result["confidence"] = "high"
            result["signal"]     = signal
            return result
        else:
            # HTML had a Magento-like string but REST API is 404 — not real Magento
            # Fall through to other checks
            pass

    # ── Step 6: Drupal ────────────────────────────────────────────────────────
    is_drupal, signal = test_drupal(html)
    if is_drupal:
        result["platform"]   = "drupal"
        result["confidence"] = "high"
        result["signal"]     = signal
        return result

    # ── Step 7: BigCommerce ───────────────────────────────────────────────────
    is_bc, signal = test_bigcommerce(html)
    if is_bc:
        result["platform"]   = "bigcommerce"
        result["confidence"] = "high"
        result["signal"]     = signal
        return result

    # ── Step 8: Squarespace ───────────────────────────────────────────────────
    is_ss, signal = test_squarespace(html)
    if is_ss:
        result["platform"]   = "squarespace"
        result["confidence"] = "medium"
        result["signal"]     = signal
        return result

    # ── Step 9: Weebly ────────────────────────────────────────────────────────
    is_weebly, signal = test_weebly(html)
    if is_weebly:
        result["platform"]   = "weebly"
        result["confidence"] = "medium"
        result["signal"]     = signal
        return result

    # ── Unknown ───────────────────────────────────────────────────────────────
    result["platform"]   = "custom"
    result["confidence"] = "low"
    result["signal"]     = "No known platform signals detected"
    return result


def detect_batch(urls):
    """Detect platform for a list of URLs."""
    results = []
    for url in urls:
        url = url.strip()
        if not url or url.startswith("#"):
            continue
        print(f"  Checking {url}...")
        r = detect_platform(url)
        results.append(r)
    return results


def print_result(r):
    print(f"\n  URL      : {r['url']}")
    print(f"  Domain   : {r['domain']}")
    print(f"  Platform : {r['platform'].upper()}")
    print(f"  Confidence: {r['confidence']}")
    print(f"  Signal   : {r['signal']}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Vendor Platform Detector")
    parser.add_argument("--url",   help="Single vendor URL to detect")
    parser.add_argument("--batch", help="Text file with one URL per line")
    args = parser.parse_args()

    if args.url:
        print(f"\nDetecting platform for: {args.url}")
        r = detect_platform(args.url)
        print_result(r)

    elif args.batch:
        with open(args.batch) as f:
            urls = f.readlines()
        print(f"\nDetecting platforms for {len(urls)} URLs...\n")
        results = detect_batch(urls)
        print(f"\n{'='*60}")
        print(f"{'DOMAIN':<35} {'PLATFORM':<15} {'CONFIDENCE'}")
        print(f"{'='*60}")
        for r in results:
            print(f"{r['domain']:<35} {r['platform']:<15} {r['confidence']}")
        print(f"{'='*60}")

        # Summary
        from collections import Counter
        platforms = Counter(r["platform"] for r in results)
        print(f"\nSummary:")
        for p, count in platforms.most_common():
            print(f"  {p:<15} {count} vendor(s)")

    else:
        parser.print_help()
