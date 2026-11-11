"""Catálogo de servicios/comercios internos visitables por Villa (modo VISITOR).

Cada Villa tiene su propio catálogo (no todas tienen los mismos negocios).
NUNCA se exponen teléfonos del Administrador ni de residentes en este catálogo.
"""

from pydantic import BaseModel, Field

from app.models.lot import normalize_villa_id

# SHADOW FEATURE: catálogo de servicios / minimapa en modo VISITOR.
# Desactivado hasta validar con cada Administrador qué negocios existen realmente.
VISITOR_SERVICES_ENABLED = False


class VillaService(BaseModel):
    code: str = Field(..., examples=["PANADERIA", "BROSTERIA_MARCOS_HUAMAN"])
    name: str = Field(..., examples=["Panadería", "Brostería Marcos Huamán"])
    category: str = Field(default="COMERCIO", examples=["COMERCIO", "AMENIDAD"])


_COMMON: list[VillaService] = [
    VillaService(code="PANADERIA", name="Panadería"),
    VillaService(code="BOTICA", name="Botica"),
    VillaService(code="PARQUE", name="Parque", category="AMENIDAD"),
    VillaService(code="CANCHITA", name="Canchita", category="AMENIDAD"),
]

# Negocios custom por Villa (se ampliará luego a tabla administrable por cada Admin)
_CUSTOM_BY_VILLA: dict[str, list[VillaService]] = {
    "VC3": [
        VillaService(code="MARCIANOS_CLARITA", name="Marcianos Clarita"),
        VillaService(code="BROSTERIA_MARCOS_HUAMAN", name="Brostería Marcos Huamán"),
    ],
}


def list_villa_services(villa_id: str) -> list[VillaService]:
    norm = normalize_villa_id(villa_id)
    return [*_COMMON, *_CUSTOM_BY_VILLA.get(norm, [])]


def find_villa_service(villa_id: str, code: str) -> VillaService | None:
    norm_code = code.strip().upper().replace(" ", "_")
    return next((s for s in list_villa_services(villa_id) if s.code == norm_code), None)
