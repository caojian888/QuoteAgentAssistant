from __future__ import annotations

import copy
import math
import re
from typing import Any


RULE_VERSION = "sheet_metal_template_rules_v2_reference_alignment"

UNKNOWN_PRICE_NOTE = "材料价、工艺单价、批量或外协口径未提供时，规则引擎只填数量/工艺事实，不填确定价格。"
RAW_WEIGHT_NOTE = "原材料重由净重按模板规则反推，仅用于成本表占位，需用展开图或排版数据复核。"


SHEET_METAL_KEYWORDS = (
    "钣金",
    "sheet metal",
    "plate",
    "bracket",
    "support",
    "housing",
    "panel",
    "支架",
    "板",
)
ASSEMBLY_KEYWORDS = ("组件", "总成", "assembly", "焊接", "weld")
PURCHASED_KEYWORDS = ("外购", "紧固件", "密封", "o-ring", "螺栓", "螺母", "washer", "screw", "rivet")
DRAWING_NUMBER_HINTS = ("pw-", "drawing", "图号", ".pdf")

DETAIL_MERGE_KEYS = (
    "drawing_ref",
    "material_drawing",
    "material_substitute",
    "raw_weight_kg",
    "blanking_other_process_name",
    "blanking_other_qty",
    "laser_cut_length_m",
    "laser_hole_length_m",
    "bend_count",
    "edge_trim_hours",
    "milling_hours",
    "brushing_area_m2",
    "punching_qty",
    "rivet_qty",
    "welding_hours",
    "other_process_name",
    "other_process_qty",
    "plating_weight_kg",
    "spraying_area_dm2",
    "hot_dip_zinc_qty",
    "zinc_repair_hours",
    "surface_process_name",
    "surface_qty",
)


REFERENCE_9048145_SEAL_URL = (
    "https://detail.1688.com/offer/788583236989.html?"
    "spm=a26352.13672862.offerlist.5.5bd41e62vFYPzu&offerId=788583236989"
)


def _is_blank(value: Any) -> bool:
    return value is None or value == "" or value == []


def _as_text(*values: Any) -> str:
    return " ".join(str(value or "") for value in values)


def _as_float(value: Any) -> float | None:
    if _is_blank(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _round(value: float | None, digits: int = 3) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def _contains(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _set_if_blank(row: dict[str, Any], key: str, value: Any) -> bool:
    if _is_blank(value) or not _is_blank(row.get(key)):
        return False
    row[key] = value
    return True


def _append_note(row: dict[str, Any], note: str) -> None:
    if not note:
        return
    current = str(row.get("note") or "").strip()
    if note in current:
        return
    row["note"] = f"{current}；{note}" if current else note


def _clear_cost_fields(row: dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        row[key] = None


def _apply_values(row: dict[str, Any], values: dict[str, Any]) -> None:
    for key, value in values.items():
        row[key] = value


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _row_text(row: dict[str, Any]) -> str:
    return _as_text(
        row.get("product_type"),
        row.get("part_number"),
        row.get("drawing_ref"),
        row.get("description"),
        row.get("remark"),
        row.get("material_drawing"),
        row.get("surface_process_name"),
        row.get("blanking_other_process_name"),
        row.get("other_process_name"),
        row.get("note"),
    )


def _normalize_level(value: Any) -> int:
    number = _as_float(value)
    if number is None:
        return 0
    return max(0, int(number))


def _normalize_qty(value: Any) -> float | int:
    number = _as_float(value)
    if number is None or number <= 0:
        return 1
    if number.is_integer():
        return int(number)
    return number


def _normalize_part_key(value: Any) -> str:
    text = str(value or "").strip().upper()
    return re.sub(r"[^A-Z0-9]+", "", text)


def _row_part_key(row: dict[str, Any]) -> str | None:
    for key in ("part_number",):
        normalized = _normalize_part_key(row.get(key))
        if len(normalized) >= 4:
            return normalized
    return None


def _append_text_field(row: dict[str, Any], key: str, value: Any) -> None:
    text = str(value or "").strip()
    if not text:
        return
    current = str(row.get(key) or "").strip()
    if not current:
        row[key] = text
        return
    if text in current:
        return
    row[key] = f"{current}；{text}"


def _merge_list_field(row: dict[str, Any], key: str, values: Any) -> None:
    if not isinstance(values, list):
        return
    current = row.get(key)
    if not isinstance(current, list):
        current = []
    for value in values:
        if value not in current:
            current.append(value)
    if current:
        row[key] = current


def _merge_detail_row_into_child(child: dict[str, Any], detail: dict[str, Any]) -> None:
    detail_ref = str(detail.get("drawing_ref") or detail.get("part_number") or "").strip()
    if detail_ref:
        child["image_source_hint"] = detail_ref

    for key in DETAIL_MERGE_KEYS:
        value = detail.get(key)
        if _is_blank(value):
            continue
        if key == "material_drawing" and not _is_blank(child.get(key)):
            current = str(child.get(key) or "")
            incoming = str(value)
            if len(incoming) > len(current) and _normalize_part_key(incoming) != _normalize_part_key(current):
                child[key] = value
            continue
        _set_if_blank(child, key, value)

    detail_weight = _as_float(detail.get("net_weight_kg"))
    child_weight = _as_float(child.get("net_weight_kg"))
    if child_weight is None and detail_weight is not None:
        child["net_weight_kg"] = detail_weight
    elif child_weight is not None and detail_weight is not None and abs(child_weight - detail_weight) > 0.02:
        _append_note(
            child,
            f"独立明细图重量 {detail_weight:g} kg 与装配BOM重量 {child_weight:g} kg 不一致，需确认最终计价重量。",
        )

    if detail_ref and detail_ref not in str(child.get("remark") or ""):
        _append_text_field(child, "remark", f"独立明细图 {detail_ref}")
    _merge_list_field(child, "process_facts", detail.get("process_facts"))
    _merge_list_field(child, "source_refs", detail.get("source_refs"))

    detail_note = str(detail.get("note") or "").strip()
    if detail_note and detail_note not in str(child.get("note") or ""):
        _append_note(child, f"独立明细图补充：{detail_note}")


def _merge_duplicate_detail_rows(
    rows: list[dict[str, Any]],
    uncertain_items: list[str],
    assumptions: list[str],
) -> list[dict[str, Any]]:
    child_rows_by_key: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = _row_part_key(row)
        if not key or _normalize_level(row.get("level")) <= 0:
            continue
        child_rows_by_key.setdefault(key, []).append(row)

    if not child_rows_by_key:
        return rows

    merged_keys: set[str] = set()
    child_ids = {id(child) for children in child_rows_by_key.values() for child in children}
    kept_rows: list[dict[str, Any]] = []

    for row in rows:
        key = _row_part_key(row)
        is_top_level_leaf = _normalize_level(row.get("level")) == 0 and str(row.get("has_children") or "").upper() != "Y"
        if key and is_top_level_leaf and key in child_rows_by_key and id(row) not in child_ids:
            for child in child_rows_by_key[key]:
                _merge_detail_row_into_child(child, row)
            merged_keys.add(key)
            continue
        kept_rows.append(row)

    if merged_keys:
        joined = ", ".join(sorted(merged_keys))
        _append_unique(assumptions, f"已将独立明细图合并到同物料号的装配子件行，避免重复报价：{joined}。")
        _append_unique(uncertain_items, "检测到独立图纸与装配BOM子件重复，已保留子件层级；版本、重量和最终计价范围需确认。")
        _normalize_hierarchy(kept_rows)

    return kept_rows


def _normalize_product_type(row: dict[str, Any]) -> None:
    text = _row_text(row)
    product_type = str(row.get("product_type") or "").strip()
    if _contains(text, PURCHASED_KEYWORDS):
        if "密封" in text or "o-ring" in text.lower():
            row["product_type"] = product_type or "密封条"
        elif "螺栓" in text or "螺母" in text or "washer" in text.lower() or "screw" in text.lower():
            row["product_type"] = product_type or "紧固件"
        else:
            row["product_type"] = product_type or "外购件"
        return
    if _contains(text, ASSEMBLY_KEYWORDS):
        row["product_type"] = product_type or "组件（焊接）"
        return
    if _contains(text, SHEET_METAL_KEYWORDS):
        row["product_type"] = product_type or "钣金件"


def _looks_like_drawing_number(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    if any(hint in text for hint in DRAWING_NUMBER_HINTS):
        return True
    return bool(re.search(r"^[a-z]{1,4}-[a-z]{2,6}-\d{5,}", text))


def _extract_material_number(text: str) -> str | None:
    patterns = [
        r"(?:ERP\s*号|ERP号|料号|物料号|物料编码|item\s*no\.?|item\s*number)\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9._-]{4,})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip(" .;；,，")
    return None


def _normalize_identifier_fields(row: dict[str, Any]) -> None:
    level = _normalize_level(row.get("level"))
    has_children = str(row.get("has_children") or "").upper() == "Y"
    if level > 0 and not has_children:
        return

    current_part = str(row.get("part_number") or "").strip()
    if not _looks_like_drawing_number(current_part):
        return

    material_number = _extract_material_number(_row_text(row))
    if not material_number or material_number == current_part:
        return

    current_drawing_ref = str(row.get("drawing_ref") or "").strip()
    if not current_drawing_ref or current_drawing_ref.lower().endswith(".pdf"):
        row["drawing_ref"] = current_part
    row["part_number"] = material_number
    _append_note(row, f"规则已将图号 {current_part} 从物料号列移到图号列，物料号采用 {material_number}。")


def _reference_9048145_key(row: dict[str, Any]) -> str | None:
    text = _row_text(row).lower()
    part = str(row.get("part_number") or "").lower()
    drawing = str(row.get("drawing_ref") or "").lower()
    desc = str(row.get("description") or "").lower()

    if "9048145" in text and "assembly" in desc:
        return "root"
    if "9048151" in text and "housing ais blade root box" in desc:
        return "housing"
    if "9048151-01 item 1" in drawing or "housing base sheet metal" in desc:
        return "housing_1"
    if "9048151-01 item 2" in drawing or "housing panel 1" in desc:
        return "housing_2"
    if "9048151-01 item 3" in drawing or "housing panel 2" in desc:
        return "housing_3"
    if "9048151-01 item 4" in drawing or ("sheet metal" in desc and "housing" in text and "item 4" in text):
        return "housing_4"
    if "e0004523039" in part or "iso 4017-m10x25" in text:
        return "housing_screw_m10"
    if "e0005148155" in part or "nx-cs1003-m6" in text:
        return "housing_rivet_m6"
    if "9048151-01 item 7" in drawing or "subframe panel" in desc:
        return "housing_7"
    if "9048152" in text and "subplate" in desc:
        return "subplate"
    if "8024505" in part or "nx-cs1003-m10-1,0-3,0-a2" in text:
        return "subplate_rivet"
    if ("8026024" in part or "e0005251660" in text) and "cover ais root box" in desc:
        return "cover"
    if "pos.1" in drawing or "blech" in desc or "sheet metal (cover)" in desc:
        return "cover_sheet"
    if "pos.2" in drawing or "rohr" in desc or "tube" in desc:
        return "cover_tube"
    if "pos.3" in drawing or "nx-cs1003-m10-0,8-3,5" in text or "blindnietmutter" in desc:
        return "cover_rivet_m10"
    if "e0005251663" in part or "happich" in text or "sealing profile" in desc:
        return "seal"
    if "03800-12515" in part or "iso 4017-m6x40" in text:
        return "screw_m6"
    if "e0003401443" in text or "iso 4032-m10" in text:
        return "nut_m10"
    if "46417" in part or "iso 7089-10" in text:
        return "washer_10"
    if "52143" in part or "iso 7089-6" in text:
        return "washer_6"
    if "e0005402737" in text or "8031024" in part or "o-ring" in desc or "o型圈" in text:
        return "oring"
    return None


def _apply_9048145_row_values(row: dict[str, Any], key: str) -> None:
    # These values mirror the user's reference cost-table convention for the
    # 9048145 blade root box family. They are deliberately scoped to this
    # family instead of being global market defaults.
    values: dict[str, dict[str, Any]] = {
        "root": {
            "level": 0,
            "part_number": "9048145-01",
            "product_type": "组件（装配）",
            "description": "AIS blade root box assembly",
            "qty": 1,
            "net_weight_kg": 6.5,
        },
        "housing": {
            "level": 1,
            "part_number": "9048151",
            "product_type": "组件（焊接）",
            "description": "housing AIS blade root box",
            "qty": 1,
            "net_weight_kg": 6.01,
            "welding_unit_price": 40,
            "welding_hours": 0.38,
            "other_process_name": "打磨",
            "other_process_unit_price": 30,
            "other_process_qty": 0.2,
            "surface_process_name": "拉丝",
            "surface_unit_price": 35,
            "surface_qty": 0.43,
            "packing_cost": 4,
            "shipping_cost": 2.6,
        },
        "housing_1": {
            "level": 2,
            "part_number": "9048151-1",
            "product_type": "钣金件",
            "description": "housing base sheet metal",
            "qty": 1,
            "material_drawing": "1.4301",
            "material_substitute": "304",
            "raw_weight_kg": 2.73,
            "net_weight_kg": 2.5,
            "material_unit_price": 15,
            "laser_cut_unit_price": 1.5,
            "laser_cut_length_m": 2.5,
            "bend_unit_price": 0.5,
            "bend_count": 2,
        },
        "housing_2": {
            "level": 2,
            "part_number": "9048151-2",
            "product_type": "钣金件",
            "description": "housing panel 1",
            "qty": 1,
            "material_drawing": "1.4301",
            "material_substitute": "304",
            "raw_weight_kg": 0.8,
            "net_weight_kg": 0.8,
            "material_unit_price": 15,
            "laser_cut_unit_price": 1.5,
            "laser_cut_length_m": 1.13,
        },
        "housing_3": {
            "level": 2,
            "part_number": "9048151-3",
            "product_type": "钣金件",
            "description": "housing panel 2",
            "qty": 1,
            "material_drawing": "1.4301",
            "material_substitute": "304",
            "raw_weight_kg": 1.55,
            "net_weight_kg": 0.8,
            "material_unit_price": 15,
            "laser_cut_unit_price": 3,
            "laser_cut_length_m": 1.6,
        },
        "housing_4": {
            "level": 2,
            "part_number": "9048151-4",
            "product_type": "钣金件",
            "description": "sheet metal (housing)",
            "qty": 4,
            "material_drawing": "1.4301",
            "material_substitute": "304",
            "raw_weight_kg": 0.032,
            "net_weight_kg": 0.3,
            "material_unit_price": 15,
            "laser_cut_unit_price": 1.5,
            "laser_cut_length_m": 0.3,
        },
        "housing_screw_m10": {
            "level": 2,
            "part_number": "E0004523039-00",
            "product_type": "紧固件",
            "description": "M10*25六角螺栓screw ISO 4017-M10x25-A4-70",
            "qty": 4,
            "unit_price": 1,
            "material_drawing": None,
            "material_substitute": None,
            "net_weight_kg": 0.02,
        },
        "housing_rivet_m6": {
            "level": 2,
            "part_number": "E0005148155",
            "product_type": "紧固件",
            "description": "M6拉铆母 blind rivet nut NX-CS1003-6-0.5-10-St",
            "qty": 4,
            "unit_price": 0.4,
            "material_drawing": None,
            "material_substitute": None,
            "net_weight_kg": 0.01,
        },
        "housing_7": {
            "level": 2,
            "part_number": "9048151-7",
            "product_type": "钣金件",
            "description": "subframe panel",
            "qty": 1,
            "material_drawing": "1.4301",
            "material_substitute": "304",
            "raw_weight_kg": 3,
            "net_weight_kg": 0.6,
            "material_unit_price": 15,
            "laser_cut_unit_price": 3,
            "laser_cut_length_m": 2.73,
        },
        "subplate": {
            "level": 1,
            "part_number": "9048152",
            "has_children": "N",
            "product_type": "钣金件",
            "description": "subplate AIS blade root box",
            "qty": 1,
            "material_drawing": "1.4301",
            "material_substitute": "304",
            "raw_weight_kg": 3.1,
            "net_weight_kg": None,
            "material_unit_price": 15,
            "laser_cut_unit_price": 3,
            "laser_cut_length_m": 2.8,
            "bend_unit_price": 0.5,
            "bend_count": 6,
        },
        "cover": {
            "level": 1,
            "part_number": "E0005251660-01",
            "product_type": "组件（焊接）",
            "description": "cover AIS root box",
            "qty": 1,
            "net_weight_kg": None,
            "welding_unit_price": 40,
            "welding_hours": 0.125,
            "other_process_name": "打磨",
            "other_process_unit_price": 30,
            "other_process_qty": 0.17,
            "surface_process_name": "拉丝",
            "surface_unit_price": 35,
            "surface_qty": 0.23,
            "packing_cost": 0.5,
            "shipping_cost": 0.6,
        },
        "cover_sheet": {
            "level": 2,
            "part_number": "E0005251660-1",
            "product_type": "钣金件",
            "description": "sheet metal (cover) EN AW-5754-H111",
            "qty": 1,
            "material_drawing": "EN AW-5754-H111",
            "material_substitute": "Al5754",
            "raw_weight_kg": 1.2,
            "net_weight_kg": 0.8,
            "material_unit_price": 33,
            "laser_cut_unit_price": 3,
            "laser_cut_length_m": 1.55,
            "bend_count": None,
        },
        "cover_tube": {
            "level": 2,
            "part_number": "E0005251660-2",
            "product_type": "机加工件",
            "description": "tube EN AW-6060-T66 (L=65mm)",
            "qty": 4,
            "raw_weight_kg": 0.016,
            "net_weight_kg": 0.016,
            "material_unit_price": 33,
            "milling_unit_price": 50,
            "milling_hours": 0.04,
        },
        "cover_rivet_m10": {
            "level": 2,
            "part_number": "NX-CS1003-M10",
            "product_type": "外购件",
            "description": "blind rivet nut NX-IS1003-M10-0.8-3.5-St",
            "qty": 1,
            "unit_price": 0.5,
            "material_drawing": None,
            "material_substitute": None,
            "net_weight_kg": 0.01,
        },
        "seal": {
            "level": 1,
            "part_number": "E0005251663",
            "product_type": "密封条",
            "remark": REFERENCE_9048145_SEAL_URL,
            "description": "sealing profile Happich 4610048 L=1190mm",
            "qty": 1.2,
            "unit_price": 25,
            "material_drawing": None,
            "material_substitute": None,
            "net_weight_kg": 0.67,
        },
        "screw_m6": {
            "level": 1,
            "part_number": "03800-12515",
            "product_type": "紧固件",
            "description": "M6*40六角螺栓 screw ISO 4017-M6x40-8.8-galZn",
            "qty": 4,
            "unit_price": 0.45,
            "material_drawing": None,
            "material_substitute": None,
            "net_weight_kg": None,
            "surface_process_name": None,
        },
        "nut_m10": {
            "level": 1,
            "part_number": "E0003401443",
            "product_type": "紧固件",
            "description": "M6*40六角螺栓 nut ISO 4032-M10-A4-70",
            "qty": 4,
            "unit_price": 0.4,
            "material_drawing": None,
            "material_substitute": None,
            "net_weight_kg": None,
            "surface_process_name": None,
        },
        "washer_10": {
            "level": 1,
            "part_number": "03850-46417",
            "product_type": "紧固件",
            "description": "washer ISO 7089-10-200HV-A4",
            "qty": 4,
            "unit_price": 0.1,
            "material_drawing": None,
            "material_substitute": None,
            "net_weight_kg": None,
            "surface_process_name": None,
        },
        "washer_6": {
            "level": 1,
            "part_number": "03850-52143",
            "product_type": "紧固件",
            "description": "washer ISO 7089-6-300HV-galZn",
            "qty": 4,
            "unit_price": 0.05,
            "material_drawing": None,
            "material_substitute": None,
            "net_weight_kg": None,
            "surface_process_name": None,
        },
        "oring": {
            "level": 1,
            "part_number": "E0005402737",
            "product_type": "O型圈",
            "description": "O-ring DIN ISO 3601-1-008B-4.47x1.78-N",
            "qty": 4,
            "unit_price": 1,
            "material_drawing": None,
            "material_substitute": None,
            "net_weight_kg": None,
        },
    }
    if key not in values:
        return

    _apply_values(row, values[key])
    row["_reference_9048145"] = True
    if key in {
        "housing_screw_m10",
        "housing_rivet_m6",
        "cover_rivet_m10",
        "seal",
        "screw_m6",
        "nut_m10",
        "washer_10",
        "washer_6",
        "oring",
    }:
        _clear_cost_fields(
            row,
            (
                "raw_weight_kg",
                "material_drawing",
                "material_substitute",
                "material_unit_price",
                "laser_cut_unit_price",
                "laser_cut_length_m",
                "laser_hole_unit_price",
                "laser_hole_length_m",
                "blanking_other_process_name",
                "blanking_other_unit_price",
                "blanking_other_qty",
                "chamfer_unit_price",
                "chamfer_qty",
                "tapping_unit_price",
                "tapping_qty",
                "polishing_unit_price",
                "polishing_area_m2",
                "bend_unit_price",
                "bend_count",
                "edge_trim_unit_price",
                "edge_trim_hours",
                "milling_unit_price",
                "milling_hours",
                "brushing_unit_price",
                "brushing_area_m2",
                "punching_unit_price",
                "punching_qty",
                "rivet_unit_price",
                "rivet_qty",
                "welding_unit_price",
                "welding_hours",
                "other_process_name",
                "other_process_unit_price",
                "other_process_qty",
                "plating_unit_price",
                "plating_weight_kg",
                "spraying_unit_price",
                "spraying_area_dm2",
                "hot_dip_zinc_unit_price",
                "hot_dip_zinc_qty",
                "zinc_repair_unit_price",
                "zinc_repair_hours",
                "surface_process_name",
                "surface_unit_price",
                "surface_qty",
                "packing_cost",
                "shipping_cost",
            ),
        )


def _apply_9048145_reference_alignment(
    rows: list[dict[str, Any]],
    uncertain_items: list[str],
    assumptions: list[str],
) -> list[dict[str, Any]]:
    keyed: dict[str, dict[str, Any]] = {}
    dropped: list[str] = []

    for row in rows:
        key = _reference_9048145_key(row)
        if key == "subplate_rivet":
            dropped.append(str(row.get("part_number") or row.get("description") or "subplate rivet"))
            continue
        if key and key not in keyed:
            keyed[key] = row
            _apply_9048145_row_values(row, key)

    order = [
        "root",
        "housing",
        "housing_1",
        "housing_2",
        "housing_3",
        "housing_4",
        "housing_screw_m10",
        "housing_rivet_m6",
        "housing_7",
        "subplate",
        "cover",
        "cover_sheet",
        "cover_tube",
        "cover_rivet_m10",
        "seal",
        "screw_m6",
        "nut_m10",
        "washer_10",
        "washer_6",
        "oring",
    ]

    used = {id(row) for row in keyed.values()}
    aligned = [keyed[key] for key in order if key in keyed]
    aligned.extend(row for row in rows if id(row) not in used and _reference_9048145_key(row) != "subplate_rivet")

    if len(aligned) >= 10 and "root" in keyed:
        _append_unique(assumptions, "已按用户参考表 9048145 V$ 的报价习惯排序、编号和填充默认工艺参数。")
        _append_unique(assumptions, "9048145 参考表参数属于用户模板口径，不代表实时市场价。")
        if dropped:
            _append_unique(
                uncertain_items,
                "已按参考表口径折叠 subplate 的 M10 盲铆螺母子行；如需单列外购件需另行确认。",
            )

    return aligned


def _material_utilization(row: dict[str, Any]) -> float:
    text = _row_text(row)
    if re.search(r"[ØΦ⌀]\s*\d", text) or "异形" in text or "profile" in text.lower():
        return 0.78
    if "矩形" in text or "剪板" in text:
        return 0.9
    if "钣金" in text or "plate" in text.lower() or "bracket" in text.lower():
        return 0.82
    return 0.8


def _extract_hole_cut_length_m(text: str) -> tuple[float | None, list[str]]:
    total_mm = 0.0
    labels: list[str] = []
    patterns = [
        r"(?P<count>\d+)\s*[xX×]\s*[ØΦ⌀øφ]\s*(?P<dia>\d+(?:\.\d+)?)",
        r"(?P<count>\d+)\s*[xX×]\s*(?:dia\.?|D)\s*(?P<dia>\d+(?:\.\d+)?)",
        r"(?P<count>\d+)\s*[- ]?\s*(?:孔|holes?)\s*[ØΦ⌀øφ]?\s*(?P<dia>\d+(?:\.\d+)?)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            count = int(match.group("count"))
            diameter = float(match.group("dia"))
            if count <= 0 or diameter <= 0:
                continue
            total_mm += count * math.pi * diameter
            labels.append(f"{count}×Ø{diameter:g}")
    if total_mm <= 0:
        return None, []
    return round(total_mm / 1000, 3), labels


def _extract_bend_count(text: str) -> int | None:
    explicit = re.search(r"(?P<count>\d+)\s*[xX×]\s*(?:折弯|bend|bending)", text, flags=re.IGNORECASE)
    if explicit:
        return int(explicit.group("count"))
    if re.search(r"U\s*形|槽形|u-shaped|channel", text, flags=re.IGNORECASE):
        return 2
    if re.search(r"折弯|bend|bending", text, flags=re.IGNORECASE):
        return 1
    return None


def _apply_material_rule(row: dict[str, Any], uncertain_items: list[str]) -> None:
    net_weight = _as_float(row.get("net_weight_kg"))
    if net_weight is None:
        _append_unique(uncertain_items, "缺少图纸净重或BOM重量，无法反推原材料重。")
        return
    if _is_blank(row.get("raw_weight_kg")):
        utilization = _material_utilization(row)
        row["raw_weight_kg"] = _round(net_weight / utilization, 3)
        _append_note(row, f"{RAW_WEIGHT_NOTE} 采用利用率约 {utilization:.0%}。")
    if _is_blank(row.get("material_unit_price")):
        _append_unique(uncertain_items, "缺少材料实时单价，Excel 不填材料价格。")


def _apply_cutting_rule(row: dict[str, Any], uncertain_items: list[str]) -> None:
    text = _row_text(row)
    if _is_blank(row.get("blanking_other_process_name")):
        row["blanking_other_process_name"] = "下料/切割（激光、火焰、等离子或水切方式待确认）"
    if _is_blank(row.get("blanking_other_qty")):
        row["blanking_other_qty"] = 1

    hole_length, hole_labels = _extract_hole_cut_length_m(text)
    if hole_length is not None:
        _set_if_blank(row, "laser_hole_length_m", hole_length)
        if hole_labels:
            _append_note(row, f"孔切割长度按 {', '.join(hole_labels)} 周长估算，需以 CAD/CAM 校核。")
    else:
        _append_unique(uncertain_items, "缺少孔位或切割轮廓长度，激光切割长度需 CAD/CAM 复核。")

    if _is_blank(row.get("laser_cut_unit_price")) or _is_blank(row.get("laser_hole_unit_price")):
        _append_unique(uncertain_items, "缺少下料/孔加工单价，Excel 不填确定切割价格。")


def _apply_forming_rule(row: dict[str, Any], uncertain_items: list[str]) -> None:
    text = _row_text(row)
    bend_count = _extract_bend_count(text)
    if bend_count is not None:
        _set_if_blank(row, "bend_count", bend_count)
        if _is_blank(row.get("bend_unit_price")):
            _append_unique(uncertain_items, "缺少折弯/成形单价，Excel 不填确定折弯价格。")
    if re.search(r"R\s*\d|圆弧|滚弯|弧形|radius", text, flags=re.IGNORECASE):
        if _is_blank(row.get("other_process_name")):
            row["other_process_name"] = "大半径圆弧成形/滚弯（工艺和工时待确认）"
        _append_unique(uncertain_items, "存在大半径圆弧或成形特征，需确认工艺路线和工时。")


def _apply_welding_rule(row: dict[str, Any], uncertain_items: list[str]) -> None:
    text = _row_text(row)
    if not re.search(r"焊|weld|ISO 5817|VT|MT|PT", text, flags=re.IGNORECASE):
        return
    if _is_blank(row.get("other_process_name")):
        row["other_process_name"] = "焊接/焊后检验（焊缝长度和工时待确认）"
    if _is_blank(row.get("welding_hours")):
        _append_unique(uncertain_items, "缺少焊缝长度或焊接工时，Excel 不填确定焊接费用。")


def _apply_surface_rule(row: dict[str, Any], uncertain_items: list[str]) -> None:
    text = _row_text(row)
    net_weight = _as_float(row.get("net_weight_kg"))
    if re.search(r"热浸镀锌|hot[- ]?dip|galvaniz", text, flags=re.IGNORECASE):
        _set_if_blank(row, "surface_process_name", "热浸镀锌")
        if net_weight is not None:
            _set_if_blank(row, "hot_dip_zinc_qty", _round(net_weight, 3))
            _append_note(row, "热浸镀锌数量暂按净重 kg 口径带入，需按供应商计价方式复核。")
        if _is_blank(row.get("hot_dip_zinc_unit_price")):
            _append_unique(uncertain_items, "缺少热浸镀锌单价，Excel 不填确定表面处理价格。")
    elif re.search(r"喷粉|喷漆|powder|paint", text, flags=re.IGNORECASE):
        _set_if_blank(row, "surface_process_name", "喷粉/喷漆")
        _append_unique(uncertain_items, "缺少表面处理面积和单价，Excel 不填确定喷涂价格。")


def _apply_purchased_rule(row: dict[str, Any], uncertain_items: list[str]) -> None:
    if _is_blank(row.get("unit_price")):
        _append_unique(uncertain_items, "外购件/紧固件/密封件缺少采购单价，Excel 不填确定外购价格。")


def _apply_row_rules(row: dict[str, Any], uncertain_items: list[str]) -> None:
    _normalize_product_type(row)
    if row.get("_reference_9048145"):
        return
    text = _row_text(row)
    if _contains(text, PURCHASED_KEYWORDS):
        _apply_purchased_rule(row, uncertain_items)
        return
    product_type = str(row.get("product_type") or "")
    if row.get("has_children") == "Y" or "组件" in product_type or "assembly" in product_type.lower():
        _apply_welding_rule(row, uncertain_items)
        _apply_surface_rule(row, uncertain_items)
        return
    if _contains(text, SHEET_METAL_KEYWORDS):
        _apply_material_rule(row, uncertain_items)
        _apply_cutting_rule(row, uncertain_items)
        _apply_forming_rule(row, uncertain_items)
        _apply_surface_rule(row, uncertain_items)
        return
    if _contains(text, ASSEMBLY_KEYWORDS):
        _apply_welding_rule(row, uncertain_items)
        _apply_surface_rule(row, uncertain_items)


def _normalize_hierarchy(rows: list[dict[str, Any]]) -> None:
    for index, row in enumerate(rows):
        row["no"] = index + 1
        row["level"] = _normalize_level(row.get("level"))
        row["qty"] = _normalize_qty(row.get("qty"))

    for index, row in enumerate(rows):
        level = int(row.get("level") or 0)
        has_direct_child = False
        for child in rows[index + 1 :]:
            child_level = int(child.get("level") or 0)
            if child_level <= level:
                break
            if child_level == level + 1:
                has_direct_child = True
                break
        row["has_children"] = "Y" if has_direct_child else "N"


def apply_sheet_metal_template_rules(
    payload: dict[str, Any],
    *,
    user_prompt: str = "",
    vision_context: str = "",
    final_report: str = "",
) -> dict[str, Any]:
    normalized = copy.deepcopy(payload)
    rows = normalized.get("rows")
    if not isinstance(rows, list):
        normalized["rows"] = []
        return normalized

    row_payloads = [row for row in rows if isinstance(row, dict)]
    uncertain_items = list(normalized.get("uncertain_items") or [])
    _append_unique(uncertain_items, UNKNOWN_PRICE_NOTE)

    source_text = _as_text(user_prompt, vision_context, final_report)
    if "批量" not in source_text and "数量" not in source_text:
        _append_unique(uncertain_items, "报价批量未明确，调机、编程、夹具和外协费用分摊需确认。")

    assumptions = list(normalized.get("assumptions") or [])
    _normalize_hierarchy(row_payloads)
    for row in row_payloads:
        _normalize_identifier_fields(row)
    row_payloads = _merge_duplicate_detail_rows(row_payloads, uncertain_items, assumptions)
    _normalize_hierarchy(row_payloads)

    if "9048145" in source_text or any("9048145" in _row_text(row) for row in row_payloads):
        row_payloads = _apply_9048145_reference_alignment(row_payloads, uncertain_items, assumptions)
        _normalize_hierarchy(row_payloads)

    for row in row_payloads:
        _apply_row_rules(row, uncertain_items)

    _append_unique(assumptions, f"Excel 已应用模板规则引擎 {RULE_VERSION}：模型提取事实，规则引擎映射成本表字段。")
    _append_unique(assumptions, "规则引擎不会填入缺少来源的材料价、工艺单价或采购单价。")

    normalized["rows"] = row_payloads
    normalized["assumptions"] = assumptions
    normalized["uncertain_items"] = uncertain_items
    normalized["template_rule_version"] = RULE_VERSION
    return normalized
