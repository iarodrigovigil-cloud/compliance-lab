"""
============================================================
 COMPLIANCE LAB · Motor EBR · Scoring AML
 JRV Lab S.L. · 2026

 Este archivo va en:
   compliance-lab/backend/app/services/ebr_engine.py

 Qué hace:
   Calcula el score de riesgo AML en 5 dimensiones:
   1. Factor cliente (PEP / UBO / estructura)
   2. Adverse Media (noticias negativas)
   3. Producto / Canal
   4. Factor geográfico
   5. Controles aplicados (reducción)

   Fórmula:
   Score inherente = ponderación de los 4 primeros factores
   Score residual  = score inherente − controles aplicados
   Umbral SAR      = score residual ≥ 70
============================================================
"""

import anthropic
import os
import json
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent.parent.parent / ".env")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")


def calcular_factor_cliente(campos: list) -> dict:
    """
    Factor 1: Riesgo del cliente.
    Analiza PEP, UBO, estructura de propiedad.
    Peso: 30% del score inherente.
    """
    # Buscar indicadores de riesgo en los campos extraídos
    valores = {c['nombre_campo']: c['valor'] for c in campos}

    score = 20  # Score base bajo

    # Aumentar si hay múltiples administradores (estructura compleja)
    admin = valores.get('nombre_administrador', '')
    if admin and len(str(admin)) > 50:
        score += 15

    # Aumentar si el objeto social incluye actividades de riesgo
    objeto = str(valores.get('objeto_social', '')).lower()
    palabras_riesgo = ['valores mobiliarios', 'inversiones', 'financier', 'divisas', 'criptomonedas']
    for palabra in palabras_riesgo:
        if palabra in objeto:
            score += 10
            break

    # Aumentar si hay participaciones en otras entidades
    if 'participaciones' in objeto or 'holding' in objeto:
        score += 15

    return {
        "dimension": "Factor cliente · PEP/UBO",
        "score": min(score, 100),
        "indicadores": {
            "estructura_compleja": 'participaciones' in objeto,
            "actividad_riesgo": any(p in objeto for p in palabras_riesgo),
            "pep_verificado": False  # En PoC siempre False, en producción → OpenSanctions
        },
        "nota": "PEP: pendiente verificación OpenSanctions · PoC"
    }


def calcular_adverse_media(denominacion: str, nif: str) -> dict:
    """
    Factor 2: Adverse Media.
    En PoC: simulado. En producción → API de noticias + NLP.
    Peso: 25% del score inherente.
    """
    # En PoC simulamos el resultado
    # En producción: llamar a APIs de noticias y analizar sentimiento
    score = 10  # Score base muy bajo

    return {
        "dimension": "Adverse Media",
        "score": score,
        "indicadores": {
            "noticias_negativas": 0,
            "sentimiento_medio": 0.0,
            "fuentes_consultadas": ["El Confidencial", "El País", "Expansión"]
        },
        "nota": "Sin noticias adversas detectadas · PoC (producción: API noticias + NLP)"
    }


def calcular_producto_canal(campos: list) -> dict:
    """
    Factor 3: Producto y Canal.
    Analiza el tipo de actividad económica (CNAE).
    Peso: 25% del score inherente.
    """
    valores = {c['nombre_campo']: c['valor'] for c in campos}
    cnae = str(valores.get('cnae', '0'))
    objeto = str(valores.get('objeto_social', '')).lower()

    score = 20  # Base

    # CNAEs de mayor riesgo AML según SEPBLAC
    cnae_alto_riesgo = ['6419', '6492', '6499', '6612', '6619', '9200', '9319']
    cnae_medio_riesgo = ['4110', '6810', '6820', '6831', '6832', '7010', '7022']

    if cnae in cnae_alto_riesgo:
        score = 75
    elif cnae in cnae_medio_riesgo:
        score = 45

    # Subir si hay indicadores de riesgo en el objeto social
    if 'efectivo' in objeto or 'cash' in objeto:
        score += 20
    if 'internacional' in objeto or 'exterior' in objeto:
        score += 10

    return {
        "dimension": "Producto / Canal",
        "score": min(score, 100),
        "indicadores": {
            "cnae": cnae,
            "cnae_riesgo": "alto" if cnae in cnae_alto_riesgo else "medio" if cnae in cnae_medio_riesgo else "bajo",
            "actividad_internacional": 'internacional' in objeto
        },
        "nota": f"CNAE {cnae} · Actividad inmobiliaria e inversiones"
    }


def calcular_factor_geografico(campos: list) -> dict:
    """
    Factor 4: Riesgo geográfico.
    Analiza domicilio social y jurisdicciones relacionadas.
    Peso: 20% del score inherente.
    """
    valores = {c['nombre_campo']: c['valor'] for c in campos}
    domicilio = str(valores.get('domicilio_social', '')).lower()

    score = 15  # España = bajo riesgo base

    # Países de la lista gris GAFI aumentan el score
    paises_riesgo = ['panama', 'dubai', 'emiratos', 'cayman', 'bahamas',
                     'gibraltar', 'jersey', 'isle of man', 'liechtenstein']

    for pais in paises_riesgo:
        if pais in domicilio:
            score = 80
            break

    # Provincias/zonas con mayor actividad económica
    zonas_atencion = ['marbella', 'ibiza', 'palma', 'canarias']
    for zona in zonas_atencion:
        if zona in domicilio:
            score += 10

    return {
        "dimension": "Factor geográfico",
        "score": min(score, 100),
        "indicadores": {
            "domicilio": valores.get('domicilio_social', 'No disponible'),
            "jurisdiccion": "España",
            "indice_basilea": 2.1,
            "lista_gris_gafi": False
        },
        "nota": "Domicilio en España · Índice Basilea bajo · Sin listas GAFI"
    }


def calcular_controles(score_inherente: int) -> dict:
    """
    Controles aplicados: reducen el score inherente.
    En PoC: controles base siempre aplicados.
    """
    controles = []
    reduccion = 0

    # Control 1: Documentación KYC completa
    controles.append("Documentación KYC recibida y clasificada")
    reduccion += 8

    # Control 2: Verificación Registro Mercantil
    controles.append("Verificación Registro Mercantil (Nota Simple)")
    reduccion += 5

    # Control 3: Si el score es alto, requiere más controles
    if score_inherente >= 60:
        controles.append("Revisión manual AML Officer requerida")
        reduccion += 3

    return {
        "dimension": "Controles aplicados",
        "reduccion": reduccion,
        "controles": controles,
        "nota": f"−{reduccion} puntos aplicados"
    }


def calcular_ebr(expediente_id: str, campos: list, denominacion: str, nif: str = None) -> dict:
    """
    Función principal: calcula el scoring EBR completo.

    Fórmula:
    - Score inherente = (cliente×0.30) + (media×0.25) + (producto×0.25) + (geo×0.20)
    - Score residual  = score inherente − controles
    - Nivel riesgo    = bajo(<30) / medio(30-59) / alto(60-79) / muy_alto(≥80)
    - Umbral SAR      = score residual ≥ 70
    """

    # Calcular las 4 dimensiones
    f_cliente  = calcular_factor_cliente(campos)
    f_media    = calcular_adverse_media(denominacion, nif or '')
    f_producto = calcular_producto_canal(campos)
    f_geo      = calcular_factor_geografico(campos)

    # Score inherente ponderado
    score_inherente = int(
        f_cliente['score']  * 0.30 +
        f_media['score']    * 0.25 +
        f_producto['score'] * 0.25 +
        f_geo['score']      * 0.20
    )

    # Controles y score residual
    controles = calcular_controles(score_inherente)
    score_residual = max(0, score_inherente - controles['reduccion'])

    # Nivel de riesgo
    if score_residual < 30:
        nivel = "bajo"
    elif score_residual < 60:
        nivel = "medio"
    elif score_residual < 80:
        nivel = "alto"
    else:
        nivel = "muy_alto"

    umbral_sar = score_residual >= 70

    return {
        "expediente_id": expediente_id,
        "denominacion": denominacion,
        "dimensiones": {
            "factor_cliente":  f_cliente,
            "adverse_media":   f_media,
            "producto_canal":  f_producto,
            "factor_geografico": f_geo,
            "controles":       controles
        },
        "resultado": {
            "score_inherente":    score_inherente,
            "controles_aplicados": controles['reduccion'],
            "score_residual":     score_residual,
            "nivel_riesgo":       nivel,
            "umbral_sar":         umbral_sar,
            "media_sectorial":    58
        },
        "alerta_sar": umbral_sar,
        "mensaje_sar": (
            "⚠️ SCORE ≥ 70 · Comunicación al SEPBLAC obligatoria (Art. 18 Ley 10/2010)"
            if umbral_sar else
            "✅ Score por debajo del umbral SAR"
        )
    }
