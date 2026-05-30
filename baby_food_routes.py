# baby_food_routes.py
# Add to your existing app.py:
#
#   from baby_food_routes import register_baby_food_routes
#   register_baby_food_routes(app)
#
# Requirements:
#   pip install requests flask

import re
import requests
from flask import Flask, jsonify, request, render_template

OPEN_FOOD_FACTS_URL = "https://world.openfoodfacts.org/api/v0/product/{barcode}.json"
HEADERS = {"User-Agent": "BookFinderKidsApp/1.0 (family-friendly food scanner)"}

# ── Rules engine ──────────────────────────────────────────────────────────────

INGREDIENT_RULES = [
    # Always avoid
    { "match": ["honey", "miel"],
      "status": "avoid", "min_age": 999,
      "reason": "Risk of infant botulism — never give to babies under 12 months" },
    { "match": ["unpasteurized", "raw milk", "lait cru"],
      "status": "avoid", "min_age": 999,
      "reason": "Unpasteurized products carry harmful bacteria risk for babies" },
    { "match": ["saccharin", "aspartame", "sucralose", "acesulfame", "stevia"],
      "status": "avoid", "min_age": 24,
      "reason": "Artificial sweeteners not recommended for young children" },
    { "match": ["alcohol", "wine", "beer", "spirits"],
      "status": "avoid", "min_age": 999,
      "reason": "Alcohol is harmful to babies and young children" },
    { "match": ["caffeine", "coffee", "tea extract"],
      "status": "avoid", "min_age": 36,
      "reason": "Caffeine not appropriate for babies or toddlers" },
    # Avoid/caution under 12 months
    { "match": ["added salt", "sodium chloride", "sel ajouté"],
      "status": "caution", "min_age": 12,
      "reason": "Avoid added salt under 12 months — kidneys can't process it well" },
    { "match": ["sugar", "sucrose", "glucose syrup", "corn syrup", "dextrose", "fructose"],
      "status": "caution", "min_age": 12,
      "reason": "Added sugars not recommended under 12 months" },
    { "match": ["nitrate", "nitrite", "sodium nitrate", "sodium nitrite"],
      "status": "caution", "min_age": 12,
      "reason": "Nitrates in processed meats should be limited under 12 months" },
    # Common allergens
    { "match": ["peanut", "arachide"],
      "status": "caution", "min_age": 0,
      "reason": "Top allergen — introduce intentionally and watch for reactions (3-day rule)" },
    { "match": ["almond", "cashew", "walnut", "pecan", "pistachio", "hazelnut", "tree nut"],
      "status": "caution", "min_age": 0,
      "reason": "Tree nut allergen — introduce carefully, choking risk if whole" },
    { "match": ["egg", "albumin"],
      "status": "caution", "min_age": 0,
      "reason": "Common allergen — introduce intentionally around 6 months" },
    { "match": ["wheat", "gluten", "flour"],
      "status": "caution", "min_age": 0,
      "reason": "Common allergen — introduce intentionally" },
    { "match": ["soy", "soya", "soja"],
      "status": "caution", "min_age": 0,
      "reason": "Common allergen — introduce intentionally" },
    { "match": ["sesame", "tahini"],
      "status": "caution", "min_age": 0,
      "reason": "Common allergen — introduce intentionally" },
    { "match": ["milk", "dairy", "whey", "casein", "lactose"],
      "status": "caution", "min_age": 0,
      "reason": "Common allergen — dairy as ingredient is generally fine after 6 months" },
    # High mercury fish
    { "match": ["tuna", "swordfish", "shark", "king mackerel"],
      "status": "caution", "min_age": 0,
      "reason": "High mercury fish — limit to 1–2 servings/week" },
    # Choking hazards
    { "match": ["whole nut", "whole grape", "popcorn"],
      "status": "avoid", "min_age": 48,
      "reason": "Choking hazard for children under 4 years" },
]

# ── Daily intake limits by age (Health Canada / NASEM / CPS) ─────────────────
# Sources:
#   - Sodium 7-12m: 370mg/day (NASEM AI, solidstarts.com)
#   - Sodium 1-3y:  1200mg/day (Health Canada DV update 2021)
#   - Sugar  6-12m: no added sugar (Health Canada)
#   - Sugar  1-4y:  16g/day max added sugar (AHA guideline)
#   - Calories 6-12m: ~750-900 kcal/day (from solids ~200-300 kcal)
#   - Calories 1-3y:  ~1000-1400 kcal/day
#   - Fat: not restricted under 2y (Health Canada)

DAILY_INTAKE = {
    # age_months: { nutrient: { ai, ul, unit, note } }
    # ai  = adequate intake / recommended
    # ul  = upper limit (don't exceed)
    # unit = display unit
    range(0,  7):  {
        "calories": {"ai": None,  "ul": None,  "unit": "kcal", "note": "Breast milk/formula only"},
        "sodium":   {"ai": None,  "ul": None,  "unit": "mg",   "note": "No solids yet"},
        "sugar":    {"ai": 0,     "ul": 0,     "unit": "g",    "note": "No added sugar"},
        "fat":      {"ai": None,  "ul": None,  "unit": "g",    "note": "From breast milk/formula only"},
    },
    range(7,  13): {
        "calories": {"ai": 800,   "ul": None,  "unit": "kcal", "note": "~200–300 kcal from solids, rest from milk"},
        "sodium":   {"ai": 370,   "ul": 370,   "unit": "mg",   "note": "NASEM Adequate Intake for 7–12 months"},
        "sugar":    {"ai": 0,     "ul": 0,     "unit": "g",    "note": "No added sugar recommended under 12 months"},
        "fat":      {"ai": None,  "ul": None,  "unit": "g",    "note": "Fat not restricted under 2 years"},
        "protein":  {"ai": 11,    "ul": None,  "unit": "g",    "note": "NASEM AI for 7–12 months"},
    },
    range(13, 37): {
        "calories": {"ai": 1200,  "ul": None,  "unit": "kcal", "note": "Average for active toddler 1–3 years"},
        "sodium":   {"ai": 1200,  "ul": 1500,  "unit": "mg",   "note": "Health Canada DV for 1–4 years; UL 1500mg"},
        "sugar":    {"ai": None,  "ul": 16,    "unit": "g",    "note": "AHA: max ~4 tsp (16g) added sugar/day under 4y"},
        "fat":      {"ai": None,  "ul": None,  "unit": "g",    "note": "Fat not restricted under 2 years"},
        "protein":  {"ai": 13,    "ul": None,  "unit": "g",    "note": "NASEM AI for 1–3 years"},
    },
}


def get_daily_intake(age_months: int) -> dict:
    """Return daily intake guidelines for the given age in months."""
    for age_range, limits in DAILY_INTAKE.items():
        if age_months in age_range:
            return limits
    return DAILY_INTAKE[range(13, 37)]  # default to toddler


def check_ingredients(ingredients_text: str, age_months: int) -> list[dict]:
    flags = []
    parts = re.split(r"[,;]", ingredients_text)
    seen  = set()
    for part in parts:
        part = part.strip().strip("()[]").strip()
        if not part or part.lower() in seen:
            continue
        seen.add(part.lower())
        for rule in INGREDIENT_RULES:
            if any(kw in part.lower() for kw in rule["match"]):
                status = rule["status"]
                if age_months < rule["min_age"] and status == "caution":
                    status = "avoid"
                flags.append({"name": part, "status": status, "reason": rule["reason"]})
                break
    return flags


def build_nutrition_analysis(nutriments: dict, age_months: int, serving_g: float = None) -> dict:
    """
    Build full nutrition analysis including:
    - Per-serving values
    - Daily intake limits for this age
    - How many servings before exceeding daily limit
    - Status flag per nutrient
    """
    daily = get_daily_intake(age_months)
    analysis = []

    # nutriment field → (display label, per_100g field, unit, is_mg)
    nutrient_map = {
        "sodium":   ("Sodium",   "sodium_100g",          "mg",   True),
        "sugars":   ("Sugar",    "sugars_100g",           "g",    False),
        "calories": ("Calories", "energy-kcal_100g",      "kcal", False),
        "fat":      ("Fat",      "fat_100g",              "g",    False),
        "protein":  ("Protein",  "proteins_100g",         "g",    False),
        "saturated-fat": ("Saturated Fat", "saturated-fat_100g", "g", False),
    }

    for key, (label, field, unit, is_mg) in nutrient_map.items():
        val_per_100g = nutriments.get(field)
        if val_per_100g is None:
            continue

        # Convert to display unit
        val_per_100g = float(val_per_100g)
        if is_mg:
            val_per_100g_display = val_per_100g * 1000  # g → mg
        else:
            val_per_100g_display = val_per_100g

        # Per-serving value
        val_per_serving = None
        if serving_g:
            val_per_serving = val_per_100g_display * serving_g / 100

        # Daily limits
        dl = daily.get(key, {})
        ai = dl.get("ai")
        ul = dl.get("ul")
        dl_note = dl.get("note", "")

        # Status
        status = "safe"
        status_reason = ""
        if ul is not None and val_per_serving is not None and ul > 0:
            pct_of_ul_per_serving = (val_per_serving / ul) * 100
            if pct_of_ul_per_serving > 50:
                status = "avoid"
                status_reason = f"One serving = {pct_of_ul_per_serving:.0f}% of daily limit"
            elif pct_of_ul_per_serving > 25:
                status = "caution"
                status_reason = f"One serving = {pct_of_ul_per_serving:.0f}% of daily limit"
        elif key == "sugar" and age_months < 12 and val_per_serving and val_per_serving > 0:
            status = "caution"
            status_reason = "Added sugars not recommended under 12 months"
        elif key == "sodium" and age_months < 12:
            if val_per_serving and val_per_serving > 100:
                status = "avoid"
                status_reason = "Too high in sodium for babies under 12 months"
            elif val_per_serving and val_per_serving > 50:
                status = "caution"
                status_reason = "Moderate sodium — monitor total daily intake"

        # Max servings before hitting limit
        max_servings = None
        if ul and val_per_serving and val_per_serving > 0:
            max_servings = ul / val_per_serving
            max_servings = round(max_servings, 1)

        # Percent of daily limit per serving
        pct_daily = None
        if ul and val_per_serving:
            pct_daily = round((val_per_serving / ul) * 100, 1)
        elif ai and val_per_serving:
            pct_daily = round((val_per_serving / ai) * 100, 1)

        entry = {
            "name":              label,
            "unit":              unit,
            "per_100g":          round(val_per_100g_display, 2),
            "per_serving":       round(val_per_serving, 2) if val_per_serving is not None else None,
            "daily_ai":          ai,
            "daily_ul":          ul,
            "daily_note":        dl_note,
            "pct_daily":         pct_daily,
            "max_servings":      max_servings,
            "status":            status,
            "status_reason":     status_reason,
        }
        analysis.append(entry)

    return analysis


def age_stage(age_months: int) -> str:
    if age_months < 6:  return "Babies under 6 months should only have breast milk or formula."
    if age_months < 8:  return "At 6–8 months, introduce single-ingredient purées one at a time."
    if age_months < 10: return "At 8–10 months, soft finger foods and mashed textures are great."
    if age_months < 12: return "At 10–12 months, most soft foods are fine — still avoid honey and added salt."
    if age_months < 18: return "After 12 months, most foods are appropriate — watch portion sizes."
    return "Toddlers can eat most foods — still supervise for choking hazards."


def analyze(product: dict, age_months: int) -> dict:
    ingredients_text = (product.get("ingredients_text_en") or
                        product.get("ingredients_text") or
                        product.get("ingredients") or "")
    nutriments   = product.get("nutriments", {})
    serving_g    = product.get("serving_g")  # optional serving size in grams

    ing_flags    = check_ingredients(ingredients_text, age_months) if ingredients_text else []
    nut_analysis = build_nutrition_analysis(nutriments, age_months, serving_g)

    # Determine overall status
    ing_statuses = [f["status"] for f in ing_flags]
    nut_statuses = [n["status"] for n in nut_analysis]
    all_statuses = ing_statuses + nut_statuses

    if "avoid" in all_statuses:
        overall        = "avoid"
        overall_reason = f"Contains {all_statuses.count('avoid')} ingredient(s)/nutrient(s) to avoid for this age."
    elif "caution" in all_statuses:
        overall        = "caution"
        overall_reason = f"Generally okay but has {all_statuses.count('caution')} item(s) to watch."
    else:
        overall        = "safe"
        overall_reason = "No concerning ingredients or nutrition flags found for this age group."

    tips = [
        "Follow the 3-day rule when introducing any new food — introduce one at a time.",
        "Portion sizes for babies are much smaller than adults — start with 1–2 teaspoons.",
        "Always supervise feeding and never leave your baby alone while eating.",
    ]
    if any("nut" in f["name"].lower() for f in ing_flags):
        tips.append("Nuts should be in smooth/butter form — never whole to children under 5.")
    if serving_g:
        tips.append(f"Standard serving size for this product is {serving_g}g — adjust for your baby's appetite.")

    daily_summary = {
        k: v for k, v in get_daily_intake(age_months).items()
        if v.get("ai") or v.get("ul")
    }

    return {
        "product_name":   (product.get("product_name_en") or
                           product.get("product_name") or
                           product.get("name") or "Unknown product"),
        "brand":          product.get("brands") or product.get("brand") or "",
        "image_url":      (product.get("image_front_url") or
                           product.get("image_url") or ""),
        "overall":        overall,
        "overall_reason": overall_reason,
        "age_note":       age_stage(age_months),
        "age_months":     age_months,
        "ingredients":    ing_flags,
        "nutrition":      nut_analysis,
        "daily_limits":   daily_summary,
        "tips":           tips,
        "allergens":      [a.replace("en:", "").replace("-", " ")
                           for a in product.get("allergens_tags", [])],
        "disclaimer":     "This is general guidance only, not medical advice. Always consult your pediatrician before introducing new foods.",
    }


# ── Routes ────────────────────────────────────────────────────────────────────

def register_baby_food_routes(app: Flask):

    @app.route("/baby-food-scanner")
    def baby_food_scanner():
        return render_template("baby_food_scanner.html")

    @app.route("/api/baby-food-lookup")
    def baby_food_lookup():
        """GET /api/baby-food-lookup?barcode=0123456789&age_months=8"""
        barcode    = request.args.get("barcode", "").strip()
        age_months = request.args.get("age_months", "8")

        if not barcode:
            return jsonify({"error": "No barcode provided"}), 400
        try:
            age_months = max(0, min(36, int(age_months)))
        except ValueError:
            return jsonify({"error": "Invalid age"}), 400

        # 1. Check local Superstore database first
        try:
            from superstore_db import get_product
            local = get_product(barcode)
            if local:
                local_product = {
                    "product_name":     local["name"],
                    "brands":           local["brand"] or "",
                    "ingredients_text": local["ingredients"] or "",
                    "nutriments":       local["nutriments"],
                    "image_url":        local["image_url"] or "",
                    "allergens_tags":   [],
                }
                result = analyze(local_product, age_months)
                result["barcode"] = barcode
                result["source"]  = "superstore_db"
                return jsonify(result)
        except Exception:
            pass  # DB not set up yet

        # 2. Fall back to Open Food Facts
        try:
            resp = requests.get(
                OPEN_FOOD_FACTS_URL.format(barcode=barcode),
                headers=HEADERS, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            return jsonify({"error": f"Could not reach Open Food Facts: {e}"}), 502

        if data.get("status") != 1:
            return jsonify({"error": "Product not found", "barcode": barcode, "not_found": True}), 404

        result = analyze(data.get("product", {}), age_months)
        result["barcode"] = barcode
        result["source"]  = "openfoodfacts"
        return jsonify(result)