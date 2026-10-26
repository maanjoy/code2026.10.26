"""把抓回来的原始数据整理成可直接喂给应用的输入文件。

产出（都在 aeris/data/）：
  orders.json          两笔订单 + SKU 行项目 + 运单映射（数据驱动的唯一输入源）
  sku_catalog.json     698 条 SKU 全表，字段已数值化，按 SKU 建索引
  sku_order.json       本单涉及的 9 条 SKU 明细
  expected_totals.json 参考校验值（仅供测试比对，绝不能写进业务逻辑）

金额一律用「整数分」+ Decimal ROUND_HALF_UP，避免浮点与银行家舍入的 1 分误差。
"""
import json, pathlib, re
from decimal import Decimal, ROUND_HALF_UP

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

CENT = Decimal("0.01")


def money(x) -> Decimal:
    """Decimal -> 保留两位，四舍五入（HALF_UP）。"""
    return Decimal(x).quantize(CENT, rounding=ROUND_HALF_UP)


def num(s, default=None):
    """从 '68.0g' / '76.0mm' / '450528.0mm³' / '175.00' 里抽出数字。"""
    if s is None:
        return default
    m = re.search(r"-?\d+(?:\.\d+)?", str(s))
    return float(m.group()) if m else default


def normalize(row: dict) -> dict:
    return {
        "sku": row.get("SKU"),
        "product_name": (row.get("ProductName") or "").strip(),
        "description": (row.get("Description") or "").strip(),
        "rrp": float(money(row.get("RRP") or 0)),          # AUD，含 GST
        "size": row.get("Size"),
        "dosage_type": row.get("DosageType"),
        "product_type": row.get("ProductType"),
        "schedule": row.get("Schedule"),
        "tga_category": row.get("TGACategory"),
        "status": row.get("Status"),
        "barcode": row.get("Barcode"),
        "plant_species": row.get("PlantSpecies"),
        "spectrum": row.get("Spectrum"),
        "route": row.get("RouteOfAdministration"),
        # 物流属性：统一成基本单位
        "weight_g": num(row.get("weight")),                 # g
        "length_mm": num(row.get("length")),                # mm
        "width_mm": num(row.get("width")),                  # mm
        "height_mm": num(row.get("height")),                # mm
        "volume_mm3": num(row.get("volume")),               # mm³
        "volumetric_gross_weight_kg": num(row.get("Volumetric_GrossWeight")),  # kg
        "_raw": row,
    }


def main():
    # ---------- 1. SKU 全表 ----------
    full = json.loads((DATA / "product_list_full.json").read_text(encoding="utf-8"))
    catalog = {}
    for r in full["rows"]:
        n = normalize(r)
        if n["sku"]:
            catalog[n["sku"]] = n
    (DATA / "sku_catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ---------- 2. 本单 9 条 ----------
    order_rows = json.loads((DATA / "order_skus.json").read_text(encoding="utf-8"))["rows"]
    order_skus = {normalize(r)["sku"]: normalize(r) for r in order_rows}
    (DATA / "sku_order.json").write_text(
        json.dumps(order_skus, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ---------- 3. 订单（以 HTML v2 为准） ----------
    orders = {
        "origin": {
            "postcode": "2111",
            "suburb": "RYDE",       # 注意：AusPost testbed 里 RYDE 只认 2112，见 README §7
            "state": "NSW",
            "country": "AU",
        },
        "tracking": {
            "Track 1": {"number": "2FWZ50008569", "courier": "StarTrack / Australia Post"},
            "Track 2": {"number": "2FWZ50008645", "courier": "StarTrack / Australia Post"},
            "Track 3": {"number": "305506914", "courier": "TNT"},
        },
        "orders": [
            {
                "order_no": "PO-20251130-00072",
                "order_date": "30/11/25",
                "status": "Completed",
                "company": "Harbour Wellness Dispensary",
                "customer": "Alex Mercer",
                "phone": "0400 000 001",
                "email": "alex.mercer@example.com.au",
                "address": "Unit 4, 88 Sample Street, South Yarra VIC 3141",
                "suburb": "SOUTH YARRA", "state": "VIC", "postcode": "3141", "country": "AU",
                "items": [
                    {"sku": "TBAMET10",  "qty": 3, "tracking": "Track 1"},
                    {"sku": "TBAMET28",  "qty": 1, "tracking": "Track 1"},
                    {"sku": "TBOPAL28",  "qty": 1, "tracking": "Track 1"},
                    {"sku": "HARNIG",    "qty": 4, "tracking": "Track 1"},
                    {"sku": "LELCBD100", "qty": 6, "tracking": "Track 1"},
                ],
            },
            {
                "order_no": "PO-20251203-00046",
                "order_date": "03/12/25",
                "status": "In Transit",
                "company": "Riverside Botanical Dispensary",
                "customer": "Priya Raman",
                "phone": "0400 000 002",
                "email": "priya.raman@example.com.au",
                "address": "Suite 2, 15 Example Parade, Fitzroy VIC 3065",
                "suburb": "FITZROY", "state": "VIC", "postcode": "3065", "country": "AU",
                "items": [
                    {"sku": "AURPUR10",  "qty": 10, "tracking": "Track 2"},
                    {"sku": "HALGEO15",  "qty": 1,  "tracking": "Track 3"},
                    {"sku": "MCMW10",    "qty": 2,  "tracking": "Track 3"},
                    {"sku": "MCBO30",    "qty": 3,  "tracking": "Track 3"},
                ],
            },
        ],
    }
    (DATA / "orders.json").write_text(
        json.dumps(orders, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ---------- 4. 参考校验值（仅测试用） ----------
    out = {"_note": "仅供测试比对，禁止写进业务逻辑。金额单位：AUD，整数分参与运算。",
           "orders": []}
    for o in orders["orders"]:
        lines, sub_cents = [], 0
        for it in o["items"]:
            p = order_skus.get(it["sku"])
            if not p:
                lines.append({"sku": it["sku"], "error": "SKU not found"})
                continue
            rrp = Decimal(str(p["rrp"]))
            unit_ex = money(rrp / Decimal("1.1"))            # RRP 含 GST -> 除 1.1
            line_ex = money(unit_ex * it["qty"])
            sub_cents += int(line_ex * 100)
            lines.append({
                "sku": it["sku"], "qty": it["qty"],
                "rrp_incl_gst": float(rrp),
                "unit_price_ex_gst": float(unit_ex),
                "line_subtotal_ex_gst": float(line_ex),
            })
        sub = Decimal(sub_cents) / 100
        gst = money(sub * Decimal("0.1"))
        ship = Decimal("0.00")                                # 默认降级值
        out["orders"].append({
            "order_no": o["order_no"],
            "lines": lines,
            "subtotal_ex_gst": float(sub),
            "gst": float(gst),
            "shipment_fee": float(ship),
            "total": float(money(sub + gst + ship)),
        })
    (DATA / "expected_totals.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("sku_catalog.json :", len(catalog), "条")
    print("sku_order.json   :", len(order_skus), "条")
    print("orders.json      :", len(orders["orders"]), "笔订单")
    for o in out["orders"]:
        print(f'  {o["order_no"]}  subtotal={o["subtotal_ex_gst"]:.2f}  '
              f'gst={o["gst"]:.2f}  total={o["total"]:.2f}')


if __name__ == "__main__":
    main()
