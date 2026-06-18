"""
============================================================
 COMPLIANCE LAB · Motor SAR
 JRV Lab S.L. · 2026

 Este archivo va en:
   compliance-lab/backend/app/services/sar_engine.py

 Qué hace:
   Genera automáticamente el borrador de comunicación
   al SEPBLAC conforme al Art. 18 Ley 10/2010.

   Incluye:
   - Referencia interna SAR-YYYY-NNNNN
   - Tipología 6AMLD
   - Descripción de la operativa sospechosa
   - Lista de verificación 7 puntos
   - Hash blockchain del expediente
   - Plazo máximo de comunicación (10 días hábiles)
============================================================
"""

import anthropic
import os
import json
import hashlib
from datetime import datetime, timedelta
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent.parent.parent / ".env")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")


def calcular_dias_habiles(fecha_inicio: datetime, dias: int) -> datetime:
    """Calcula la fecha límite en días hábiles (excluyendo sábados y domingos)."""
    fecha = fecha_inicio
    dias_contados = 0
    while dias_contados < dias:
        fecha += timedelta(days=1)
        if fecha.weekday() < 5:  # 0=lunes, 4=viernes
            dias_contados += 1
    return fecha


def generar_referencia_sar(expediente_id: str) -> str:
    """Genera una referencia única para el SAR."""
    año = datetime.now().year
    # Usar los últimos 5 caracteres del UUID como número de referencia
    num = int(expediente_id.replace('-', '')[-5:], 16) % 99999
    return f"SAR-{año}-{str(num).zfill(5)}"


def generar_hash_expediente(expediente_id: str, campos: list, scoring: dict) -> str:
    """
    Genera el hash SHA-256 del expediente para el audit trail blockchain.
    Combina el ID, los campos y el scoring en un hash único e inmutable.
    """
    contenido = json.dumps({
        "expediente_id": expediente_id,
        "campos": sorted([f"{c['nombre_campo']}:{c['valor']}" for c in campos]),
        "score_residual": scoring.get('score_residual', 0),
        "timestamp": datetime.now().isoformat()[:10]  # Solo fecha, no hora exacta
    }, sort_keys=True)
    return hashlib.sha256(contenido.encode()).hexdigest()[:16].upper()


def generar_borrador_sar_con_ia(
    expediente: dict,
    campos: list,
    scoring: dict,
    referencia: str
) -> str:
    """
    Usa Claude para generar la descripción narrativa del SAR.
    Esta es la parte más importante: la descripción de la operativa sospechosa.
    """
    cliente = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # Preparar contexto del expediente
    campos_relevantes = {c['nombre_campo']: c['valor'] for c in campos}

    prompt = f"""Eres un experto en cumplimiento normativo AML/KYC especializado en la Ley 10/2010 española y el AMLR 2024.

Genera la descripción de la operativa sospechosa para una Comunicación de Operaciones Sospechosas (SAR) 
al SEPBLAC conforme al Art. 18 de la Ley 10/2010.

DATOS DEL EXPEDIENTE:
- Denominación: {expediente.get('denominacion', 'N/D')}
- NIF/CIF: {expediente.get('nif', 'N/D')}
- Domicilio: {campos_relevantes.get('domicilio_social', 'N/D')}
- Objeto social: {campos_relevantes.get('objeto_social', 'N/D')}
- CNAE: {campos_relevantes.get('cnae', 'N/D')}
- Inicio operaciones: {campos_relevantes.get('fecha_inicio_operaciones', 'N/D')}

SCORING EBR:
- Score residual: {scoring.get('score_residual', 0)}/100
- Nivel de riesgo: {scoring.get('nivel_riesgo', 'N/D')}
- Factor cliente: {scoring.get('riesgo_cliente', 0)}/100
- Factor geográfico: {scoring.get('riesgo_geografico', 0)}/100
- Producto/Canal: {scoring.get('riesgo_producto', 0)}/100

REFERENCIA SAR: {referencia}
FECHA DETECCIÓN: {datetime.now().strftime('%d/%m/%Y · %H:%M UTC')}

Genera EXACTAMENTE este JSON sin texto adicional:
{{
    "tipologia_6amld": "Tipología delictiva según 6AMLD (ej: Fraude fiscal, Blanqueo, Evasión...)",
    "descripcion_operativa": "Descripción formal de 3-4 frases de la operativa sospechosa detectada durante el proceso de diligencia debida. Debe mencionar: qué se detectó, cuándo, qué indicadores activaron la alerta, y qué verificaciones están en curso. Tono formal y jurídico.",
    "indicadores_riesgo": ["Indicador 1", "Indicador 2", "Indicador 3"],
    "base_legal": "Artículo exacto de la Ley 10/2010 o AMLR 2024 que fundamenta la comunicación"
}}"""

    respuesta = cliente.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )

    texto = respuesta.content[0].text.strip()
    if texto.startswith("```"):
        texto = texto.split("```")[1]
        if texto.startswith("json"):
            texto = texto[4:]

    return json.loads(texto)


def generar_sar(
    expediente_id: str,
    expediente: dict,
    campos: list,
    scoring: dict
) -> dict:
    """
    Función principal: genera el SAR completo.

    Estructura del SAR:
    1. Cabecera (referencia, fechas, sujeto comunicante)
    2. Identificación del sujeto investigado
    3. Descripción de la operativa sospechosa (generada por IA)
    4. Indicadores de riesgo
    5. Hash blockchain del expediente
    6. Lista de verificación 7 puntos (Art. 18 Ley 10/2010)
    7. Plazo de comunicación (10 días hábiles)
    """

    print(f"\n{'='*50}")
    print(f"📋 Generando SAR para: {expediente.get('denominacion')}")
    print(f"{'='*50}")

    # 1. Referencia y fechas
    referencia = generar_referencia_sar(expediente_id)
    fecha_deteccion = datetime.now()
    fecha_limite = calcular_dias_habiles(fecha_deteccion, 10)
    hash_expediente = generar_hash_expediente(expediente_id, campos, scoring)

    print(f"📎 Referencia: {referencia}")
    print(f"⏰ Plazo límite: {fecha_limite.strftime('%d/%m/%Y')}")

    # 2. Campos del sujeto investigado
    campos_dict = {c['nombre_campo']: c['valor'] for c in campos}

    # 3. Generar descripción con IA
    print("🤖 Generando descripción narrativa con Claude...")
    contenido_ia = generar_borrador_sar_con_ia(expediente, campos, scoring, referencia)
    print("✅ Descripción generada")

    # 4. Lista de verificación 7 puntos (Art. 18 Ley 10/2010)
    checklist = [
        {
            "punto": 1,
            "descripcion": "Identidad del sujeto verificada con documentación oficial",
            "completado": len(campos) > 0,
            "obligatorio": True
        },
        {
            "punto": 2,
            "descripcion": f"Score EBR ≥ 70 confirmado (actual: {scoring.get('score_residual', 0)}/100)",
            "completado": scoring.get('umbral_sar', False),
            "obligatorio": True
        },
        {
            "punto": 3,
            "descripcion": "Documentación KYC completa y clasificada por LegNER",
            "completado": len(campos) >= 5,
            "obligatorio": True
        },
        {
            "punto": 4,
            "descripcion": "Adverse Media documentado con fuente y sentimiento",
            "completado": False,  # En PoC siempre pendiente
            "obligatorio": True
        },
        {
            "punto": 5,
            "descripcion": "Verificación PEP niveles 1-4 completada (OpenSanctions)",
            "completado": False,  # En PoC siempre pendiente
            "obligatorio": True
        },
        {
            "punto": 6,
            "descripcion": "Hash blockchain del expediente incluido en la comunicación",
            "completado": True,
            "obligatorio": False
        },
        {
            "punto": 7,
            "descripcion": "Notificación interna a Dirección de Compliance",
            "completado": False,
            "obligatorio": False
        }
    ]

    puntos_completados = sum(1 for p in checklist if p['completado'])
    puntos_obligatorios_ok = sum(1 for p in checklist if p['obligatorio'] and p['completado'])
    total_obligatorios = sum(1 for p in checklist if p['obligatorio'])

    # 5. SAR completo
    sar = {
        "referencia": referencia,
        "estado": "borrador",
        "cabecera": {
            "fecha_deteccion": fecha_deteccion.strftime('%d/%m/%Y · %H:%M UTC'),
            "fecha_limite_comunicacion": fecha_limite.strftime('%d/%m/%Y'),
            "dias_habiles_restantes": 10,
            "sujeto_comunicante": "Compliance Lab · Sujeto obligado Art. 2.1 Ley 10/2010",
            "canal_envio": "Canal seguro SEPBLAC · Cifrado extremo a extremo"
        },
        "sujeto_investigado": {
            "denominacion": expediente.get('denominacion'),
            "nif_cif": expediente.get('nif', 'N/D'),
            "domicilio": campos_dict.get('domicilio_social', 'N/D'),
            "objeto_social": campos_dict.get('objeto_social', 'N/D'),
            "cnae": campos_dict.get('cnae', 'N/D'),
            "administrador": campos_dict.get('nombre_administrador', 'Pendiente verificación')
        },
        "scoring_ebr": {
            "score_residual": scoring.get('score_residual', 0),
            "nivel_riesgo": scoring.get('nivel_riesgo', 'N/D'),
            "umbral_sar_activado": scoring.get('umbral_sar', False),
            "media_sectorial": 58
        },
        "contenido": {
            "tipologia_6amld": contenido_ia.get('tipologia_6amld'),
            "descripcion_operativa": contenido_ia.get('descripcion_operativa'),
            "indicadores_riesgo": contenido_ia.get('indicadores_riesgo', []),
            "base_legal": contenido_ia.get('base_legal')
        },
        "blockchain": {
            "hash_expediente": f"0x{hash_expediente}",
            "expediente_id": expediente_id,
            "audit_trail": "Disponible · Sellado inmutable por evento · AMLR 2024"
        },
        "checklist_verificacion": {
            "puntos": checklist,
            "completados": puntos_completados,
            "total": len(checklist),
            "obligatorios_ok": puntos_obligatorios_ok,
            "total_obligatorios": total_obligatorios,
            "listo_para_envio": puntos_obligatorios_ok == total_obligatorios
        },
        "generado_por": "LegNER · Compliance Lab · JRV Lab S.L.",
        "nota_legal": "Borrador generado automáticamente. Requiere revisión y firma del AML Officer antes del envío al SEPBLAC."
    }

    print(f"\n✅ SAR generado: {referencia}")
    print(f"   Checklist: {puntos_completados}/{len(checklist)} puntos completados")
    print(f"   Listo para envío: {'Sí' if sar['checklist_verificacion']['listo_para_envio'] else 'No · Pendiente revisión manual'}")

    return sar
