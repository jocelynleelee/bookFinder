import requests
import re
import json
from bs4 import BeautifulSoup

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Business-User-Agent": "PCXWEB",
    "Origin": "https://www.realcanadiansuperstore.ca",
    "Referer": "https://www.realcanadiansuperstore.ca/",
    "Site-Banner": "superstore",
    "User-Agent": (
        "Mozilla/5.0"
    ),
    "x-apikey": "C1xujSegT5j3ap3yexJjqhOfELwGKYvz",
    "x-application-type": "Web",
    "x-loblaw-tenant-id": "ONLINE_GROCERIES",
}


def get_category_json(url):
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/html"
    }

    r = requests.get(url, headers=headers)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")

    script = soup.find("script", id="__NEXT_DATA__")
    if not script:
        raise ValueError("No __NEXT_DATA__ found")

    return json.loads(script.text)

def extract_products(next_data):
    products = []

    try:
        # common path in PC Express / Loblaws stack
        items = next_data["props"]["pageProps"]["initialData"]["results"]["products"]

        for p in items:
            products.append({
                "productId": p.get("productId"),
                "name": p.get("name"),
                "brand": p.get("brand"),
                "url": p.get("productUrl"),
            })

    except Exception as e:
        print("parse error:", e)

    return products

def extract_product_id(url: str):
    match = re.search(r"/p/([^/?]+)", url)

    if not match:
        raise ValueError("Could not extract product id")

    return match.group(1)


def get_superstore_product(product_id):
    try:
        # product_id = extract_product_id(url)
        api_url = (
            f"https://api.pcexpress.ca/pcx-bff/api/v1/products/{product_id}"
        )

        params = {
            "lang": "en",
            "date": "29052026",
            "pickupType": "STORE",
            "storeId": "1517",
            "banner": "superstore",
        }

        r = requests.get(
            api_url,
            headers=HEADERS,
            params=params,
            timeout=30,
        )

        print("status:", r.status_code)

        r.raise_for_status()
    except requests.exceptions.HTTPError:
        return {}
    return r.json()

def get_baby_food_ids(url):
    html = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}).text

    ids = extract_product_ids_from_html(html)

    return sorted(ids)

def extract_product_ids_from_html(html):
    # matches 21597406_EA style IDs
    pattern = r"\b\d{6,10}_(?:EA|CS)\b"

    return set(re.findall(pattern, html))

# url = (
#     "https://www.realcanadiansuperstore.ca/en/"
#     "cheddar-style-crunchers-baby-snack/p/21597406_EA"
# )

# product_ids = get_baby_food_ids()
# for product_id in product_ids:
#     data = get_superstore_product(product_id)
#     print(data.keys())
#     print(data.get("name"))
#     print(data.get("ingredients"))
#     print(data.get("nutritionFacts"))