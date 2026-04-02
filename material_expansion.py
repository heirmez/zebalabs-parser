"""Level 2 BOM: expand DWG component codes -> raw materials using tblItem.

Data files (no header rows in either file):

  tblItem.csv  (primary — 35 cols)
    [0]  ItemID          numeric
    [1]  ProductType     'BLOCK' | 'LAYER' | 'FINISHED PRODUCTS' | 'RAW MATERIALS'
    [2]  ItemName
    [3]  ItemCode        may have tab+duplicate text; use first token only
    [9]  Volume          m³ per unit
    [11] Unit            'no' | 'rm' | 'mtr' | 'kg' | 'sheet' | ...
    [17] ProductTypeID   1=BLOCK 2=LAYER 3=RAW MATERIALS
    ── RAW MATERIALS items ──
    [18] PRate           price per unit
    ── BLOCK / LAYER / FINISHED PRODUCTS items ──
    [19] RawMaterialIDs  comma-sep numeric ItemIDs
    [20] RawMaterialQtys comma-sep floats (qty per 1 parent unit)
    [34] PRate           block price

  raw_materials.csv  (legacy 7-col export, optional fallback)
    [0]  ItemID
    [1]  ItemCode
    [2]  ItemName
    [3]  ProductType string
    [4]  RawMaterialIDs  (same numeric IDs as above)
    [5]  RawMaterialQtys
    [6]  RawMaterialSlNos

Logic:
  1.  Load tblItem.csv -> build by_id (all items keyed by numeric ID)
                          build by_code (all items keyed by ItemCode.upper())
  2.  For each DWG component code:
        - look up by_code  -> BLOCK entry with raw_ids list
        - expand each raw_id via by_id -> actual raw material (name, code, unit, rate)
        - accumulate qty:  furn_qty * comp_qty * raw_qty_per_unit
  3.  to_flat_list() -> sorted A-Z list for procurement BOM
"""

import csv
import os


def _float(v) -> float:
    try:
        return float(v or 0)
    except (ValueError, TypeError):
        return 0.0


def _clean_code(raw: str) -> str:
    """Strip whitespace and remove any tab-duplicated suffix."""
    return raw.split("\t")[0].strip() if raw else ""


# ── Loaders ──────────────────────────────────────────────────────────────────

def _load_from_tbl_item(path: str) -> tuple[dict, dict]:
    """Parse tblItem.csv (35-column no-header format).

    Returns (by_code, by_id) where both are plain dicts.
    """
    by_code: dict = {}
    by_id: dict   = {}

    RAW_TYPES = {"RAW MATERIALS"}
    BLOCK_TYPES = {"BLOCK", "LAYER", "FINISHED PRODUCTS"}

    with open(path, "r", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if len(row) < 12:
                continue
            item_id      = row[0].strip()
            product_type = row[1].strip().upper()
            item_name    = row[2].strip()
            item_code    = _clean_code(row[3])
            volume       = _float(row[9])       # col 9 = volume per unit
            unit         = row[11].strip().lower() if row[11].strip() else ""

            if not item_id:
                continue

            if product_type in RAW_TYPES:
                rate = _float(row[18] if len(row) > 18 else 0)
                entry = {
                    "item_code":    item_code,
                    "item_name":    item_name,
                    "product_type": product_type,
                    "unit":         unit,
                    "rate":         rate,
                    "volume":       volume,
                    "weight":       0.0,
                    "raw_ids":      [],
                    "raw_qtys":     [],
                }
                by_id[item_id] = entry
                if item_code:
                    by_code[item_code.upper()] = entry

            elif product_type in BLOCK_TYPES:
                rate         = _float(row[34] if len(row) > 34 else 0)
                raw_ids_str  = row[19].strip() if len(row) > 19 else ""
                raw_qtys_str = row[20].strip() if len(row) > 20 else ""

                raw_ids: list[str] = [x.strip() for x in raw_ids_str.split(",") if x.strip()]
                raw_qtys: list[float] = []
                for q in raw_qtys_str.split(","):
                    q = q.strip()
                    raw_qtys.append(_float(q) if q else 0.0)

                entry = {
                    "item_code":    item_code,
                    "item_name":    item_name,
                    "product_type": product_type,
                    "unit":         unit,
                    "rate":         rate,
                    "volume":       volume,
                    "weight":       0.0,
                    "raw_ids":      raw_ids,
                    "raw_qtys":     raw_qtys,
                }
                by_id[item_id] = entry
                if item_code:
                    by_code[item_code.upper()] = entry

    return by_code, by_id


def _load_from_raw_materials_csv(path: str) -> tuple[dict, dict]:
    """Parse raw_materials.csv (7-column no-header format).

    Returns (by_code, by_id) — by_id only covers items in raw_materials.csv;
    raw material details can't be resolved without tblItem.csv.
    """
    by_code: dict = {}
    by_id: dict   = {}

    with open(path, "r", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if len(row) < 5:
                continue
            item_id      = row[0].strip()
            item_code    = row[1].strip()
            item_name    = row[2].strip()
            product_type = row[3].strip().upper()
            raw_ids_str  = row[4].strip()
            raw_qtys_str = row[5].strip() if len(row) > 5 else ""

            if not item_code:
                continue

            raw_ids: list[str] = [x.strip() for x in raw_ids_str.split(",") if x.strip()]
            raw_qtys: list[float] = []
            for q in raw_qtys_str.split(","):
                q = q.strip()
                raw_qtys.append(_float(q) if q else 0.0)

            entry = {
                "item_code":    item_code,
                "item_name":    item_name,
                "product_type": product_type,
                "unit":         "",
                "rate":         0.0,
                "volume":       0.0,
                "weight":       0.0,
                "raw_ids":      raw_ids,
                "raw_qtys":     raw_qtys,
            }
            if item_id:
                by_id[item_id] = entry
            by_code[item_code.upper()] = entry

    return by_code, by_id


# ── Public API ────────────────────────────────────────────────────────────────

def load_tbl_item(csv_path: str) -> tuple[dict, dict]:
    """Load item catalog from tblItem.csv (preferred) or raw_materials.csv.

    Resolution order:
      1. If csv_path ends with 'tblItem.csv'       -> load it directly
      2. Otherwise look for tblItem.csv in same dir -> load it
      3. Fall back to raw_materials.csv (limited — no raw-material names)

    Returns:
        (by_code, by_id)
        by_code: {ITEM_CODE_UPPER: item_entry}
        by_id:   {STR_ITEM_ID:    item_entry}
        Each item_entry has keys:
            item_code, item_name, product_type, unit, rate,
            volume, weight, raw_ids (list[str]), raw_qtys (list[float])

        Both dicts are empty if csv_path is not found.
    """
    if not csv_path or not os.path.isfile(csv_path):
        return {}, {}

    # Try tblItem.csv path resolution
    name_lower = os.path.basename(csv_path).lower()
    if name_lower == "tblitem.csv":
        return _load_from_tbl_item(csv_path)

    # Given raw_materials.csv — look for sibling tblItem.csv
    sibling = os.path.join(os.path.dirname(csv_path), "tblItem.csv")
    if os.path.isfile(sibling):
        return _load_from_tbl_item(sibling)

    # Last resort: raw_materials.csv only (raw material names will be IDs)
    return _load_from_raw_materials_csv(csv_path)


def expand_components(rooms: list, by_code: dict, by_id: dict) -> dict:
    """Expand component codes from extraction rooms into aggregated raw materials.

    For each furniture component code:
      - Found in by_code with raw_ids → BLOCK/LAYER, expand into raw materials
      - Not found or no raw_ids       → pass through as-is (already a raw material)

    Args:
        rooms:   list of parsed room dicts (from extraction JSON)
        by_code: {ITEM_CODE_UPPER: entry}   from load_tbl_item()
        by_id:   {STR_ITEM_ID:    entry}    from load_tbl_item()

    Returns:
        {ITEM_CODE_UPPER: {item_code, item_name, unit, rate,
                           volume, weight, qty (total across project)}}
    """
    materials: dict = {}

    def _add(key: str, qty: float, meta: dict) -> None:
        if key not in materials:
            materials[key] = {**meta, "qty": 0.0}
        materials[key]["qty"] = round(materials[key]["qty"] + qty, 6)

    for room in rooms:
        for furniture in room.get("furniture", []):
            furn_qty = furniture.get("quantity", 1)
            for comp in furniture.get("components", []):
                comp_code: str  = comp.get("code", "")
                comp_qty: float = comp.get("qty", 0)
                total_qty       = furn_qty * comp_qty

                entry = by_code.get(comp_code.upper())

                if entry and entry.get("raw_ids"):
                    # ── BLOCK / LAYER → expand raw materials ─────────────
                    for i, raw_id in enumerate(entry["raw_ids"]):
                        rqty = entry["raw_qtys"][i] if i < len(entry["raw_qtys"]) else 0.0
                        if rqty <= 0:
                            continue
                        expanded = round(total_qty * rqty, 6)
                        rm = by_id.get(raw_id, {})
                        rm_code = rm.get("item_code") or raw_id
                        _add(rm_code.upper(), expanded, {
                            "item_code": rm_code,
                            "item_name": rm.get("item_name") or rm_code,
                            "unit":      rm.get("unit", ""),
                            "rate":      rm.get("rate", 0),
                            "volume":    rm.get("volume", 0),
                            "weight":    rm.get("weight", 0),
                        })
                else:
                    # ── RAW or unknown → keep as-is ──────────────────────
                    e = entry or {}
                    _add(comp_code.upper(), total_qty, {
                        "item_code": comp_code,
                        "item_name": e.get("item_name") or comp.get("description", comp_code),
                        "unit":      e.get("unit")      or comp.get("unit", ""),
                        "rate":      e.get("rate")      or comp.get("unit_price", 0),
                        "volume":    e.get("volume")    or comp.get("volume_per_unit", 0),
                        "weight":    e.get("weight")    or comp.get("weight_per_unit", 0),
                    })

    return materials


def to_flat_list(materials: dict) -> list:
    """Sorted flat list of raw materials, A→Z by item_name.

    Returns:
        [{sl_no, item_code, item_name, unit, qty, rate, volume, weight}]
    """
    rows = sorted(materials.values(), key=lambda x: x["item_name"].upper())
    result = []
    for i, m in enumerate(rows, 1):
        qty = round(m["qty"], 4)
        result.append({
            "sl_no":     i,
            "item_code": m["item_code"],
            "item_name": m["item_name"],
            "unit":      m["unit"],
            "qty":       qty,
            "rate":      round(m["rate"], 2) if m["rate"] else 0,
            "volume":    round(qty * m["volume"], 4) if m.get("volume") else 0,
            "weight":    round(qty * m["weight"], 4) if m.get("weight") else 0,
        })
    return result
