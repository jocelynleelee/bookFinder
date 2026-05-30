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

NUTRITION_RULES = [
    { "nutrient": "sodium",
      "limits": [
          { "threshold": 100, "min_age": 0,  "max_age": 12, "status": "avoid",
            "reason": "Too high in sodium for babies under 12 months" },
          { "threshold": 50,  "min_age": 0,  "max_age": 12, "status": "caution",
            "reason": "Moderate sodium — watch total daily intake under 12 months" },
          { "threshold": 600, "min_age": 12, "max_age": 36, "status": "caution",
            "reason": "High sodium for a toddler — limit portion size" },
      ]},
    { "nutrient": "sugars",
      "limits": [
          { "threshold": 5, "min_age": 0, "max_age": 12, "status": "caution",
            "reason": "Contains sugars — added sugars not recommended under 12 months" },
      ]},
]


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
                flags.append({ "name": part, "status": status, "reason": rule["reason"] })
                break
    return flags


def check_nutrition(nutriments: dict, age_months: int) -> list[dict]:
    flags = []
    field_map = {
        "sodium":  ("Sodium",        "sodium_100g",   1000),     # g → mg
        "sugars":  ("Sugar",         "sugars_100g",   1),        # already g
        "salt":    ("Salt (sodium)", "salt_100g",     393),      # g salt → mg sodium
    }
    for key, (label, field, factor) in field_map.items():
        val = nutriments.get(field)
        if val is None:
            continue
        val_mg = float(val) * factor
        for rule in NUTRITION_RULES:
            if rule["nutrient"] == key:
                for limit in sorted(rule["limits"], key=lambda x: x["threshold"]):
                    if (limit["min_age"] <= age_months < limit["max_age"]
                            and val_mg > limit["threshold"]):
                        flags.append({
                            "name":   label,
                            "value":  f"{val_mg:.0f}mg per 100g",
                            "status": limit["status"],
                            "reason": limit["reason"],
                        })
                        break
    return flags


def age_stage(age_months: int) -> str:
    if age_months < 6:  return "Babies under 6 months should only have breast milk or formula."
    if age_months < 8:  return "At 6–8 months, introduce single-ingredient purées one at a time."
    if age_months < 10: return "At 8–10 months, soft finger foods and mashed textures are great."
    if age_months < 12: return "At 10–12 months, most soft foods are fine — still avoid honey and added salt."
    if age_months < 18: return "After 12 months, most foods are appropriate — watch portion sizes."
    return "Toddlers can eat most foods — still supervise for choking hazards."


def analyze(product: dict, age_months: int) -> dict:
    ingredients_text = (product.get("ingredients_text_en")
                        or product.get("ingredients_text") or "")
    ing_flags  = check_ingredients(ingredients_text, age_months) if ingredients_text else []
    nut_flags  = check_nutrition(product.get("nutriments", {}), age_months)
    all_flags  = ing_flags + nut_flags
    statuses   = [f["status"] for f in all_flags]

    if "avoid" in statuses:
        overall        = "avoid"
        overall_reason = f"Contains {statuses.count('avoid')} ingredient(s) to avoid for this age."
    elif "caution" in statuses:
        overall        = "caution"
        overall_reason = f"Generally okay but {statuses.count('caution')} ingredient(s) to watch."
    else:
        overall        = "safe"
        overall_reason = "No concerning ingredients found for this age group."

    tips = ["Follow the 3-day rule when introducing any new food.",
            "Portion sizes for babies are much smaller than adult servings — start with 1–2 teaspoons.",
            "Always supervise feeding and never leave your baby alone while eating."]
    if any("nut" in f["name"].lower() for f in ing_flags):
        tips.append("Nuts should be in smooth/butter form — never whole to children under 5.")

    return {
        "product_name":   product.get("product_name_en") or product.get("product_name") or "Unknown product",
        "brand":          product.get("brands", ""),
        "image_url":      product.get("image_front_url") or product.get("image_url"),
        "overall":        overall,
        "overall_reason": overall_reason,
        "age_note":       age_stage(age_months),
        "ingredients":    ing_flags,
        "nutrition":      nut_flags,
        "tips":           tips,
        "allergens":      [a.replace("en:", "").replace("-", " ") for a in product.get("allergens_tags", [])],
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
