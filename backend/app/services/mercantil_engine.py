"""
============================================================
 COMPLIANCE LAB · Motor OpenMercantil (BORME)
 JRV Lab S.L. · 2026

 API pública OpenMercantil — sin autenticación.
 Base URL: https://openmercantil.es/api/v1
 Docs: https://openmercantil.es/api/documentacion
============================================================
"""
import httpx
import asyncio
import json
import logging

BASE_URL = "https://openmercantil.es/api/v1"
logger = logging.getLogger(__name__)


async def consultar_borme(nif: str, redis_client=None) -> dict:
    """Consulta datos BORME de una empresa por NIF via OpenMercantil."""
    if not nif:
        return _respuesta_vacia("NIF no proporcionado")

    nif_limpio = nif.upper().replace("-", "").replace(" ", "")
    cache_key = f"borme:{nif_limpio}"

    # Caché Redis
    if redis_client:
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception:
            pass

    resultado = await _buscar_por_nif(nif_limpio)

    if redis_client and resultado.get("encontrado"):
        try:
            await redis_client.setex(cache_key, 86400, json.dumps(resultado))
        except Exception:
            pass

    return resultado


async def _buscar_por_nif(nif: str) -> dict:
    async with httpx.AsyncClient(timeout=15, verify=False) as client:
        try:
            # Paso 1: buscar por NIF/CIF
            r = await client.get(f"{BASE_URL}/search", params={"q": nif, "limit": 5})
            if r.status_code != 200:
                return _respuesta_vacia(f"Error búsqueda: {r.status_code}")

            data = r.json()
            items = data.get("items", [])

            if not items:
                return _respuesta_vacia(f"Empresa con NIF {nif} no encontrada")

            # Buscar coincidencia exacta por CIF
            empresa_search = None
            for item in items:
                cif = str(item.get("cif", "")).upper().replace("-", "")
                if cif == nif:
                    empresa_search = item
                    break

            if not empresa_search:
                empresa_search = items[0]

            slug = empresa_search.get("slug", "")
            nombre = empresa_search.get("name", "")
            cif = empresa_search.get("cif", nif)
            last_seen = empresa_search.get("last_seen", "")
            acts_count = empresa_search.get("acts_count", 0)

            # Paso 2: obtener ficha completa
            empresa_detail = {}
            if slug:
                try:
                    r2 = await client.get(f"{BASE_URL}/company/{slug}")
                    if r2.status_code == 200:
                        empresa_detail = r2.json()
                except Exception:
                    pass

            # Combinar datos de búsqueda y detalle
            return _parsear_respuesta(empresa_search, empresa_detail, nif, last_seen, acts_count)

        except httpx.TimeoutException:
            return _respuesta_vacia("Timeout conectando a OpenMercantil")
        except Exception as e:
            return _respuesta_vacia(f"Error: {str(e)}")


def _parsear_respuesta(search: dict, detail: dict, nif: str, last_seen: str, acts_count: int) -> dict:
    """Combina datos de búsqueda y detalle."""
    # Nombre
    nombre = detail.get("name") or detail.get("nombre") or search.get("name", "")

    # Campos del detalle (pueden variar según la respuesta)
    forma_juridica = detail.get("forma_juridica") or detail.get("legal_form") or ""
    fecha_const = detail.get("fecha_constitucion") or detail.get("incorporation_date") or ""
    objeto = detail.get("objeto_social") or detail.get("activity") or detail.get("actividad") or ""
    domicilio = detail.get("domicilio") or detail.get("address") or detail.get("registered_address") or ""
    if isinstance(domicilio, dict):
        parts = [domicilio.get("calle", ""), domicilio.get("municipio", ""), domicilio.get("provincia", "")]
        domicilio = " ".join(p for p in parts if p)

    capital = detail.get("capital_social") or detail.get("share_capital") or 0
    try:
        capital = float(str(capital).replace(".", "").replace(",", ".").replace("€", "").strip())
    except Exception:
        capital = 0

    cnae = str(detail.get("cnae") or detail.get("cnae_codigo") or "")
    administradores = detail.get("administradores") or detail.get("directors") or detail.get("cargos") or []
    situacion = detail.get("situacion") or detail.get("status") or "activa"
    slug = search.get("slug", "")

    senales = _detectar_senales_riesgo(last_seen, capital, situacion, acts_count)

    return {
        "encontrado": True,
        "nif": nif,
        "denominacion": nombre,
        "forma_juridica": forma_juridica,
        "fecha_constitucion": fecha_const,
        "fecha_ultimo_deposito": last_seen,
        "objeto_social": objeto,
        "domicilio": domicilio,
        "capital_social": capital,
        "cnae": cnae,
        "administradores": administradores if isinstance(administradores, list) else [],
        "situacion": situacion,
        "num_actos_borme": acts_count,
        "senales_riesgo": senales,
        "fuente": "OpenMercantil/BORME",
        "slug": slug,
        "url": f"https://openmercantil.es/empresa/{slug}" if slug else ""
    }


def _detectar_senales_riesgo(last_seen: str, capital: float, situacion: str, acts_count: int) -> list:
    senales = []

    if not last_seen:
        senales.append("Sin actividad reciente en BORME")
    else:
        # Si la última actividad fue hace más de 3 años
        try:
            from datetime import datetime
            ultima = datetime.strptime(last_seen, "%Y-%m-%d")
            anos = (datetime.now() - ultima).days / 365
            if anos > 3:
                senales.append(f"Sin actividad BORME desde {last_seen} ({int(anos)} años)")
        except Exception:
            pass

    if capital and capital < 1000:
        senales.append(f"Capital social muy bajo: {capital}€")

    if situacion and situacion.lower() not in ("activa", "active", ""):
        senales.append(f"Situación registral: {situacion}")

    if acts_count == 0:
        senales.append("Sin actos registrales en BORME")

    return senales


def _respuesta_vacia(motivo: str) -> dict:
    return {
        "encontrado": False, "nif": "", "denominacion": "", "forma_juridica": "",
        "fecha_constitucion": "", "fecha_ultimo_deposito": "", "objeto_social": "",
        "domicilio": "", "capital_social": 0, "cnae": "", "administradores": [],
        "situacion": "", "num_actos_borme": 0, "senales_riesgo": [],
        "fuente": "OpenMercantil/BORME", "slug": "", "url": "",
        "motivo_no_encontrado": motivo
    }
