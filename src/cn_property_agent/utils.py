from __future__ import annotations

import math
import unicodedata


def normalize_text(value: str | None) -> str | None:
    """Conservative identity normalization: Unicode normalization + whitespace folding."""
    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(normalized.split())


def haversine_distance_m(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    radius_m = 6_371_008.8
    phi1 = math.radians(latitude_a)
    phi2 = math.radians(latitude_b)
    delta_phi = math.radians(latitude_b - latitude_a)
    delta_lambda = math.radians(longitude_b - longitude_a)
    hav = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return radius_m * 2 * math.atan2(math.sqrt(hav), math.sqrt(1 - hav))
