"""
COMPLIANCE LAB · Backend API · v1.0 · Multi-tenancy Modelo C
JRV Lab S.L. · 2026
"""

from fastapi import FastAPI, HTTPException, UploadFile, File, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from typing import Optional
from pathlib import Path
from dotenv import load_dotenv
from app.auth import hash_password, verificar_password, crear_token, get_current_user, require_supervisor, require_admin
import asyncpg
import os
import uuid
import shutil
from datetime import datetime
import sys

load_dotenv(Path(__file__).parent.parent / ".env")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://compliancelab:cl_password_2026@localhost:5432/compliancelab_db"
)

UPLOADS_DIR = Path(__file__).parent.parent.parent / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

FRONTEND_DIR = Path(__file__).parent.parent.parent / "frontend" / "public"

app = FastAPI(
    title="Compliance Lab API",
    description="Plataforma KYC/AML · Mejor del mercado · JRV Lab S.L.",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup():
    try:
        app.state.db = await asyncpg.create_pool(DATABASE_URL)
        print("✅ Conectado a PostgreSQL")
    except Exception as e:
        print(f"⚠️  Sin conexión a PostgreSQL: {e}")
        app.state.db = None

@app.on_event("shutdown")
async def shutdown():
    if app.state.db:
        await app.state.db.close()

async def get_db():
    if not app.state.db:
        raise HTTPException(503, "Base de datos no disponible. Ejecuta: docker-compose up -d")
    return app.state.db

class ExpedienteCrear(BaseModel):
    denominacion: str
    nif: Optional[str] = None
    tipo_entidad: str = "persona_juridica"
    notas: Optional[str] = None

# ── Helper: inyectar tenant en conexión PostgreSQL ────────
async def set_org_context(conn, organizacion_id: str):
    """Activa RLS para el tenant en esta conexión."""
    if organizacion_id:
        await conn.execute(f"SET app.current_org_id = '{organizacion_id}'")

# ══════════════════════════════════════════
# SISTEMA
# ══════════════════════════════════════════

@app.get("/", tags=["Sistema"])
async def raiz():
    return {
        "app": "Compliance Lab API",
        "version": "0.3.0-poc",
        "estado": "✅ Funcionando",
        "fase": "PoC · Fase 1",
        "empresa": "JRV Lab S.L.",
        "frontend": "http://127.0.0.1:8000/app",
        "docs": "http://127.0.0.1:8000/docs"
    }

@app.get("/health", tags=["Sistema"])
async def health():
    return {
        "api": "ok",
        "base_de_datos": "ok" if app.state.db else "sin conexion",
        "timestamp": datetime.now().isoformat()
    }

# ══════════════════════════════════════════
# EXPEDIENTES
# ══════════════════════════════════════════

@app.get("/expedientes", tags=["Expedientes"])
async def listar_expedientes(
    estado: Optional[str] = None,
    limite: int = 50,
    db=Depends(get_db),
    user=Depends(get_current_user)
):
    async with db.acquire() as conn:
        await set_org_context(conn, user["organizacion_id"])
        # AML Officer solo ve expedientes asignados a él
        # Supervisor y Admin ven todos los de la organización
        if user["rol"] == "aml_officer":
            officer_filter = "AND (e.asignado_a = $3::uuid OR e.asignado_a IS NULL)"
            params = [user["organizacion_id"], estado, user["id"], limite]
        else:
            officer_filter = ""
            params = [user["organizacion_id"], estado, limite]

        idx_limite = len(params)
        query = f"""
            SELECT e.id::text, e.codigo, e.denominacion, e.nif, e.estado,
                   e.estado_supervision, e.nivel_riesgo, e.score_ebr,
                   e.aml_officer_nombre, e.fecha_creacion,
                   COUNT(d.id) as num_documentos
            FROM expedientes e
            LEFT JOIN documentos d ON d.expediente_id = e.id
            WHERE e.organizacion_id = $1::uuid
            AND ($2::text IS NULL OR e.estado::text = $2::text)
            {officer_filter}
            GROUP BY e.id, e.codigo, e.denominacion, e.nif, e.estado,
                     e.estado_supervision, e.nivel_riesgo, e.score_ebr,
                     e.aml_officer_nombre, e.fecha_creacion
            ORDER BY e.fecha_creacion DESC LIMIT ${idx_limite}
        """
        filas = await conn.fetch(query, *params)
    return [dict(f) for f in filas]

@app.post("/expedientes", tags=["Expedientes"], status_code=201)
async def crear_expediente(
    datos: ExpedienteCrear,
    db=Depends(get_db),
    user=Depends(get_current_user)
):
    async with db.acquire() as conn:
        await set_org_context(conn, user["organizacion_id"])
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM expedientes WHERE organizacion_id=$1::uuid",
            user["organizacion_id"]
        )
        codigo = f"EXP-{datetime.now().year}-{str(total + 1).zfill(3)}"
        fila = await conn.fetchrow(
            """INSERT INTO expedientes
               (codigo, denominacion, nif, tipo_entidad, notas, organizacion_id,
                asignado_a, aml_officer_nombre, estado_supervision)
               VALUES ($1,$2,$3,$4,$5,$6::uuid,$7::uuid,$8,'borrador')
               RETURNING id::text, codigo, denominacion, estado, estado_supervision, fecha_creacion""",
            codigo, datos.denominacion, datos.nif, datos.tipo_entidad, datos.notas,
            user["organizacion_id"], user["id"], user.get("email", "")
        )
        await conn.execute(
            """INSERT INTO audit_trail
               (expediente_id, tipo_evento, descripcion, actor, hash_evento,
                numero_bloque, organizacion_id)
               VALUES ($1::uuid,'expediente_creado',$2,$3,$4,1,$5::uuid)""",
            fila['id'], f"Expediente creado: {datos.denominacion}",
            user["email"], str(uuid.uuid4()).replace('-','')[:64],
            user["organizacion_id"]
        )
    return {"mensaje": "✅ Expediente creado", "expediente": dict(fila)}

@app.get("/expedientes/{expediente_id}", tags=["Expedientes"])
async def obtener_expediente(expediente_id: str, db=Depends(get_db)):
    async with db.acquire() as conn:
        expediente = await conn.fetchrow(
            "SELECT * FROM expedientes WHERE id = $1::uuid", expediente_id)
        if not expediente:
            raise HTTPException(404, f"Expediente {expediente_id} no encontrado")
        documentos = await conn.fetch(
            """SELECT id::text, nombre_archivo, tipo_documento,
                      estado_procesamiento, confianza_clasificacion, fecha_subida
               FROM documentos WHERE expediente_id = $1::uuid
               ORDER BY fecha_subida DESC""", expediente_id)
        audit = await conn.fetch(
            """SELECT tipo_evento, descripcion, actor, fecha_evento
               FROM audit_trail WHERE expediente_id = $1::uuid
               ORDER BY fecha_evento DESC LIMIT 10""", expediente_id)
    exp = dict(expediente)
    exp['id'] = str(exp['id'])
    return {
        "expediente": exp,
        "documentos": [dict(d) for d in documentos],
        "num_documentos": len(documentos),
        "audit_trail": [dict(a) for a in audit]
    }

# ══════════════════════════════════════════
# DOCUMENTOS · LEGNER
# ══════════════════════════════════════════

@app.post("/expedientes/{expediente_id}/documentos", tags=["Documentos · LegNER"])
async def subir_documento(
    expediente_id: str,
    archivo: UploadFile = File(...),
    db=Depends(get_db)
):
    from app.services.legner_engine import procesar_documento_kyc

    if False:
        raise HTTPException(400, "Solo se aceptan archivos PDF")

    async with db.acquire() as conn:
        expediente = await conn.fetchrow(
            "SELECT id, denominacion FROM expedientes WHERE id = $1::uuid", expediente_id)
        if not expediente:
            raise HTTPException(404, f"Expediente {expediente_id} no encontrado")

    nombre_unico = f"{uuid.uuid4()}_{archivo.filename}"
    ruta_archivo = UPLOADS_DIR / nombre_unico
    with open(ruta_archivo, "wb") as f:
        shutil.copyfileobj(archivo.file, f)
    tamanio = os.path.getsize(ruta_archivo)

    async with db.acquire() as conn:
        doc = await conn.fetchrow(
            """INSERT INTO documentos
               (expediente_id, nombre_archivo, ruta_archivo, tamanio_bytes, formato, estado_procesamiento)
               VALUES ($1::uuid, $2, $3, $4, 'pdf', 'clasificando')
               RETURNING id::text""",
            expediente_id, archivo.filename, str(ruta_archivo), tamanio)
        documento_id = doc['id']

    try:
        resultado = procesar_documento_kyc(str(ruta_archivo))
        clasificacion = resultado['clasificacion']
        campos = resultado['campos_extraidos']

        async with db.acquire() as conn:
            await conn.execute(
                """UPDATE documentos SET tipo_documento=$1, confianza_clasificacion=$2,
                   estado_procesamiento=$3, requiere_revision_manual=$4, fecha_procesado=NOW()
                   WHERE id=$5::uuid""",
                clasificacion['tipo'], clasificacion['confianza'],
                'completado' if clasificacion['accion'] == 'procesar' else 'manual',
                clasificacion['accion'] == 'manual', documento_id)
            for campo in campos:
                if campo.get('valor') is not None:
                    await conn.execute(
                        """INSERT INTO campos_extraidos
                           (documento_id, expediente_id, nombre_campo, valor, tipo_campo, confianza)
                           VALUES ($1::uuid, $2::uuid, $3, $4, 'texto', $5)""",
                        documento_id, expediente_id,
                        campo['nombre'], str(campo['valor']), campo.get('confianza', 0))
            await conn.execute(
                """INSERT INTO audit_trail
                   (expediente_id, documento_id, tipo_evento, descripcion, actor, hash_evento, numero_bloque)
                   VALUES ($1::uuid, $2::uuid, 'documento_procesado', $3, 'sistema_legner', $4,
                           (SELECT COALESCE(MAX(numero_bloque),0)+1 FROM audit_trail WHERE expediente_id=$1::uuid))""",
                expediente_id, documento_id,
                f"LegNER: {clasificacion['nombre']} · {clasificacion['confianza']}%",
                str(uuid.uuid4()).replace('-', '')[:64])
            await conn.execute(
                """UPDATE expedientes SET estado='en_proceso', fecha_actualizacion=NOW()
                   WHERE id=$1::uuid AND estado='pendiente'""", expediente_id)

        campos_con_valor = [c for c in campos if c.get('valor') is not None]
        return {
            "mensaje": "✅ Documento procesado por LegNER",
            "documento_id": documento_id,
            "archivo": archivo.filename,
            "clasificacion": {
                "tipo": clasificacion['tipo'],
                "nombre": clasificacion['nombre'],
                "confianza": f"{clasificacion['confianza']}%",
                "justificacion": clasificacion['justificacion']
            },
            "extraccion": {
                "total_campos": len(campos),
                "campos_extraidos": len(campos_con_valor),
                "campos": campos_con_valor
            },
            "requiere_revision_manual": clasificacion['accion'] == 'manual'
        }
    except Exception as e:
        async with db.acquire() as conn:
            await conn.execute(
                "UPDATE documentos SET estado_procesamiento='error', motivo_revision=$1 WHERE id=$2::uuid",
                str(e), documento_id)
        raise HTTPException(500, f"Error procesando con LegNER: {e}")

@app.get("/expedientes/{expediente_id}/campos", tags=["Documentos · LegNER"])
async def ver_campos(expediente_id: str, db=Depends(get_db)):
    async with db.acquire() as conn:
        campos = await conn.fetch(
            """SELECT c.nombre_campo, c.valor, c.confianza,
                      c.revisado_manualmente, d.nombre_archivo, d.tipo_documento
               FROM campos_extraidos c
               JOIN documentos d ON d.id = c.documento_id
               WHERE c.expediente_id = $1::uuid
               ORDER BY d.fecha_subida, c.nombre_campo""", expediente_id)
    return {"expediente_id": expediente_id, "total_campos": len(campos), "campos": [dict(c) for c in campos]}


def _parsear_fecha_es(valor: str):
    """Convierte 'DD/MM/YYYY' a un objeto date ordenable. Devuelve None si no se puede parsear."""
    if not valor:
        return None
    from datetime import datetime
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(valor.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


# Campos que representan la fecha "oficial" del propio documento (no de subida).
# Orden de prioridad cuando varios coexisten en el mismo documento:
# una diligencia de subsanación posterior prevalece sobre la fecha de inscripción,
# que a su vez prevalece sobre la fecha de la escritura original.
_CAMPOS_FECHA_DOCUMENTO = [
    "fecha_diligencia_subsanacion",
    "fecha_inscripcion_registro",
    "fecha_escritura",
    "fecha_documento",
]
_PRIORIDAD_FECHA = {nombre: i for i, nombre in enumerate(_CAMPOS_FECHA_DOCUMENTO)}


@app.get("/expedientes/{expediente_id}/campos-consolidados", tags=["Documentos · LegNER"])
async def ver_campos_consolidados(expediente_id: str, db=Depends(get_db)):
    """
    Consolida los campos extraídos de TODOS los documentos del expediente.
    Para cada nombre de campo (ej: capital_social), determina el valor VIGENTE
    como el de la escritura con fecha de documento más reciente, y conserva
    el historial completo ordenado cronológicamente.

    Esto resuelve el caso de ampliaciones de capital, cambios de administrador,
    cambios de domicilio, etc. donde varios documentos aportan valores distintos
    para el mismo campo a lo largo del tiempo.
    """
    async with db.acquire() as conn:
        filas = await conn.fetch(
            """SELECT c.id, c.nombre_campo, c.valor, c.confianza, c.documento_id,
                      d.nombre_archivo, d.tipo_documento, d.fecha_subida
               FROM campos_extraidos c
               JOIN documentos d ON d.id = c.documento_id
               WHERE c.expediente_id = $1::uuid
               ORDER BY d.fecha_subida""", expediente_id)

    if not filas:
        return {"expediente_id": expediente_id, "campos_consolidados": []}

    # 1 — Para cada documento, averiguar su "fecha real" (la de la escritura/diligencia, no la de subida)
    fecha_real_por_doc = {}
    prioridad_actual_por_doc = {}
    for f in filas:
        if f["nombre_campo"] in _CAMPOS_FECHA_DOCUMENTO:
            fecha_parseada = _parsear_fecha_es(f["valor"])
            if not fecha_parseada:
                continue
            prioridad_campo = _PRIORIDAD_FECHA[f["nombre_campo"]]
            prioridad_previa = prioridad_actual_por_doc.get(f["documento_id"])
            # Menor número = mayor prioridad (ej: diligencia de subsanación gana a fecha_escritura)
            if prioridad_previa is None or prioridad_campo < prioridad_previa:
                fecha_real_por_doc[f["documento_id"]] = fecha_parseada
                prioridad_actual_por_doc[f["documento_id"]] = prioridad_campo

    # 2 — Agrupar todos los campos por nombre_campo
    from collections import defaultdict
    grupos = defaultdict(list)
    for f in filas:
        fecha_doc = fecha_real_por_doc.get(f["documento_id"])
        grupos[f["nombre_campo"]].append({
            "valor": f["valor"],
            "confianza": float(f["confianza"]) if f["confianza"] is not None else None,
            "documento_id": str(f["documento_id"]),
            "nombre_archivo": f["nombre_archivo"],
            "tipo_documento": f["tipo_documento"],
            "fecha_documento": fecha_doc.isoformat() if fecha_doc else None,
            "fecha_subida": f["fecha_subida"].isoformat() if f["fecha_subida"] else None,
        })

    # 3 — Para cada campo, ordenar el historial cronológicamente y determinar el vigente
    campos_consolidados = []
    for nombre_campo, historial in grupos.items():
        # Solo entran en el cálculo de "vigente" las entradas con valor real
        con_valor = [h for h in historial if h["valor"] not in (None, "null", "")]
        if not con_valor:
            continue

        # Orden cronológico: primero por fecha_documento (si existe), luego por fecha_subida
        # como criterio de desempate (resuelve el caso de dos documentos con la misma
        # fecha_escritura, donde el subido más tarde es la versión corregida/vigente)
        def _clave_orden(h):
            if h["fecha_documento"]:
                return (0, h["fecha_documento"], h["fecha_subida"] or "")
            return (1, "", h["fecha_subida"] or "")

        historial_ordenado = sorted(con_valor, key=_clave_orden)
        vigente = historial_ordenado[-1]  # el más reciente

        # Solo se considera "cambiante" si hay más de un valor distinto en el historial
        valores_distintos = {h["valor"] for h in historial_ordenado}
        tiene_historial = len(valores_distintos) > 1

        campos_consolidados.append({
            "nombre_campo": nombre_campo,
            "valor_vigente": vigente["valor"],
            "confianza_vigente": vigente["confianza"],
            "fecha_vigente": vigente["fecha_documento"] or vigente["fecha_subida"],
            "origen_documento": vigente["nombre_archivo"],
            "tiene_historial": tiene_historial,
            "historial": historial_ordenado if tiene_historial else []
        })

    # Orden alfabético por nombre de campo para presentación estable
    campos_consolidados.sort(key=lambda c: c["nombre_campo"])

    return {
        "expediente_id": expediente_id,
        "total_campos": len(campos_consolidados),
        "campos_con_historial": len([c for c in campos_consolidados if c["tiene_historial"]]),
        "campos_consolidados": campos_consolidados
    }

# ══════════════════════════════════════════
# CORRECCIÓN MANUAL DE CAMPOS + VISOR HTML
# ══════════════════════════════════════════

class CampoCorreccion(BaseModel):
    valor: str
    motivo: str = "Corrección manual AML Officer"

@app.patch("/expedientes/{expediente_id}/campos/{nombre_campo}", tags=["Documentos · LegNER"])
async def corregir_campo(expediente_id: str, nombre_campo: str, body: CampoCorreccion, db=Depends(get_db)):
    """Corrige manualmente el valor vigente de un campo KYC y lo registra en audit_trail."""
    async with db.acquire() as conn:
        campo = await conn.fetchrow(
            """SELECT ce.id, ce.valor, ce.documento_id
               FROM campos_extraidos ce
               JOIN documentos d ON d.id = ce.documento_id
               WHERE ce.expediente_id = $1::uuid AND ce.nombre_campo = $2
               ORDER BY d.fecha_subida DESC LIMIT 1""",
            expediente_id, nombre_campo)
        if not campo:
            raise HTTPException(404, f"Campo '{nombre_campo}' no encontrado")
        valor_anterior = campo["valor"]
        await conn.execute(
            "UPDATE campos_extraidos SET valor=$1, revisado_manualmente=true, confianza=99 WHERE id=$2",
            body.valor, campo["id"])
        await conn.execute(
            """INSERT INTO audit_trail
               (expediente_id, documento_id, tipo_evento, descripcion, actor, hash_evento, numero_bloque)
               VALUES ($1::uuid, $2::uuid, 'correccion_manual', $3, 'aml_officer', $4,
                       (SELECT COALESCE(MAX(numero_bloque),0)+1 FROM audit_trail WHERE expediente_id=$1::uuid))""",
            expediente_id, campo["documento_id"],
            f"Campo '{nombre_campo}': '{valor_anterior}' → '{body.valor}' · {body.motivo}",
            str(uuid.uuid4()).replace("-", "")[:64])
    return {"mensaje": f"✅ Campo '{nombre_campo}' corregido", "valor_anterior": valor_anterior, "valor_nuevo": body.valor}


@app.get("/expedientes/{expediente_id}/campos/{nombre_campo}/visor", tags=["Documentos · LegNER"])
async def visor_campo(expediente_id: str, nombre_campo: str, db=Depends(get_db)):
    """Muestra campos del documento fuente con resaltado. Usa PDF si existe, sino campos de BD."""
    import re, html as html_lib, os
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT ce.valor, d.id as doc_id, d.ruta_archivo, d.nombre_archivo, d.tipo_documento
               FROM campos_extraidos ce
               JOIN documentos d ON d.id = ce.documento_id
               WHERE ce.expediente_id = $1::uuid AND ce.nombre_campo = $2
               ORDER BY d.fecha_subida DESC LIMIT 1""",
            expediente_id, nombre_campo)
        if not row:
            raise HTTPException(404, f"Campo '{nombre_campo}' no encontrado")
        todos_campos = await conn.fetch(
            "SELECT nombre_campo, valor, confianza, revisado_manualmente FROM campos_extraidos WHERE documento_id = $1::uuid ORDER BY nombre_campo",
            row["doc_id"])

    valor_actual = str(row["valor"] or "")
    terminos_map = {
        "capital_social": ["capital social", "cifra capital", "articulo 6", "se fija en"],
        "capital_social_anterior": ["capital social", "capital anterior"],
        "domicilio_social": ["domicilio", "calle", "avenida", "plaza"],
        "denominacion_social": ["denominada", "denominacion", "mercantil"],
        "nif_cif": ["nif", "cif", "b88"],
        "fecha_escritura": ["escritura", "otorgada", "autorizada"],
        "fecha_inscripcion_registro": ["inscrita", "inscripcion", "registro mercantil"],
        "fecha_diligencia_subsanacion": ["diligencia", "subsanan", "errores"],
    }
    terminos = terminos_map.get(nombre_campo, [nombre_campo.replace("_", " ")])

    def resaltar(t):
        if valor_actual and len(valor_actual) > 2:
            t = re.sub(f"({re.escape(valor_actual)})",
                r'<mark style="background:#fde68a;color:#92400e;font-weight:700;border-radius:2px">\1</mark>',
                t, flags=re.IGNORECASE)
        for term in terminos:
            t = re.sub(f"({re.escape(term)})",
                r'<mark style="background:#bfdbfe;color:#1e3a8a;border-radius:2px">\1</mark>',
                t, flags=re.IGNORECASE)
        return t

    texto_pdf = None
    try:
        if os.path.exists(row["ruta_archivo"]):
            from app.services.legner_engine import extraer_texto_pdf
            texto_pdf = extraer_texto_pdf(row["ruta_archivo"])
    except Exception:
        texto_pdf = None

    if texto_pdf:
        parrafos = [f'<p style="margin-bottom:6px;line-height:1.6">{ln.strip()}</p>'
                    for ln in resaltar(html_lib.escape(texto_pdf)).split("\n") if ln.strip()]
        cuerpo = "".join(parrafos)
        modo = "Texto completo del documento"
    else:
        filas = []
        for c in todos_campos:
            es = c["nombre_campo"] == nombre_campo
            bg = "#fef3c7" if es else "#fff"
            fw = "700" if es else "400"
            rev = " ✓" if c["revisado_manualmente"] else ""
            val = resaltar(html_lib.escape(str(c["valor"] or "")))
            nm = html_lib.escape(c["nombre_campo"].replace("_", " "))
            filas.append(f'<tr style="background:{bg}"><td style="padding:6px 10px;border-bottom:1px solid #f3f4f6;font-weight:{fw}">{nm}{rev}</td><td style="padding:6px 10px;border-bottom:1px solid #f3f4f6">{val}</td><td style="padding:6px 10px;border-bottom:1px solid #f3f4f6;color:#9ca3af;font-size:11px">{c["confianza"]}%</td></tr>')
        cuerpo = ('<div style="background:#fff3cd;border:1px solid #ffc107;border-radius:6px;padding:10px 14px;margin-bottom:16px;font-size:12px;color:#856404">⚠️ PDF no disponible. Se muestran los <strong>campos extraídos por LegNER</strong>. El campo que revisas aparece en amarillo.</div>'
            + '<table style="width:100%;border-collapse:collapse;font-size:13px"><thead><tr style="background:#f8fafc"><th style="padding:8px 10px;text-align:left;border-bottom:2px solid #e5e7eb;color:#6b7280;font-size:11px">CAMPO</th><th style="padding:8px 10px;text-align:left;border-bottom:2px solid #e5e7eb;color:#6b7280;font-size:11px">VALOR</th><th style="padding:8px 10px;text-align:left;border-bottom:2px solid #e5e7eb;color:#6b7280;font-size:11px">CONF.</th></tr></thead><tbody>'
            + "".join(filas) + '</tbody></table>')
        modo = "Campos extraídos por LegNER (PDF no disponible)"

    h = html_lib.escape
    html_visor = (f'<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"><style>body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:13px;color:#1a1a2e;max-width:960px;margin:0 auto;padding:20px;background:#fff}}.hdr{{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:12px 16px;margin-bottom:12px;font-size:12px;color:#6b7280}}.hdr strong{{color:#1a1a2e}}mark{{padding:1px 3px;border-radius:2px}}</style></head><body>'
        + f'<div class="hdr"><strong>{h(row["nombre_archivo"])}</strong> · Tipo: {h(row["tipo_documento"] or "desconocido")} · Campo: <strong>{nombre_campo.replace("_"," ")}</strong> · Valor actual: <strong style="color:#0F6E56">{h(valor_actual)}</strong><br><span style="font-size:10px;margin-top:4px;display:inline-block">📋 {modo}</span></div>'
        + '<div style="display:flex;gap:16px;margin-bottom:14px;font-size:11px;color:#6b7280"><span>🟡 <mark style="background:#fde68a;color:#92400e">Valor actual</mark></span><span>🔵 <mark style="background:#bfdbfe;color:#1e3a8a">Términos relacionados</mark></span></div>'
        + cuerpo + '</body></html>')

    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=html_visor)


# ══════════════════════════════════════════
# SCORING EBR · PASO 7 ⭐ NUEVO
# ══════════════════════════════════════════

@app.post("/expedientes/{expediente_id}/scoring", tags=["Scoring EBR · AML"])
async def calcular_scoring(expediente_id: str, db=Depends(get_db)):
    """
    Calcula el scoring AML/EBR v2.0 completo del expediente:
    - Consulta OpenMercantil (BORME) si hay NIF disponible
    - 5 dimensiones de riesgo con BORME al 25%
    - Score inherente y residual
    - Nivel de riesgo (bajo/medio/alto/muy_alto)
    - Alerta SAR si score >= 70 (Art. 18 Ley 10/2010)
    - Guarda resultado completo incluyendo datos BORME en base de datos
    """
    import json as _json
    from app.services.ebr_engine import calcular_ebr
    from app.services.mercantil_engine import consultar_borme as buscar_empresa_borme

    # 1 — Obtener expediente y campos
    async with db.acquire() as conn:
        expediente = await conn.fetchrow(
            "SELECT * FROM expedientes WHERE id = $1::uuid", expediente_id)
        if not expediente:
            raise HTTPException(404, f"Expediente {expediente_id} no encontrado")
        campos = await conn.fetch(
            """SELECT nombre_campo, valor, confianza
               FROM campos_extraidos WHERE expediente_id = $1::uuid""",
            expediente_id)

    if not campos:
        raise HTTPException(400, "No hay campos extraídos. Sube y procesa documentos primero.")

    campos_lista = [dict(c) for c in campos]
    nif = expediente['nif']

    # 2 — Consultar BORME via OpenMercantil (si hay NIF)
    datos_borme = None
    senales_borme = []
    if nif:
        try:
            datos_borme = await buscar_empresa_borme(nif)
            if datos_borme and datos_borme.get("encontrado"):
                senales_borme = datos_borme.get("senales_riesgo", [])
        except Exception as e:
            print(f"⚠️ OpenMercantil no disponible: {e}")
            datos_borme = None

    # 3 — Calcular EBR v2.0
    resultado = calcular_ebr(
        expediente_id=expediente_id,
        campos=campos_lista,
        denominacion=expediente['denominacion'],
        nif=nif,
        datos_borme=datos_borme
    )
    r    = resultado['resultado']
    dims = resultado['dimensiones']

    # 4 — Guardar en base de datos
    async with db.acquire() as conn:
        # INSERT inicial (compatibilidad con registros anteriores)
        await conn.execute(
            """INSERT INTO scoring_aml
               (expediente_id, riesgo_cliente, riesgo_geografico, riesgo_producto,
                riesgo_canal, score_inherente, controles_aplicados, score_residual,
                nivel_riesgo, umbral_sar)
               VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10)
               ON CONFLICT DO NOTHING""",
            expediente_id,
            dims['factor_cliente']['score'],
            dims['factor_geografico']['score'],
            dims['producto_canal']['score'],
            dims['adverse_media']['score'],
            r['score_inherente'],
            r['controles_aplicados'],
            r['score_residual'],
            r['nivel_riesgo'],
            r['umbral_sar']
        )
        # UPDATE completo EBR v2.0 con BORME
        await conn.execute(
            """UPDATE scoring_aml SET
               riesgo_cliente       = $2,
               riesgo_geografico    = $3,
               riesgo_producto      = $4,
               riesgo_canal         = $5,
               riesgo_adverse_media = $6,
               score_inherente      = $7,
               controles_aplicados  = $8,
               score_residual       = $9,
               nivel_riesgo         = $10,
               umbral_sar           = $11,
               datos_borme          = $12,
               senales_borme        = $13,
               borme_consultado_en  = NOW(),
               fecha_actualizacion  = NOW()
               WHERE expediente_id  = $1::uuid""",
            expediente_id,
            dims['factor_cliente']['score'],
            dims['factor_geografico']['score'],
            dims['producto_canal']['score'],
            dims['adverse_media']['score'],
            dims['adverse_media']['score'],
            r['score_inherente'],
            r['controles_aplicados'],
            r['score_residual'],
            r['nivel_riesgo'],
            r['umbral_sar'],
            _json.dumps(datos_borme) if datos_borme else None,
            senales_borme if senales_borme else None
        )
        # Actualizar expediente
        await conn.execute(
            """UPDATE expedientes SET
               score_ebr=$1, nivel_riesgo=$2, fecha_actualizacion=NOW()
               WHERE id=$3::uuid""",
            r['score_residual'], r['nivel_riesgo'], expediente_id)
        # Audit trail
        metodo = "EBR-v2-BORME" if datos_borme and datos_borme.get("encontrado") else "EBR-v1-BASE"
        await conn.execute(
            """INSERT INTO audit_trail
               (expediente_id, tipo_evento, descripcion, actor, hash_evento, numero_bloque)
               VALUES ($1::uuid, 'scoring_calculado', $2, 'motor_ebr', $3,
                       (SELECT COALESCE(MAX(numero_bloque),0)+1
                        FROM audit_trail WHERE expediente_id=$1::uuid))""",
            expediente_id,
            f"EBR: score {r['score_residual']}/100 · nivel {r['nivel_riesgo']} · SAR: {r['umbral_sar']} · método: {metodo}",
            str(uuid.uuid4()).replace('-', '')[:64]
        )

    return {
        "mensaje": "✅ Scoring EBR v2.0 calculado",
        "expediente": expediente['denominacion'],
        "borme_consultado": datos_borme is not None,
        "borme_encontrado": datos_borme.get("encontrado", False) if datos_borme else False,
        "scoring": resultado
    }

@app.get("/expedientes/{expediente_id}/scoring", tags=["Scoring EBR · AML"])
async def ver_scoring(expediente_id: str, db=Depends(get_db)):
    """Devuelve el último scoring calculado para el expediente."""
    async with db.acquire() as conn:
        scoring = await conn.fetchrow(
            """SELECT * FROM scoring_aml WHERE expediente_id=$1::uuid
               ORDER BY fecha_calculo DESC LIMIT 1""", expediente_id)
    if not scoring:
        raise HTTPException(404, "Sin scoring calculado. Ejecuta POST /scoring primero.")
    return dict(scoring)

# ══════════════════════════════════════════
# AUDIT TRAIL · STATS
# ══════════════════════════════════════════

@app.get("/expedientes/{expediente_id}/audit", tags=["Blockchain · Audit Trail"])
async def ver_audit(expediente_id: str, db=Depends(get_db)):
    async with db.acquire() as conn:
        eventos = await conn.fetch(
            """SELECT tipo_evento, descripcion, actor,
                      hash_evento, numero_bloque, fecha_evento
               FROM audit_trail WHERE expediente_id=$1::uuid
               ORDER BY numero_bloque ASC""", expediente_id)
    return {"expediente_id": expediente_id, "total_eventos": len(eventos), "eventos": [dict(e) for e in eventos]}

@app.get("/stats", tags=["Dashboard"])
async def estadisticas(db=Depends(get_db)):
    async with db.acquire() as conn:
        stats = await conn.fetchrow("""
            SELECT COUNT(*) as total_expedientes,
                   COUNT(*) FILTER (WHERE estado='pendiente') as pendientes,
                   COUNT(*) FILTER (WHERE estado='en_proceso') as en_proceso,
                   COUNT(*) FILTER (WHERE estado='aprobado') as aprobados,
                   COUNT(*) FILTER (WHERE nivel_riesgo='alto') as riesgo_alto
            FROM expedientes""")
        total_docs = await conn.fetchval("SELECT COUNT(*) FROM documentos")
        total_campos = await conn.fetchval("SELECT COUNT(*) FROM campos_extraidos")
        total_scoring = await conn.fetchval("SELECT COUNT(*) FROM scoring_aml")
    return {
        "expedientes": dict(stats),
        "total_documentos": total_docs,
        "total_campos_extraidos": total_campos,
        "total_scorings": total_scoring,
        "fase": "PoC · Fase 1",
        "precision_legner": "98%"
    }

# ══════════════════════════════════════════
# FRONTEND
# ══════════════════════════════════════════

FRONTEND_HTML = ""

@app.get("/app", tags=["Frontend"])
async def frontend():
    from fastapi.responses import HTMLResponse
    with open(os.path.join(os.path.dirname(__file__), "..", "frontend.html"), "r", encoding="utf-8") as f: html = f.read()
    return HTMLResponse(html)

# ══════════════════════════════════════════
# GENERACIÓN SAR · PASO 8 ⭐ NUEVO
# ══════════════════════════════════════════

@app.post("/expedientes/{expediente_id}/sar", tags=["SAR · Art. 18 Ley 10/2010"])
async def generar_sar(expediente_id: str, db=Depends(get_db)):
    """
    ⭐ NUEVO EN PASO 8 ⭐

    Genera automáticamente el borrador SAR conforme al Art. 18 Ley 10/2010:
    - Referencia interna SAR-YYYY-NNNNN
    - Tipología delictiva 6AMLD
    - Descripción narrativa de la operativa sospechosa (generada por Claude)
    - Hash blockchain del expediente
    - Lista de verificación 7 puntos
    - Plazo máximo: 10 días hábiles
    """
    from app.services.sar_engine import generar_sar as _generar_sar

    async with db.acquire() as conn:
        expediente = await conn.fetchrow(
            "SELECT * FROM expedientes WHERE id = $1::uuid", expediente_id)
        if not expediente:
            raise HTTPException(404, f"Expediente {expediente_id} no encontrado")

        campos = await conn.fetch(
            "SELECT nombre_campo, valor, confianza FROM campos_extraidos WHERE expediente_id = $1::uuid",
            expediente_id)

        scoring = await conn.fetchrow(
            "SELECT * FROM scoring_aml WHERE expediente_id = $1::uuid ORDER BY fecha_calculo DESC LIMIT 1",
            expediente_id)

    if not campos:
        raise HTTPException(400, "Sin campos extraídos. Sube documentos primero.")
    if not scoring:
        raise HTTPException(400, "Sin scoring calculado. Ejecuta POST /scoring primero.")

    campos_lista = [dict(c) for c in campos]
    scoring_dict = dict(scoring)

    sar = _generar_sar(
        expediente_id=expediente_id,
        expediente=dict(expediente),
        campos=campos_lista,
        scoring=scoring_dict
    )

    # Registrar en audit trail
    async with db.acquire() as conn:
        await conn.execute(
            """INSERT INTO audit_trail
               (expediente_id, tipo_evento, descripcion, actor, hash_evento, numero_bloque)
               VALUES ($1::uuid, 'sar_generado', $2, 'sistema_legner', $3,
                       (SELECT COALESCE(MAX(numero_bloque),0)+1
                        FROM audit_trail WHERE expediente_id=$1::uuid))""",
            expediente_id,
            f"SAR generado: {sar['referencia']} · Art. 18 Ley 10/2010",
            str(uuid.uuid4()).replace('-', '')[:64]
        )

    return {
        "mensaje": f"✅ Borrador SAR generado: {sar['referencia']}",
        "sar": sar
    }


# ── AGENTE KYC ────────────────────────────────────────────
from app.agent_loop import ejecutar_agente

class AgenteRequest(BaseModel):
    expediente_id: str
    instruccion: str = "Realiza el análisis KYC completo"

@app.post("/api/agente/analizar")
async def agente_analizar(req: AgenteRequest):
    prompt = (
        f"{req.instruccion} del expediente con ID {req.expediente_id}. "
        f"Consulta primero los documentos disponibles, clasifica cada uno, "
        f"calcula el scoring EBR y entrega un resumen con recomendación."
    )
    try:
        resultado = await ejecutar_agente(prompt, app.state.db)
        return {"ok": True, "data": resultado}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ── AUTENTICACIÓN JWT ─────────────────────────────────────
from fastapi.security import OAuth2PasswordRequestForm

@app.post("/auth/login", tags=["Auth"])
async def login(form: OAuth2PasswordRequestForm = Depends(), db=Depends(get_db)):
    async with db.acquire() as conn:
        usuario = await conn.fetchrow(
            """SELECT u.id, u.email, u.nombre, u.rol, u.hash_password,
                      u.organizacion_id, u.bloqueado, u.intentos_fallidos,
                      o.nombre as org_nombre, o.tipo_sujeto_obligado as org_tipo
               FROM usuarios u
               JOIN organizaciones o ON o.id = u.organizacion_id
               WHERE u.email = $1 AND u.activo = true""",
            form.username
        )
        if not usuario:
            raise HTTPException(401, "Email o contraseña incorrectos")
        if usuario["bloqueado"]:
            raise HTTPException(403, "Cuenta bloqueada. Contacta con tu administrador.")
        if not verificar_password(form.password, usuario["hash_password"] or ""):
            # Incrementar intentos fallidos
            await conn.execute(
                """UPDATE usuarios SET intentos_fallidos = intentos_fallidos + 1,
                   bloqueado = (intentos_fallidos + 1 >= 5)
                   WHERE id = $1""", usuario["id"])
            raise HTTPException(401, "Email o contraseña incorrectos")
        # Reset intentos y actualizar último acceso
        await conn.execute(
            "UPDATE usuarios SET intentos_fallidos=0, ultimo_acceso=NOW() WHERE id=$1",
            usuario["id"])

    token = crear_token({
        "sub":             usuario["email"],
        "rol":             usuario["rol"],
        "id":              str(usuario["id"]),
        "nombre":          usuario["nombre"],
        "organizacion_id": str(usuario["organizacion_id"]),
        "org_nombre":      usuario["org_nombre"],
        "org_tipo":        usuario["org_tipo"],
    })
    return {
        "access_token":    token,
        "token_type":      "bearer",
        "rol":             usuario["rol"],
        "nombre":          usuario["nombre"],
        "organizacion_id": str(usuario["organizacion_id"]),
        "org_nombre":      usuario["org_nombre"],
        "org_tipo":        usuario["org_tipo"],
    }

@app.get("/auth/me", tags=["Auth"])
async def me(user=Depends(get_current_user)):
    return user

# ── GESTIÓN DE ORGANIZACIONES (solo admin) ────────────────

@app.get("/api/organizaciones", tags=["Admin · Organizaciones"])
async def listar_organizaciones(db=Depends(get_db), user=Depends(require_admin)):
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """SELECT o.*, COUNT(u.id) as num_usuarios, COUNT(e.id) as num_expedientes
               FROM organizaciones o
               LEFT JOIN usuarios u ON u.organizacion_id = o.id
               LEFT JOIN expedientes e ON e.organizacion_id = o.id
               GROUP BY o.id ORDER BY o.fecha_creacion DESC"""
        )
    return [dict(r) for r in rows]

class OrganizacionCrear(BaseModel):
    nombre: str
    cif: Optional[str] = None
    tipo_sujeto_obligado: str
    plan: str = "poc"
    dominio_email: Optional[str] = None
    umbral_riesgo_bajo: int = 30
    umbral_riesgo_medio: int = 60
    umbral_riesgo_alto: int = 80
    plazo_revision_expediente: int = 30
    plazo_aprobacion_supervisor: int = 5
    plazo_rescreening_dias: int = 30

@app.post("/api/organizaciones", tags=["Admin · Organizaciones"], status_code=201)
async def crear_organizacion(datos: OrganizacionCrear, db=Depends(get_db), user=Depends(require_admin)):
    async with db.acquire() as conn:
        org = await conn.fetchrow(
            """INSERT INTO organizaciones
               (nombre, cif, tipo_sujeto_obligado, plan, dominio_email,
                umbral_riesgo_bajo, umbral_riesgo_medio, umbral_riesgo_alto,
                plazo_revision_expediente, plazo_aprobacion_supervisor, plazo_rescreening_dias)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
               RETURNING id, nombre, tipo_sujeto_obligado, plan""",
            datos.nombre, datos.cif, datos.tipo_sujeto_obligado, datos.plan, datos.dominio_email,
            datos.umbral_riesgo_bajo, datos.umbral_riesgo_medio, datos.umbral_riesgo_alto,
            datos.plazo_revision_expediente, datos.plazo_aprobacion_supervisor, datos.plazo_rescreening_dias
        )
        # Insertar configuraciones por defecto
        await conn.execute(
            """INSERT INTO configuracion_organizacion (organizacion_id, clave, valor, descripcion)
               VALUES ($1,'rescreening_activo','true','Rescreening automático diario'),
                      ($1,'documentos_minimos','2','Documentos mínimos por expediente'),
                      ($1,'confianza_minima_kyc','70','Confianza mínima LegNER sin revisión')""",
            org["id"])
    return {"mensaje": "✅ Organización creada", "organizacion": dict(org)}

# ── GESTIÓN DE USUARIOS (admin de cada organización) ──────

class UsuarioInvitar(BaseModel):
    email: str
    nombre: str
    rol: str = "aml_officer"
    password: str

@app.post("/api/usuarios/invitar", tags=["Admin · Usuarios"], status_code=201)
async def invitar_usuario(datos: UsuarioInvitar, db=Depends(get_db), user=Depends(require_admin)):
    if datos.rol not in ["admin", "supervisor", "aml_officer"]:
        raise HTTPException(400, "Rol inválido. Usa: admin | supervisor | aml_officer")
    async with db.acquire() as conn:
        existe = await conn.fetchrow("SELECT id FROM usuarios WHERE email=$1", datos.email)
        if existe:
            raise HTTPException(400, "Email ya registrado")
        nuevo = await conn.fetchrow(
            """INSERT INTO usuarios (email, nombre, rol, organizacion_id, hash_password, activo)
               VALUES ($1,$2,$3,$4::uuid,$5,true)
               RETURNING id, email, nombre, rol""",
            datos.email, datos.nombre, datos.rol,
            user["organizacion_id"], hash_password(datos.password)
        )
        await conn.execute(
            """INSERT INTO audit_trail (tipo_evento, descripcion, actor, hash_evento,
               numero_bloque, organizacion_id)
               VALUES ('usuario_creado',$1,$2,$3,1,$4::uuid)""",
            f"Usuario {datos.email} ({datos.rol}) creado por {user['email']}",
            user["email"], str(uuid.uuid4()).replace('-','')[:64], user["organizacion_id"])
    return {"mensaje": f"✅ Usuario {datos.email} creado", "usuario": dict(nuevo)}

@app.get("/api/usuarios", tags=["Admin · Usuarios"])
async def listar_usuarios(db=Depends(get_db), user=Depends(require_admin)):
    async with db.acquire() as conn:
        await set_org_context(conn, user["organizacion_id"])
        rows = await conn.fetch(
            """SELECT id, email, nombre, rol, activo, ultimo_acceso,
                      bloqueado, intentos_fallidos, fecha_creacion
               FROM usuarios WHERE organizacion_id=$1::uuid ORDER BY rol, nombre""",
            user["organizacion_id"])
    return [dict(r) for r in rows]

# ── LISTAS DE SANCIONES ───────────────────────────────────
from app.services.sanctions_engine import consultar_sanciones

@app.get("/api/sanciones/consultar", tags=["Sanciones · Art. 9 Ley 10/2010"])
async def consultar_sanciones_endpoint(nombre: str, nif: str = None):
    resultado = await consultar_sanciones(nombre, nif)
    return resultado

@app.get("/api/sanciones/expediente/{expediente_id}", tags=["Sanciones · Art. 9 Ley 10/2010"])
async def sanciones_expediente(expediente_id: str, db=Depends(get_db)):
    async with db.acquire() as conn:
        exp = await conn.fetchrow(
            "SELECT denominacion, nif FROM expedientes WHERE id=$1",
            expediente_id
        )
    if not exp:
        raise HTTPException(404, "Expediente no encontrado")
    resultado = await consultar_sanciones(exp["denominacion"], exp["nif"])
    return resultado

# ── EXPORTACIÓN PDF ───────────────────────────────────────
from app.services.pdf_engine import generar_pdf_kyc
from fastapi.responses import Response

@app.get("/api/expedientes/{expediente_id}/pdf", tags=["Informes PDF"])
async def exportar_pdf(expediente_id: str, db=Depends(get_db)):
    async with db.acquire() as conn:
        expediente = await conn.fetchrow(
            "SELECT * FROM expedientes WHERE id=$1", expediente_id
        )
        campos = await conn.fetch(
            "SELECT nombre_campo, valor, confianza FROM campos_extraidos WHERE expediente_id=$1",
            expediente_id
        )
        scoring = await conn.fetchrow(
            "SELECT * FROM scoring_aml WHERE expediente_id=$1 ORDER BY fecha_calculo DESC LIMIT 1",
            expediente_id
        )

    if not expediente:
        raise HTTPException(404, "Expediente no encontrado")

    campos_lista  = [dict(c) for c in campos]
    scoring_dict  = dict(scoring) if scoring else {}
    exp_dict      = dict(expediente)

    pdf_bytes = generar_pdf_kyc(exp_dict, campos_lista, scoring_dict)

    nombre_archivo = f"KYC_{exp_dict.get('denominacion','expediente').replace(' ','_')}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={nombre_archivo}"}
    )


# ── GESTIÓN DE USUARIOS ────────────────────────────────────
from pydantic import BaseModel as PydanticBase, EmailStr
from typing import Optional

class UsuarioCreate(PydanticBase):
    email: str
    nombre: str
    apellidos: Optional[str] = None
    cargo: Optional[str] = None
    rol: str = "aml_officer"
    password: str

class UsuarioUpdate(PydanticBase):
    nombre: Optional[str] = None
    apellidos: Optional[str] = None
    cargo: Optional[str] = None
    rol: Optional[str] = None
    activo: Optional[bool] = None
    password: Optional[str] = None

@app.get("/api/usuarios", tags=["Gestión de Usuarios"])
async def listar_usuarios(db=Depends(get_db)):
    import asyncpg as _asyncpg
    _conn = await _asyncpg.connect(os.getenv("DATABASE_URL_ADMIN", "postgresql://compliancelab:cl_app_2026@localhost:5432/compliancelab_db"))
    try:
        rows = await _conn.fetch("SELECT id, email, nombre, apellidos, cargo, rol, activo, fecha_creacion, ultimo_acceso FROM usuarios ORDER BY fecha_creacion DESC")
    finally:
        await _conn.close()
    return [dict(r) for r in rows]

@app.post("/api/usuarios", tags=["Gestión de Usuarios"])
async def crear_usuario(data: UsuarioCreate, db=Depends(get_db)):
    from app.auth import hash_password
    pwd_hash = hash_password(data.password)
    import asyncpg as _pg2
    _c2 = await _pg2.connect(os.getenv("DATABASE_URL_ADMIN", "postgresql://compliancelab:cl_app_2026@localhost:5432/compliancelab_db"))
    try:
        existing = await _c2.fetchrow("SELECT id FROM usuarios WHERE email=$1", data.email)
        if existing:
            raise HTTPException(status_code=400, detail="Email ya registrado")
        row = await _c2.fetchrow("INSERT INTO usuarios (email, nombre, apellidos, cargo, rol, password_hash, activo) VALUES ($1, $2, $3, $4, $5::rolusuario, $6, true) RETURNING id, email, nombre, rol, activo, fecha_creacion", data.email, data.nombre, data.apellidos, data.cargo, data.rol, pwd_hash)
    finally:
        await _c2.close()
    return dict(row)

@app.put("/api/usuarios/{usuario_id}", tags=["Gestión de Usuarios"])
async def actualizar_usuario(usuario_id: str, data: UsuarioUpdate, db=Depends(get_db)):
    from app.auth import hash_password
    async with db.acquire() as conn:
        if data.nombre is not None:
            await conn.execute("UPDATE usuarios SET nombre=$1 WHERE id=$2::uuid", data.nombre, usuario_id)
        if data.apellidos is not None:
            await conn.execute("UPDATE usuarios SET apellidos=$1 WHERE id=$2::uuid", data.apellidos, usuario_id)
        if data.cargo is not None:
            await conn.execute("UPDATE usuarios SET cargo=$1 WHERE id=$2::uuid", data.cargo, usuario_id)
        if data.rol is not None:
            await conn.execute("UPDATE usuarios SET rol=$1::rolusuario WHERE id=$2::uuid", data.rol, usuario_id)
        if data.activo is not None:
            await conn.execute("UPDATE usuarios SET activo=$1 WHERE id=$2::uuid", data.activo, usuario_id)
        if data.password is not None:
            await conn.execute("UPDATE usuarios SET password_hash=$1 WHERE id=$2::uuid", hash_password(data.password), usuario_id)
        row = await conn.fetchrow("""
            SELECT id, email, nombre, apellidos, cargo, rol, activo, fecha_creacion
            FROM usuarios WHERE id=$1::uuid
        """, usuario_id)
    return dict(row)

@app.delete("/api/usuarios/{usuario_id}", tags=["Gestión de Usuarios"])
async def desactivar_usuario(usuario_id: str, db=Depends(get_db)):
    async with db.acquire() as conn:
        await conn.execute("UPDATE usuarios SET activo=false WHERE id=$1::uuid", usuario_id)
    return {"ok": True, "mensaje": "Usuario desactivado"}


# ── DECISIÓN AML OFFICER ──────────────────────────────────
class DecisionRequest(BaseModel):
    decision: str  # aprobar | rechazar | escalar
    motivo: Optional[str] = None

@app.post("/expedientes/{expediente_id}/decision", tags=["Panel AML"])
async def tomar_decision_aml(expediente_id: str, data: DecisionRequest, db=Depends(get_db)):
    import uuid as _uuid
    estados = {
        "aprobar": "aprobado",
        "rechazar": "rechazado",
        "escalar": "en_proceso"
    }
    if data.decision not in estados:
        raise HTTPException(status_code=400, detail="Decisión inválida. Use: aprobar, rechazar, escalar")

    nuevo_estado = estados[data.decision]
    mensajes = {
        "aprobar": "✅ Expediente aprobado con Diligencia Debida Reforzada · Registrado en audit trail",
        "rechazar": "❌ Expediente rechazado · Operación denegada · Registrado en audit trail",
        "escalar": "⬆️ Expediente escalado a Dirección de Compliance · Notificación registrada"
    }

    async with db.acquire() as conn:
        exp = await conn.fetchrow(
            "SELECT id, denominacion FROM expedientes WHERE id=$1::uuid",
            expediente_id
        )
        if not exp:
            raise HTTPException(status_code=404, detail="Expediente no encontrado")
        await conn.execute(
            "UPDATE expedientes SET estado=$1, fecha_actualizacion=NOW() WHERE id=$2::uuid",
            nuevo_estado, expediente_id
        )
        await conn.execute(
            """INSERT INTO audit_trail
               (expediente_id, tipo_evento, descripcion, actor, hash_evento)
               VALUES ($1::uuid, $2, $3, 'aml-officer', $4)""",
            expediente_id,
            "decision_aml_officer",
            f"Decisión AML: {data.decision.upper()} · {data.motivo or mensajes[data.decision]}",
            str(_uuid.uuid4()).replace("-", "")[:64]
        )
    return {
        "ok": True,
        "expediente_id": expediente_id,
        "decision": data.decision,
        "nuevo_estado": nuevo_estado,
        "mensaje": mensajes[data.decision]
    }


# ══════════════════════════════════════════════════════════
# AGENTE 2 · SUPERVISOR DE CALIDAD KYC
# Art. 26 Ley 10/2010 · Control Interno
# ══════════════════════════════════════════════════════════

@app.post("/expedientes/{expediente_id}/enviar-revision", tags=["Agente 2 · Calidad KYC"])
async def enviar_a_revision(
    expediente_id: str,
    db=Depends(get_db),
    user=Depends(get_current_user)
):
    """
    Agente 2: Verifica la calidad completa del expediente antes de enviarlo
    al Supervisor. Bloquea si hay gaps críticos. Cubre Art. 26 Ley 10/2010.

    Verificaciones:
    - Campos KYC obligatorios presentes y con valor
    - Ningún campo obligatorio con confianza < umbral sin revisión manual
    - EBR score calculado y actualizado (< 30 días)
    - Screening de sanciones ejecutado (< 30 días)
    - Mínimo de documentos según configuración del tenant
    """
    async with db.acquire() as conn:
        await set_org_context(conn, user["organizacion_id"])

        # Obtener expediente
        exp = await conn.fetchrow(
            "SELECT * FROM expedientes WHERE id=$1::uuid AND organizacion_id=$2::uuid",
            expediente_id, user["organizacion_id"])
        if not exp:
            raise HTTPException(404, "Expediente no encontrado")

        # Obtener configuración de la organización
        configs = await conn.fetch(
            "SELECT clave, valor FROM configuracion_organizacion WHERE organizacion_id=$1::uuid",
            user["organizacion_id"])
        config = {r["clave"]: r["valor"] for r in configs}
        confianza_minima = float(config.get("confianza_minima_kyc", 70))
        docs_minimos = int(config.get("documentos_minimos", 2))

        # Obtener organización para campos obligatorios
        org = await conn.fetchrow(
            "SELECT campos_obligatorios, plazo_rescreening_dias FROM organizaciones WHERE id=$1::uuid",
            user["organizacion_id"])
        import json as _json
        campos_obligatorios = _json.loads(org["campos_obligatorios"]) if org["campos_obligatorios"] else []
        plazo_rescreening = org["plazo_rescreening_dias"] or 30

        # Obtener campos consolidados del expediente
        campos = await conn.fetch(
            """SELECT nombre_campo, valor, confianza, revisado_manualmente
               FROM campos_extraidos WHERE expediente_id=$1::uuid""",
            expediente_id)
        campos_dict = {r["nombre_campo"]: r for r in campos}

        # Obtener documentos
        num_docs = await conn.fetchval(
            "SELECT COUNT(*) FROM documentos WHERE expediente_id=$1::uuid AND estado_procesamiento='completado'",
            expediente_id)

        # Obtener scoring más reciente
        scoring = await conn.fetchrow(
            "SELECT fecha_calculo FROM scoring_aml WHERE expediente_id=$1::uuid ORDER BY fecha_calculo DESC LIMIT 1",
            expediente_id)

        # Obtener último screening de sanciones del audit_trail
        ultimo_screening = await conn.fetchrow(
            """SELECT fecha_evento FROM audit_trail
               WHERE expediente_id=$1::uuid AND tipo_evento='screening_sanciones'
               ORDER BY fecha_evento DESC LIMIT 1""",
            expediente_id)

    # ── Ejecutar verificaciones ──────────────────────────
    verificaciones = []
    bloqueantes = 0

    # 1. Documentos mínimos
    if num_docs >= docs_minimos:
        verificaciones.append({"verificacion": "documentos_minimos", "resultado": "ok",
            "detalle": f"{num_docs} documentos procesados (mínimo: {docs_minimos})"})
    else:
        bloqueantes += 1
        verificaciones.append({"verificacion": "documentos_minimos", "resultado": "bloqueante",
            "detalle": f"Solo {num_docs} documentos completados. Se requieren {docs_minimos}"})

    # 2. Campos obligatorios presentes
    campos_faltantes = [c for c in campos_obligatorios if c not in campos_dict or not campos_dict[c]["valor"]]
    if not campos_faltantes:
        verificaciones.append({"verificacion": "campos_obligatorios", "resultado": "ok",
            "detalle": f"Todos los campos obligatorios presentes ({len(campos_obligatorios)})"})
    else:
        bloqueantes += 1
        verificaciones.append({"verificacion": "campos_obligatorios", "resultado": "bloqueante",
            "detalle": f"Campos obligatorios faltantes: {', '.join(campos_faltantes)}"})

    # 3. Confianza mínima en campos obligatorios
    campos_baja_confianza = [
        c for c in campos_obligatorios
        if c in campos_dict
        and campos_dict[c]["valor"]
        and campos_dict[c]["confianza"] is not None
        and float(campos_dict[c]["confianza"]) < confianza_minima
        and not campos_dict[c]["revisado_manualmente"]
    ]
    if not campos_baja_confianza:
        verificaciones.append({"verificacion": "confianza_minima", "resultado": "ok",
            "detalle": f"Confianza ≥ {confianza_minima}% en todos los campos obligatorios"})
    else:
        verificaciones.append({"verificacion": "confianza_minima", "resultado": "advertencia",
            "detalle": f"Campos con baja confianza sin revisar: {', '.join(campos_baja_confianza)}"})

    # 4. EBR score calculado
    from datetime import timedelta
    ahora = datetime.utcnow()
    if scoring and (ahora - scoring["fecha_calculo"]).days < 30:
        verificaciones.append({"verificacion": "ebr_calculado", "resultado": "ok",
            "detalle": f"EBR calculado el {scoring['fecha_calculo'].strftime('%d/%m/%Y')}"})
    else:
        bloqueantes += 1
        verificaciones.append({"verificacion": "ebr_calculado", "resultado": "bloqueante",
            "detalle": "EBR no calculado o desactualizado (>30 días). Recalcula antes de enviar."})

    # 5. Screening de sanciones vigente
    if ultimo_screening and (ahora - ultimo_screening["fecha_evento"]).days < plazo_rescreening:
        verificaciones.append({"verificacion": "screening_vigente", "resultado": "ok",
            "detalle": f"Screening ejecutado el {ultimo_screening['fecha_evento'].strftime('%d/%m/%Y')}"})
    else:
        bloqueantes += 1
        verificaciones.append({"verificacion": "screening_vigente", "resultado": "bloqueante",
            "detalle": f"Screening no ejecutado o caducado (plazo: {plazo_rescreening} días)"})

    # ── Guardar checklist y actualizar estado si OK ──────
    async with db.acquire() as conn:
        await set_org_context(conn, user["organizacion_id"])
        # Borrar checklist previo
        await conn.execute(
            "DELETE FROM checklist_calidad WHERE expediente_id=$1::uuid", expediente_id)
        # Insertar verificaciones actuales
        for v in verificaciones:
            await conn.execute(
                """INSERT INTO checklist_calidad
                   (organizacion_id, expediente_id, verificacion, resultado, detalle)
                   VALUES ($1::uuid,$2::uuid,$3,$4,$5)""",
                user["organizacion_id"], expediente_id,
                v["verificacion"], v["resultado"], v["detalle"])

        if bloqueantes == 0:
            # Enviar a revisión del supervisor
            await conn.execute(
                """UPDATE expedientes SET estado_supervision='en_revision',
                   fecha_actualizacion=NOW() WHERE id=$1::uuid""",
                expediente_id)
            await conn.execute(
                """INSERT INTO audit_trail
                   (expediente_id, tipo_evento, descripcion, actor, hash_evento,
                    numero_bloque, organizacion_id)
                   VALUES ($1::uuid,'enviado_revision',
                   'Expediente enviado a revisión del Supervisor tras verificación de calidad',$2,$3,
                   (SELECT COALESCE(MAX(numero_bloque),0)+1 FROM audit_trail WHERE expediente_id=$1::uuid),
                   $4::uuid)""",
                expediente_id, user["email"],
                str(uuid.uuid4()).replace('-','')[:64], user["organizacion_id"])

    return {
        "expediente_id":    expediente_id,
        "bloqueantes":      bloqueantes,
        "puede_continuar":  bloqueantes == 0,
        "mensaje":          "✅ Expediente enviado a revisión del Supervisor" if bloqueantes == 0
                            else f"❌ {bloqueantes} verificación(es) bloqueante(s) pendientes",
        "verificaciones":   verificaciones
    }


@app.get("/expedientes/{expediente_id}/checklist", tags=["Agente 2 · Calidad KYC"])
async def ver_checklist(
    expediente_id: str,
    db=Depends(get_db),
    user=Depends(get_current_user)
):
    """Devuelve el último checklist de calidad del expediente."""
    async with db.acquire() as conn:
        await set_org_context(conn, user["organizacion_id"])
        rows = await conn.fetch(
            """SELECT verificacion, resultado, detalle, fecha_verificacion
               FROM checklist_calidad WHERE expediente_id=$1::uuid
               ORDER BY fecha_verificacion DESC""",
            expediente_id)
    return {"expediente_id": expediente_id, "verificaciones": [dict(r) for r in rows]}


@app.post("/supervisor/expedientes/{expediente_id}/decision", tags=["Supervisor · Segunda Línea"])
async def decision_supervisor(
    expediente_id: str,
    db=Depends(get_db),
    user=Depends(require_supervisor)
):
    """
    El Supervisor aprueba o rechaza un expediente en revisión.
    Solo accesible por rol supervisor o admin.
    """
    from pydantic import BaseModel as _BM
    class DecisionSup(_BM):
        decision: str  # 'aprobado' | 'rechazado'
        motivo: Optional[str] = None

    # Esta función se llama desde el body — simplificado para claridad
    raise HTTPException(501, "Usa POST /supervisor/expedientes/{id}/decision con body {decision, motivo}")


# ══════════════════════════════════════════════════════════
# AGENTE 3 · ALERTAS Y RESCREENING AUTOMÁTICO
# Art. 9 Ley 10/2010 · Supervisión continua
# ══════════════════════════════════════════════════════════

@app.post("/api/agente3/ejecutar", tags=["Agente 3 · Alertas y Rescreening"])
async def ejecutar_agente3(db=Depends(get_db), user=Depends(require_admin)):
    """
    Agente 3: Ejecuta el ciclo completo de supervisión continua para
    todas las organizaciones activas.

    Acciones:
    1. Rescreening de sanciones en expedientes activos (OpenSanctions)
    2. Detecta expedientes abandonados (sin actividad > plazo configurado)
    3. Detecta expedientes en revisión que superan el plazo de aprobación
    4. Genera alertas en BD para cada incidencia detectada
    """
    from app.services.sanctions_engine import consultar_sanciones
    alertas_generadas = []
    ahora = datetime.utcnow()

    async with db.acquire() as conn:
        # Obtener todas las organizaciones activas
        orgs = await conn.fetch("SELECT * FROM organizaciones WHERE activa=true")

        for org in orgs:
            org_id = str(org["id"])

            # ── 1. Expedientes activos de esta organización ──
            expedientes = await conn.fetch(
                """SELECT e.id, e.denominacion, e.nif, e.estado, e.estado_supervision,
                          e.fecha_actualizacion, e.score_ebr
                   FROM expedientes e
                   WHERE e.organizacion_id=$1::uuid
                   AND e.estado NOT IN ('cerrado', 'archivado')""",
                org_id)

            for exp in expedientes:
                exp_id = str(exp["id"])
                dias_sin_actividad = (ahora - exp["fecha_actualizacion"]).days

                # ── 2. Expediente abandonado ─────────────────
                if dias_sin_actividad > org["plazo_revision_expediente"]:
                    existe = await conn.fetchrow(
                        """SELECT id FROM alertas WHERE expediente_id=$1::uuid
                           AND tipo_alerta='expediente_abandonado' AND resuelta=false""",
                        exp_id)
                    if not existe:
                        await conn.execute(
                            """INSERT INTO alertas
                               (organizacion_id, expediente_id, tipo_alerta, severidad,
                                titulo, descripcion, datos_alerta)
                               VALUES ($1::uuid,$2::uuid,'expediente_abandonado','media',
                               $3, $4, $5::jsonb)""",
                            org_id, exp_id,
                            f"Expediente sin actividad: {exp['denominacion']}",
                            f"Sin actividad hace {dias_sin_actividad} días (plazo: {org['plazo_revision_expediente']} días)",
                            f'{{"dias_sin_actividad": {dias_sin_actividad}, "plazo": {org["plazo_revision_expediente"]}}}')
                        alertas_generadas.append({
                            "org": org["nombre"], "tipo": "expediente_abandonado",
                            "expediente": exp["denominacion"]})

                # ── 3. Aprobación pendiente vencida ──────────
                if exp["estado_supervision"] == "en_revision" and dias_sin_actividad > org["plazo_aprobacion_supervisor"]:
                    existe = await conn.fetchrow(
                        """SELECT id FROM alertas WHERE expediente_id=$1::uuid
                           AND tipo_alerta='aprobacion_pendiente' AND resuelta=false""",
                        exp_id)
                    if not existe:
                        await conn.execute(
                            """INSERT INTO alertas
                               (organizacion_id, expediente_id, tipo_alerta, severidad,
                                titulo, descripcion, datos_alerta)
                               VALUES ($1::uuid,$2::uuid,'aprobacion_pendiente','alta',
                               $3,$4,$5::jsonb)""",
                            org_id, exp_id,
                            f"Aprobación vencida: {exp['denominacion']}",
                            f"En revisión hace {dias_sin_actividad} días (plazo supervisor: {org['plazo_aprobacion_supervisor']} días)",
                            f'{{"dias_en_revision": {dias_sin_actividad}}}')
                        alertas_generadas.append({
                            "org": org["nombre"], "tipo": "aprobacion_pendiente",
                            "expediente": exp["denominacion"]})

                # ── 4. Rescreening de sanciones ──────────────
                ultimo_screening = await conn.fetchrow(
                    """SELECT fecha_evento FROM audit_trail
                       WHERE expediente_id=$1::uuid AND tipo_evento='screening_sanciones'
                       ORDER BY fecha_evento DESC LIMIT 1""",
                    exp_id)

                necesita_rescreening = (
                    not ultimo_screening or
                    (ahora - ultimo_screening["fecha_evento"]).days >= org["plazo_rescreening_dias"]
                )

                if necesita_rescreening and exp["nif"]:
                    try:
                        resultado = await consultar_sanciones(exp["denominacion"], exp["nif"])
                        es_hit = resultado.get("encontrado", False)
                        num_hits = resultado.get("total_hits", 0)

                        # Registrar screening en audit_trail
                        await conn.execute(
                            """INSERT INTO audit_trail
                               (expediente_id, tipo_evento, descripcion, actor,
                                hash_evento, numero_bloque, organizacion_id)
                               VALUES ($1::uuid,'screening_sanciones',$2,'agente3',$3,
                               (SELECT COALESCE(MAX(numero_bloque),0)+1 FROM audit_trail WHERE expediente_id=$1::uuid),
                               $4::uuid)""",
                            exp_id,
                            f"Rescreening automático: {'HIT DETECTADO' if es_hit else 'sin coincidencias'} ({num_hits} hits)",
                            str(uuid.uuid4()).replace('-','')[:64], org_id)

                        # Si hay hit nuevo → alerta crítica
                        if es_hit:
                            await conn.execute(
                                """INSERT INTO alertas
                                   (organizacion_id, expediente_id, tipo_alerta, severidad,
                                    titulo, descripcion, datos_alerta)
                                   VALUES ($1::uuid,$2::uuid,'sancion_nueva','critica',
                                   $3,$4,$5::jsonb)""",
                                org_id, exp_id,
                                f"⚠️ ALERTA SANCIONES: {exp['denominacion']}",
                                f"Rescreening detectó {num_hits} coincidencia(s) en listas de sanciones",
                                f'{{"hits": {num_hits}, "resultado": {str(resultado)[:500]}}}')
                            alertas_generadas.append({
                                "org": org["nombre"], "tipo": "sancion_nueva",
                                "expediente": exp["denominacion"], "hits": num_hits})
                    except Exception as e:
                        pass  # No interrumpir el ciclo por un fallo de rescreening individual

    return {
        "ejecutado_en":      ahora.isoformat(),
        "alertas_generadas": len(alertas_generadas),
        "detalle":           alertas_generadas
    }


@app.get("/api/alertas", tags=["Agente 3 · Alertas y Rescreening"])
async def listar_alertas(
    resuelta: Optional[bool] = False,
    severidad: Optional[str] = None,
    db=Depends(get_db),
    user=Depends(require_supervisor)
):
    """Lista las alertas activas de la organización. Solo Supervisor y Admin."""
    async with db.acquire() as conn:
        await set_org_context(conn, user["organizacion_id"])
        query = """SELECT a.*, e.denominacion as expediente_nombre, e.codigo
                   FROM alertas a
                   LEFT JOIN expedientes e ON e.id = a.expediente_id
                   WHERE a.organizacion_id=$1::uuid AND a.resuelta=$2"""
        params = [user["organizacion_id"], resuelta]
        if severidad:
            query += f" AND a.severidad=$3"
            params.append(severidad)
        query += " ORDER BY a.fecha_alerta DESC"
        rows = await conn.fetch(query, *params)
    return [dict(r) for r in rows]


@app.patch("/api/alertas/{alerta_id}/resolver", tags=["Agente 3 · Alertas y Rescreening"])
async def resolver_alerta(
    alerta_id: str,
    db=Depends(get_db),
    user=Depends(require_supervisor)
):
    """Marca una alerta como resuelta."""
    async with db.acquire() as conn:
        await set_org_context(conn, user["organizacion_id"])
        r = await conn.execute(
            """UPDATE alertas SET resuelta=true, resuelta_por=$1::uuid,
               fecha_resolucion=NOW()
               WHERE id=$2::uuid AND organizacion_id=$3::uuid""",
            user["id"], alerta_id, user["organizacion_id"])
        if r == "UPDATE 0":
            raise HTTPException(404, "Alerta no encontrada")
    return {"mensaje": "✅ Alerta resuelta", "alerta_id": alerta_id}


@app.get("/api/dashboard/metricas", tags=["Dashboard · Métricas"])
async def metricas_dashboard(db=Depends(get_db), user=Depends(get_current_user)):
    """Métricas reales del dashboard por organización y rol."""
    async with db.acquire() as conn:
        await set_org_context(conn, user["organizacion_id"])
        org_id = user["organizacion_id"]

        total_expedientes = await conn.fetchval(
            "SELECT COUNT(*) FROM expedientes WHERE organizacion_id=$1::uuid", org_id)
        por_estado = await conn.fetch(
            "SELECT estado, COUNT(*) as total FROM expedientes WHERE organizacion_id=$1::uuid GROUP BY estado",
            org_id)
        alertas_criticas = await conn.fetchval(
            "SELECT COUNT(*) FROM alertas WHERE organizacion_id=$1::uuid AND resuelta=false AND severidad='critica'",
            org_id)
        alertas_activas = await conn.fetchval(
            "SELECT COUNT(*) FROM alertas WHERE organizacion_id=$1::uuid AND resuelta=false",
            org_id)
        en_revision = await conn.fetchval(
            "SELECT COUNT(*) FROM expedientes WHERE organizacion_id=$1::uuid AND estado_supervision='en_revision'",
            org_id)
        score_promedio = await conn.fetchval(
            "SELECT ROUND(AVG(score_ebr)) FROM expedientes WHERE organizacion_id=$1::uuid AND score_ebr > 0",
            org_id)
        docs_procesados = await conn.fetchval(
            "SELECT COUNT(*) FROM documentos WHERE organizacion_id=$1::uuid AND estado_procesamiento='completado'",
            org_id)

    return {
        "organizacion":       user["org_nombre"],
        "total_expedientes":  total_expedientes,
        "en_revision":        en_revision,
        "alertas_activas":    alertas_activas,
        "alertas_criticas":   alertas_criticas,
        "score_promedio":     score_promedio or 0,
        "docs_procesados":    docs_procesados,
        "por_estado":         {r["estado"]: r["total"] for r in por_estado},
    }
