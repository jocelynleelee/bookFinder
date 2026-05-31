"""
Superstore Baby Food Database
==============================
Save products fetched from Superstore API into a local SQLite database.
The baby food scanner checks this DB before Open Food Facts.

Usage — integrate into your existing Superstore API script:

    from superstore_db import save_product, get_product, list_products

    # After fetching from Superstore API:
    save_product(
        gtin          = data.get("gtin"),
        name          = data.get("name"),
        brand         = data.get("brand"),
        ingredients   = data.get("ingredients"),
        nutrition     = data.get("nutritionFacts"),  # the list
    )
"""

import sqlite3
import json
import re
import time
from datetime import datetime
from pathlib import Path
from get_superstore_product import *
DB_PATH = "baby_products.db"


# ── Database setup ─────────────────────────────────────────────────────────────

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS baby_products (
            gtin             TEXT PRIMARY KEY,
            name             TEXT NOT NULL,
            brand            TEXT,
            ingredients      TEXT,
            nutrition_json   TEXT,   -- raw nutritionFacts list as JSON
            nutriments_json  TEXT,   -- parsed per-100g nutriments (Open Food Facts format)
            image_url        TEXT,
            source           TEXT DEFAULT 'superstore',
            added_at         TEXT DEFAULT (datetime('now')),
            updated_at       TEXT DEFAULT (datetime('now'))
        )
    """)
    clean_db(conn)
    conn.commit()
    conn.close()


# ── Nutrition parser ───────────────────────────────────────────────────────────

def parse_value(value_str: str | None) -> float | None:
    """Parse '10 mg', '1.5 g', '35 cal' → float."""
    if not value_str:
        return None
    try:
        return float(re.sub(r"[^\d.]", "", value_str.split()[0]))
    except (ValueError, IndexError):
        return None


def parse_nutriments(nutrition_list: list) -> dict:
    """
    Convert Superstore nutritionFacts list → Open Food Facts-style nutriments dict.
    Values stored as g per 100g (sodium in g, others in g).
    """
    if not nutrition_list:
        return {}

    nut = nutrition_list[0]  # use first nutrition object

    # Serving size in grams (for per-100g conversion)
    serving_g = None
    for top in (nut.get("topNutrition") or []):
        if top.get("code") == "servingSizeEN":
            serving_g = parse_value(top.get("valueInGram"))
            break

    def per100(value_str: str | None) -> float | None:
        val = parse_value(value_str)
        if val is None:
            return None
        if serving_g and serving_g > 0:
            return round(val / serving_g * 100, 4)
        return val

    nutriments = {}

    # Calories
    cal = per100((nut.get("calories") or {}).get("valueInGram"))
    if cal is not None:
        nutriments["energy-kcal_100g"] = cal

    # Total fat
    fat = per100((nut.get("totalFat") or {}).get("valueInGram"))
    if fat is not None:
        nutriments["fat_100g"] = fat

    # Saturated fat (inside totalFat.subNutrients)
    for sub in ((nut.get("totalFat") or {}).get("subNutrients") or []):
        if sub.get("code") == "saturatedFat":
            sf = per100(sub.get("valueInGram"))
            if sf is not None:
                nutriments["saturated-fat_100g"] = sf

    # Sodium — valueInGram is in mg, store as g per 100g
    sodium_raw = (nut.get("sodium") or {}).get("valueInGram")
    if sodium_raw:
        sodium_mg = parse_value(sodium_raw)
        if sodium_mg is not None:
            per100_mg = (sodium_mg / serving_g * 100) if serving_g else sodium_mg
            nutriments["sodium_100g"] = round(per100_mg / 1000, 6)  # mg → g

    # Carbohydrates + sugar + fibre
    carb = per100((nut.get("totalCarbohydrate") or {}).get("valueInGram"))
    if carb is not None:
        nutriments["carbohydrates_100g"] = carb

    for sub in ((nut.get("totalCarbohydrate") or {}).get("subNutrients") or []):
        if sub.get("code") == "sugar":
            sugar = per100(sub.get("valueInGram"))
            if sugar is not None:
                nutriments["sugars_100g"] = sugar
        elif sub.get("code") == "dietaryFiber":
            fiber = per100(sub.get("valueInGram"))
            if fiber is not None:
                nutriments["fiber_100g"] = fiber

    # Protein
    protein = per100((nut.get("protein") or {}).get("valueInGram"))
    if protein is not None:
        nutriments["proteins_100g"] = protein

    # Potassium (mg → g per 100g)
    potassium_raw = (nut.get("potassium") or {}).get("valueInGram")
    if potassium_raw:
        pot_mg = parse_value(potassium_raw)
        if pot_mg is not None:
            per100_mg = (pot_mg / serving_g * 100) if serving_g else pot_mg
            nutriments["potassium_100g"] = round(per100_mg / 1000, 6)

    # Cholesterol (mg → g per 100g)
    chol_raw = (nut.get("cholesterol") or {}).get("valueInGram")
    if chol_raw:
        chol_mg = parse_value(chol_raw)
        if chol_mg is not None:
            per100_mg = (chol_mg / serving_g * 100) if serving_g else chol_mg
            nutriments["cholesterol_100g"] = round(per100_mg / 1000, 6)

    return nutriments


# ── Save / get ─────────────────────────────────────────────────────────────────

def save_product(
    gtin:        str,
    name:        str,
    brand:       str       = "",
    ingredients: str       = "",
    nutrition:   list      = None,
    image_url:   str       = "",
    source:      str       = "superstore",
) -> bool:
    """
    Save a product to the database.
    Returns True if inserted, False if updated.
    """
    init_db()
    
    gtin = str(gtin).strip().lstrip("0") or str(gtin).strip()
    nutrition = nutrition or []
    nutriments = parse_nutriments(nutrition)
    now = datetime.now().isoformat()

    conn = get_conn()
    
    existing = conn.execute(
        "SELECT 1 FROM baby_products WHERE gtin = ?",
        (gtin,)
    ).fetchone()

    if existing:
        conn.execute("""
            UPDATE baby_products SET
                name           = ?,
                brand          = ?,
                ingredients    = ?,
                nutrition_json = ?,
                nutriments_json = ?,
                image_url      = ?,
                source         = ?,
                updated_at     = ?
            WHERE gtin = ?
        """, (name, brand, ingredients,
              json.dumps(nutrition), json.dumps(nutriments),
              image_url, source, now, gtin))
        inserted = False
    else:
        conn.execute("""
            INSERT INTO baby_products
            (gtin, name, brand, ingredients, nutrition_json, nutriments_json, image_url, source, added_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (gtin, name, brand, ingredients,
              json.dumps(nutrition), json.dumps(nutriments),
              image_url, source, now, now))
        inserted = True

    conn.commit()
    conn.close()

    action = "Saved" if inserted else "Updated"
    print(f"  {action} → {name} (GTIN: {gtin})")
    return inserted


def get_product(gtin: str) -> dict | None:
    """Look up a product by GTIN. Returns None if not found."""
    init_db()
    gtin = str(gtin).strip()

    conn = get_conn()
    # Try exact match first, then without leading zeros
    row = (conn.execute("SELECT * FROM baby_products WHERE gtin = ?", (gtin,)).fetchone() or
           conn.execute("SELECT * FROM baby_products WHERE gtin = ?", (gtin.lstrip("0"),)).fetchone() or
           conn.execute("SELECT * FROM baby_products WHERE LTRIM(gtin, '0') = LTRIM(?, '0')", (gtin,)).fetchone())
    conn.close()

    if not row:
        return None

    return {
        "gtin":          row["gtin"],
        "name":          row["name"],
        "brand":         row["brand"],
        "ingredients":   row["ingredients"],
        "nutrition":     json.loads(row["nutrition_json"] or "[]"),
        "nutriments":    json.loads(row["nutriments_json"] or "{}"),
        "image_url":     row["image_url"],
        "source":        row["source"],
        "added_at":      row["added_at"],
    }


def list_products() -> list[dict]:
    """Return all products in the database."""
    init_db()
    conn = get_conn()
    rows = conn.execute(
        "SELECT gtin, name, brand, source, added_at FROM baby_products ORDER BY added_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def clean_db(conn):
    conn.execute("""
        DELETE FROM baby_products
        WHERE
            ingredients IS NULL OR TRIM(ingredients) = '' OR ingredients = '[]'
            OR nutrition_json IS NULL OR TRIM(nutrition_json) = '' OR nutrition_json = '[]' OR nutrition_json = '{}'
    """)
    conn.commit()

# ── CLI helper ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if "--list" in sys.argv:
        products = list_products()
        if not products:
            print("No products in database.")
        else:
            print(f"\n{'GTIN':<16} {'Name':<45} {'Brand':<20} {'Source':<12} Added")
            print("-" * 110)
            for p in products:
                print(f"{p['gtin']:<16} {p['name'][:43]:<45} {(p['brand'] or '')[:18]:<20} {p['source']:<12} {p['added_at'][:10]}")
        sys.exit(0)

    # Example: save the sample product from the docstring
    print("Saving sample product...")
    url = "https://www.realcanadiansuperstore.ca/en/food/natural-and-organic/c/28189?navid=flyout-L2-Natural-Organic"
    product_ids = get_baby_food_ids(url)
    for product_id in product_ids:
        data = get_superstore_product(product_id)
        if not data.get("nutritionFacts"):
            continue
        save_product(
            data.get("gtin"),
            data.get("name"),
            data.get("brand"),
            data.get("ingredients"),
            data.get("nutritionFacts"),
            data["imageAssets"][0]["mediumUrl"]
        )
        time.sleep(2)
    print(f"\nDatabase saved to: {DB_PATH}")
    print("Run with --list to see all products.")
