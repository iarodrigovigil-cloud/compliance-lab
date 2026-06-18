"""
LegNER · Clasificador de documentos KYC
JRV Lab S.L. · 2026

8 tipos de documento soportados:
1. Nota Simple Registral
2. Certificado de Vigencia y Cargo
3. Escritura de Constitución
4. Acta de Titularidad Real
5. Declaración de Titularidad Real
6. Documento de Identidad (DNI/Pasaporte)
7. Certificado Fiscal (ATR)
8. Documento Extranjero (verificación manual)
"""

TIPOS_DOCUMENTO = {
    "nota_simple":            "Nota Simple Registral",
    "certificado_vigencia":   "Certificado de Vigencia y Cargo",
    "escritura_constitucion": "Escritura de Constitución",
    "acta_titularidad":       "Acta de Titularidad Real",
    "declaracion_titularidad":"Declaración de Titularidad Real",
    "documento_identidad":    "Documento de Identidad",
    "certificado_fiscal":     "Certificado Fiscal (ATR)",
    "extranjero":             "Documento Extranjero · Verificación Manual",
}

# TODO Paso 4: implementar con Claude API
