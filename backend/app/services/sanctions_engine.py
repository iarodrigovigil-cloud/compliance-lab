# sanctions_engine.py
import httpx
import os
from pathlib import Path
from dotenv import load_dotenv

_env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(dotenv_path=_env_path)

OPENSANCTIONS_KEY = os.getenv("OPENSANCTIONS_API_KEY", "")
BASE_URL = "https://api.opensanctions.org"


async def consultar_sanciones(nombre: str, nif: str = None) -> dict:
    if not OPENSANCTIONS_KEY:
        return {"nombre_consultado": nombre, "nif_consultado": nif, "encontrado_en_sanciones": False, "score_riesgo_sanciones": 0, "coincidencias": [], "listas_consultadas": [], "total_coincidencias": 0, "recomendacion": "⚠️ OPENSANCTIONS_API_KEY no configurada en .env"}

    resultados = []
    headers = {"Authorization": f"ApiKey {OPENSANCTIONS_KEY}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(verify=False, timeout=15) as client:
        for schema in ["Person", "Company", "LegalEntity"]:
            payload = {"queries": {"q1": {"schema": schema, "properties": {"name": [nombre]}}}}
            if nif:
                payload["queries"]["q1"]["properties"]["registrationNumber"] = [nif]
            try:
                r = await client.post(f"{BASE_URL}/match/default", json=payload, headers=headers)
                if r.status_code == 200:
                    for qr in r.json().get("responses", {}).values():
                        for res in qr.get("results", []):
                            if res.get("score", 0) >= 0.7:
                                parsed = _parsear_entidad(res)
                                if parsed not in resultados:
                                    resultados.append(parsed)
            except Exception:
                pass

    encontrado = len(resultados) > 0
    return {"nombre_consultado": nombre, "nif_consultado": nif, "encontrado_en_sanciones": encontrado, "score_riesgo_sanciones": 100 if encontrado else 0, "coincidencias": resultados, "listas_consultadas": ["OFAC", "UE Consolidated", "ONU", "UN SC", "OpenSanctions"], "total_coincidencias": len(resultados), "recomendacion": "🚨 BLOQUEAR OPERACIÓN · Sujeto en lista de sanciones · Art. 42 Ley 10/2010" if encontrado else "✅ Sin coincidencias en listas de sanciones internacionales"}


def _parsear_entidad(entidad: dict) -> dict:
    props = entidad.get("properties", {})
    return {"id": entidad.get("id"), "nombre": props.get("name", ["Desconocido"])[0] if props.get("name") else "Desconocido", "schema": entidad.get("schema"), "datasets": entidad.get("datasets", []), "score": round(entidad.get("score", 0), 2), "pais": props.get("country", ["Desconocido"])[0] if props.get("country") else "Desconocido"}
