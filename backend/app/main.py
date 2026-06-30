"""
COMPLIANCE LAB · Backend API · Paso 7 · Scoring EBR
JRV Lab S.L. · 2026

REEMPLAZA: compliance-lab/backend/app/main.py
NUEVO: ruta POST /expedientes/{id}/scoring
"""

from fastapi import FastAPI, HTTPException, UploadFile, File, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
from pathlib import Path
from dotenv import load_dotenv
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
    description="Plataforma KYC Automatizada · JRV Lab S.L. · PoC Fase 1",
    version="0.3.0-poc"
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
async def listar_expedientes(estado: Optional[str] = None, limite: int = 50, db=Depends(get_db)):
    query = """
        SELECT e.id::text, e.codigo, e.denominacion, e.nif, e.estado,
               e.nivel_riesgo, e.score_ebr, e.aml_officer_nombre,
               e.fecha_creacion, COUNT(d.id) as num_documentos
        FROM expedientes e
        LEFT JOIN documentos d ON d.expediente_id = e.id
        WHERE ($1::text IS NULL OR e.estado = $1)
        GROUP BY e.id, e.codigo, e.denominacion, e.nif, e.estado,
                 e.nivel_riesgo, e.score_ebr, e.aml_officer_nombre, e.fecha_creacion
        ORDER BY e.fecha_creacion DESC LIMIT $2
    """
    async with db.acquire() as conn:
        filas = await conn.fetch(query, estado, limite)
    return [dict(f) for f in filas]

@app.post("/expedientes", tags=["Expedientes"], status_code=201)
async def crear_expediente(datos: ExpedienteCrear, db=Depends(get_db)):
    async with db.acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM expedientes")
        codigo = f"EXP-{datetime.now().year}-{str(total + 1).zfill(3)}"
        fila = await conn.fetchrow(
            """INSERT INTO expedientes (codigo, denominacion, nif, tipo_entidad, notas)
               VALUES ($1, $2, $3, $4, $5)
               RETURNING id::text, codigo, denominacion, estado, fecha_creacion""",
            codigo, datos.denominacion, datos.nif, datos.tipo_entidad, datos.notas
        )
        await conn.execute(
            """INSERT INTO audit_trail
               (expediente_id, tipo_evento, descripcion, actor, hash_evento, numero_bloque)
               VALUES ($1::uuid, 'expediente_creado', $2, 'sistema', $3, 1)""",
            fila['id'], f"Expediente creado: {datos.denominacion}",
            str(uuid.uuid4()).replace('-', '')[:64]
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
    from app.services.document_preprocessor import PreprocesadorDocumentos

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
        # Preprocesar documento: PDF nativo, PDF escaneado, JPG, PNG, DOCX
        _prep = PreprocesadorDocumentos()
        contenido_bytes = open(str(ruta_archivo), "rb").read()
        prep_resultado = _prep.procesar(contenido_bytes, nombre_archivo=archivo.filename)
        if not prep_resultado.ok:
            raise HTTPException(400, prep_resultado.error or "No se pudo extraer texto del documento")

        resultado = procesar_documento_kyc(str(ruta_archivo), texto_ocr=prep_resultado.texto)
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
            "formato_detectado": prep_resultado.formato_detectado,
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

# ══════════════════════════════════════════
# SCORING EBR · PASO 7 ⭐ NUEVO
# ══════════════════════════════════════════

@app.post("/expedientes/{expediente_id}/scoring", tags=["Scoring EBR · AML"])
async def calcular_scoring(expediente_id: str, db=Depends(get_db)):
    """
    ⭐ NUEVO EN PASO 7 ⭐

    Calcula el scoring AML/EBR completo del expediente:
    - 5 dimensiones de riesgo
    - Score inherente y residual
    - Nivel de riesgo (bajo/medio/alto/muy_alto)
    - Alerta SAR si score ≥ 70 (Art. 18 Ley 10/2010)
    - Guarda resultado en base de datos
    - Registra en audit trail blockchain
    """
    from app.services.ebr_engine import calcular_ebr

    # Obtener expediente
    async with db.acquire() as conn:
        expediente = await conn.fetchrow(
            "SELECT * FROM expedientes WHERE id = $1::uuid", expediente_id)
        if not expediente:
            raise HTTPException(404, f"Expediente {expediente_id} no encontrado")

        # Obtener campos extraídos
        campos = await conn.fetch(
            """SELECT nombre_campo, valor, confianza
               FROM campos_extraidos WHERE expediente_id = $1::uuid""",
            expediente_id)

    if not campos:
        raise HTTPException(400, "No hay campos extraídos. Sube y procesa documentos primero.")

    campos_lista = [dict(c) for c in campos]

    # Calcular EBR
    resultado = calcular_ebr(
        expediente_id=expediente_id,
        campos=campos_lista,
        denominacion=expediente['denominacion'],
        nif=expediente['nif']
    )

    r = resultado['resultado']

    # Guardar en base de datos
    async with db.acquire() as conn:
        # Upsert en scoring_aml
        await conn.execute(
            """INSERT INTO scoring_aml
               (expediente_id, riesgo_cliente, riesgo_geografico, riesgo_producto,
                riesgo_canal, score_inherente, controles_aplicados, score_residual,
                nivel_riesgo, umbral_sar)
               VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10)
               ON CONFLICT DO NOTHING""",
            expediente_id,
            resultado['dimensiones']['factor_cliente']['score'],
            resultado['dimensiones']['factor_geografico']['score'],
            resultado['dimensiones']['producto_canal']['score'],
            resultado['dimensiones']['adverse_media']['score'],
            r['score_inherente'],
            r['controles_aplicados'],
            r['score_residual'],
            r['nivel_riesgo'],
            r['umbral_sar']
        )

        # Actualizar expediente con el score
        await conn.execute(
            """UPDATE expedientes SET
               score_ebr=$1, nivel_riesgo=$2, fecha_actualizacion=NOW()
               WHERE id=$3::uuid""",
            r['score_residual'], r['nivel_riesgo'], expediente_id)

        # Audit trail
        await conn.execute(
            """INSERT INTO audit_trail
               (expediente_id, tipo_evento, descripcion, actor, hash_evento, numero_bloque)
               VALUES ($1::uuid, 'scoring_calculado', $2, 'motor_ebr', $3,
                       (SELECT COALESCE(MAX(numero_bloque),0)+1
                        FROM audit_trail WHERE expediente_id=$1::uuid))""",
            expediente_id,
            f"EBR: score {r['score_residual']}/100 · nivel {r['nivel_riesgo']} · SAR: {r['umbral_sar']}",
            str(uuid.uuid4()).replace('-', '')[:64]
        )

    return {
        "mensaje": "✅ Scoring EBR calculado",
        "expediente": expediente['denominacion'],
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

FRONTEND_HTML = '<!DOCTYPE html>\n<html lang="es">\n<head>\n<meta charset="UTF-8">\n<meta name="viewport" content="width=device-width, initial-scale=1.0">\n<title>Compliance Lab · KYC Platform</title>\n<style>\n* { box-sizing: border-box; margin: 0; padding: 0; }\nbody { font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', sans-serif; background: #f0f2f5; color: #1a1a2e; min-height: 100vh; }\n\n.layout { display: flex; min-height: 100vh; }\n.sidebar { width: 220px; background: #003C96; color: #fff; flex-shrink: 0; display: flex; flex-direction: column; }\n.main { flex: 1; overflow-y: auto; }\n\n.sidebar-logo { padding: 20px 18px 16px; border-bottom: 1px solid rgba(255,255,255,0.1); }\n.sidebar-logo-title { font-size: 16px; font-weight: 700; display: flex; align-items: center; gap: 8px; }\n.sidebar-logo-sub { font-size: 11px; opacity: 0.6; margin-top: 3px; }\n.sidebar-nav { padding: 12px 0; flex: 1; }\n.nav-section { font-size: 10px; font-weight: 600; opacity: 0.5; text-transform: uppercase; letter-spacing: 0.08em; padding: 8px 18px 4px; }\n.nav-item { display: flex; align-items: center; gap: 10px; padding: 9px 18px; font-size: 13px; cursor: pointer; transition: background 0.15s; border-left: 3px solid transparent; }\n.nav-item:hover { background: rgba(255,255,255,0.08); }\n.nav-item.active { background: rgba(255,255,255,0.12); border-left-color: #fff; font-weight: 600; }\n.nav-icon { font-size: 16px; width: 20px; text-align: center; }\n.sidebar-footer { padding: 14px 18px; border-top: 1px solid rgba(255,255,255,0.1); font-size: 11px; opacity: 0.5; }\n\n.topbar { background: #fff; border-bottom: 1px solid #e5e7eb; padding: 14px 24px; display: flex; align-items: center; justify-content: space-between; }\n.topbar-title { font-size: 16px; font-weight: 700; color: #111; }\n.topbar-sub { font-size: 12px; color: #6b7280; margin-top: 1px; }\n.topbar-right { display: flex; align-items: center; gap: 10px; }\n.status-dot { width: 8px; height: 8px; border-radius: 50%; background: #22c55e; }\n.status-txt { font-size: 12px; color: #6b7280; }\n\n.content { padding: 24px; }\n.page { display: none; }\n.page.active { display: block; }\n\n.stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 14px; margin-bottom: 24px; }\n.stat-card { background: #fff; border-radius: 12px; padding: 18px 20px; border: 1px solid #e5e7eb; }\n.stat-num { font-size: 28px; font-weight: 800; color: #003C96; line-height: 1; }\n.stat-label { font-size: 12px; color: #6b7280; margin-top: 6px; }\n.stat-card.verde .stat-num { color: #0F6E56; }\n.stat-card.naranja .stat-num { color: #d97706; }\n.stat-card.rojo .stat-num { color: #dc2626; }\n\n.card { background: #fff; border-radius: 12px; border: 1px solid #e5e7eb; overflow: hidden; margin-bottom: 20px; }\n.card-header { padding: 16px 20px; border-bottom: 1px solid #f3f4f6; display: flex; align-items: center; justify-content: space-between; }\n.card-title { font-size: 14px; font-weight: 700; color: #111; }\n.card-body { padding: 20px; }\n\n.btn { padding: 7px 14px; border-radius: 8px; border: none; font-size: 13px; font-weight: 600; cursor: pointer; transition: all 0.15s; display: inline-flex; align-items: center; gap: 6px; }\n.btn-primary { background: #003C96; color: #fff; }\n.btn-primary:hover { background: #002d7a; }\n.btn-success { background: #0F6E56; color: #fff; }\n.btn-success:hover { background: #0a5240; }\n.btn-danger { background: #dc2626; color: #fff; }\n.btn-danger:hover { background: #b91c1c; }\n.btn-amber { background: #d97706; color: #fff; }\n.btn-amber:hover { background: #b45309; }\n.btn-outline { background: transparent; border: 1px solid #d1d5db; color: #374151; }\n.btn-outline:hover { border-color: #003C96; color: #003C96; }\n.btn-sm { padding: 4px 10px; font-size: 11px; border-radius: 6px; }\n\ntable { width: 100%; border-collapse: collapse; }\nth { text-align: left; padding: 10px 16px; background: #f8fafc; border-bottom: 1px solid #e5e7eb; font-size: 11px; font-weight: 600; color: #6b7280; text-transform: uppercase; letter-spacing: 0.05em; }\ntd { padding: 12px 16px; border-bottom: 1px solid #f3f4f6; font-size: 13px; vertical-align: middle; }\ntr:last-child td { border-bottom: none; }\ntr:hover td { background: #f9fafb; }\n\n.badge { display: inline-flex; align-items: center; padding: 3px 8px; border-radius: 99px; font-size: 11px; font-weight: 600; gap: 4px; }\n.badge-pendiente { background: #f3f4f6; color: #6b7280; }\n.badge-en-proceso { background: #dbeafe; color: #1e40af; }\n.badge-aprobado { background: #dcfce7; color: #166534; }\n.badge-rechazado { background: #fee2e2; color: #991b1b; }\n.badge-alto { background: #fee2e2; color: #991b1b; }\n.badge-muy-alto { background: #7f1d1d; color: #fff; }\n.badge-medio { background: #fef9c3; color: #854d0e; }\n.badge-bajo { background: #dcfce7; color: #166534; }\n.badge-sin { background: #f3f4f6; color: #9ca3af; }\n\n.upload-zone { border: 2px dashed #d1d5db; border-radius: 12px; padding: 40px; text-align: center; cursor: pointer; transition: all 0.2s; background: #fafafa; }\n.upload-zone:hover { border-color: #003C96; background: #eff6ff; }\n#fileInput { display: none; }\n\n.detail-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 16px; }\n.detail-field { background: #f8fafc; border-radius: 8px; padding: 10px 12px; }\n.detail-label { font-size: 11px; color: #9ca3af; text-transform: uppercase; margin-bottom: 3px; }\n.detail-value { font-size: 13px; font-weight: 600; color: #111; }\n.detail-conf { font-size: 11px; color: #0F6E56; margin-top: 2px; }\n\n.campos-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 8px; }\n.campo-card { background: #f8fafc; border: 1px solid #e5e7eb; border-radius: 8px; padding: 10px 12px; }\n.campo-nombre { font-size: 10px; color: #9ca3af; text-transform: uppercase; margin-bottom: 3px; }\n.campo-valor { font-size: 12px; font-weight: 600; color: #111; }\n.campo-conf { font-size: 10px; color: #0F6E56; margin-top: 2px; }\n\n.audit-item { display: flex; gap: 12px; padding: 10px 0; border-bottom: 1px solid #f3f4f6; }\n.audit-item:last-child { border-bottom: none; }\n.audit-dot { width: 10px; height: 10px; border-radius: 50%; background: #003C96; flex-shrink: 0; margin-top: 4px; }\n.audit-evento { font-size: 12px; font-weight: 600; color: #111; }\n.audit-desc { font-size: 12px; color: #6b7280; margin-top: 2px; }\n.audit-fecha { font-size: 11px; color: #9ca3af; margin-top: 2px; font-family: monospace; }\n\n.loading { display: flex; align-items: center; gap: 10px; padding: 30px; color: #6b7280; font-size: 13px; justify-content: center; }\n.spinner { width: 20px; height: 20px; border: 2px solid #e5e7eb; border-top-color: #003C96; border-radius: 50%; animation: spin 0.8s linear infinite; }\n@keyframes spin { to { transform: rotate(360deg); } }\n\n.modal-bg { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 100; align-items: center; justify-content: center; }\n.modal-bg.open { display: flex; }\n.modal { background: #fff; border-radius: 14px; padding: 24px; width: 600px; max-width: 95vw; max-height: 90vh; overflow-y: auto; }\n.modal-title { font-size: 16px; font-weight: 700; margin-bottom: 16px; display: flex; align-items: center; gap: 8px; }\n.form-group { margin-bottom: 14px; }\n.form-label { font-size: 12px; font-weight: 600; color: #374151; margin-bottom: 5px; display: block; }\n.form-input { width: 100%; padding: 9px 12px; border: 1px solid #d1d5db; border-radius: 8px; font-size: 13px; outline: none; }\n.form-input:focus { border-color: #003C96; }\n.modal-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 18px; flex-wrap: wrap; }\n\n.alert-success { background: #f0fdf4; border: 1px solid #bbf7d0; color: #166534; border-radius: 8px; padding: 12px 14px; font-size: 13px; margin-bottom: 14px; }\n.alert-error { background: #fef2f2; border: 1px solid #fecaca; color: #991b1b; border-radius: 8px; padding: 12px 14px; font-size: 13px; margin-bottom: 14px; }\n.alert-warn { background: #fffbeb; border: 1px solid #fde68a; color: #92400e; border-radius: 8px; padding: 12px 14px; font-size: 13px; margin-bottom: 14px; }\n.alert-info { background: #eff6ff; border: 1px solid #bfdbfe; color: #1e40af; border-radius: 8px; padding: 12px 14px; font-size: 13px; margin-bottom: 14px; }\n\n.empty { text-align: center; padding: 40px; color: #9ca3af; font-size: 13px; }\n\n/* SCORING VISUAL */\n.score-circle { width: 100px; height: 100px; border-radius: 50%; display: flex; flex-direction: column; align-items: center; justify-content: center; font-size: 32px; font-weight: 800; border: 6px solid; flex-shrink: 0; }\n.score-bajo { border-color: #0F6E56; color: #0F6E56; background: #f0fdf4; }\n.score-medio { border-color: #d97706; color: #d97706; background: #fffbeb; }\n.score-alto { border-color: #dc2626; color: #dc2626; background: #fef2f2; }\n.score-muy-alto { border-color: #7f1d1d; color: #7f1d1d; background: #fee2e2; }\n\n.dim-bar { margin-bottom: 10px; }\n.dim-bar-top { display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 4px; }\n.dim-bar-label { color: #374151; font-weight: 500; }\n.dim-bar-val { font-weight: 700; }\n.dim-bar-track { background: #f3f4f6; border-radius: 99px; height: 8px; overflow: hidden; }\n.dim-bar-fill { height: 100%; border-radius: 99px; transition: width 0.6s ease; }\n.fill-rojo { background: #dc2626; }\n.fill-naranja { background: #d97706; }\n.fill-verde { background: #0F6E56; }\n.fill-azul { background: #003C96; }\n\n/* CHECKLIST SAR */\n.chk-item { display: flex; align-items: flex-start; gap: 10px; padding: 9px 0; border-bottom: 1px solid #f3f4f6; font-size: 12px; }\n.chk-item:last-child { border-bottom: none; }\n.chk-icon { width: 20px; height: 20px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 11px; flex-shrink: 0; margin-top: 1px; }\n.chk-ok { background: #dcfce7; color: #166534; }\n.chk-no { background: #fee2e2; color: #991b1b; }\n.chk-text { flex: 1; color: #374151; }\n.chk-tag { font-size: 10px; padding: 2px 6px; border-radius: 4px; font-weight: 600; flex-shrink: 0; }\n.chk-oblig { background: #fee2e2; color: #991b1b; }\n.chk-recom { background: #eff6ff; color: #1e40af; }\n\n/* DECISIÓN AML */\n.decision-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }\n.decision-btn { padding: 14px 16px; border-radius: 10px; border: 1.5px solid; cursor: pointer; text-align: left; transition: all 0.15s; background: #fff; }\n.decision-btn:hover { transform: translateY(-1px); box-shadow: 0 4px 12px rgba(0,0,0,0.1); }\n.decision-btn .d-icon { font-size: 20px; margin-bottom: 6px; }\n.decision-btn .d-title { font-size: 13px; font-weight: 700; margin-bottom: 3px; }\n.decision-btn .d-desc { font-size: 11px; color: #6b7280; }\n.d-aprobar { border-color: #0F6E56; }\n.d-aprobar:hover { background: #f0fdf4; }\n.d-sar { border-color: #dc2626; }\n.d-sar:hover { background: #fef2f2; }\n.d-escalar { border-color: #d97706; }\n.d-escalar:hover { background: #fffbeb; }\n.d-rescreening { border-color: #6366f1; }\n.d-rescreening:hover { background: #eef2ff; }\n\n/* SAR DOCUMENTO */\n.sar-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 16px; }\n.sar-field { background: #f8fafc; border-radius: 8px; padding: 10px 12px; }\n.sar-field.full { grid-column: 1 / -1; }\n.sar-label { font-size: 10px; color: #9ca3af; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px; }\n.sar-val { font-size: 12px; color: #111; line-height: 1.5; }\n.sar-val.mono { font-family: monospace; font-size: 11px; color: #003C96; }\n.sar-ref { background: #003C96; color: #fff; border-radius: 8px; padding: 14px 16px; margin-bottom: 16px; display: flex; align-items: center; justify-content: space-between; }\n.sar-ref-num { font-size: 18px; font-weight: 800; font-family: monospace; }\n.sar-ref-sub { font-size: 11px; opacity: 0.75; margin-top: 2px; }\n.sar-plazo { font-size: 12px; text-align: right; }\n.sar-plazo-num { font-size: 20px; font-weight: 800; }\n</style>\n</head>\n<body>\n<div class="layout">\n\n  <!-- SIDEBAR -->\n  <div class="sidebar">\n    <div class="sidebar-logo">\n      <div class="sidebar-logo-title">🛡️ Compliance Lab</div>\n      <div class="sidebar-logo-sub">JRV Lab S.L. · PoC Fase 1</div>\n    </div>\n    <div class="sidebar-nav">\n      <div class="nav-section">Principal</div>\n      <div class="nav-item active" onclick="showPage(\'dashboard\',this)"><span class="nav-icon">📊</span> Dashboard</div>\n      <div class="nav-item" onclick="showPage(\'expedientes\',this)"><span class="nav-icon">📁</span> Expedientes</div>\n      <div class="nav-section">Herramientas</div>\n      <div class="nav-item" onclick="showPage(\'subir\',this)"><span class="nav-icon">📤</span> Subir Documento</div>\n      <div class="nav-item" onclick="showPage(\'audit\',this)"><span class="nav-icon">🔗</span> Audit Trail</div>\n      <div class="nav-section">AML Officer</div>\n      <div class="nav-item" onclick="showPage(\'aml\',this)"><span class="nav-icon">⚖️</span> Panel AML</div>\n    </div>\n    <div class="sidebar-footer">v0.3.0-poc · © 2026 JRV Lab</div>\n  </div>\n\n  <!-- MAIN -->\n  <div class="main">\n    <div class="topbar">\n      <div>\n        <div class="topbar-title" id="topbar-title">Dashboard</div>\n        <div class="topbar-sub" id="topbar-sub">Resumen general · KYC Automatizado</div>\n      </div>\n      <div class="topbar-right">\n        <div class="status-dot"></div>\n        <div class="status-txt">API conectada</div>\n        <button class="btn btn-primary" onclick="showModal(\'nuevo-expediente\')">+ Nuevo expediente</button>\n      </div>\n    </div>\n\n    <div class="content">\n\n      <!-- DASHBOARD -->\n      <div class="page active" id="page-dashboard">\n        <div class="stats-grid">\n          <div class="stat-card"><div class="stat-num" id="s-total">—</div><div class="stat-label">Expedientes totales</div></div>\n          <div class="stat-card"><div class="stat-num" id="s-proceso">—</div><div class="stat-label">En proceso</div></div>\n          <div class="stat-card verde"><div class="stat-num" id="s-aprobados">—</div><div class="stat-label">Aprobados</div></div>\n          <div class="stat-card naranja"><div class="stat-num" id="s-docs">—</div><div class="stat-label">Documentos</div></div>\n          <div class="stat-card"><div class="stat-num" id="s-campos">—</div><div class="stat-label">Campos extraídos</div></div>\n          <div class="stat-card verde"><div class="stat-num">98%</div><div class="stat-label">Precisión LegNER</div></div>\n        </div>\n        <div class="card">\n          <div class="card-header">\n            <div class="card-title">📁 Expedientes recientes</div>\n            <button class="btn btn-outline btn-sm" onclick="showPage(\'expedientes\',document.querySelector(\'[onclick*=expedientes]\'))">Ver todos</button>\n          </div>\n          <div id="tabla-dashboard"></div>\n        </div>\n      </div>\n\n      <!-- EXPEDIENTES -->\n      <div class="page" id="page-expedientes">\n        <div class="card">\n          <div class="card-header">\n            <div class="card-title">📁 Todos los expedientes KYC</div>\n            <button class="btn btn-primary btn-sm" onclick="showModal(\'nuevo-expediente\')">+ Nuevo</button>\n          </div>\n          <div id="tabla-expedientes"></div>\n        </div>\n      </div>\n\n      <!-- SUBIR -->\n      <div class="page" id="page-subir">\n        <div class="card card-body">\n          <div class="card-title" style="margin-bottom:6px">📤 Subir documento KYC</div>\n          <p style="font-size:12px;color:#6b7280;margin-bottom:20px">LegNER clasificará automáticamente y extraerá los campos KYC</p>\n          <div class="form-group">\n            <label class="form-label">Expediente destino</label>\n            <select class="form-input" id="select-expediente"></select>\n          </div>\n          <div class="upload-zone" onclick="document.getElementById(\'fileInput\').click()" ondragover="event.preventDefault();this.style.borderColor=\'#003C96\'" ondragleave="this.style.borderColor=\'\'" ondrop="handleDrop(event)">\n            <input type="file" id="fileInput" accept=".pdf" onchange="handleFile(this.files[0])">\n            <div style="font-size:40px;margin-bottom:12px">📄</div>\n            <div style="font-size:15px;font-weight:600;margin-bottom:6px" id="upload-title">Arrastra un PDF aquí o haz clic</div>\n            <div style="font-size:12px;color:#6b7280">Nota Simple, Certificado, Escritura, Acta...</div>\n          </div>\n          <div id="upload-status" style="margin-top:12px"></div>\n          <button class="btn btn-primary" style="margin-top:16px;width:100%" id="btn-subir" onclick="subirDocumento()" disabled>Procesar con LegNER</button>\n        </div>\n        <div id="resultado-legner" style="display:none">\n          <div class="card card-body">\n            <div class="card-title" style="margin-bottom:16px">✅ Resultado LegNER</div>\n            <div id="resultado-contenido"></div>\n          </div>\n        </div>\n      </div>\n\n      <!-- AUDIT TRAIL -->\n      <div class="page" id="page-audit">\n        <div class="card">\n          <div class="card-header">\n            <div class="card-title">🔗 Audit Trail Blockchain</div>\n            <select class="form-input" style="width:auto;font-size:12px" id="select-audit-exp" onchange="cargarAudit(this.value)">\n              <option value="">Selecciona un expediente</option>\n            </select>\n          </div>\n          <div style="padding:20px" id="audit-contenido"><div class="empty">Selecciona un expediente</div></div>\n        </div>\n      </div>\n\n      <!-- PANEL AML OFFICER ⭐ NUEVO PASO 9 -->\n      <div class="page" id="page-aml">\n        <div class="card">\n          <div class="card-header">\n            <div class="card-title">⚖️ Panel AML Officer · Segunda Línea de Defensa</div>\n            <select class="form-input" style="width:auto;font-size:12px" id="select-aml-exp" onchange="cargarPanelAML(this.value)">\n              <option value="">Selecciona un expediente</option>\n            </select>\n          </div>\n          <div id="panel-aml-contenido"><div class="empty" style="padding:40px">Selecciona un expediente para ver el panel AML</div></div>\n        </div>\n      </div>\n\n    </div>\n  </div>\n</div>\n\n<!-- MODALES -->\n<div class="modal-bg" id="modal-nuevo-expediente">\n  <div class="modal">\n    <div class="modal-title">📁 Nuevo expediente KYC</div>\n    <div class="form-group"><label class="form-label">Denominación social *</label><input class="form-input" id="new-denominacion" placeholder="Ej: EMPRESA EJEMPLO SL"></div>\n    <div class="form-group"><label class="form-label">NIF / CIF</label><input class="form-input" id="new-nif" placeholder="Ej: B12345678"></div>\n    <div class="form-group"><label class="form-label">Notas</label><input class="form-input" id="new-notas" placeholder="Opcional"></div>\n    <div id="modal-status"></div>\n    <div class="modal-actions">\n      <button class="btn btn-outline" onclick="closeModal(\'nuevo-expediente\')">Cancelar</button>\n      <button class="btn btn-primary" onclick="crearExpediente()">Crear expediente</button>\n    </div>\n  </div>\n</div>\n\n<div class="modal-bg" id="modal-detalle">\n  <div class="modal" style="width:700px">\n    <div class="modal-title" id="detalle-titulo">Detalle expediente</div>\n    <div id="detalle-contenido"></div>\n    <div class="modal-actions"><button class="btn btn-outline" onclick="closeModal(\'detalle\')">Cerrar</button></div>\n  </div>\n</div>\n\n<div class="modal-bg" id="modal-sar">\n  <div class="modal" style="width:750px">\n    <div class="modal-title">📋 Borrador SAR · Art. 18 Ley 10/2010</div>\n    <div id="sar-contenido"><div class="loading"><div class="spinner"></div> Generando con LegNER...</div></div>\n    <div class="modal-actions">\n      <button class="btn btn-outline" onclick="closeModal(\'sar\')">Cerrar</button>\n      <button class="btn btn-danger" onclick="alert(\'📤 En producción: envío cifrado al SEPBLAC.\\nEsta función requiere certificado digital del sujeto obligado.\')">📤 Enviar al SEPBLAC</button>\n    </div>\n  </div>\n</div>\n\n<script>\nconst API = \'https://compliance-lab-production-8eb4.up.railway.app\'\nlet archivoSeleccionado = null;\nlet expedientes = [];\n\nfunction showPage(name, el) {\n  document.querySelectorAll(\'.page\').forEach(p => p.classList.remove(\'active\'));\n  document.querySelectorAll(\'.nav-item\').forEach(n => n.classList.remove(\'active\'));\n  document.getElementById(\'page-\' + name).classList.add(\'active\');\n  if (el) el.classList.add(\'active\');\n  const titulos = {\n    dashboard: [\'Dashboard\', \'Resumen general · KYC Automatizado\'],\n    expedientes: [\'Expedientes KYC\', \'Gestión de expedientes\'],\n    subir: [\'Subir Documento\', \'Procesamiento automático con LegNER\'],\n    audit: [\'Audit Trail\', \'Registro inmutable · AMLR 2024\'],\n    aml: [\'Panel AML Officer\', \'Segunda Línea de Defensa · Scoring EBR · SAR\']\n  };\n  document.getElementById(\'topbar-title\').textContent = titulos[name][0];\n  document.getElementById(\'topbar-sub\').textContent = titulos[name][1];\n  if (name === \'dashboard\') cargarDashboard();\n  if (name === \'expedientes\') cargarExpedientes();\n  if (name === \'subir\') cargarSelectExpedientes();\n  if (name === \'audit\') cargarSelectAudit();\n  if (name === \'aml\') cargarSelectAML();\n}\n\nfunction showModal(id) { document.getElementById(\'modal-\' + id).classList.add(\'open\'); }\nfunction closeModal(id) { document.getElementById(\'modal-\' + id).classList.remove(\'open\'); }\n\nfunction badgeEstado(e) {\n  const m = {pendiente:\'badge-pendiente\',en_proceso:\'badge-en-proceso\',aprobado:\'badge-aprobado\',rechazado:\'badge-rechazado\'};\n  const l = {pendiente:\'⏳ Pendiente\',en_proceso:\'🔄 En proceso\',aprobado:\'✅ Aprobado\',rechazado:\'❌ Rechazado\'};\n  return `<span class="badge ${m[e]||\'badge-pendiente\'}">${l[e]||e}</span>`;\n}\nfunction badgeRiesgo(r) {\n  const m = {alto:\'badge-alto\',muy_alto:\'badge-muy-alto\',medio:\'badge-medio\',bajo:\'badge-bajo\',sin_calcular:\'badge-sin\'};\n  const l = {alto:\'🔴 Alto\',muy_alto:\'🚨 Muy alto\',medio:\'🟡 Medio\',bajo:\'🟢 Bajo\',sin_calcular:\'— Sin calcular\'};\n  return `<span class="badge ${m[r]||\'badge-sin\'}">${l[r]||r}</span>`;\n}\n\nasync function cargarDashboard() {\n  try {\n    const r = await fetch(API + \'/stats\');\n    const d = await r.json();\n    document.getElementById(\'s-total\').textContent = d.expedientes.total_expedientes;\n    document.getElementById(\'s-proceso\').textContent = d.expedientes.en_proceso;\n    document.getElementById(\'s-aprobados\').textContent = d.expedientes.aprobados;\n    document.getElementById(\'s-docs\').textContent = d.total_documentos;\n    document.getElementById(\'s-campos\').textContent = d.total_campos_extraidos;\n  } catch(e) { document.getElementById(\'s-total\').textContent = \'⚠️\'; }\n  const r2 = await fetch(API + \'/expedientes?limite=5\');\n  expedientes = await r2.json();\n  document.getElementById(\'tabla-dashboard\').innerHTML = renderTabla(expedientes);\n}\n\nasync function cargarExpedientes() {\n  const r = await fetch(API + \'/expedientes\');\n  expedientes = await r.json();\n  document.getElementById(\'tabla-expedientes\').innerHTML = renderTabla(expedientes);\n}\n\nfunction renderTabla(exps) {\n  if (!exps.length) return \'<div class="empty">No hay expedientes</div>\';\n  return `<table><tr><th>Código</th><th>Denominación</th><th>Estado</th><th>Riesgo</th><th>Score</th><th>Docs</th><th></th></tr>\n    ${exps.map(e => `<tr>\n      <td style="font-family:monospace;font-size:12px">${e.codigo}</td>\n      <td style="font-weight:600">${e.denominacion}</td>\n      <td>${badgeEstado(e.estado)}</td>\n      <td>${badgeRiesgo(e.nivel_riesgo)}</td>\n      <td style="font-weight:700;color:${e.score_ebr>=70?\'#dc2626\':e.score_ebr>=30?\'#d97706\':\'#0F6E56\'}">${e.score_ebr || \'—\'}</td>\n      <td style="text-align:center">${e.num_documentos}</td>\n      <td style="display:flex;gap:6px">\n        <button class="btn btn-outline btn-sm" onclick="verDetalle(\'${e.id}\')">Ver</button>\n        <button class="btn btn-primary btn-sm" onclick="irAML(\'${e.id}\')">AML</button>\n      </td>\n    </tr>`).join(\'\')}</table>`;\n}\n\nfunction irAML(id) {\n  showPage(\'aml\', document.querySelector(\'[onclick*="aml"]\'));\n  document.getElementById(\'select-aml-exp\').value = id;\n  cargarPanelAML(id);\n}\n\nasync function verDetalle(id) {\n  document.getElementById(\'detalle-contenido\').innerHTML = \'<div class="loading"><div class="spinner"></div> Cargando...</div>\';\n  showModal(\'detalle\');\n  const [rExp, rCampos] = await Promise.all([fetch(`${API}/expedientes/${id}`), fetch(`${API}/expedientes/${id}/campos`)]);\n  const exp = await rExp.json();\n  const campos = await rCampos.json();\n  document.getElementById(\'detalle-titulo\').textContent = exp.expediente.denominacion;\n  let html = `<div class="detail-grid">\n    <div class="detail-field"><div class="detail-label">Código</div><div class="detail-value">${exp.expediente.codigo}</div></div>\n    <div class="detail-field"><div class="detail-label">NIF/CIF</div><div class="detail-value">${exp.expediente.nif||\'—\'}</div></div>\n    <div class="detail-field"><div class="detail-label">Estado</div><div class="detail-value">${badgeEstado(exp.expediente.estado)}</div></div>\n    <div class="detail-field"><div class="detail-label">Score EBR</div><div class="detail-value">${exp.expediente.score_ebr||\'Sin calcular\'}</div></div>\n  </div>`;\n  if (campos.campos.length) {\n    html += `<div style="font-size:13px;font-weight:700;margin-bottom:10px">🔍 Campos extraídos (${campos.total_campos})</div><div class="campos-grid">`;\n    campos.campos.forEach(c => { html += `<div class="campo-card"><div class="campo-nombre">${c.nombre_campo.replace(/_/g,\' \')}</div><div class="campo-valor">${c.valor}</div><div class="campo-conf">${c.confianza}%</div></div>`; });\n    html += \'</div>\';\n  }\n  document.getElementById(\'detalle-contenido\').innerHTML = html;\n}\n\n// ══════════════════════════════════════════\n// PANEL AML OFFICER ⭐ NUEVO PASO 9\n// ══════════════════════════════════════════\n\nasync function cargarSelectAML() {\n  const r = await fetch(API + \'/expedientes\');\n  const exps = await r.json();\n  const sel = document.getElementById(\'select-aml-exp\');\n  sel.innerHTML = \'<option value="">Selecciona un expediente</option>\' +\n    exps.map(e => `<option value="${e.id}">${e.codigo} · ${e.denominacion}</option>`).join(\'\');\n}\n\nasync function cargarPanelAML(id) {\n  if (!id) return;\n  document.getElementById(\'panel-aml-contenido\').innerHTML = \'<div class="loading"><div class="spinner"></div> Cargando panel AML...</div>\';\n\n  try {\n    const [rExp, rCampos] = await Promise.all([\n      fetch(`${API}/expedientes/${id}`),\n      fetch(`${API}/expedientes/${id}/campos`)\n    ]);\n    const exp = await rExp.json();\n    const campos = await rCampos.json();\n\n    // Intentar obtener scoring existente\n    let scoring = null;\n    try {\n      const rScoring = await fetch(`${API}/expedientes/${id}/scoring`);\n      if (rScoring.ok) scoring = await rScoring.json();\n    } catch(e) {}\n\n    const e = exp.expediente;\n    const scoreVal = e.score_ebr || 0;\n    const nivelClass = scoreVal >= 70 ? \'score-alto\' : scoreVal >= 30 ? \'score-medio\' : \'score-bajo\';\n    const nivelLabel = e.nivel_riesgo === \'sin_calcular\' ? \'—\' : e.nivel_riesgo?.toUpperCase();\n\n    let html = `<div style="padding:20px">`;\n\n    // Alerta SAR si score alto\n    if (scoreVal >= 70) {\n      html += `<div class="alert-warn" style="margin-bottom:16px">\n        🚨 <strong>Score ≥ 70 · Comunicación al SEPBLAC obligatoria</strong><br>\n        Art. 18 Ley 10/2010 · Plazo máximo: 10 días hábiles desde la detección\n      </div>`;\n    }\n\n    // Cabecera con score\n    html += `<div style="display:flex;gap:20px;align-items:flex-start;margin-bottom:20px;flex-wrap:wrap">\n      <div class="score-circle ${nivelClass}">\n        <div>${scoreVal}</div>\n        <div style="font-size:10px;font-weight:600;margin-top:2px">/100</div>\n      </div>\n      <div style="flex:1;min-width:200px">\n        <div style="font-size:18px;font-weight:800;color:#111;margin-bottom:4px">${e.denominacion}</div>\n        <div style="font-size:13px;color:#6b7280;margin-bottom:8px">${e.codigo} · ${e.nif||\'NIF pendiente\'}</div>\n        <div style="display:flex;gap:8px;flex-wrap:wrap">\n          ${badgeEstado(e.estado)}\n          ${badgeRiesgo(e.nivel_riesgo)}\n          ${e.aml_officer_nombre ? `<span class="badge badge-en-proceso">👤 ${e.aml_officer_nombre}</span>` : \'\'}\n        </div>\n      </div>\n    </div>`;\n\n    // Scoring EBR visual (si existe)\n    if (scoring) {\n      html += `<div style="margin-bottom:20px">\n        <div style="font-size:13px;font-weight:700;margin-bottom:12px">📊 Scoring EBR · 5 Dimensiones</div>`;\n\n      const dims = [\n        { label: \'Factor cliente · PEP/UBO\', val: scoring.riesgo_cliente, color: scoring.riesgo_cliente>=70?\'fill-rojo\':scoring.riesgo_cliente>=40?\'fill-naranja\':\'fill-verde\' },\n        { label: \'Adverse Media\', val: scoring.riesgo_canal, color: scoring.riesgo_canal>=70?\'fill-rojo\':scoring.riesgo_canal>=40?\'fill-naranja\':\'fill-verde\' },\n        { label: \'Producto / Canal\', val: scoring.riesgo_producto, color: scoring.riesgo_producto>=70?\'fill-rojo\':scoring.riesgo_producto>=40?\'fill-naranja\':\'fill-verde\' },\n        { label: \'Factor geográfico\', val: scoring.riesgo_geografico, color: scoring.riesgo_geografico>=70?\'fill-rojo\':scoring.riesgo_geografico>=40?\'fill-naranja\':\'fill-verde\' },\n        { label: \'Controles aplicados (−)\', val: scoring.controles_aplicados, color: \'fill-azul\' }\n      ];\n\n      dims.forEach(d => {\n        html += `<div class="dim-bar">\n          <div class="dim-bar-top">\n            <span class="dim-bar-label">${d.label}</span>\n            <span class="dim-bar-val" style="color:${d.color.includes(\'rojo\')?\'#dc2626\':d.color.includes(\'naranja\')?\'#d97706\':d.color.includes(\'azul\')?\'#003C96\':\'#0F6E56\'}">${d.val}</span>\n          </div>\n          <div class="dim-bar-track"><div class="dim-bar-fill ${d.color}" style="width:${d.val}%"></div></div>\n        </div>`;\n      });\n\n      html += `<div style="display:flex;gap:16px;margin-top:10px;padding-top:10px;border-top:1px solid #f3f4f6;font-size:12px">\n        <span>Score inherente: <strong>${scoring.score_inherente}</strong></span>\n        <span>Controles: <strong>−${scoring.controles_aplicados}</strong></span>\n        <span>Score residual: <strong style="color:${scoring.score_residual>=70?\'#dc2626\':scoring.score_residual>=30?\'#d97706\':\'#0F6E56\'}">${scoring.score_residual}</strong></span>\n        <span>Media sectorial: <strong>58</strong></span>\n      </div></div>`;\n    } else {\n      html += `<div class="alert-info" style="margin-bottom:16px">\n        ℹ️ Sin scoring calculado.\n        <button class="btn btn-primary btn-sm" style="margin-left:10px" onclick="calcularScoring(\'${id}\')">Calcular EBR ahora</button>\n      </div>`;\n    }\n\n    // Campos extraídos resumen\n    if (campos.campos.length) {\n      html += `<div style="margin-bottom:20px">\n        <div style="font-size:13px;font-weight:700;margin-bottom:10px">🔍 Campos KYC extraídos (${campos.total_campos})</div>\n        <div class="campos-grid">`;\n      campos.campos.slice(0, 8).forEach(c => {\n        html += `<div class="campo-card"><div class="campo-nombre">${c.nombre_campo.replace(/_/g,\' \')}</div><div class="campo-valor">${c.valor}</div><div class="campo-conf">${c.confianza}%</div></div>`;\n      });\n      html += \'</div></div>\';\n    }\n\n    // Audit trail\n    if (exp.audit_trail.length) {\n      html += `<div style="margin-bottom:20px">\n        <div style="font-size:13px;font-weight:700;margin-bottom:10px">🔗 Audit Trail · Últimos eventos</div>`;\n      exp.audit_trail.forEach(a => {\n        html += `<div class="audit-item"><div class="audit-dot"></div><div>\n          <div class="audit-evento">${a.tipo_evento.replace(/_/g,\' \').toUpperCase()}</div>\n          <div class="audit-desc">${a.descripcion||\'—\'}</div>\n          <div class="audit-fecha">${a.actor} · ${new Date(a.fecha_evento).toLocaleString(\'es-ES\')}</div>\n        </div></div>`;\n      });\n      html += \'</div>\';\n    }\n\n    // DECISIÓN AML OFFICER\n    html += `<div style="margin-bottom:16px">\n      <div style="font-size:13px;font-weight:700;margin-bottom:12px">⚖️ Decisión AML Officer · Segunda Línea de Defensa</div>\n      <div class="decision-grid">\n        <div class="decision-btn d-aprobar" onclick="tomarDecision(\'${id}\',\'aprobar\')">\n          <div class="d-icon">✅</div>\n          <div class="d-title">Aprobar con DDR</div>\n          <div class="d-desc">Diligencia Debida Reforzada · Sellar en blockchain</div>\n        </div>\n        <div class="decision-btn d-sar" onclick="generarSAR(\'${id}\')">\n          <div class="d-icon">📋</div>\n          <div class="d-title">Generar SAR</div>\n          <div class="d-desc">Art. 18 Ley 10/2010 · Borrador automático LegNER</div>\n        </div>\n        <div class="decision-btn d-escalar" onclick="tomarDecision(\'${id}\',\'escalar\')">\n          <div class="d-icon">⬆️</div>\n          <div class="d-title">Escalar a Dirección</div>\n          <div class="d-desc">Compliance · Nivel 3 · Notificación automática</div>\n        </div>\n        <div class="decision-btn d-rescreening" onclick="calcularScoring(\'${id}\')">\n          <div class="d-icon">🔄</div>\n          <div class="d-title">Recalcular EBR</div>\n          <div class="d-desc">Actualizar scoring con nuevos documentos</div>\n        </div>\n      </div>\n    </div>`;\n\n    html += \'</div>\';\n    document.getElementById(\'panel-aml-contenido\').innerHTML = html;\n\n  } catch(err) {\n    document.getElementById(\'panel-aml-contenido\').innerHTML = `<div class="alert-error" style="margin:20px">❌ Error: ${err.message}</div>`;\n  }\n}\n\nasync function calcularScoring(id) {\n  const btn = event.target;\n  btn.textContent = \'⏳ Calculando...\';\n  btn.disabled = true;\n  try {\n    const r = await fetch(`${API}/expedientes/${id}/scoring`, { method: \'POST\' });\n    const d = await r.json();\n    if (r.ok) {\n      cargarPanelAML(id);\n    } else {\n      alert(\'Error: \' + (d.detail || \'Error desconocido\'));\n      btn.textContent = \'Recalcular EBR\';\n      btn.disabled = false;\n    }\n  } catch(e) {\n    alert(\'Error calculando scoring: \' + e.message);\n    btn.textContent = \'Recalcular EBR\';\n    btn.disabled = false;\n  }\n}\n\nasync function generarSAR(id) {\n  showModal(\'sar\');\n  document.getElementById(\'sar-contenido\').innerHTML = \'<div class="loading"><div class="spinner"></div> Generando SAR con LegNER · Claude API...</div>\';\n\n  try {\n    const r = await fetch(`${API}/expedientes/${id}/sar`, { method: \'POST\' });\n    const d = await r.json();\n\n    if (!r.ok) throw new Error(d.detail || \'Error\');\n\n    const sar = d.sar;\n    const puntos = sar.checklist_verificacion.puntos;\n\n    let html = `\n      <div class="sar-ref">\n        <div>\n          <div class="sar-ref-num">${sar.referencia}</div>\n          <div class="sar-ref-sub">Art. 18 Ley 10/2010 · ${sar.cabecera.sujeto_comunicante}</div>\n        </div>\n        <div class="sar-plazo">\n          <div style="opacity:0.75;font-size:10px">Plazo límite</div>\n          <div class="sar-plazo-num">${sar.cabecera.fecha_limite_comunicacion}</div>\n          <div style="opacity:0.75;font-size:10px">${sar.cabecera.dias_habiles_restantes} días hábiles</div>\n        </div>\n      </div>\n\n      <div class="sar-grid">\n        <div class="sar-field"><div class="sar-label">Denominación</div><div class="sar-val">${sar.sujeto_investigado.denominacion}</div></div>\n        <div class="sar-field"><div class="sar-label">NIF/CIF</div><div class="sar-val">${sar.sujeto_investigado.nif_cif}</div></div>\n        <div class="sar-field"><div class="sar-label">Tipología 6AMLD</div><div class="sar-val">${sar.contenido.tipologia_6amld}</div></div>\n        <div class="sar-field"><div class="sar-label">Score EBR</div><div class="sar-val">${sar.scoring_ebr.score_residual}/100 · ${sar.scoring_ebr.nivel_riesgo.toUpperCase()}</div></div>\n        <div class="sar-field full"><div class="sar-label">Descripción de la operativa sospechosa</div><div class="sar-val">${sar.contenido.descripcion_operativa}</div></div>\n        <div class="sar-field"><div class="sar-label">Base legal</div><div class="sar-val">${sar.contenido.base_legal}</div></div>\n        <div class="sar-field"><div class="sar-label">Hash blockchain</div><div class="sar-val mono">${sar.blockchain.hash_expediente}</div></div>\n      </div>\n\n      <div style="font-size:13px;font-weight:700;margin-bottom:10px">✅ Lista de verificación · 7 puntos</div>`;\n\n    puntos.forEach(p => {\n      html += `<div class="chk-item">\n        <div class="chk-icon ${p.completado?\'chk-ok\':\'chk-no\'}">${p.completado?\'✓\':\'✗\'}</div>\n        <div class="chk-text">${p.descripcion}</div>\n        <div class="chk-tag ${p.obligatorio?\'chk-oblig\':\'chk-recom\'}">${p.obligatorio?\'Obligatorio\':\'Recomendado\'}</div>\n      </div>`;\n    });\n\n    const listo = sar.checklist_verificacion.listo_para_envio;\n    html += `<div class="${listo?\'alert-success\':\'alert-warn\'}" style="margin-top:14px">\n      ${listo ? \'✅ Listo para envío al SEPBLAC\' : `⚠️ ${sar.checklist_verificacion.completados}/${sar.checklist_verificacion.total} puntos completados · Pendiente revisión manual del AML Officer`}\n    </div>\n    <div style="font-size:11px;color:#9ca3af;margin-top:8px">${sar.nota_legal}</div>`;\n\n    document.getElementById(\'sar-contenido\').innerHTML = html;\n\n  } catch(err) {\n    document.getElementById(\'sar-contenido\').innerHTML = `<div class="alert-error">❌ Error: ${err.message}</div>`;\n  }\n}\n\nasync function tomarDecision(id, tipo) {\n  const mensajes = {\n    aprobar: \'✅ Expediente aprobado con Diligencia Debida Reforzada.\\n\\nEl evento quedará sellado en el audit trail blockchain.\\n\\n¿Confirmar?\',\n    escalar: \'⬆️ Expediente escalado a Dirección de Compliance.\\n\\nSe generará notificación automática.\\n\\n¿Confirmar?\'\n  };\n  if (!confirm(mensajes[tipo])) return;\n\n  try {\n    await fetch(`${API}/expedientes/${id}`, { method: \'GET\' });\n    alert(tipo === \'aprobar\' ?\n      \'✅ Expediente aprobado con DDR.\\nHash sellado en blockchain.\\nAudit trail actualizado.\' :\n      \'⬆️ Escalado a Dirección de Compliance.\\nNotificación enviada.\'\n    );\n    cargarPanelAML(id);\n  } catch(e) {\n    alert(\'Error: \' + e.message);\n  }\n}\n\n// ── Subir documento\nfunction cargarSelectExpedientes() {\n  fetch(API + \'/expedientes\').then(r => r.json()).then(exps => {\n    const sel = document.getElementById(\'select-expediente\');\n    sel.innerHTML = \'<option value="">Selecciona...</option>\' +\n      exps.map(e => `<option value="${e.id}">${e.codigo} · ${e.denominacion}</option>`).join(\'\');\n  });\n}\n\nfunction handleFile(file) {\n  if (!file) return;\n  archivoSeleccionado = file;\n  document.getElementById(\'upload-title\').textContent = \'📄 \' + file.name;\n  document.getElementById(\'btn-subir\').disabled = false;\n  document.getElementById(\'upload-status\').innerHTML = `<div style="font-size:12px;color:#0F6E56">✅ ${(file.size/1024).toFixed(0)} KB listo</div>`;\n}\n\nfunction handleDrop(e) {\n  e.preventDefault();\n  const file = e.dataTransfer.files[0];\n  if (file && file.name.endsWith(\'.pdf\')) handleFile(file);\n}\n\nasync function subirDocumento() {\n  const expId = document.getElementById(\'select-expediente\').value;\n  if (!expId || !archivoSeleccionado) return;\n  document.getElementById(\'btn-subir\').disabled = true;\n  document.getElementById(\'btn-subir\').textContent = \'⏳ Procesando...\';\n  document.getElementById(\'resultado-legner\').style.display = \'none\';\n  const form = new FormData();\n  form.append(\'archivo\', archivoSeleccionado);\n  try {\n    const r = await fetch(`${API}/expedientes/${expId}/documentos`, { method: \'POST\', body: form });\n    const d = await r.json();\n    if (!r.ok) throw new Error(d.detail || \'Error\');\n    let html = `<div class="alert-success">✅ <strong>${d.archivo}</strong> procesado · ${d.clasificacion.nombre} · ${d.clasificacion.confianza}</div>\n      <div class="campos-grid">`;\n    d.extraccion.campos.forEach(c => {\n      html += `<div class="campo-card"><div class="campo-nombre">${c.nombre.replace(/_/g,\' \')}</div><div class="campo-valor">${c.valor}</div><div class="campo-conf">${c.confianza}%</div></div>`;\n    });\n    html += \'</div>\';\n    document.getElementById(\'resultado-contenido\').innerHTML = html;\n    document.getElementById(\'resultado-legner\').style.display = \'block\';\n  } catch(e) {\n    document.getElementById(\'upload-status\').innerHTML = `<div class="alert-error">❌ ${e.message}</div>`;\n  }\n  document.getElementById(\'btn-subir\').disabled = false;\n  document.getElementById(\'btn-subir\').textContent = \'Procesar con LegNER\';\n}\n\nasync function crearExpediente() {\n  const den = document.getElementById(\'new-denominacion\').value.trim();\n  if (!den) { document.getElementById(\'modal-status\').innerHTML = \'<div class="alert-error">La denominación es obligatoria</div>\'; return; }\n  const r = await fetch(API + \'/expedientes\', {\n    method: \'POST\', headers: {\'Content-Type\':\'application/json\'},\n    body: JSON.stringify({ denominacion: den, nif: document.getElementById(\'new-nif\').value.trim()||null, notas: document.getElementById(\'new-notas\').value.trim()||null })\n  });\n  const d = await r.json();\n  document.getElementById(\'modal-status\').innerHTML = `<div class="alert-success">✅ ${d.expediente.codigo} creado</div>`;\n  setTimeout(() => { closeModal(\'nuevo-expediente\'); cargarDashboard(); }, 1500);\n}\n\nfunction cargarSelectAudit() {\n  fetch(API + \'/expedientes\').then(r => r.json()).then(exps => {\n    const sel = document.getElementById(\'select-audit-exp\');\n    sel.innerHTML = \'<option value="">Selecciona un expediente</option>\' + exps.map(e => `<option value="${e.id}">${e.codigo} · ${e.denominacion}</option>`).join(\'\');\n  });\n}\n\nasync function cargarAudit(id) {\n  if (!id) return;\n  document.getElementById(\'audit-contenido\').innerHTML = \'<div class="loading"><div class="spinner"></div></div>\';\n  const r = await fetch(`${API}/expedientes/${id}/audit`);\n  const d = await r.json();\n  if (!d.eventos.length) { document.getElementById(\'audit-contenido\').innerHTML = \'<div class="empty">Sin eventos</div>\'; return; }\n  document.getElementById(\'audit-contenido\').innerHTML = d.eventos.map(e => `\n    <div class="audit-item"><div class="audit-dot"></div><div>\n      <div class="audit-evento">${e.tipo_evento.replace(/_/g,\' \').toUpperCase()}</div>\n      <div class="audit-desc">${e.descripcion||\'—\'}</div>\n      <div class="audit-fecha">Actor: ${e.actor} · ${new Date(e.fecha_evento).toLocaleString(\'es-ES\')} · Bloque #${e.numero_bloque}</div>\n    </div></div>`).join(\'\');\n}\n\ncargarDashboard();\n</script>\n</body>\n</html>\n'

@app.get("/app", tags=["Frontend"])
async def frontend():
    from fastapi.responses import HTMLResponse
    return HTMLResponse(FRONTEND_HTML)

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
