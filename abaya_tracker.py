#!/usr/bin/env python3
"""
Abaya Tracker - Noon, Amazon.sa aur Salla stores ka data jama karta hai
aur batata hai kaun sa product/design zyada bik raha hai.

IDEA: Koi site asal sales nahi dikhati. Tool sirf wo sold number leta hai jo site khud dikhati hai.
Amazon.sa kuch products par "X bought in past month" bhi dikhata hai, wo bhi save hota hai.

COMMANDS:
  python abaya_tracker.py scrape --query abaya                   # Noon + Amazon.sa
  python abaya_tracker.py scrape --query abaya --salla https://STORE.com/ar
  python abaya_tracker.py cities                                 # Riyadh/Jeddah/Makkah/Madinah ka hissa (Google Trends)
  python abaya_tracker.py report --days 30
"""
import argparse
import csv
import html
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime

HISTORY = "history.csv"
FIELDS = ["date", "site", "query", "title", "url", "price", "rating", "reviews", "bought_month"]


def to_num(s):
    if s is None:
        return 0.0
    s = str(s).translate(str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,")).strip()
    m = re.search(r"([\d][\d,]*\.?\d*)\s*([kKmM]?)", s)
    if not m:
        return 0.0
    v = float(m.group(1).replace(",", ""))
    if m.group(2).lower() == "k":
        v *= 1000
    elif m.group(2).lower() == "m":
        v *= 1_000_000
    return v


def parse_card(text):
    """Card ke text se price, rating, reviews, bought nikalta hai (generic)."""
    t = text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,"))
    price = 0.0
    m = re.search(r"(?:SAR|SR|ر\.?س\.?)\s*([\d,]+\.?\d*)|([\d,]+\.?\d*)\s*(?:SAR|SR|ر\.?س)", t)
    if m:
        price = to_num(m.group(1) or m.group(2))
    rating = 0.0
    m = re.search(r"(?<![\d.,])([0-5]\.\d)(?![\d])", t)
    if m and 0 < float(m.group(1)) <= 5:
        rating = float(m.group(1))
    reviews = 0.0
    m = re.search(r"\(\s*([\d.,]+\s*[kK]?)\s*\)", t) or re.search(
        r"([\d.,]+\s*[kK]?)\s*(?:ratings?|reviews?|تقييم)", t)
    if m:
        reviews = to_num(m.group(1))
    bought = 0.0
    m = re.search(r"([\d.,]+\s*[kK]?\+?)\s*(?:bought in past month|sold recently|تم شراؤه|تم بيع)", t)
    if m:
        bought = to_num(m.group(1))
    if not bought:
        m = re.search(r"(?:تم بيع|تم شراء|مباع|بيع)\s*([\d.,]+\s*[kK]?\+?)", t) or re.search(
            r"(?:sold|bought)\s*([\d.,]+\s*[kK]?\+?)", t, re.I)
        if m:
            bought = to_num(m.group(1))
    return price, rating, reviews, bought


def scroll(page, rounds=6):
    for _ in range(rounds):
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(1200)


def scrape_amazon(page, query, pages):
    rows = []
    for p in range(1, pages + 1):
        page.goto(f"https://www.amazon.sa/s?k={query}&page={p}", timeout=60000)
        page.wait_for_timeout(2500)
        for card in page.query_selector_all('div[data-component-type="s-search-result"]'):
            h = card.query_selector("h2")
            a = card.query_selector("h2 a") or card.query_selector("a.a-link-normal")
            if not h or not a:
                continue
            price, rating, reviews, bought = parse_card(card.inner_text())
            href = a.get_attribute("href") or ""
            rows.append(("Amazon.sa", h.inner_text().strip(), "https://www.amazon.sa" + href.split("?")[0],
                         price, rating, reviews, bought))
    return rows


def scrape_noon(page, query, pages):
    rows = []
    seen = set()
    for p in range(1, pages + 1):
        page.goto(f"https://www.noon.com/saudi-en/search/?q={query}&page={p}", timeout=60000)
        page.wait_for_timeout(3000)
        scroll(page)
        for a in page.query_selector_all('a[href*="/p/"]'):
            href = a.get_attribute("href") or ""
            if href in seen:
                continue
            seen.add(href)
            text = a.inner_text().strip()
            lines = [l for l in text.split("\n") if l.strip()]
            if len(lines) < 2:
                continue
            title = max(lines[:4], key=len)
            price, rating, reviews, bought = parse_card(text)
            rows.append(("Noon", title, "https://www.noon.com" + href.split("?")[0], price, rating, reviews, bought))
    return rows


def scrape_salla(page, base, query, pages):
    rows = []
    seen = set()
    base = base.rstrip("/")
    for p in range(1, pages + 1):
        page.goto(f"{base}/search?q={query}&page={p}", timeout=60000)
        page.wait_for_timeout(3000)
        scroll(page, 4)
        cards = page.query_selector_all("custom-salla-product-card, salla-product-card, .product-entry, .s-product-card-entry")
        for c in cards:
            a = c.query_selector("a[href]")
            if not a:
                continue
            href = a.get_attribute("href") or ""
            if href in seen:
                continue
            seen.add(href)
            text = c.inner_text().strip()
            lines = [l for l in text.split("\n") if l.strip()]
            if not lines:
                continue
            price, rating, reviews, bought = parse_card(text)
            rows.append(("Salla:" + base.split("//")[-1].split("/")[0], lines[0], href if href.startswith("http") else base.split("/ar")[0] + href,
                         price, rating, reviews, bought))
    return rows


SALLA_SHOWCASE = ["https://salla.com/en/showcase", "https://salla.com/en/showcase/fashion",
                  "https://salla.com/en/showcase/clothing"]
SKIP_DOMAINS = ("salla.com", "salla.sa", "instagram.com", "twitter.com", "x.com", "snapchat.com", "tiktok.com",
                "youtube.com", "facebook.com", "linkedin.com", "apple.com", "google.com", "whatsapp.com",
                "t.me", "wa.me", "play.google", "apps.apple")


def discover_salla(page, limit):
    """Salla showcase pages se stores ke link khud dhoondta hai (sirf showcase wale, saare nahi)."""
    found = []
    for url in SALLA_SHOWCASE:
        try:
            page.goto(url, timeout=60000)
            page.wait_for_timeout(3000)
            scroll(page, 4)
            for a_ in page.query_selector_all("a[href^='http']"):
                href = (a_.get_attribute("href") or "").split("?")[0].rstrip("/")
                host = href.split("//")[-1].split("/")[0].lower()
                if href and not any(d in host for d in SKIP_DOMAINS) and href not in found:
                    found.append(href)
        except Exception as e:
            print(f"Salla showcase {url}: fail ({e})", file=sys.stderr)
    print(f"Salla showcase se {len(found)} stores mile (limit {limit}).")
    return found[:limit]


def cmd_scrape(a):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("Pehle install karein:\n  pip install playwright\n  playwright install chromium")
    today = date.today().isoformat()
    allrows = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not a.show)
        ctx = browser.new_context(locale="en-US", viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        jobs = []
        if not a.only or a.only == "noon":
            jobs.append(("Noon", lambda: scrape_noon(page, a.query, a.pages)))
        if not a.only or a.only == "amazon":
            jobs.append(("Amazon.sa", lambda: scrape_amazon(page, a.query, a.pages)))
        stores = list(a.salla or [])
        if a.salla_auto:
            stores += discover_salla(page, a.max_stores)
        if os.path.exists("stores.txt"):  # har line mein ek Salla store ka URL
            with open("stores.txt", encoding="utf-8") as sf:
                stores += [l.strip() for l in sf if l.strip().startswith("http")]
        for s in dict.fromkeys(stores):
            jobs.append((s, lambda s=s: scrape_salla(page, s, a.query, a.pages)))
        for name, fn in jobs:
            try:
                r = fn()
                print(f"{name}: {len(r)} products mile")
                allrows += r
            except Exception as e:
                print(f"{name}: fail ({e}). Site ne block kiya ya selectors badal gaye hain.", file=sys.stderr)
        browser.close()
    new = not os.path.exists(HISTORY)
    with open(HISTORY, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(FIELDS)
        for site, title, url, price, rating, reviews, bought in allrows:
            w.writerow([today, site, a.query, title, url, price, rating, reviews, bought])
    print(f"{len(allrows)} rows {HISTORY} mein save hui ({today}).")
    print("Tip: har roz ek baar chalayein, tab sales ka andaza behtar hota hai.")


def load_history():
    if not os.path.exists(HISTORY):
        sys.exit("history.csv nahi mili. Pehle 'scrape' chalayein.")
    with open(HISTORY, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def analyze(rows, days, today=None):
    today = today or date.today()
    by_prod = defaultdict(list)
    for r in rows:
        d = datetime.strptime(r["date"], "%Y-%m-%d").date()
        if (today - d).days <= days:
            by_prod[(r["site"], r["url"])].append((d, r))
    out = []
    for (site, url), lst in by_prod.items():
        lst.sort(key=lambda x: x[0])
        first, last = lst[0], lst[-1]
        span = max((last[0] - first[0]).days, 0)
        r0, r1 = to_num(first[1]["reviews"]), to_num(last[1]["reviews"])
        delta = max(r1 - r0, 0)
        est_period = 0.0  # review se sales ka andaza hata diya gaya
        per_day = est_period / span if span else 0
        bought = to_num(last[1]["bought_month"])  # site ka apna "sold/bought" number (akhri din ka)
        out.append({
            "site": site, "title": last[1]["title"], "url": url,
            "price": to_num(last[1]["price"]), "rating": last[1]["rating"],
            "reviews_now": r1, "new_reviews": delta, "days_tracked": span,
            "est_sales_period": est_period, "est_sales_per_day": per_day,
            "bought_month": bought,
        })
    return out


DESIGNS = [  # (naam, keywords - English aur Arabic)
    ("Open front", ["open front", "front open", "open abaya", "مفتوح"]),
    ("Kimono", ["kimono", "كيمونو"]),
    ("Butterfly", ["butterfly", "فراشة", "فراشه", "بترفلاي"]),
    ("Bisht/Cape", ["bisht", "cape", "بشت", "كاب"]),
    ("Beaded/Crystal", ["bead", "crystal", "stone", "pearl", "خرز", "كريستال", "ستراس", "لؤلؤ"]),
    ("Embroidered", ["embroider", "تطريز", "مطرز"]),
    ("Lace", ["lace", "دانتيل"]),
    ("Pleated", ["pleat", "بليسيه"]),
    ("Cardigan", ["cardigan", "كارديجان"]),
    ("Prayer set", ["prayer", "صلاة", "صلاه"]),
    ("Closed/Plain", ["closed", "plain", "simple", "سادة", "ساده", "مقفل"]),
]
COLORS = [
    ("Black", ["black", "أسود", "اسود"]), ("Beige", ["beige", "بيج"]), ("Brown", ["brown", "بني"]),
    ("Mocha", ["mocha", "موكا"]), ("Navy", ["navy", "كحلي"]), ("Grey", ["grey", "gray", "رمادي"]),
    ("Burgundy", ["burgundy", "عنابي"]), ("Green", ["green", "أخضر", "اخضر"]),
    ("Olive", ["olive", "زيتي"]), ("White", ["white", "أبيض", "ابيض"]), ("Pink", ["pink", "وردي"]),
    ("Blue", ["blue", "أزرق", "ازرق"]), ("Cream", ["cream", "كريمي"]),
]
FANCY_WORDS = ["party", "evening", "occasion", "wedding", "eid", "سهرة", "سهره", "مناسبات", "زفاف", "عيد",
               "embroider", "bead", "crystal", "تطريز", "خرز", "كريستال", "ستراس"]


def classify(title, price):
    t = title.lower()
    design = next((n for n, ks in DESIGNS if any(k in t for k in ks)), "Other")
    color = next((n for n, ks in COLORS if any(k in t for k in ks)), "Other")
    kind = "fancy" if (any(k in t for k in FANCY_WORDS) or price >= 450) else "daily"
    return design, color, kind


# ---------------- City split ----------------
# Aabadi (million, taqreeban). Google Trends sirf "dilchaspi ka hissa" batata hai, volume nahi,
# isliye interest x aabadi se city ka andaza banta hai.
POP = {"Riyadh": 7.0, "Jeddah": 4.7, "Makkah": 2.4, "Madinah": 1.4, "Dammam": 1.4}
CITIES = list(POP)
ALIASES = {"riyadh": "Riyadh", "jeddah": "Jeddah", "jiddah": "Jeddah", "mecca": "Makkah", "makkah": "Makkah",
           "medina": "Madinah", "madinah": "Madinah", "al madinah": "Madinah", "al-madinah": "Madinah",
           "dammam": "Dammam", "ad dammam": "Dammam", "al dammam": "Dammam", "dammam city": "Dammam",
           "الرياض": "Riyadh", "جدة": "Jeddah", "جده": "Jeddah", "مكة": "Makkah", "مكة المكرمة": "Makkah",
           "مكه": "Makkah", "المدينة": "Madinah", "المدينة المنورة": "Madinah", "المدينه": "Madinah",
           "الدمام": "Dammam"}
DESIGN_TRENDS = {
    "overall": "عباية", "Open front": "عباية مفتوحة", "Kimono": "عباية كيمونو",
    "Butterfly": "عباية فراشة", "Embroidered": "عباية مطرزة", "Beaded/Crystal": "عباية كريستال",
    "Bisht/Cape": "بشت نسائي", "Lace": "عباية دانتيل", "Closed/Plain": "عباية سادة",
}
CITY_TRENDS_FILE = "city_trends.csv"   # cities command banata hai: keyword,city,interest
CITY_MANUAL_FILE = "city_weights.csv"  # aap khud bana sakte hain: city,weight (apna asli data)


def normalize(d):
    s = sum(d.values()) or 1.0
    return {k: v / s for k, v in d.items()}


def load_city_weights():
    """Returns (overall_shares, per_design_shares, source_text)."""
    overall = normalize({c: POP[c] for c in CITIES})
    per, source = {}, "ASSUMPTION: sirf aabadi (Trends data nahi mila, 'cities' command chalayein)"
    if os.path.exists(CITY_TRENDS_FILE):
        tmp = defaultdict(dict)
        with open(CITY_TRENDS_FILE, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                tmp[r["keyword"]][r["city"]] = to_num(r["interest"])
        for k, d in tmp.items():
            if len(d) >= 3 and sum(d.values()) > 0:
                lo = min(d.values())
                per[k] = normalize({c: d.get(c, lo) * POP[c] for c in CITIES})
        if "overall" in per:
            overall = per.pop("overall")
            source = "Google Trends (12 mahine) x aabadi - andaza"
    if os.path.exists(CITY_MANUAL_FILE):
        with open(CITY_MANUAL_FILE, encoding="utf-8") as f:
            m = {r["city"].strip(): to_num(r["weight"]) for r in csv.DictReader(f)}
        if sum(m.values()) > 0:
            overall = normalize({c: m.get(c, 0.0) for c in CITIES})
            per = {}
            source = "Aapka apna data (city_weights.csv)"
    od = load_orders()
    if od:
        overall, per = od[0], od[1]
        source = f"ASLI ORDERS (orders.csv, {od[2]:,.0f} units) - yeh andaza nahi, asli data hai"
    return overall, per, source


ORDERS_FILE = "orders.csv"


def load_orders():
    """Asli orders (store owner ke dashboard export se). Columns: city, product (ya title), qty.
    Returns (overall_shares, per_design_shares, total_units) ya None."""
    if not os.path.exists(ORDERS_FILE):
        return None
    with open(ORDERS_FILE, encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        rows = [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()} for r in rd]
    def pick(r, names):
        for n in names:
            if r.get(n):
                return r[n]
        return ""
    overall, per, total = defaultdict(float), defaultdict(lambda: defaultdict(float)), 0.0
    for r in rows:
        c = ALIASES.get(pick(r, ["city", "المدينة", "مدينة"]).lower())
        if not c:
            continue
        qty = to_num(pick(r, ["qty", "quantity", "الكمية"])) or 1.0
        title = pick(r, ["product", "title", "المنتج", "اسم المنتج"])
        design = pick(r, ["design"]) or classify(title, 0)[0]
        overall[c] += qty
        per[design][c] += qty
        total += qty
    if total <= 0:
        return None
    return (normalize({c: overall.get(c, 0.0) for c in CITIES}),
            {d: normalize({c: v.get(c, 0.0) for c in CITIES}) for d, v in per.items()}, total)


def cmd_cities(a):
    try:
        from pytrends.request import TrendReq
    except ImportError:
        sys.exit("Pehle: pip install pytrends")
    import time
    pt = TrendReq(hl="ar", tz=180)
    out = []
    for name, kw in DESIGN_TRENDS.items():
        try:
            pt.build_payload([kw], geo="SA", timeframe="today 12-m")
            d = pt.interest_by_region(resolution="CITY", inc_low_vol=True).reset_index()
        except Exception as e:
            print(f"{name}: fail ({e})", file=sys.stderr)
            time.sleep(5)
            continue
        found = {}
        for _, row in d.iterrows():
            c = ALIASES.get(str(row["geoName"]).lower().strip())
            if c:
                found[c] = float(row[kw])
        if len(found) < 3:
            print(f"{name}: 5 mein se 3 se kam shehron ka data mila, ye keyword chhor diya.")
        else:
            low = min(found.values())
            miss = [c for c in CITIES if c not in found]
            if miss:
                print(f"{name}: {', '.join(miss)} ka data nahi mila, sabse kam value ({low:.0f}) lagayi.")
            out += [(name, c, found.get(c, low)) for c in CITIES]
            print(f"{name}: " + ", ".join(f"{c}={found.get(c, low):.0f}" for c in CITIES))
        time.sleep(3)
    if not out:
        sys.exit("Google Trends se kuch nahi aaya (internet / rate limit). Thori der baad dobara try karein.")
    with open(CITY_TRENDS_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["keyword", "city", "interest"])
        w.writerows(out)
    print(f"{CITY_TRENDS_FILE} save hui.")


# ---------------- Report ----------------
def bars(title, items, unit=""):
    items = [(k, v) for k, v in items if v > 0]
    if not items:
        return ""
    mx = max(v for _, v in items) or 1
    tot = sum(v for _, v in items) or 1
    rows = "".join(
        f"<div class='r'><span class='n'>{html.escape(str(k))}</span>"
        f"<span class='b' style='width:{max(2, int(55 * v / mx))}%'></span>"
        f"<span class='v'>{v:,.0f}{unit} ({100 * v / tot:.0f}%)</span></div>" for k, v in items)
    return f"<h3>{html.escape(title)}</h3>{rows}"


def save_csv(name, header, rows):
    with open(name, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def cmd_report(a):
    res = analyze(load_history(), a.days)
    if not res:
        sys.exit("Is muddat ka data nahi mila.")
    use_est = any(r["bought_month"] > 0 for r in res)  # kam az kam ek product ne sold number dikhaya
    mlabel = "Sold (site ke mutabiq)" if use_est else "Reviews (kisi product ne sold number nahi dikhaya)"
    if use_est:
        res = [r for r in res if r["bought_month"] > 0]  # jis ka sold number nahi, wo ranking mein nahi
    for r in res:
        r["metric"] = r["bought_month"] if use_est else r["reviews_now"]
        r["design"], r["color"], r["kind"] = classify(r["title"], r["price"])
    res.sort(key=lambda r: r["metric"], reverse=True)
    save_csv("top_products.csv", ["site", "title", "design", "color", "type", "price", "reviews_now",
                                  "new_reviews", "est_sales_period", "est_sales_per_day", "bought_month", "url"],
             [[r["site"], r["title"], r["design"], r["color"], r["kind"], r["price"], r["reviews_now"],
               r["new_reviews"], r["est_sales_period"], round(r["est_sales_per_day"], 1), r["bought_month"],
               r["url"]] for r in res])

    def agg(key):
        d = defaultdict(float)
        for r in res:
            d[r[key]] += r["metric"]
        return sorted(d.items(), key=lambda x: -x[1])

    designs, colors, kinds, sites = agg("design"), agg("color"), agg("kind"), agg("site")
    bands = defaultdict(float)
    for r in res:
        p = r["price"]
        b = "<100" if p < 100 else "100-200" if p < 200 else "200-300" if p < 300 else "300-500" if p < 500 else "500+"
        bands[b] += r["metric"]
    order = ["<100", "100-200", "200-300", "300-500", "500+"]
    total = sum(r["metric"] for r in res)

    overall, per, source = load_city_weights()
    city_tot = [(c, total * overall[c]) for c in CITIES]
    save_csv("city_split.csv", ["city", "share_percent", "metric"],
             [[c, round(100 * overall[c], 1), round(v)] for c, v in city_tot])

    # Design x City
    dc_rows, dc_html = [], ""
    for name, v in designs:
        share = per.get(name, overall)
        cells = [v * share[c] for c in CITIES]
        dc_rows.append([name] + [round(x) for x in cells])
        mark = "" if name in per else "*"
        dc_html += (f"<tr><td>{html.escape(name)}{mark}</td>" + "".join(f"<td>{x:,.0f}</td>" for x in cells)
                    + f"<td><b>{v:,.0f}</b></td></tr>")
    save_csv("design_by_city.csv", ["design"] + CITIES, dc_rows)

    top10 = []
    for i, r in enumerate(res[:10], 1):
        units = r["bought_month"] if use_est else 0.0
        top10.append([i, r["site"], r["title"], r["design"], r["color"], r["price"], round(units),
                      round(units * r["price"]), r["url"]])
    save_csv("top10_winners.csv", ["rank", "site", "title", "design", "color", "price_sar",
                                   "sold_units", "sold_value_sar", "url"], top10)
    if use_est:
        t10 = "".join(
            f"<tr><td>{x[0]}</td><td>{html.escape(x[1])}</td>"
            f"<td><a href='{html.escape(x[8])}' target='_blank'>{html.escape(x[2][:60])}</a></td>"
            f"<td>{x[3]}</td><td>{x[4]}</td><td>{x[5]:.0f}</td><td><b>{x[6]:,}</b></td><td><b>{x[7]:,}</b></td></tr>"
            for x in top10)
        top10_html = (f"<h2>Top 10 winning products (aakhri {a.days} din)</h2><table><tr><th>#</th><th>Site</th>"
                      f"<th>Product</th><th>Design</th><th>Color</th><th>SAR</th><th>Bike (site ke mutabiq)</th>"
                      f"<th>Sales value (SAR)</th></tr>{t10}</table>")
    else:
        top10_html = ("<h2>Top 10 winning products</h2><p>Kisi bhi product par Noon/Amazon/Salla ne sold ka number nahi "
                      "dikhaya, is liye bike ki ranking nahi ban saki. Asli bike ka number sirf wahi products dete hain jin par "
                      "site khud likhti hai.</p>")
    def top_block(kind, title):
        sub = [r for r in res if r["kind"] == kind][:10]
        rows_ = []
        for i, r in enumerate(sub, 1):
            u = round(r["bought_month"]) if use_est else 0
            rows_.append([i, r["site"], r["title"], r["design"], r["color"], r["price"], r["reviews_now"],
                          u, round(u * r["price"]), r["url"]])
        save_csv(f"top10_{kind}.csv", ["rank", "site", "title", "design", "color", "price_sar", "reviews",
                                       "sold_units", "sold_value_sar", "url"], rows_)
        if not rows_:
            return f"<h2>{title}</h2><p>Is category ka data abhi nahi mila.</p>"
        body = "".join(
            f"<tr><td>{x[0]}</td><td>{html.escape(x[1])}</td>"
            f"<td><a href='{html.escape(x[9])}' target='_blank'>{html.escape(x[2][:60])}</a></td>"
            f"<td>{x[3]}</td><td>{x[4]}</td><td>{x[5]:.0f}</td><td>{x[6]:.0f}</td>"
            + (f"<td><b>{x[7]:,}</b></td><td><b>{x[8]:,}</b></td>" if use_est else "<td>-</td><td>-</td>")
            + "</tr>" for x in rows_)
        note = "" if use_est else "<p class='note'>Is category ke kisi product par site ne sold number nahi dikhaya.</p>"
        return (f"<h2>{title}</h2>{note}<table><tr><th>#</th><th>Site</th><th>Product</th><th>Design</th>"
                f"<th>Color</th><th>SAR</th><th>Reviews</th><th>Bike (site ke mutabiq)</th><th>Sales value (SAR)</th></tr>{body}</table>")

    daily_html = top_block("daily", f"Top 10 DAILY WEAR (aakhri {a.days} din)")
    fancy_html = top_block("fancy", f"Top 10 FANCY WEAR (aakhri {a.days} din)")
    top_rows = "".join(
        f"<tr><td>{i}</td><td>{html.escape(r['site'])}</td>"
        f"<td><a href='{html.escape(r['url'])}' target='_blank'>{html.escape(r['title'][:60])}</a></td>"
        f"<td>{r['design']}</td><td>{r['color']}</td><td>{r['kind']}</td><td>{r['price']:.0f}</td>"
        f"<td>{r['new_reviews']:.0f}</td><td>{r['metric']:,.0f}</td><td>{r['bought_month']:.0f}</td></tr>"
        for i, r in enumerate(res[: a.top], 1))
    page = f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Abaya Market Report</title><style>
body{{font-family:system-ui;max-width:900px;margin:auto;padding:12px;background:#faf7f2;color:#222}}
h2{{border-bottom:2px solid #c9a96e;margin-top:28px}} .r{{display:flex;align-items:center;gap:8px;margin:3px 0;font-size:13px}}
.n{{width:120px;flex:none}} .b{{background:#c9a96e;height:13px;border-radius:3px}} .v{{font-size:12px;color:#555}}
table{{border-collapse:collapse;font-size:12px;display:block;overflow-x:auto}} td,th{{padding:4px 8px;border-bottom:1px solid #ddd;text-align:left}}
th{{background:#c9a96e}} .note{{font-size:12px;color:#777}}</style></head><body>
<h1>Abaya Market Report (aakhri {a.days} din)</h1>
<p>Metric: <b>{mlabel}</b>. Total tracked: <b>{total:,.0f}</b> (sirf wo products jo tool ne track kiye).</p>
{top10_html}{daily_html}{fancy_html}<h2>Shehar ka hissa</h2><p class='note'>Source: {html.escape(source)}</p>
{bars("City-wise", city_tot)}
<h2>Design</h2>{bars("Design-wise", designs)}{bars("Daily vs Fancy", kinds)}
<h2>Rang, price, store</h2>{bars("Color-wise", colors)}{bars("Price (SAR)", [(b, bands[b]) for b in order])}{bars("Store-wise", sites)}
<h2>Design x City</h2><table><tr><th>Design</th>{"".join(f"<th>{c}</th>" for c in CITIES)}<th>Total</th></tr>{dc_html}</table>
<p class='note'>* = is design ka alag city data nahi mila, overall city hissa use hua.</p>
<h2>Top products</h2><table><tr><th>#</th><th>Site</th><th>Product</th><th>Design</th><th>Color</th><th>Type</th><th>SAR</th>
<th>Naye reviews</th><th>{mlabel.split(' (')[0]}</th><th>Bought/month</th></tr>{top_rows}</table>
<p class='note'>Bike ke numbers wahi hain jo site ne khud product par likhe (aksar rounded, jaise 1K+). Jis product par site ne number nahi likha wo ranking mein nahi. City split andaza hai: Noon/Amazon khareedaar ka shehar nahi batate.</p>
</body></html>"""
    with open("top_sellers.html", "w", encoding="utf-8") as f:
        f.write(page)
    print("Report ban gayi: top_sellers.html")
    print("CSV files: top10_daily.csv, top10_fancy.csv, top10_winners.csv, top_products.csv, city_split.csv, design_by_city.csv")
    print("City source:", source)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scrape")
    s.add_argument("--query", default="abaya")
    s.add_argument("--pages", type=int, default=3)
    s.add_argument("--salla", nargs="*", help="Salla store URLs")
    s.add_argument("--only", choices=["noon", "amazon"])
    s.add_argument("--salla-auto", action="store_true", help="Salla showcase se stores khud dhoondo")
    s.add_argument("--max-stores", type=int, default=25)
    s.add_argument("--show", action="store_true", help="Browser window dikhao")
    s.set_defaults(fn=cmd_scrape)
    r = sub.add_parser("report")
    r.add_argument("--days", type=int, default=30)
    r.add_argument("--top", type=int, default=30)
    r.set_defaults(fn=cmd_report)
    c = sub.add_parser("cities")
    c.set_defaults(fn=cmd_cities)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
