from __future__ import annotations

import json
import math

from agents import function_tool


def json_result(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


@function_tool
def rectangular_part_weight_kg(
    material: str,
    length_mm: float,
    width_mm: float,
    thickness_mm: float,
    quantity: int = 1,
) -> str:
    """Calculate theoretical weight for a rectangular copper/aluminum/steel part."""
    material_key = material.strip().lower()
    density_map = {
        "copper": 8.93,
        "cu": 8.93,
        "铜": 8.93,
        "aluminum": 2.70,
        "aluminium": 2.70,
        "al": 2.70,
        "铝": 2.70,
        "steel": 7.85,
        "fe": 7.85,
        "钢": 7.85,
    }
    density = density_map.get(material_key)
    if density is None:
        raise ValueError("material must be copper/铜, aluminum/铝, or steel/钢")

    volume_cm3 = length_mm * width_mm * thickness_mm / 1000.0
    weight_each = volume_cm3 * density / 1000.0
    return json_result(
        {
            "density_g_cm3": density,
            "quantity": quantity,
            "weight_kg_each": round(weight_each, 6),
            "weight_kg_total": round(weight_each * quantity, 6),
            "formula": "length_mm * width_mm * thickness_mm / 1000 * density_g_cm3 / 1000",
        }
    )


@function_tool
def large_hex_bolt_weight_kg(
    nominal_diameter_mm: float,
    shank_length_mm: float,
    across_flats_mm: float,
    head_height_mm: float,
    density_g_cm3: float = 7.85,
) -> str:
    """Estimate large hex bolt theoretical weight from head and shank geometry."""
    head_area_mm2 = (3.0 * math.sqrt(3.0) / 2.0) * across_flats_mm**2
    head_volume_mm3 = head_area_mm2 * head_height_mm
    shank_volume_mm3 = math.pi * (nominal_diameter_mm / 2.0) ** 2 * shank_length_mm
    total_volume_cm3 = (head_volume_mm3 + shank_volume_mm3) / 1000.0
    weight_kg = total_volume_cm3 * density_g_cm3 / 1000.0
    return json_result(
        {
            "head_volume_mm3": round(head_volume_mm3, 3),
            "shank_volume_mm3": round(shank_volume_mm3, 3),
            "density_g_cm3": density_g_cm3,
            "weight_kg": round(weight_kg, 6),
            "note": "Simplified check formula. Prefer drawing-marked weight when the skill says it has priority.",
        }
    )


@function_tool
def simple_cost_breakdown(
    material_weight_kg: float,
    material_price_per_kg: float,
    process_fee: float = 0.0,
    management_rate: float = 0.05,
    profit_rate: float = 0.05,
    package_freight: float = 0.0,
    scrap_credit: float = 0.0,
) -> str:
    """Create a simple cost total from material, process, management, profit, freight, and scrap credit."""
    material_cost = material_weight_kg * material_price_per_kg
    management_fee = material_cost * management_rate
    profit = (process_fee + management_fee + package_freight) * profit_rate
    total = material_cost + process_fee + management_fee + profit + package_freight - scrap_credit
    return json_result(
        {
            "material_cost": round(material_cost, 4),
            "process_fee": round(process_fee, 4),
            "management_fee": round(management_fee, 4),
            "profit": round(profit, 4),
            "package_freight": round(package_freight, 4),
            "scrap_credit": round(scrap_credit, 4),
            "total": round(total, 4),
        }
    )
