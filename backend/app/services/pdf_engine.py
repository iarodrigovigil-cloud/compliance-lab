# ── pdf_engine.py ─────────────────────────────────────────
"""
Genera el informe KYC completo en PDF.
JRV Lab S.L. · Compliance Lab
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from datetime import datetime
import io

AZUL_DEEP  = colors.HexColor("#003C96")
AZUL_MID   = colors.HexColor("#1464B4")
ORO        = colors.HexColor("#C4A24A")
DARK       = colors.HexColor("#0D1B2A")
GRIS_CLARO = colors.HexColor("#F0F4F8")
GRIS_TEXTO = colors.HexColor("#4A5568")


def generar_pdf_kyc(expediente: dict, campos: list, scoring: dict, sar: dict = None) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=2*cm, leftMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm
    )
    elementos = []

    estilo_titulo = ParagraphStyle(
        "titulo", fontSize=20, textColor=AZUL_DEEP,
        fontName="Helvetica-Bold", alignment=TA_CENTER, spaceAfter=6
    )
    estilo_subtitulo = ParagraphStyle(
        "subtitulo", fontSize=11, textColor=GRIS_TEXTO,
        fontName="Helvetica", alignment=TA_CENTER, spaceAfter=4
    )
    estilo_seccion = ParagraphStyle(
        "seccion", fontSize=12, textColor=colors.white,
        fontName="Helvetica-Bold", alignment=TA_LEFT,
        spaceAfter=8, spaceBefore=16,
        backColor=AZUL_DEEP, borderPadding=(6, 10, 6, 10)
    )
    estilo_normal = ParagraphStyle(
        "normal", fontSize=9, textColor=DARK,
        fontName="Helvetica", spaceAfter=4, leading=14
    )
    estilo_nota = ParagraphStyle(
        "nota", fontSize=8, textColor=GRIS_TEXTO,
        fontName="Helvetica-Oblique", spaceAfter=4
    )

    # ── CABECERA ──
    elementos.append(Paragraph("COMPLIANCE LAB", estilo_titulo))
    elementos.append(Paragraph("Informe KYC · Diligencia Debida · Ley 10/2010 / AMLR 2024", estilo_subtitulo))
    elementos.append(Paragraph(f"JRV Lab S.L. · Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}", estilo_subtitulo))
    elementos.append(HRFlowable(width="100%", thickness=2, color=ORO, spaceAfter=16))

    # ── 1. EXPEDIENTE ──
    elementos.append(Paragraph("  1. IDENTIFICACION DEL SUJETO", estilo_seccion))

    nivel = scoring.get("resultado", {}).get("nivel_riesgo", "N/D").upper()
    color_nivel = {"BAJO": "#22C55E", "MEDIO": "#F59E0B", "ALTO": "#EF4444", "MUY_ALTO": "#7C3AED"}.get(nivel, "#6B7280")

    datos_exp = [
        ["Denominacion", str(expediente.get("denominacion", "N/D"))],
        ["NIF/CIF",      str(expediente.get("nif", "N/D"))],
        ["Tipo entidad", str(expediente.get("tipo_entidad", "N/D"))],
        ["Estado",       str(expediente.get("estado", "N/D"))],
        ["Fecha apertura", str(expediente.get("fecha_creacion", "N/D"))[:10]],
        ["Nivel de riesgo", nivel],
    ]
    tabla_exp = Table(datos_exp, colWidths=[5*cm, 12*cm])
    tabla_exp.setStyle(TableStyle([
        ("BACKGROUND",     (0,0), (0,-1), GRIS_CLARO),
        ("TEXTCOLOR",      (0,0), (0,-1), AZUL_DEEP),
        ("FONTNAME",       (0,0), (0,-1), "Helvetica-Bold"),
        ("FONTSIZE",       (0,0), (-1,-1), 9),
        ("GRID",           (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E0")),
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [colors.white, GRIS_CLARO]),
        ("PADDING",        (0,0), (-1,-1), 6),
        ("TEXTCOLOR",      (1,5), (1,5), colors.HexColor(color_nivel)),
        ("FONTNAME",       (1,5), (1,5), "Helvetica-Bold"),
    ]))
    elementos.append(tabla_exp)
    elementos.append(Spacer(1, 12))

    # ── 2. CAMPOS ──
    elementos.append(Paragraph("  2. CAMPOS KYC EXTRAIDOS POR LEGNER", estilo_seccion))
    if campos:
        datos_campos = [["Campo", "Valor", "Confianza"]]
        for c in campos:
            confianza = float(c.get("confianza", 0) or 0)
            datos_campos.append([
                str(c.get("nombre_campo", "")),
                str(c.get("valor", ""))[:60],
                f"{confianza:.0%}"
            ])
        tabla_campos = Table(datos_campos, colWidths=[5.5*cm, 9.5*cm, 2*cm])
        tabla_campos.setStyle(TableStyle([
            ("BACKGROUND",     (0,0), (-1,0), AZUL_MID),
            ("TEXTCOLOR",      (0,0), (-1,0), colors.white),
            ("FONTNAME",       (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",       (0,0), (-1,-1), 8),
            ("GRID",           (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E0")),
            ("ROWBACKGROUNDS", (1,0), (-1,-1), [colors.white, GRIS_CLARO]),
            ("PADDING",        (0,0), (-1,-1), 5),
            ("ALIGN",          (2,0), (2,-1), "CENTER"),
        ]))
        elementos.append(tabla_campos)
    else:
        elementos.append(Paragraph("Sin campos extraidos.", estilo_normal))
    elementos.append(Spacer(1, 12))

    # ── 3. SCORING EBR ──
    elementos.append(Paragraph("  3. SCORING EBR - EVALUACION DE RIESGO AML", estilo_seccion))
    resultado = scoring.get("resultado", {})
    datos_ebr = [
        ["Score inherente",     str(resultado.get("score_inherente", "N/D"))],
        ["Controles aplicados", f"-{resultado.get('controles_aplicados', 0)}"],
        ["Score residual",      str(resultado.get("score_residual", "N/D"))],
        ["Nivel de riesgo",     nivel],
        ["Media sectorial",     str(resultado.get("media_sectorial", 58))],
        ["Alerta SAR",          "SI" if scoring.get("alerta_sar") else "NO"],
    ]
    tabla_ebr = Table(datos_ebr, colWidths=[5*cm, 12*cm])
    tabla_ebr.setStyle(TableStyle([
        ("BACKGROUND",     (0,0), (0,-1), GRIS_CLARO),
        ("TEXTCOLOR",      (0,0), (0,-1), AZUL_DEEP),
        ("FONTNAME",       (0,0), (0,-1), "Helvetica-Bold"),
        ("FONTSIZE",       (0,0), (-1,-1), 9),
        ("GRID",           (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E0")),
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [colors.white, GRIS_CLARO]),
        ("PADDING",        (0,0), (-1,-1), 6),
    ]))
    elementos.append(tabla_ebr)
    elementos.append(Spacer(1, 12))

    # ── PIE LEGAL ──
    elementos.append(HRFlowable(width="100%", thickness=1, color=ORO, spaceBefore=16, spaceAfter=8))
    elementos.append(Paragraph(
        "Documento generado automaticamente por Compliance Lab · JRV Lab S.L. · "
        "Sujeto a revision del AML Officer antes de su uso oficial. · "
        "Ley 10/2010 de prevencion del blanqueo de capitales · AMLR 2024",
        estilo_nota
    ))

    doc.build(elementos)
    buffer.seek(0)
    return buffer.read()
