"""
============================================================
 COMPLIANCE LAB · Motor LegNER
 JRV Lab S.L. · 2026

 Este archivo va en:
   compliance-lab/backend/app/services/legner_engine.py

 Qué hace:
   1. Lee un PDF y extrae el texto
   2. Llama a Claude API para clasificar el tipo de documento
   3. Extrae los campos KYC según el tipo detectado
   4. Devuelve todo estructurado en JSON
============================================================
"""

import anthropic
import pdfplumber
import json
import os
from pathlib import Path

# ── OCR fallback para PDFs escaneados
from pdf2image import convert_from_path
import pytesseract

# ── Leer la API Key del archivo .env
from dotenv import load_dotenv
load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()

# ── Los 9 tipos de documento KYC que reconoce LegNER
TIPOS_DOCUMENTO = {
    "nota_simple":               "Nota Simple Registral",
    "certificado_vigencia":      "Certificado de Vigencia y Cargo",
    "escritura_constitucion":    "Escritura de Constitución",
    "escritura_ampliacion_capital": "Escritura de Ampliación de Capital",
    "acta_titularidad":          "Acta de Titularidad Real",
    "declaracion_titularidad":   "Declaración de Titularidad Real",
    "documento_identidad":       "Documento de Identidad (DNI/Pasaporte)",
    "certificado_fiscal":        "Certificado Fiscal (ATR)",
    "extranjero":                "Documento Extranjero · Verificación Manual",
}


def extraer_texto_ocr(ruta_pdf: str, max_paginas: int = 20) -> str:
    """
    Fallback OCR: convierte el PDF a imágenes y extrae texto con Tesseract.
    Se usa cuando pdfplumber no encuentra texto seleccionable (PDF escaneado).
    """
    texto = ""
    try:
        imagenes = convert_from_path(ruta_pdf, dpi=300, fmt="png")
    except Exception as e:
        raise Exception(f"Error convirtiendo PDF a imagen para OCR: {e}")

    for i, imagen in enumerate(imagenes[:max_paginas]):
        try:
            texto_pagina = pytesseract.image_to_string(imagen, lang="spa+eng")
            if texto_pagina:
                texto += texto_pagina + "\n"
        except Exception as e:
            print(f"   ⚠️ OCR falló en página {i+1}: {e}")
            continue

    return texto.strip()


def extraer_texto_pdf(ruta_pdf: str) -> str:
    """
    Paso 1: Lee el PDF y extrae todo el texto.
    Primero intenta pdfplumber (PDFs nativos con texto seleccionable).
    Si no encuentra texto (PDF escaneado como imagen), recurre a OCR con Tesseract.
    """
    texto = ""
    try:
        with pdfplumber.open(ruta_pdf) as pdf:
            for pagina in pdf.pages:
                texto_pagina = pagina.extract_text()
                if texto_pagina:
                    texto += texto_pagina + "\n"
    except Exception as e:
        raise Exception(f"Error leyendo PDF: {e}")

    texto = texto.strip()

    # Fallback OCR si pdfplumber no extrajo texto suficiente (PDF escaneado)
    if len(texto) < 50:
        print("   ℹ️  PDF sin texto nativo detectado · activando OCR con Tesseract...")
        texto = extraer_texto_ocr(ruta_pdf)

    if not texto or len(texto.strip()) < 20:
        raise Exception(
            "No se pudo extraer texto del PDF, ni de forma nativa ni mediante OCR. "
            "El documento puede tener muy baja calidad de escaneo."
        )

    return texto.strip()


def clasificar_documento(texto: str) -> dict:
    """
    Paso 2: Llama a Claude para clasificar el tipo de documento.
    Claude lee el texto y decide qué tipo de documento KYC es.
    """
    cliente = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = f"""Eres LegNER, un clasificador experto de documentos KYC del sistema legal español.

Analiza el siguiente texto de un documento y clasifícalo en UNO de estos 9 tipos:

1. nota_simple - Nota Simple Registral del Registro Mercantil
2. certificado_vigencia - Certificado de Vigencia y Cargo de administradores
3. escritura_constitucion - Escritura de CONSTITUCIÓN de una sociedad nueva (creación inicial de la empresa, primer capital social)
4. escritura_ampliacion_capital - Escritura de AUMENTO/AMPLIACIÓN de capital social de una sociedad YA EXISTENTE (incluye ampliaciones por compensación de créditos, aportación dineraria o no dineraria, y sus diligencias de subsanación). Identifícala por títulos como "ESCRITURA DE ELEVACIÓN A PÚBLICO DE ACUERDOS... AUMENTO DE CAPITAL", "AMPLIACIÓN DE CAPITAL", o porque el texto menciona una sociedad ya constituida con anterioridad cuyo capital se incrementa
5. acta_titularidad - Acta de Manifestaciones de Titularidad Real
6. declaracion_titularidad - Declaración de Titularidad Real
7. documento_identidad - DNI, NIE o Pasaporte
8. certificado_fiscal - Certificado de situación fiscal (ATR)
9. extranjero - Documento de registro extranjero (requiere verificación manual)

IMPORTANTE: no confundas el tipo 3 (constitución) con el tipo 4 (ampliación). Si el documento dice que la sociedad "constituida... en escritura autorizada... el día [fecha anterior]" y ahora se reúne para "AUMENTAR" o "AMPLIAR" su capital, es tipo 4, no tipo 3.

TEXTO DEL DOCUMENTO (primeras 2000 caracteres):
{texto[:2000]}

Responde SOLO con este JSON exacto, sin texto adicional:
{{
    "tipo": "codigo_del_tipo",
    "nombre": "Nombre completo del tipo",
    "confianza": 95,
    "justificacion": "Por qué has clasificado así en una frase",
    "accion": "procesar"
}}

Para "accion" usa: "procesar" (tipos 1-8) o "manual" (tipo 9 extranjero)
Para "confianza" usa un número del 0 al 100."""

    respuesta = cliente.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )

    texto_respuesta = respuesta.content[0].text.strip()

    # Limpiar posibles backticks de markdown
    if texto_respuesta.startswith("```"):
        texto_respuesta = texto_respuesta.split("```")[1]
        if texto_respuesta.startswith("json"):
            texto_respuesta = texto_respuesta[4:]

    return json.loads(texto_respuesta)


def extraer_campos(texto: str, tipo_documento: str) -> list:
    """
    Paso 3: Extrae los campos KYC específicos según el tipo de documento.
    Claude lee el texto y extrae exactamente los campos que necesitamos.
    """
    cliente = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # Campos a extraer según el tipo de documento
    campos_por_tipo = {
        "nota_simple": [
            "fecha_documento", "denominacion_social", "domicilio_social",
            "nif_cif", "cnae", "objeto_social", "fecha_inicio_operaciones",
            "nombre_administrador", "dni_administrador", "cargo_administrador",
            "fecha_nombramiento_administrador"
        ],
        "certificado_vigencia": [
            "fecha_documento", "denominacion_social", "nif_cif",
            "nombre_administrador", "dni_administrador", "cargo_administrador",
            "fecha_nombramiento", "fecha_caducidad", "notario", "numero_protocolo"
        ],
        "escritura_constitucion": [
            "fecha_escritura", "denominacion_social", "domicilio_social",
            "nif_cif", "objeto_social", "capital_social", "notario",
            "numero_protocolo", "fecha_inscripcion_registro"
        ],
        "escritura_ampliacion_capital": [
            "fecha_escritura", "denominacion_social", "nif_cif",
            "capital_social_anterior", "capital_social",
            "importe_ampliacion", "numero_participaciones_nuevas",
            "valor_nominal_participacion", "prima_asuncion",
            "socio_aportante", "naturaleza_aportacion",
            "notario", "numero_protocolo",
            "fecha_diligencia_subsanacion", "fecha_inscripcion_registro"
        ],
        "acta_titularidad": [
            "fecha_documento", "denominacion_entidad", "nombre_titular",
            "nif_titular", "nacionalidad_titular", "fecha_nacimiento_titular",
            "porcentaje_participacion", "tipo_dominio", "declaracion_firmada"
        ],
        "certificado_fiscal": [
            "fecha_documento", "denominacion_social", "nif_cif",
            "situacion_censal", "epigrafes_iae", "fecha_alta",
            "domicilio_fiscal", "tipo_certificado"
        ],
    }

    campos = campos_por_tipo.get(tipo_documento, [
        "fecha_documento", "denominacion_social", "nif_cif", "tipo_entidad"
    ])

    prompt = f"""Eres LegNER, un extractor experto de campos KYC de documentos legales españoles.

Del siguiente documento de tipo "{TIPOS_DOCUMENTO.get(tipo_documento, tipo_documento)}", extrae estos campos:
{json.dumps(campos, ensure_ascii=False, indent=2)}

TEXTO DEL DOCUMENTO:
{texto[:25000]}

Reglas:
- Si un campo no aparece en el documento, usa null
- Las fechas en formato DD/MM/YYYY
- El NIF/CIF sin guiones (ej: B12345678)
- Para campos de administradores (pueden ser varios), devuelve una lista
- Los importes en € usa solo el número con punto decimal (ej: 201506.00). Si el importe aparece escrito en palabras (ej: "DOSCIENTOS UN MIL QUINIENTOS SEIS EUROS") conviértelo igualmente a número (201506.00)
- Para "capital_social" busca frases como "El capital social se fija en", "capital social... EUROS", "QUEDA AUMENTADO el capital social", o el artículo de estatutos que define la cifra de capital
- "fecha_diligencia_subsanacion" solo aparece si el documento incluye una diligencia notarial posterior que corrige errores; si no existe, usa null
- "fecha_inscripcion_registro" busca frases como "ha sido inscrita con fecha", "inscripción 5ª" con su fecha asociada

Responde SOLO con este JSON, sin texto adicional:
{{
    "campos": [
        {{"nombre": "nombre_del_campo", "valor": "valor extraído o null", "confianza": 95}},
        ...
    ]
}}"""

    respuesta = cliente.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}]
    )

    texto_respuesta = respuesta.content[0].text.strip()

    if texto_respuesta.startswith("```"):
        texto_respuesta = texto_respuesta.split("```")[1]
        if texto_respuesta.startswith("json"):
            texto_respuesta = texto_respuesta[4:]

    resultado = json.loads(texto_respuesta)
    return resultado.get("campos", [])


def procesar_documento_kyc(ruta_pdf: str) -> dict:
    """
    Función principal: pipeline completo de un documento KYC.
    Llama a los 3 pasos en orden y devuelve el resultado final.

    Uso:
        resultado = procesar_documento_kyc("C:/ruta/al/documento.pdf")
        print(resultado)
    """
    print(f"\n{'='*50}")
    print(f"📄 Procesando: {Path(ruta_pdf).name}")
    print(f"{'='*50}")

    # PASO 1: Extraer texto del PDF
    print("1️⃣  Extrayendo texto del PDF...")
    texto = extraer_texto_pdf(ruta_pdf)
    print(f"   ✅ {len(texto)} caracteres extraídos")

    # PASO 2: Clasificar el documento
    print("2️⃣  Clasificando documento con LegNER...")
    clasificacion = clasificar_documento(texto)
    print(f"   ✅ Tipo: {clasificacion['nombre']}")
    print(f"   ✅ Confianza: {clasificacion['confianza']}%")
    print(f"   ✅ Acción: {clasificacion['accion']}")

    # PASO 3: Extraer campos (solo si no es extranjero)
    campos = []
    if clasificacion['accion'] == 'procesar':
        print("3️⃣  Extrayendo campos KYC...")
        campos = extraer_campos(texto, clasificacion['tipo'])
        campos_con_valor = [c for c in campos if c.get('valor') is not None]
        print(f"   ✅ {len(campos_con_valor)}/{len(campos)} campos extraídos")
    else:
        print("3️⃣  Documento extranjero → requiere revisión manual")

    # Resultado final
    resultado = {
        "archivo": Path(ruta_pdf).name,
        "clasificacion": clasificacion,
        "campos_extraidos": campos,
        "resumen": {
            "tipo_documento": clasificacion['tipo'],
            "nombre_tipo": clasificacion['nombre'],
            "confianza_clasificacion": clasificacion['confianza'],
            "total_campos": len(campos),
            "campos_con_valor": len([c for c in campos if c.get('valor')]),
            "requiere_manual": clasificacion['accion'] == 'manual'
        }
    }

    print(f"\n✅ COMPLETADO: {Path(ruta_pdf).name}")
    return resultado


# ============================================================
# PRUEBA RÁPIDA — ejecuta este archivo directamente para probar
# Uso: python legner_engine.py
# ============================================================
if __name__ == "__main__":
    import sys

    print("\n🛡️  COMPLIANCE LAB · Motor LegNER · Prueba")
    print("   JRV Lab S.L. · 2026\n")

    # Si pasas una ruta como argumento: python legner_engine.py mi_documento.pdf
    if len(sys.argv) > 1:
        ruta = sys.argv[1]
    else:
        # Busca PDFs en la carpeta uploads automáticamente
        uploads = Path(__file__).parent.parent.parent / "uploads"
        pdfs = list(uploads.glob("*.pdf"))

        if not pdfs:
            print("⚠️  No hay PDFs en la carpeta uploads/")
            print(f"   Copia un PDF a: {uploads}")
            print("   O ejecuta: python legner_engine.py ruta/al/documento.pdf")
            sys.exit(1)

        ruta = str(pdfs[0])
        print(f"📂 Usando primer PDF encontrado: {Path(ruta).name}")

    try:
        resultado = procesar_documento_kyc(ruta)
        print("\n" + "="*50)
        print("📊 RESULTADO COMPLETO:")
        print("="*50)
        print(json.dumps(resultado, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"\n❌ Error: {e}")
        print("   Comprueba que el archivo .env tiene la ANTHROPIC_API_KEY correcta")
