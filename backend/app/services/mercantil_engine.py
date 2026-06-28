"""
============================================================
 COMPLIANCE LAB · Motor OpenMercantil (BORME)
 JRV Lab S.L. · 2026

 Consulta datos registrales del Registro Mercantil español
 via API OpenMercantil con caché Redis.
============================================================
"""
import httpx
import asyncio
import json
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

OPENMERCANTIL_API_KEY = os.getenv("OPENMERCANTIL_API_KEY", "")
BASE_URL = "https://api.openmercantil.com/v1"
CACHE_TTL = 86400  # 24 horas

logger = logging.getLogger(__name__)


async def consultar_borme(nif: str, redis_client=None) -> dict:
    """
    Consulta datos BORME de una empresa por NIF.
    Usa Redis como caché para evitar llamadas repetidas.
    """
    if not nif:
        return _respuesta_vacia("NIF no proporcionado")

    # Normalizar NIF
    nif_limpio = nif.upper().replace("-", "").replace(" ", "")
    cache_key = f"borme:{nif_limpio}"

    # Intentar caché Redis
    if redis_client:
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                logger.info(f"BORME cache HIT: {nif_limpio}")
                return json.loads(cached)
        except Exception as e:
            logger.warning(f"Redis error: {e}")

    if not OPENMERCANTIL_API_KEY:
        return _respuesta_vacia("OPENMERCANTIL_API_KEY no configurada")

    # Llamada a la API con reintentos
    resultado = await _llamar_api_con_reintentos(nif_limpio)

    # Guardar en caché si hay resultado
    if redis_client and resultado.get("encontrado"):
        try:
            await redis_client.setex(cache_key, CACHE_TTL, json.dumps(resultado))
        except Exception as e:
            logger.warning(f"Redis set error: {e}")

    return resultado


async def _llamar_api_con_reintentos(nif: str, max_reintentos: int = 3) -> dict:
    """Llama a OpenMercantil con backoff exponencial."""
    headers = {
        "Authorization": f"Bearer {OPENMERCANTIL_API_KEY}",
        "Content-Type": "application/json"
    }

    for intento in range(max_reintentos):
        try:
            async with httpx.AsyncClient(timeout=15, verify=False) as client:
                r = await client.get(
                    f"{BASE_URL}/empresa/{nif}",
                    headers=headers
                )

                if r.status_code == 200:
                    return _parsear_respuesta(r.json(), nif)
                elif r.status_code == 404:
                    return _respuesta_vacia(f"Empresa {nif} no encontrada en BORME")
                elif r.status_code == 401:
                    return _respuesta_vacia("API key OpenMercantil inválida")
                elif r.status_code == 429:
                    await asyncio.sleep(2 ** intento)
                    continue
                else:
                    return _respuesta_vacia(f"Error API: {r.status_code}")

        except httpx.TimeoutException:
            if intento < max_reintentos - 1:
                await asyncio.sleep(2 ** intento)
                continue
            return _respuesta_vacia("Timeout conectando a OpenMercantil")
        except Exception as e:
            return _respuesta_vacia(f"Error: {str(e)}")

    return _respuesta_vacia("Máximo de reintentos alcanzado")


def _parsear_respuesta(data: dict, nif: str) -> dict:
    """Extrae los campos relevantes para el scoring EBR."""
    return {
        "encontrado": True,
        "nif": nif,
        "denominacion": data.get("nombre", ""),
        "forma_juridica": data.get("forma_juridica", ""),
        "fecha_constitucion": data.get("fecha_constitucion", ""),
        "fecha_ultimo_deposito": data.get("fecha_ultimo_deposito_cuentas", ""),
        "objeto_social": data.get("objeto_social", ""),
        "domicilio": data.get("domicilio", ""),
        "capital_social": data.get("capital_social", 0),
        "cnae": data.get("cnae", ""),
        "administradores": data.get("administradores", []),
        "situacion": data.get("situacion", "activa"),
        # Señales de riesgo BORME
        "senales_riesgo": _detectar_senales_riesgo(data),
        "fuente": "OpenMercantil/BORME"
    }


def _detectar_senales_riesgo(data: dict) -> list:
    """Detecta señales de riesgo en los datos BORME."""
    senales = []

    # Sin depósito de cuentas reciente
    ultimo_deposito = data.get("fecha_ultimo_deposito_cuentas", "")
    if not ultimo_deposito:
        senales.append("Sin depósito de cuentas en el Registro")

    # Cambios frecuentes de administrador
    num_cambios = len(data.get("historico_administradores", []))
    if num_cambios > 3:
        senales.append(f"Múltiples cambios de administrador ({num_cambios})")

    # Capital social muy bajo
    capital = data.get("capital_social", 0)
    if capital and capital < 1000:
        senales.append(f"Capital social muy bajo: {capital}€")

    # Situación no activa
    situacion = data.get("situacion", "activa").lower()
    if situacion not in ("activa", "active"):
        senales.append(f"Situación registral: {situacion}")

    return senales


def _respuesta_vacia(motivo: str) -> dict:
    return {
        "encontrado": False,
        "nif": "",
        "denominacion": "",
        "forma_juridica": "",
        "fecha_constitucion": "",
        "fecha_ultimo_deposito": "",
        "objeto_social": "",
        "domicilio": "",
        "capital_social": 0,
        "cnae": "",
        "administradores": [],
        "situacion": "",
        "senales_riesgo": [],
        "fuente": "OpenMercantil/BORME",
        "motivo_no_encontrado": motivo
    }
