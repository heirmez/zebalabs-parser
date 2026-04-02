"""Procurement logic: purchase quantities from aggregated raw materials + stock.

Matches Zebalabs ProjectBiz Excel column layout:
  SLNo | ItemName | Unit | Qty | AdnlQty | TotQty | Rate |
  Qty on Store | TotalQty W/o Reservation |
  PurchaseIntendQty | IssuedQty | QtyBfrRoundOff

Formulas (from ProjectBiz):
  TotQty           = Qty + AdnlQty
  Available        = Qty_on_store - Reserved   (0 if no stock data)
  QtyBfrRoundOff   = max(0, TotQty - Available)
  PurchaseIntendQty = ceil(QtyBfrRoundOff)      -- always round up
"""

import math


def compute_procurement(
    flat_materials: list,
    stock_data: dict | None = None,
    adnl_qty_pct: float = 0.0,
) -> list:
    """Calculate procurement columns for each raw material.

    Args:
        flat_materials: list from material_expansion.to_flat_list()
        stock_data: {ITEM_CODE_UPPER: {"qty_on_store": float, "reserved": float}}
                    Pass None when no stock data is available (PurchaseQty = TotQty)
        adnl_qty_pct: wastage / safety-stock percentage (0.0 = none, 0.05 = 5%)

    Returns:
        List of dicts with all ProjectBiz BOM columns.
    """
    if stock_data is None:
        stock_data = {}

    result = []
    for item in flat_materials:
        code = item["item_code"].upper()
        qty: float = item["qty"]

        # Additional quantity (wastage buffer)
        adnl_qty = round(qty * adnl_qty_pct, 4) if adnl_qty_pct > 0 else 0.0
        tot_qty  = round(qty + adnl_qty, 4)

        # Stock on hand
        stock = stock_data.get(code, {})
        qty_on_store: float = float(stock.get("qty_on_store", 0))
        reserved:     float = float(stock.get("reserved", 0))
        available:    float = max(0.0, qty_on_store - reserved)

        # Purchase calculation — always round up (ceiling)
        qty_bfr_round   = max(0.0, round(tot_qty - available, 4))
        purchase_intend = math.ceil(qty_bfr_round * 100) / 100  # ceil to 2 dp

        result.append({
            "sl_no":                    item["sl_no"],
            "item_code":                item["item_code"],
            "item_name":                item["item_name"],
            "unit":                     item["unit"],
            "qty":                      qty,
            "adnl_qty":                 adnl_qty,
            "tot_qty":                  tot_qty,
            "rate":                     item.get("rate", 0),
            "qty_on_store":             qty_on_store,
            "total_qty_wo_reservation": available,
            "purchase_intend_qty":      purchase_intend,
            "issued_qty":               0.0,    # filled by stores team
            "qty_bfr_round_off":        qty_bfr_round,
            "volume":                   item.get("volume", 0),
            "weight":                   item.get("weight", 0),
        })

    return result
