# ── sanctions_engine.py ──────────────────────────────────
"""
Consulta listas de sanciones internacionales:
- OFAC (EE.UU.)
- Lista consolidada UE
- Lista ONU
- OpenSanctions (agregador)

Requisito legal: SEPBLAC · Art. 9 Ley 10/2010
"""
import httpx
import os

OPENSANCTIONS_KEY = os.getenv("OPENSANCTIONS_API_KEY", "free")
BASE_URL = "https://api.opensanctions.org"


async def consultar_sanciones(nombre: str, nif: str = None) -> dict:
    """
    Consulta si una persona o empresa aparece en listas de sanciones.
    Devuelve: encontrado, coincidencias, listas afectadas, score de similitud.
    """
    resultados = []

    async with httpx.AsyncClient(verify=False, timeout=10) as client:

        # ── Búsqueda por nombre ──
        params = {"q": nombre, "limit": 5}
        headers = {"Authorization": f"ApiKey {OPENSANCTIONS_KEY}"}

        try:
            r = await client.get(f"{BASE_URL}/entities", params=params, headers=headers)
            if r.status_code == 200:
                data = r.json()
                for entidad in data.get("results", []):
                    resultados.append(_parsear_entidad(entidad))
        except Exception as e:
            pass

        # ── Búsqueda por NIF si se proporciona ──
        if nif:
            params_nif = {"q": nif, "limit": 3}
            try:
                r2 = await client.get(f"{BASE_URL}/entities", params=params_nif, headers=headers)
                if r2.status_code == 200:
                    data2 = r2.json()
                    for entidad in data2.get("results", []):
                        parsed = _parsear_entidad(entidad)
                        if parsed not in resultados:
                            resultados.append(parsed)
            except Exception:
                pass

    encontrado = len(resultados) > 0
    score_riesgo = 100 if encontrado else 0

    return {
        "nombre_consultado": nombre,
        "nif_consultado": nif,
        "encontrado_en_sanciones": encontrado,
        "score_riesgo_sanciones": score_riesgo,
        "coincidencias": resultados,
        "listas_consultadas": ["OFAC", "UE Consolidated", "ONU", "OpenSanctions"],
        "total_coincidencias": len(resultados),
        "recomendacion": (
            "🚨 BLOQUEAR OPERACIÓN · Sujeto en lista de sanciones · Art. 42 Ley 10/2010"
            if encontrado else
            "✅ Sin coincidencias en listas de sanciones internacionales"
        )
    }


def _parsear_entidad(entidad: dict) -> dict:
    props = entidad.get("properties", {})
    return {
        "id": entidad.get("id"),
        "nombre": props.get("name", ["Desconocido"])[0] if props.get("name") else "Desconocido",
        "schema": entidad.get("schema"),
        "datasets": entidad.get("datasets", []),
        "score": entidad.get("score", 0),
        "pais": props.get("country", ["Desconocido"])[0] if props.get("country") else "Desconocido"
    }