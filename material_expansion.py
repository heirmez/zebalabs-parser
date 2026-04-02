"""Level 2 BOM: expand DWG component codes -> raw materials using tblItem.

Input:  raw_materials.csv  (from scripts/export-raw-materials.py)
        CSV columns: ItemCode, ItemName, Unit, ProductTypeID,
                     RawMaterialSlNo, RawMaterialID, RawMaterialQty,
                     PRate, Volume, Weight

Logic:
  Each DWG component (e.g. ca090vhl) is a BLOCK or FINISHED item.
  tblItem.RawMaterialID  = comma-sep raw item codes it is made of
  tblItem.RawMaterialQty = comma-sep qty per one unit of the parent

  Total raw material = furniture_count x component_qty x raw_qty_per_unit
  Aggregate identical raw materials across the whole project.
"""

import csv
import os


def _float(v) -> float:
    try:
        return float(v or 0)
    except (ValueError, TypeError):
        return 0.0


def load_tbl_item(csv_path: str) -> dict:
    """Load tblItem export CSV.

    Returns:
        {ITEM_CODE_UPPER: {
            item_code, item_name, unit, product_type_id,
            rate, volume, weight,
            raw_ids: [str],    -- RawMaterialID  split by ','
            raw_qtys: [float], -- RawMaterialQty split by ','
        }}

    product_type_id: 0=RAW  1=LAYER  2=FINISHED  3=BLOCK
    raw_ids is empty  -> item IS a raw material (no further expansion)
    raw_ids non-empty -> expand into constituent raw materials
    """
    if not csv_path or not os.path.isfile(csv_path):
        return {}

    items: dict = {}
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            code = (row.get("ItemCode") or "").strip()
            if not code:
                continue

            raw_ids = [x.strip() for x in (row.get("RawMaterialID") or "").split(",") if x.strip()]
            raw_qtys_raw = [x.strip() for x in (row.get("RawMaterialQty") or "").split(",") if x.strip()]
            raw_qtys: list[float] = []
            for q in raw_qtys_raw:
                try:
                    raw_qtys.append(float(q))
                except ValueError:
                    raw_qtys.append(0.0)

            try:
                ptid = int(row.get("ProductTypeID") or 0)
            except (ValueError, TypeError):
                ptid = 0

            items[code.upper()] = {
                "item_code": code,
                "item_name": (row.get("ItemName") or "").strip(),
                "unit": (row.get("Unit") or "").strip().lower(),
                "product_type_id": ptid,
                "rate": _float(row.get("PRate")),
                "volume": _float(row.get("Volume")),
                "weight": _float(row.get("Weight")),
                "raw_ids": raw_ids,
                "raw_qtys": raw_qtys,
            }
    return items


def expand_components(rooms: list, tbl_item: dict) -> dict:
    """Expand component codes from extraction rooms into aggregated raw materials.

    For each furniture component:
      - Has raw_ids in tbl_item  -> BLOCK/FINISHED, expand into raw materials
      - No raw_ids                -> treat as raw material itself

    Returns:
        {ITEM_CODE_UPPER: {item_code, item_name, unit, rate, volume, weight, qty}}
    """
    materials: dict = {}

    def _add(code: str, qty: float, meta: dict) -> None:
        key = code.upper()
        if key not in materials:
            materials[key] = {**meta, "qty": 0.0}
        materials[key]["qty"] = round(materials[key]["qty"] + qty, 6)

    for room in rooms:
        for furniture in room.get("furniture", []):
            furn_qty = furniture.get("quantity", 1)
            for comp in furniture.get("components", []):
                comp_code: str = comp["code"]
                total_qty: float = furn_qty * comp["qty"]
                entry = tbl_item.get(comp_code.upper())

                if entry and entry.get("raw_ids"):
                    # BLOCK / FINISHED -> expand into constituent raw materials
                    raw_ids: list = entry["raw_ids"]
                    raw_qtys: list = entry["raw_qtys"]
                    for i, raw_id in enumerate(raw_ids):
                        rqty = raw_qtys[i] if i < len(raw_qtys) else 0.0
                        if rqty <= 0:
                            continue
                        expanded = total_qty * rqty
                        rm = tbl_item.get(raw_id.upper(), {})
                        _add(raw_id, expanded, {
                            "item_code": rm.get("item_code", raw_id),
                            "item_name": rm.get("item_name", raw_id),
                            "unit":      rm.get("unit", ""),
                            "rate":      rm.get("rate", 0),
                            "volume":    rm.get("volume", 0),
                            "weight":    rm.get("weight", 0),
                        })
                else:
                    # RAW or unknown -> keep as-is
                    e = entry or {}
                    _add(comp_code, total_qty, {
                        "item_code": comp_code,
                        "item_name": e.get("item_name") or comp.get("description", comp_code),
                        "unit":      e.get("unit") or comp.get("unit", ""),
                        "rate":      e.get("rate") or comp.get("unit_price", 0),
                        "volume":    e.get("volume") or comp.get("volume_per_unit", 0),
                        "weight":    e.get("weight") or comp.get("weight_per_unit", 0),
                    })

    return materials


def to_flat_list(materials: dict) -> list:
    """Sorted flat list of raw materials, A->Z by item_name.

    Returns:
        [{sl_no, item_code, item_name, unit, qty, rate, volume, weight}]
    """
    rows = sorted(materials.values(), key=lambda x: x["item_name"].upper())
    result = []
    for i, m in enumerate(rows, 1):
        qty = round(m["qty"], 4)
        result.append({
            "sl_no": i,
            "item_code": m["item_code"],
            "item_name": m["item_name"],
            "unit":      m["unit"],
            "qty":       qty,
            "rate":      round(m["rate"], 2) if m["rate"] else 0,
            "volume":    round(qty * m["volume"], 4) if m["volume"] else 0,
            "weight":    round(qty * m["weight"], 4) if m["weight"] else 0,
        })
    return result
