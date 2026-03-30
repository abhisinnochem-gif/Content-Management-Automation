"""
Sitemap Inverted Index Tester
==============================
Tests if sitemap-based URL finding works for a vendor.

Usage:
    python test_sitemap.py --url https://purele.ca --name "Fluid Extract Ginkgo Biloba"
    python test_sitemap.py --url https://www.burtsbees.com --name "Beeswax Lip Balm"
    python test_sitemap.py --url https://www.sigvaris.com --name "Calf Compression Sock"
"""

import sys, argparse, requests, re, json, os
from urllib.parse import urlparse
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
}

def get_domain(url):
    return urlparse(url).netloc.replace("www.", "")

def fetch(url, timeout=10):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code == 200:
            return r.text
        print(f"  HTTP {r.status_code}: {url}")
        return None
    except Exception as e:
        print(f"  Error: {e}")
        return None

def get_sitemap_urls(base_url):
    """Fetch sitemap and extract all product URLs."""
    domain   = get_domain(base_url)
    cache_f  = f"sitemap_cache_{domain.replace('.','_')}.json"

    # Load cache
    if os.path.exists(cache_f):
        with open(cache_f) as f:
            urls = json.load(f)
        print(f"  Loaded {len(urls)} URLs from cache")
        return urls

    sitemap_url = base_url.rstrip("/") + "/sitemap.xml"
    print(f"  Fetching sitemap: {sitemap_url}")
    xml = fetch(sitemap_url)
    if not xml:
        print(f"  Sitemap not found")
        return []

    soup = BeautifulSoup(xml, "xml")
    urls = []

    # Check if it's a sitemap index
    sitemaps = soup.find_all("sitemap")
    if sitemaps:
        print(f"  Sitemap index found — {len(sitemaps)} sub-sitemaps")
        for sm in sitemaps[:5]:  # limit to first 5 for speed
            loc = sm.find("loc")
            if not loc:
                continue
            sub_url = loc.get_text(strip=True)
            print(f"  Fetching sub-sitemap: {sub_url}")
            sub_xml = fetch(sub_url)
            if sub_xml:
                sub_soup = BeautifulSoup(sub_xml, "xml")
                for loc in sub_soup.find_all("loc"):
                    urls.append(loc.get_text(strip=True))
    else:
        # Direct sitemap
        for loc in soup.find_all("loc"):
            urls.append(loc.get_text(strip=True))

    print(f"  Found {len(urls)} total URLs")

    # Save cache
    with open(cache_f, "w") as f:
        json.dump(urls, f)

    return urls

def build_inverted_index(urls):
    """Build inverted index: word -> list of URLs."""
    index = {}
    for url in urls:
        # Extract slug from URL
        path   = urlparse(url).path.lower()
        slug   = path.strip("/").split("/")[-1]
        words  = re.split(r"[\-_]+", slug)
        words  = [w for w in words if len(w) >= 3]
        for word in words:
            if word not in index:
                index[word] = []
            index[word].append(url)
    return index

def find_product_url(index, all_urls, product_name):
    """Find best matching URL using inverted index."""
    # Extract query words
    stop = {"the","and","with","for","from","extract","fluid","pure",
            "organic","natural","supplement","capsule","tablet","ml","mg","oz"}
    words = [w.lower() for w in re.split(r"[\s\-_,/]+", product_name)
             if len(w) >= 3 and w.lower() not in stop]
    print(f"  Query words: {words}")

    # Score each URL
    scores = {}
    for word in words:
        if word in index:
            for url in index[word]:
                scores[url] = scores.get(url, 0) + 1

    if not scores:
        print(f"  No matches found")
        return None

    # Sort by score
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    print(f"\n  Top matches:")
    for url, score in ranked[:10]:
        print(f"    score={score}  {url}")

    best_url, best_score = ranked[0]
    if best_score >= 1:
        print(f"\n  ✓ Best match (score={best_score}): {best_url}")
        return best_url

    return None

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url",  required=True, help="Vendor homepage URL")
    parser.add_argument("--name", required=True, help="Product name to search")
    args = parser.parse_args()

    base_url = args.url.rstrip("/")
    if not base_url.startswith("http"):
        base_url = "https://" + base_url

    print(f"\nVendor: {base_url}")
    print(f"Product: {args.name}")
    print(f"{'='*60}")

    # Step 1: Get sitemap URLs
    print(f"\nStep 1: Fetching sitemap...")
    urls = get_sitemap_urls(base_url)
    if not urls:
        print("No URLs found — sitemap may not be accessible")
        sys.exit(1)

    # Step 2: Build inverted index
    print(f"\nStep 2: Building inverted index...")
    index = build_inverted_index(urls)
    print(f"  Index has {len(index)} unique words")

    # Step 3: Find product
    print(f"\nStep 3: Searching for '{args.name}'...")
    result = find_product_url(index, urls, args.name)

    if result:
        print(f"\n✓ SUCCESS — Found product URL")
        print(f"  {result}")
    else:
        print(f"\n✗ FAILED — Product not found in sitemap")
