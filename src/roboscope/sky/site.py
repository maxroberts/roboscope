"""Observing site: where on Earth the telescope is.

Details:
    `Site` holds latitude (north +), longitude (east +), elevation and an optional name,
    in degrees and metres. `Sky` (sky.py) uses it to turn catalog coordinates into
    local alt/az.

    `Site.from_zip` looks up a postal code with pgeocode (GeoNames data). It needs
    network access the first time per country; pgeocode then caches the table under
    ~/pgeocode_data. A postal code gives the code area's centroid, typically within a
    few km of the real site. That's fine for pointing (1 km ≈ 0.01° of latitude) but
    not for precise timing work.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Site:
    """Observer location: latitude/longitude in degrees (north/east positive), elevation m."""
    lat_deg: float
    lon_deg: float
    elevation_m: float = 0.0
    name: str = ""

    def __post_init__(self):
        if not -90 <= self.lat_deg <= 90:
            raise ValueError(f"latitude out of range: {self.lat_deg}")
        if not -180 <= self.lon_deg <= 180:
            raise ValueError(f"longitude out of range: {self.lon_deg}")

    @classmethod
    def from_zip(cls, postal_code: str, country: str = "us",
                 elevation_m: float = 0.0) -> "Site":
        """Site at the centroid of a postal code (see module notes on accuracy/network).

        Raises:
            ValueError: unknown postal code.
        """
        import pgeocode

        row = pgeocode.Nominatim(country).query_postal_code(str(postal_code))
        if row is None or row.isna()["latitude"]:
            raise ValueError(f"unknown postal code {postal_code!r} ({country})")
        name = f"{row['place_name']}, {row.get('state_code', '')}".strip(", ")
        return cls(float(row["latitude"]), float(row["longitude"]), elevation_m, name)
