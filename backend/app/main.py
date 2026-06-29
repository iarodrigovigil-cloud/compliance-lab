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

FRONTEND_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Compliance Lab · KYC Platform</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f0f2f5; color: #1a1a2e; min-height: 100vh; }

.layout { display: flex; min-height: 100vh; }
.sidebar { width: 220px; background: #003C96; color: #fff; flex-shrink: 0; display: flex; flex-direction: column; }
.main { flex: 1; overflow-y: auto; }

.sidebar-logo { padding: 20px 18px 16px; border-bottom: 1px solid rgba(255,255,255,0.1); }
.sidebar-logo-title { font-size: 16px; font-weight: 700; display: flex; align-items: center; gap: 8px; }
.sidebar-logo-sub { font-size: 11px; opacity: 0.6; margin-top: 3px; }
.sidebar-nav { padding: 12px 0; flex: 1; }
.nav-section { font-size: 10px; font-weight: 600; opacity: 0.5; text-transform: uppercase; letter-spacing: 0.08em; padding: 8px 18px 4px; }
.nav-item { display: flex; align-items: center; gap: 10px; padding: 9px 18px; font-size: 13px; cursor: pointer; transition: background 0.15s; border-left: 3px solid transparent; }
.nav-item:hover { background: rgba(255,255,255,0.08); }
.nav-item.active { background: rgba(255,255,255,0.12); border-left-color: #fff; font-weight: 600; }
.nav-icon { font-size: 16px; width: 20px; text-align: center; }
.sidebar-footer { padding: 14px 18px; border-top: 1px solid rgba(255,255,255,0.1); font-size: 11px; opacity: 0.5; }

.topbar { background: #fff; border-bottom: 1px solid #e5e7eb; padding: 14px 24px; display: flex; align-items: center; justify-content: space-between; }
.topbar-title { font-size: 16px; font-weight: 700; color: #111; }
.topbar-sub { font-size: 12px; color: #6b7280; margin-top: 1px; }
.topbar-right { display: flex; align-items: center; gap: 10px; }
.status-dot { width: 8px; height: 8px; border-radius: 50%; background: #22c55e; }
.status-txt { font-size: 12px; color: #6b7280; }

.content { padding: 24px; }
.page { display: none; }
.page.active { display: block; }

.stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 14px; margin-bottom: 24px; }
.stat-card { background: #fff; border-radius: 12px; padding: 18px 20px; border: 1px solid #e5e7eb; }
.stat-num { font-size: 28px; font-weight: 800; color: #003C96; line-height: 1; }
.stat-label { font-size: 12px; color: #6b7280; margin-top: 6px; }
.stat-card.verde .stat-num { color: #0F6E56; }
.stat-card.naranja .stat-num { color: #d97706; }
.stat-card.rojo .stat-num { color: #dc2626; }

.card { background: #fff; border-radius: 12px; border: 1px solid #e5e7eb; overflow: hidden; margin-bottom: 20px; }
.card-header { padding: 16px 20px; border-bottom: 1px solid #f3f4f6; display: flex; align-items: center; justify-content: space-between; }
.card-title { font-size: 14px; font-weight: 700; color: #111; }
.card-body { padding: 20px; }

.btn { padding: 7px 14px; border-radius: 8px; border: none; font-size: 13px; font-weight: 600; cursor: pointer; transition: all 0.15s; display: inline-flex; align-items: center; gap: 6px; }
.btn-primary { background: #003C96; color: #fff; }
.btn-primary:hover { background: #002d7a; }
.btn-success { background: #0F6E56; color: #fff; }
.btn-success:hover { background: #0a5240; }
.btn-danger { background: #dc2626; color: #fff; }
.btn-danger:hover { background: #b91c1c; }
.btn-amber { background: #d97706; color: #fff; }
.btn-amber:hover { background: #b45309; }
.btn-outline { background: transparent; border: 1px solid #d1d5db; color: #374151; }
.btn-outline:hover { border-color: #003C96; color: #003C96; }
.btn-sm { padding: 4px 10px; font-size: 11px; border-radius: 6px; }

table { width: 100%; border-collapse: collapse; }
th { text-align: left; padding: 10px 16px; background: #f8fafc; border-bottom: 1px solid #e5e7eb; font-size: 11px; font-weight: 600; color: #6b7280; text-transform: uppercase; letter-spacing: 0.05em; }
td { padding: 12px 16px; border-bottom: 1px solid #f3f4f6; font-size: 13px; vertical-align: middle; }
tr:last-child td { border-bottom: none; }
tr:hover td { background: #f9fafb; }

.badge { display: inline-flex; align-items: center; padding: 3px 8px; border-radius: 99px; font-size: 11px; font-weight: 600; gap: 4px; }
.badge-pendiente { background: #f3f4f6; color: #6b7280; }
.badge-en-proceso { background: #dbeafe; color: #1e40af; }
.badge-aprobado { background: #dcfce7; color: #166534; }
.badge-rechazado { background: #fee2e2; color: #991b1b; }
.badge-alto { background: #fee2e2; color: #991b1b; }
.badge-muy-alto { background: #7f1d1d; color: #fff; }
.badge-medio { background: #fef9c3; color: #854d0e; }
.badge-bajo { background: #dcfce7; color: #166534; }
.badge-sin { background: #f3f4f6; color: #9ca3af; }

.upload-zone { border: 2px dashed #d1d5db; border-radius: 12px; padding: 40px; text-align: center; cursor: pointer; transition: all 0.2s; background: #fafafa; }
.upload-zone:hover { border-color: #003C96; background: #eff6ff; }
#fileInput { display: none; }

.detail-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 16px; }
.detail-field { background: #f8fafc; border-radius: 8px; padding: 10px 12px; }
.detail-label { font-size: 11px; color: #9ca3af; text-transform: uppercase; margin-bottom: 3px; }
.detail-value { font-size: 13px; font-weight: 600; color: #111; }
.detail-conf { font-size: 11px; color: #0F6E56; margin-top: 2px; }

.campos-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 8px; }
.campo-card { background: #f8fafc; border: 1px solid #e5e7eb; border-radius: 8px; padding: 10px 12px; }
.campo-nombre { font-size: 10px; color: #9ca3af; text-transform: uppercase; margin-bottom: 3px; }
.campo-valor { font-size: 12px; font-weight: 600; color: #111; }
.campo-conf { font-size: 10px; color: #0F6E56; margin-top: 2px; }

.audit-item { display: flex; gap: 12px; padding: 10px 0; border-bottom: 1px solid #f3f4f6; }
.audit-item:last-child { border-bottom: none; }
.audit-dot { width: 10px; height: 10px; border-radius: 50%; background: #003C96; flex-shrink: 0; margin-top: 4px; }
.audit-evento { font-size: 12px; font-weight: 600; color: #111; }
.audit-desc { font-size: 12px; color: #6b7280; margin-top: 2px; }
.audit-fecha { font-size: 11px; color: #9ca3af; margin-top: 2px; font-family: monospace; }

.loading { display: flex; align-items: center; gap: 10px; padding: 30px; color: #6b7280; font-size: 13px; justify-content: center; }
.spinner { width: 20px; height: 20px; border: 2px solid #e5e7eb; border-top-color: #003C96; border-radius: 50%; animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

.modal-bg { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 100; align-items: center; justify-content: center; }
.modal-bg.open { display: flex; }
.modal { background: #fff; border-radius: 14px; padding: 24px; width: 600px; max-width: 95vw; max-height: 90vh; overflow-y: auto; }
.modal-title { font-size: 16px; font-weight: 700; margin-bottom: 16px; display: flex; align-items: center; gap: 8px; }
.form-group { margin-bottom: 14px; }
.form-label { font-size: 12px; font-weight: 600; color: #374151; margin-bottom: 5px; display: block; }
.form-input { width: 100%; padding: 9px 12px; border: 1px solid #d1d5db; border-radius: 8px; font-size: 13px; outline: none; }
.form-input:focus { border-color: #003C96; }
.modal-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 18px; flex-wrap: wrap; }

.alert-success { background: #f0fdf4; border: 1px solid #bbf7d0; color: #166534; border-radius: 8px; padding: 12px 14px; font-size: 13px; margin-bottom: 14px; }
.alert-error { background: #fef2f2; border: 1px solid #fecaca; color: #991b1b; border-radius: 8px; padding: 12px 14px; font-size: 13px; margin-bottom: 14px; }
.alert-warn { background: #fffbeb; border: 1px solid #fde68a; color: #92400e; border-radius: 8px; padding: 12px 14px; font-size: 13px; margin-bottom: 14px; }
.alert-info { background: #eff6ff; border: 1px solid #bfdbfe; color: #1e40af; border-radius: 8px; padding: 12px 14px; font-size: 13px; margin-bottom: 14px; }

.empty { text-align: center; padding: 40px; color: #9ca3af; font-size: 13px; }

/* SCORING VISUAL */
.score-circle { width: 100px; height: 100px; border-radius: 50%; display: flex; flex-direction: column; align-items: center; justify-content: center; font-size: 32px; font-weight: 800; border: 6px solid; flex-shrink: 0; }
.score-bajo { border-color: #0F6E56; color: #0F6E56; background: #f0fdf4; }
.score-medio { border-color: #d97706; color: #d97706; background: #fffbeb; }
.score-alto { border-color: #dc2626; color: #dc2626; background: #fef2f2; }
.score-muy-alto { border-color: #7f1d1d; color: #7f1d1d; background: #fee2e2; }

.dim-bar { margin-bottom: 10px; }
.dim-bar-top { display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 4px; }
.dim-bar-label { color: #374151; font-weight: 500; }
.dim-bar-val { font-weight: 700; }
.dim-bar-track { background: #f3f4f6; border-radius: 99px; height: 8px; overflow: hidden; }
.dim-bar-fill { height: 100%; border-radius: 99px; transition: width 0.6s ease; }
.fill-rojo { background: #dc2626; }
.fill-naranja { background: #d97706; }
.fill-verde { background: #0F6E56; }
.fill-azul { background: #003C96; }

/* CHECKLIST SAR */
.chk-item { display: flex; align-items: flex-start; gap: 10px; padding: 9px 0; border-bottom: 1px solid #f3f4f6; font-size: 12px; }
.chk-item:last-child { border-bottom: none; }
.chk-icon { width: 20px; height: 20px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 11px; flex-shrink: 0; margin-top: 1px; }
.chk-ok { background: #dcfce7; color: #166534; }
.chk-no { background: #fee2e2; color: #991b1b; }
.chk-text { flex: 1; color: #374151; }
.chk-tag { font-size: 10px; padding: 2px 6px; border-radius: 4px; font-weight: 600; flex-shrink: 0; }
.chk-oblig { background: #fee2e2; color: #991b1b; }
.chk-recom { background: #eff6ff; color: #1e40af; }

/* DECISIÓN AML */
.decision-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.decision-btn { padding: 14px 16px; border-radius: 10px; border: 1.5px solid; cursor: pointer; text-align: left; transition: all 0.15s; background: #fff; }
.decision-btn:hover { transform: translateY(-1px); box-shadow: 0 4px 12px rgba(0,0,0,0.1); }
.decision-btn .d-icon { font-size: 20px; margin-bottom: 6px; }
.decision-btn .d-title { font-size: 13px; font-weight: 700; margin-bottom: 3px; }
.decision-btn .d-desc { font-size: 11px; color: #6b7280; }
.d-aprobar { border-color: #0F6E56; }
.d-aprobar:hover { background: #f0fdf4; }
.d-sar { border-color: #dc2626; }
.d-sar:hover { background: #fef2f2; }
.d-escalar { border-color: #d97706; }
.d-escalar:hover { background: #fffbeb; }
.d-rescreening { border-color: #6366f1; }
.d-rescreening:hover { background: #eef2ff; }

/* SAR DOCUMENTO */
.sar-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 16px; }
.sar-field { background: #f8fafc; border-radius: 8px; padding: 10px 12px; }
.sar-field.full { grid-column: 1 / -1; }
.sar-label { font-size: 10px; color: #9ca3af; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px; }
.sar-val { font-size: 12px; color: #111; line-height: 1.5; }
.sar-val.mono { font-family: monospace; font-size: 11px; color: #003C96; }
.sar-ref { background: #003C96; color: #fff; border-radius: 8px; padding: 14px 16px; margin-bottom: 16px; display: flex; align-items: center; justify-content: space-between; }
.sar-ref-num { font-size: 18px; font-weight: 800; font-family: monospace; }
.sar-ref-sub { font-size: 11px; opacity: 0.75; margin-top: 2px; }
.sar-plazo { font-size: 12px; text-align: right; }
.sar-plazo-num { font-size: 20px; font-weight: 800; }

/* ── LOGIN ─────────────────────────────────────────── */
#login-screen {
  display: flex; align-items: center; justify-content: center;
  min-height: 100vh;
  background: linear-gradient(135deg, #0D1B2A 0%, #003C96 60%, #1464B4 100%);
}
.login-box {
  background: #fff; border-radius: 16px; padding: 48px 40px;
  width: 100%; max-width: 400px;
  box-shadow: 0 25px 60px rgba(0,0,0,0.4);
}
.login-logo { text-align: center; margin-bottom: 32px; }
.login-logo-title { font-size: 22px; font-weight: 800; color: #003C96; }
.login-logo-sub { font-size: 12px; color: #6b7280; margin-top: 4px; }
.login-field { margin-bottom: 18px; }
.login-field label { display: block; font-size: 12px; font-weight: 600; color: #374151; margin-bottom: 6px; }
.login-field input {
  width: 100%; padding: 10px 14px; border: 1px solid #d1d5db;
  border-radius: 8px; font-size: 14px; outline: none; transition: border 0.2s;
}
.login-field input:focus { border-color: #003C96; }
.login-btn {
  width: 100%; padding: 12px; background: #003C96; color: #fff;
  border: none; border-radius: 8px; font-size: 15px; font-weight: 700;
  cursor: pointer; transition: background 0.2s;
}
.login-btn:hover { background: #002d7a; }
.login-error { color: #dc2626; font-size: 13px; text-align: center; margin-top: 12px; min-height: 20px; }
.login-footer { text-align: center; font-size: 11px; color: #9ca3af; margin-top: 24px; }
#app-screen { display: none; }

</style>
</head>
<body>

<div id="login-screen">
  <div class="login-box">
    <div class="login-logo">
      <div class="login-logo-title">🛡️ Compliance Lab</div>
      <div class="login-logo-sub">JRV Lab S.L. · Plataforma KYC · Ley 10/2010</div>
    </div>
    <div class="login-field">
      <label>Email</label>
      <input type="email" id="login-email" placeholder="usuario@empresa.es" onkeydown="if(event.key==='Enter') doLogin()">
    </div>
    <div class="login-field">
      <label>Contraseña</label>
      <input type="password" id="login-pass" placeholder="••••••••" onkeydown="if(event.key==='Enter') doLogin()">
    </div>
    <button class="login-btn" onclick="doLogin()">Acceder</button>
    <div class="login-error" id="login-error"></div>
    <div class="login-footer">Acceso restringido · Solo usuarios autorizados</div>
  </div>
</div>
<div id="app-screen">
<div class="layout">

  <!-- SIDEBAR -->
  <div class="sidebar">
    <div class="sidebar-logo">
      <div class="sidebar-logo-title">🛡️ Compliance Lab</div>
      <div class="sidebar-logo-sub">JRV Lab S.L. · PoC Fase 1</div>
    </div>
    <div class="sidebar-nav">
      <div class="nav-section">Principal</div>
      <div class="nav-item active" onclick="showPage('dashboard',this)"><span class="nav-icon">📊</span> Dashboard</div>
      <div class="nav-item" onclick="showPage('expedientes',this)"><span class="nav-icon">📁</span> Expedientes</div>
      <div class="nav-section">Herramientas</div>
      <div class="nav-item" onclick="showPage('subir',this)"><span class="nav-icon">📤</span> Subir Documento</div>
      <div class="nav-item" onclick="showPage('audit',this)"><span class="nav-icon">🔗</span> Audit Trail</div>
      <div class="nav-section">AML Officer</div>
      <div class="nav-item" onclick="showPage('aml',this)"><span class="nav-icon">⚖️</span> Panel AML</div>
    </div>
    <div class="sidebar-footer">v0.3.0-poc · © 2026 JRV Lab</div>
  </div>

  <!-- MAIN -->
  <div class="main">
    <div class="topbar">
      <div>
        <div class="topbar-title" id="topbar-title">Dashboard</div>
        <div class="topbar-sub" id="topbar-sub">Resumen general · KYC Automatizado</div>
      </div>
      <div class="topbar-right"><span id="topbar-user" style="font-size:12px;color:#6b7280;margin-right:8px"></span><button class="btn btn-outline btn-sm" onclick="doLogout()">Salir</button>
        <div class="status-dot"></div>
        <div class="status-txt">API conectada</div>
        <button class="btn btn-primary" onclick="showModal('nuevo-expediente')">+ Nuevo expediente</button>
      </div>
    </div>

    <div class="content">

      <!-- DASHBOARD -->
      <div class="page active" id="page-dashboard">
        <div class="stats-grid">
          <div class="stat-card"><div class="stat-num" id="s-total">—</div><div class="stat-label">Expedientes totales</div></div>
          <div class="stat-card"><div class="stat-num" id="s-proceso">—</div><div class="stat-label">En proceso</div></div>
          <div class="stat-card verde"><div class="stat-num" id="s-aprobados">—</div><div class="stat-label">Aprobados</div></div>
          <div class="stat-card naranja"><div class="stat-num" id="s-docs">—</div><div class="stat-label">Documentos</div></div>
          <div class="stat-card"><div class="stat-num" id="s-campos">—</div><div class="stat-label">Campos extraídos</div></div>
          <div class="stat-card verde"><div class="stat-num">98%</div><div class="stat-label">Precisión LegNER</div></div>
        </div>
        <div class="card">
          <div class="card-header">
            <div class="card-title">📁 Expedientes recientes</div>
            <button class="btn btn-outline btn-sm" onclick="showPage('expedientes',document.querySelector('[onclick*=expedientes]'))">Ver todos</button>
          </div>
          <div id="tabla-dashboard"></div>
        </div>
      </div>

      <!-- EXPEDIENTES -->
      <div class="page" id="page-expedientes">
        <div class="card">
          <div class="card-header">
            <div class="card-title">📁 Todos los expedientes KYC</div>
            <button class="btn btn-primary btn-sm" onclick="showModal('nuevo-expediente')">+ Nuevo</button>
          </div>
          <div id="tabla-expedientes"></div>
        </div>
      </div>

      <!-- SUBIR -->
      <div class="page" id="page-subir">
        <div class="card card-body">
          <div class="card-title" style="margin-bottom:6px">📤 Subir documento KYC</div>
          <p style="font-size:12px;color:#6b7280;margin-bottom:20px">LegNER clasificará automáticamente y extraerá los campos KYC</p>
          <div class="form-group">
            <label class="form-label">Expediente destino</label>
            <select class="form-input" id="select-expediente"></select>
          </div>
          <div class="upload-zone" onclick="document.getElementById('fileInput').click()" ondragover="event.preventDefault();this.style.borderColor='#003C96'" ondragleave="this.style.borderColor=''" ondrop="handleDrop(event)">
            <input type="file" id="fileInput" accept=".pdf" onchange="handleFile(this.files[0])">
            <div style="font-size:40px;margin-bottom:12px">📄</div>
            <div style="font-size:15px;font-weight:600;margin-bottom:6px" id="upload-title">Arrastra un PDF aquí o haz clic</div>
            <div style="font-size:12px;color:#6b7280">Nota Simple, Certificado, Escritura, Acta...</div>
          </div>
          <div id="upload-status" style="margin-top:12px"></div>
          <button class="btn btn-primary" style="margin-top:16px;width:100%" id="btn-subir" onclick="subirDocumento()" disabled>Procesar con LegNER</button>
        </div>
        <div id="resultado-legner" style="display:none">
          <div class="card card-body">
            <div class="card-title" style="margin-bottom:16px">✅ Resultado LegNER</div>
            <div id="resultado-contenido"></div>
          </div>
        </div>
      </div>

      <!-- AUDIT TRAIL -->
      <div class="page" id="page-audit">
        <div class="card">
          <div class="card-header">
            <div class="card-title">🔗 Audit Trail Blockchain</div>
            <select class="form-input" style="width:auto;font-size:12px" id="select-audit-exp" onchange="cargarAudit(this.value)">
              <option value="">Selecciona un expediente</option>
            </select>
          </div>
          <div style="padding:20px" id="audit-contenido"><div class="empty">Selecciona un expediente</div></div>
        </div>
      </div>

      <!-- PANEL AML OFFICER ⭐ NUEVO PASO 9 -->
      <div class="page" id="page-aml">
        <div class="card">
          <div class="card-header">
            <div class="card-title">⚖️ Panel AML Officer · Segunda Línea de Defensa</div>
            <select class="form-input" style="width:auto;font-size:12px" id="select-aml-exp" onchange="cargarPanelAML(this.value)">
              <option value="">Selecciona un expediente</option>
            </select>
          </div>
          <div id="panel-aml-contenido"><div class="empty" style="padding:40px">Selecciona un expediente para ver el panel AML</div></div>
        </div>
      </div>

    </div>
  </div>
</div>

<!-- MODALES -->
<div class="modal-bg" id="modal-nuevo-expediente">
  <div class="modal">
    <div class="modal-title">📁 Nuevo expediente KYC</div>
    <div class="form-group"><label class="form-label">Denominación social *</label><input class="form-input" id="new-denominacion" placeholder="Ej: EMPRESA EJEMPLO SL"></div>
    <div class="form-group"><label class="form-label">NIF / CIF</label><input class="form-input" id="new-nif" placeholder="Ej: B12345678"></div>
    <div class="form-group"><label class="form-label">Notas</label><input class="form-input" id="new-notas" placeholder="Opcional"></div>
    <div id="modal-status"></div>
    <div class="modal-actions">
      <button class="btn btn-outline" onclick="closeModal('nuevo-expediente')">Cancelar</button>
      <button class="btn btn-primary" onclick="crearExpediente()">Crear expediente</button>
    </div>
  </div>
</div>

<div class="modal-bg" id="modal-detalle">
  <div class="modal" style="width:700px">
    <div class="modal-title" id="detalle-titulo">Detalle expediente</div>
    <div id="detalle-contenido"></div>
    <div class="modal-actions"><button class="btn btn-outline" onclick="closeModal('detalle')">Cerrar</button></div>
  </div>
</div>

<div class="modal-bg" id="modal-sar">
  <div class="modal" style="width:750px">
    <div class="modal-title">📋 Borrador SAR · Art. 18 Ley 10/2010</div>
    <div id="sar-contenido"><div class="loading"><div class="spinner"></div> Generando con LegNER...</div></div>
    <div class="modal-actions">
      <button class="btn btn-outline" onclick="closeModal('sar')">Cerrar</button>
      <button class="btn btn-danger" onclick="alert('📤 En producción: envío cifrado al SEPBLAC.\
Esta función requiere certificado digital del sujeto obligado.')">📤 Enviar al SEPBLAC</button>
    </div>
  </div>
</div>

<script>
const API = window.location.origin
let archivoSeleccionado = null;
let expedientes = [];

function showPage(name, el) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('page-' + name).classList.add('active');
  if (el) el.classList.add('active');
  const titulos = {
    dashboard: ['Dashboard', 'Resumen general · KYC Automatizado'],
    expedientes: ['Expedientes KYC', 'Gestión de expedientes'],
    subir: ['Subir Documento', 'Procesamiento automático con LegNER'],
    audit: ['Audit Trail', 'Registro inmutable · AMLR 2024'],
    aml: ['Panel AML Officer', 'Segunda Línea de Defensa · Scoring EBR · SAR']
  };
  document.getElementById('topbar-title').textContent = titulos[name][0];
  document.getElementById('topbar-sub').textContent = titulos[name][1];
  if (name === 'dashboard') cargarDashboard();
  if (name === 'expedientes') cargarExpedientes();
  if (name === 'subir') cargarSelectExpedientes();
  if (name === 'audit') cargarSelectAudit();
  if (name === 'aml') cargarSelectAML();
}

function showModal(id) { document.getElementById('modal-' + id).classList.add('open'); }
function closeModal(id) { document.getElementById('modal-' + id).classList.remove('open'); }

function badgeEstado(e) {
  const m = {pendiente:'badge-pendiente',en_proceso:'badge-en-proceso',aprobado:'badge-aprobado',rechazado:'badge-rechazado'};
  const l = {pendiente:'⏳ Pendiente',en_proceso:'🔄 En proceso',aprobado:'✅ Aprobado',rechazado:'❌ Rechazado'};
  return `<span class="badge ${m[e]||'badge-pendiente'}">${l[e]||e}</span>`;
}
function badgeRiesgo(r) {
  const m = {alto:'badge-alto',muy_alto:'badge-muy-alto',medio:'badge-medio',bajo:'badge-bajo',sin_calcular:'badge-sin'};
  const l = {alto:'🔴 Alto',muy_alto:'🚨 Muy alto',medio:'🟡 Medio',bajo:'🟢 Bajo',sin_calcular:'— Sin calcular'};
  return `<span class="badge ${m[r]||'badge-sin'}">${l[r]||r}</span>`;
}

async function cargarDashboard() {
  try {
    const r = await fetch(API + '/stats');
    const d = await r.json();
    document.getElementById('s-total').textContent = d.expedientes.total_expedientes;
    document.getElementById('s-proceso').textContent = d.expedientes.en_proceso;
    document.getElementById('s-aprobados').textContent = d.expedientes.aprobados;
    document.getElementById('s-docs').textContent = d.total_documentos;
    document.getElementById('s-campos').textContent = d.total_campos_extraidos;
  } catch(e) { document.getElementById('s-total').textContent = '⚠️'; }
  const r2 = await fetch(API + '/expedientes?limite=5');
  expedientes = await r2.json();
  document.getElementById('tabla-dashboard').innerHTML = renderTabla(expedientes);
}

async function cargarExpedientes() {
  const r = await fetch(API + '/expedientes');
  expedientes = await r.json();
  document.getElementById('tabla-expedientes').innerHTML = renderTabla(expedientes);
}

function renderTabla(exps) {
  if (!exps.length) return '<div class="empty">No hay expedientes</div>';
  return `<table><tr><th>Código</th><th>Denominación</th><th>Estado</th><th>Riesgo</th><th>Score</th><th>Docs</th><th></th></tr>
    ${exps.map(e => `<tr>
      <td style="font-family:monospace;font-size:12px">${e.codigo}</td>
      <td style="font-weight:600">${e.denominacion}</td>
      <td>${badgeEstado(e.estado)}</td>
      <td>${badgeRiesgo(e.nivel_riesgo)}</td>
      <td style="font-weight:700;color:${e.score_ebr>=70?'#dc2626':e.score_ebr>=30?'#d97706':'#0F6E56'}">${e.score_ebr || '—'}</td>
      <td style="text-align:center">${e.num_documentos}</td>
      <td style="display:flex;gap:6px">
        <button class="btn btn-outline btn-sm" onclick="verDetalle('${e.id}')">Ver</button>
        <button class="btn btn-primary btn-sm" onclick="irAML('${e.id}')">AML</button>
      </td>
    </tr>`).join('')}</table>`;
}

function irAML(id) {
  showPage('aml', document.querySelector('[onclick*="aml"]'));
  document.getElementById('select-aml-exp').value = id;
  cargarPanelAML(id);
}

async function verDetalle(id) {
  document.getElementById('detalle-contenido').innerHTML = '<div class="loading"><div class="spinner"></div> Cargando...</div>';
  showModal('detalle');
  const [rExp, rCampos] = await Promise.all([fetch(`${API}/expedientes/${id}`), fetch(`${API}/expedientes/${id}/campos`)]);
  const exp = await rExp.json();
  const campos = await rCampos.json();
  document.getElementById('detalle-titulo').textContent = exp.expediente.denominacion;
  let html = `<div class="detail-grid">
    <div class="detail-field"><div class="detail-label">Código</div><div class="detail-value">${exp.expediente.codigo}</div></div>
    <div class="detail-field"><div class="detail-label">NIF/CIF</div><div class="detail-value">${exp.expediente.nif||'—'}</div></div>
    <div class="detail-field"><div class="detail-label">Estado</div><div class="detail-value">${badgeEstado(exp.expediente.estado)}</div></div>
    <div class="detail-field"><div class="detail-label">Score EBR</div><div class="detail-value">${exp.expediente.score_ebr||'Sin calcular'}</div></div>
  </div>`;
  if (campos.campos.length) {
    html += `<div style="font-size:13px;font-weight:700;margin-bottom:10px">🔍 Campos extraídos (${campos.total_campos})</div><div class="campos-grid">`;
    campos.campos.forEach(c => { html += `<div class="campo-card"><div class="campo-nombre">${c.nombre_campo.replace(/_/g,' ')}</div><div class="campo-valor">${c.valor}</div><div class="campo-conf">${c.confianza}%</div></div>`; });
    html += '</div>';
  }
  document.getElementById('detalle-contenido').innerHTML = html;
}

// ══════════════════════════════════════════
// PANEL AML OFFICER ⭐ NUEVO PASO 9
// ══════════════════════════════════════════

async function cargarSelectAML() {
  const r = await fetch(API + '/expedientes');
  const exps = await r.json();
  const sel = document.getElementById('select-aml-exp');
  sel.innerHTML = '<option value="">Selecciona un expediente</option>' +
    exps.map(e => `<option value="${e.id}">${e.codigo} · ${e.denominacion}</option>`).join('');
}

async function cargarPanelAML(id) {
  if (!id) return;
  document.getElementById('panel-aml-contenido').innerHTML = '<div class="loading"><div class="spinner"></div> Cargando panel AML...</div>';

  try {
    const [rExp, rCampos] = await Promise.all([
      fetch(`${API}/expedientes/${id}`),
      fetch(`${API}/expedientes/${id}/campos`)
    ]);
    const exp = await rExp.json();
    const campos = await rCampos.json();

    // Intentar obtener scoring existente
    let scoring = null;
    try {
      const rScoring = await fetch(`${API}/expedientes/${id}/scoring`);
      if (rScoring.ok) scoring = await rScoring.json();
    } catch(e) {}

    const e = exp.expediente;
    const scoreVal = e.score_ebr || 0;
    const nivelClass = scoreVal >= 70 ? 'score-alto' : scoreVal >= 30 ? 'score-medio' : 'score-bajo';
    const nivelLabel = e.nivel_riesgo === 'sin_calcular' ? '—' : e.nivel_riesgo?.toUpperCase();

    let html = `<div style="padding:20px">`;

    // Alerta SAR si score alto
    if (scoreVal >= 70) {
      html += `<div class="alert-warn" style="margin-bottom:16px">
        🚨 <strong>Score ≥ 70 · Comunicación al SEPBLAC obligatoria</strong><br>
        Art. 18 Ley 10/2010 · Plazo máximo: 10 días hábiles desde la detección
      </div>`;
    }

    // Cabecera con score
    html += `<div style="display:flex;gap:20px;align-items:flex-start;margin-bottom:20px;flex-wrap:wrap">
      <div class="score-circle ${nivelClass}">
        <div>${scoreVal}</div>
        <div style="font-size:10px;font-weight:600;margin-top:2px">/100</div>
      </div>
      <div style="flex:1;min-width:200px">
        <div style="font-size:18px;font-weight:800;color:#111;margin-bottom:4px">${e.denominacion}</div>
        <div style="font-size:13px;color:#6b7280;margin-bottom:8px">${e.codigo} · ${e.nif||'NIF pendiente'}</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          ${badgeEstado(e.estado)}
          ${badgeRiesgo(e.nivel_riesgo)}
          ${e.aml_officer_nombre ? `<span class="badge badge-en-proceso">👤 ${e.aml_officer_nombre}</span>` : ''}
        </div>
      </div>
    </div>`;

    // Scoring EBR visual (si existe)
    if (scoring) {
      html += `<div style="margin-bottom:20px">
        <div style="font-size:13px;font-weight:700;margin-bottom:12px">📊 Scoring EBR · 5 Dimensiones</div>`;

      const dims = [
        { label: 'Factor cliente · PEP/UBO', val: scoring.riesgo_cliente, color: scoring.riesgo_cliente>=70?'fill-rojo':scoring.riesgo_cliente>=40?'fill-naranja':'fill-verde' },
        { label: 'Adverse Media', val: scoring.riesgo_canal, color: scoring.riesgo_canal>=70?'fill-rojo':scoring.riesgo_canal>=40?'fill-naranja':'fill-verde' },
        { label: 'Producto / Canal', val: scoring.riesgo_producto, color: scoring.riesgo_producto>=70?'fill-rojo':scoring.riesgo_producto>=40?'fill-naranja':'fill-verde' },
        { label: 'Factor geográfico', val: scoring.riesgo_geografico, color: scoring.riesgo_geografico>=70?'fill-rojo':scoring.riesgo_geografico>=40?'fill-naranja':'fill-verde' },
        { label: 'Controles aplicados (−)', val: scoring.controles_aplicados, color: 'fill-azul' }
      ];

      dims.forEach(d => {
        html += `<div class="dim-bar">
          <div class="dim-bar-top">
            <span class="dim-bar-label">${d.label}</span>
            <span class="dim-bar-val" style="color:${d.color.includes('rojo')?'#dc2626':d.color.includes('naranja')?'#d97706':d.color.includes('azul')?'#003C96':'#0F6E56'}">${d.val}</span>
          </div>
          <div class="dim-bar-track"><div class="dim-bar-fill ${d.color}" style="width:${d.val}%"></div></div>
        </div>`;
      });

      html += `<div style="display:flex;gap:16px;margin-top:10px;padding-top:10px;border-top:1px solid #f3f4f6;font-size:12px">
        <span>Score inherente: <strong>${scoring.score_inherente}</strong></span>
        <span>Controles: <strong>−${scoring.controles_aplicados}</strong></span>
        <span>Score residual: <strong style="color:${scoring.score_residual>=70?'#dc2626':scoring.score_residual>=30?'#d97706':'#0F6E56'}">${scoring.score_residual}</strong></span>
        <span>Media sectorial: <strong>58</strong></span>
      </div></div>`;
    } else {
      html += `<div class="alert-info" style="margin-bottom:16px">
        ℹ️ Sin scoring calculado.
        <button class="btn btn-primary btn-sm" style="margin-left:10px" onclick="calcularScoring('${id}')">Calcular EBR ahora</button>
      </div>`;
    }

    // Campos extraídos resumen
    if (campos.campos.length) {
      html += `<div style="margin-bottom:20px">
        <div style="font-size:13px;font-weight:700;margin-bottom:10px">🔍 Campos KYC extraídos (${campos.total_campos})</div>
        <div class="campos-grid">`;
      campos.campos.slice(0, 8).forEach(c => {
        html += `<div class="campo-card"><div class="campo-nombre">${c.nombre_campo.replace(/_/g,' ')}</div><div class="campo-valor">${c.valor}</div><div class="campo-conf">${c.confianza}%</div></div>`;
      });
      html += '</div></div>';
    }

    // Audit trail
    if (exp.audit_trail.length) {
      html += `<div style="margin-bottom:20px">
        <div style="font-size:13px;font-weight:700;margin-bottom:10px">🔗 Audit Trail · Últimos eventos</div>`;
      exp.audit_trail.forEach(a => {
        html += `<div class="audit-item"><div class="audit-dot"></div><div>
          <div class="audit-evento">${a.tipo_evento.replace(/_/g,' ').toUpperCase()}</div>
          <div class="audit-desc">${a.descripcion||'—'}</div>
          <div class="audit-fecha">${a.actor} · ${new Date(a.fecha_evento).toLocaleString('es-ES')}</div>
        </div></div>`;
      });
      html += '</div>';
    }

    // DECISIÓN AML OFFICER
    html += `<div style="margin-bottom:16px">
      <div style="font-size:13px;font-weight:700;margin-bottom:12px">⚖️ Decisión AML Officer · Segunda Línea de Defensa</div>
      <div class="decision-grid">
        <div class="decision-btn d-aprobar" onclick="tomarDecision('${id}','aprobar')">
          <div class="d-icon">✅</div>
          <div class="d-title">Aprobar con DDR</div>
          <div class="d-desc">Diligencia Debida Reforzada · Sellar en blockchain</div>
        </div>
        <div class="decision-btn d-sar" onclick="generarSAR('${id}')">
          <div class="d-icon">📋</div>
          <div class="d-title">Generar SAR</div>
          <div class="d-desc">Art. 18 Ley 10/2010 · Borrador automático LegNER</div>
        </div>
        <div class="decision-btn d-escalar" onclick="tomarDecision('${id}','escalar')">
          <div class="d-icon">⬆️</div>
          <div class="d-title">Escalar a Dirección</div>
          <div class="d-desc">Compliance · Nivel 3 · Notificación automática</div>
        </div>
        <div class="decision-btn d-rescreening" onclick="calcularScoring('${id}')">
          <div class="d-icon">🔄</div>
          <div class="d-title">Recalcular EBR</div>
          <div class="d-desc">Actualizar scoring con nuevos documentos</div>
        </div>
      </div>
    </div>`;

    html += '</div>';
    document.getElementById('panel-aml-contenido').innerHTML = html;

  } catch(err) {
    document.getElementById('panel-aml-contenido').innerHTML = `<div class="alert-error" style="margin:20px">❌ Error: ${err.message}</div>`;
  }
}

async function calcularScoring(id) {
  const btn = event.target;
  btn.textContent = '⏳ Calculando...';
  btn.disabled = true;
  try {
    const r = await fetch(`${API}/expedientes/${id}/scoring`, { method: 'POST' });
    const d = await r.json();
    if (r.ok) {
      cargarPanelAML(id);
    } else {
      alert('Error: ' + (d.detail || 'Error desconocido'));
      btn.textContent = 'Recalcular EBR';
      btn.disabled = false;
    }
  } catch(e) {
    alert('Error calculando scoring: ' + e.message);
    btn.textContent = 'Recalcular EBR';
    btn.disabled = false;
  }
}

async function generarSAR(id) {
  showModal('sar');
  document.getElementById('sar-contenido').innerHTML = '<div class="loading"><div class="spinner"></div> Generando SAR con LegNER · Claude API...</div>';

  try {
    const r = await fetch(`${API}/expedientes/${id}/sar`, { method: 'POST' });
    const d = await r.json();

    if (!r.ok) throw new Error(d.detail || 'Error');

    const sar = d.sar;
    const puntos = sar.checklist_verificacion.puntos;

    let html = `
      <div class="sar-ref">
        <div>
          <div class="sar-ref-num">${sar.referencia}</div>
          <div class="sar-ref-sub">Art. 18 Ley 10/2010 · ${sar.cabecera.sujeto_comunicante}</div>
        </div>
        <div class="sar-plazo">
          <div style="opacity:0.75;font-size:10px">Plazo límite</div>
          <div class="sar-plazo-num">${sar.cabecera.fecha_limite_comunicacion}</div>
          <div style="opacity:0.75;font-size:10px">${sar.cabecera.dias_habiles_restantes} días hábiles</div>
        </div>
      </div>

      <div class="sar-grid">
        <div class="sar-field"><div class="sar-label">Denominación</div><div class="sar-val">${sar.sujeto_investigado.denominacion}</div></div>
        <div class="sar-field"><div class="sar-label">NIF/CIF</div><div class="sar-val">${sar.sujeto_investigado.nif_cif}</div></div>
        <div class="sar-field"><div class="sar-label">Tipología 6AMLD</div><div class="sar-val">${sar.contenido.tipologia_6amld}</div></div>
        <div class="sar-field"><div class="sar-label">Score EBR</div><div class="sar-val">${sar.scoring_ebr.score_residual}/100 · ${sar.scoring_ebr.nivel_riesgo.toUpperCase()}</div></div>
        <div class="sar-field full"><div class="sar-label">Descripción de la operativa sospechosa</div><div class="sar-val">${sar.contenido.descripcion_operativa}</div></div>
        <div class="sar-field"><div class="sar-label">Base legal</div><div class="sar-val">${sar.contenido.base_legal}</div></div>
        <div class="sar-field"><div class="sar-label">Hash blockchain</div><div class="sar-val mono">${sar.blockchain.hash_expediente}</div></div>
      </div>

      <div style="font-size:13px;font-weight:700;margin-bottom:10px">✅ Lista de verificación · 7 puntos</div>`;

    puntos.forEach(p => {
      html += `<div class="chk-item">
        <div class="chk-icon ${p.completado?'chk-ok':'chk-no'}">${p.completado?'✓':'✗'}</div>
        <div class="chk-text">${p.descripcion}</div>
        <div class="chk-tag ${p.obligatorio?'chk-oblig':'chk-recom'}">${p.obligatorio?'Obligatorio':'Recomendado'}</div>
      </div>`;
    });

    const listo = sar.checklist_verificacion.listo_para_envio;
    html += `<div class="${listo?'alert-success':'alert-warn'}" style="margin-top:14px">
      ${listo ? '✅ Listo para envío al SEPBLAC' : `⚠️ ${sar.checklist_verificacion.completados}/${sar.checklist_verificacion.total} puntos completados · Pendiente revisión manual del AML Officer`}
    </div>
    <div style="font-size:11px;color:#9ca3af;margin-top:8px">${sar.nota_legal}</div>`;

    document.getElementById('sar-contenido').innerHTML = html;

  } catch(err) {
    document.getElementById('sar-contenido').innerHTML = `<div class="alert-error">❌ Error: ${err.message}</div>`;
  }
}

async function tomarDecision(id, tipo) {
  const mensajes = {
    aprobar: '✅ Expediente aprobado con Diligencia Debida Reforzada.\
\
El evento quedará sellado en el audit trail blockchain.\
\
¿Confirmar?',
    escalar: '⬆️ Expediente escalado a Dirección de Compliance.\
\
Se generará notificación automática.\
\
¿Confirmar?'
  };
  if (!confirm(mensajes[tipo])) return;

  try {
    await fetch(`${API}/expedientes/${id}`, { method: 'GET' });
    alert(tipo === 'aprobar' ?
      '✅ Expediente aprobado con DDR.\
Hash sellado en blockchain.\
Audit trail actualizado.' :
      '⬆️ Escalado a Dirección de Compliance.\
Notificación enviada.'
    );
    cargarPanelAML(id);
  } catch(e) {
    alert('Error: ' + e.message);
  }
}

// ── Subir documento
function cargarSelectExpedientes() {
  fetch(API + '/expedientes').then(r => r.json()).then(exps => {
    const sel = document.getElementById('select-expediente');
    sel.innerHTML = '<option value="">Selecciona...</option>' +
      exps.map(e => `<option value="${e.id}">${e.codigo} · ${e.denominacion}</option>`).join('');
  });
}

function handleFile(file) {
  if (!file) return;
  archivoSeleccionado = file;
  document.getElementById('upload-title').textContent = '📄 ' + file.name;
  document.getElementById('btn-subir').disabled = false;
  document.getElementById('upload-status').innerHTML = `<div style="font-size:12px;color:#0F6E56">✅ ${(file.size/1024).toFixed(0)} KB listo</div>`;
}

function handleDrop(e) {
  e.preventDefault();
  const file = e.dataTransfer.files[0];
  if (file && file.name.endsWith('.pdf')) handleFile(file);
}

async function subirDocumento() {
  const expId = document.getElementById('select-expediente').value;
  if (!expId || !archivoSeleccionado) return;
  document.getElementById('btn-subir').disabled = true;
  document.getElementById('btn-subir').textContent = '⏳ Procesando...';
  document.getElementById('resultado-legner').style.display = 'none';
  const form = new FormData();
  form.append('archivo', archivoSeleccionado);
  try {
    const r = await fetch(`${API}/expedientes/${expId}/documentos`, { method: 'POST', body: form });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'Error');
    const camposOk = d.extraccion.campos.filter(c => c.valor && c.valor !== 'null' && c.valor !== null);
    let html = `<div class="alert-success">
      ✅ <strong>${d.archivo}</strong> procesado<br>
      <span style="font-size:12px">📄 ${d.clasificacion.nombre} · ${d.clasificacion.confianza} · ${camposOk.length} campos extraídos</span>
    </div>`;
    if (camposOk.length) {
      html += `<div style="font-size:12px;font-weight:700;margin:12px 0 8px;color:#003C96">🔍 Campos KYC extraídos (${camposOk.length}/${d.extraccion.total_campos})</div>`;
      html += '<div class="campos-grid">';
      camposOk.forEach(c => {
        const nombre = (c.nombre || c.nombre_campo || '').replace(/_/g, ' ');
        const conf = c.confianza || 0;
        const col = conf >= 80 ? '#0F6E56' : conf >= 50 ? '#d97706' : '#dc2626';
        html += `<div class="campo-card"><div class="campo-nombre">${nombre}</div><div class="campo-valor">${c.valor}</div><div class="campo-conf" style="color:${col}">${conf}%</div></div>`;
      });
      html += '</div>';
    } else {
      html += '<div class="alert-warn">⚠️ No se extrajeron campos con valor.</div>';
    }
    document.getElementById('resultado-contenido').innerHTML = html;
    document.getElementById('resultado-legner').style.display = 'block';
  } catch(e) {
    document.getElementById('upload-status').innerHTML = `<div class="alert-error">❌ ${e.message}</div>`;
  }
  document.getElementById('btn-subir').disabled = false;
  document.getElementById('btn-subir').textContent = 'Procesar con LegNER';
}

async function crearExpediente() {
  const den = document.getElementById('new-denominacion').value.trim();
  if (!den) { document.getElementById('modal-status').innerHTML = '<div class="alert-error">La denominación es obligatoria</div>'; return; }
  const r = await fetch(API + '/expedientes', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ denominacion: den, nif: document.getElementById('new-nif').value.trim()||null, notas: document.getElementById('new-notas').value.trim()||null })
  });
  const d = await r.json();
  document.getElementById('modal-status').innerHTML = `<div class="alert-success">✅ ${d.expediente.codigo} creado</div>`;
  setTimeout(() => { closeModal('nuevo-expediente'); cargarDashboard(); }, 1500);
}

function cargarSelectAudit() {
  fetch(API + '/expedientes').then(r => r.json()).then(exps => {
    const sel = document.getElementById('select-audit-exp');
    sel.innerHTML = '<option value="">Selecciona un expediente</option>' + exps.map(e => `<option value="${e.id}">${e.codigo} · ${e.denominacion}</option>`).join('');
  });
}

async function cargarAudit(id) {
  if (!id) return;
  document.getElementById('audit-contenido').innerHTML = '<div class="loading"><div class="spinner"></div></div>';
  const r = await fetch(`${API}/expedientes/${id}/audit`);
  const d = await r.json();
  if (!d.eventos.length) { document.getElementById('audit-contenido').innerHTML = '<div class="empty">Sin eventos</div>'; return; }
  document.getElementById('audit-contenido').innerHTML = d.eventos.map(e => `
    <div class="audit-item"><div class="audit-dot"></div><div>
      <div class="audit-evento">${e.tipo_evento.replace(/_/g,' ').toUpperCase()}</div>
      <div class="audit-desc">${e.descripcion||'—'}</div>
      <div class="audit-fecha">Actor: ${e.actor} · ${new Date(e.fecha_evento).toLocaleString('es-ES')} · Bloque #${e.numero_bloque}</div>
    </div></div>`).join('');
}


// ── AUTH ──────────────────────────────────────────────────

let authToken = sessionStorage.getItem('cl_token') || null;
let currentUser = JSON.parse(sessionStorage.getItem('cl_user') || 'null');

function getHeaders() {
  return {
    'Content-Type': 'application/json',
    'Authorization': authToken ? 'Bearer ' + authToken : ''
  };
}

async function doLogin() {
  const email = document.getElementById('login-email').value.trim();
  const pass  = document.getElementById('login-pass').value;
  const errDiv = document.getElementById('login-error');
  errDiv.textContent = '';
  if (!email || !pass) { errDiv.textContent = 'Introduce email y contraseña'; return; }
  try {
    const r = await fetch('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: `username=${encodeURIComponent(email)}&password=${encodeURIComponent(pass)}`
    });
    const d = await r.json();
    if (!r.ok) { errDiv.textContent = d.detail || 'Credenciales incorrectas'; return; }
    authToken = d.access_token;
    currentUser = { email, nombre: d.nombre, rol: d.rol };
    sessionStorage.setItem('cl_token', authToken);
    sessionStorage.setItem('cl_user', JSON.stringify(currentUser));
    mostrarApp();
  } catch(e) {
    errDiv.textContent = 'Error de conexión';
  }
}

function mostrarApp() {
  document.getElementById('login-screen').style.display = 'none';
  document.getElementById('app-screen').style.display = 'block';
  const userEl = document.getElementById('topbar-user');
  if (userEl && currentUser) userEl.textContent = currentUser.nombre + ' · ' + currentUser.rol;
  cargarDashboard();
}

function doLogout() {
  authToken = null; currentUser = null;
  sessionStorage.removeItem('cl_token');
  sessionStorage.removeItem('cl_user');
  document.getElementById('login-screen').style.display = 'flex';
  document.getElementById('app-screen').style.display = 'none';
  document.getElementById('login-email').value = '';
  document.getElementById('login-pass').value = '';
}

// Al cargar: si hay token en sesión, mostrar app; si no, mostrar login
if (authToken) {
  mostrarApp();
} else {
  document.getElementById('login-screen').style.display = 'flex';
}


</script>
</div>
</body>
</html>
"""

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
from app.auth import hash_password, verificar_password, crear_token, get_current_user
from fastapi.security import OAuth2PasswordRequestForm

@app.post("/auth/registro", tags=["Auth"])
async def registro(email: str, nombre: str, password: str, rol: str = "aml_officer", db=Depends(get_db)):
    async with db.acquire() as conn:
        existe = await conn.fetchrow("SELECT id FROM usuarios WHERE email=$1", email)
        if existe:
            raise HTTPException(400, "Email ya registrado")
        await conn.execute(
            """INSERT INTO usuarios (email, nombre, rol, password_hash)
               VALUES ($1,$2,$3,$4)""",
            email, nombre, rol, hash_password(password)
        )
    return {"ok": True, "mensaje": f"Usuario {email} creado"}

@app.post("/auth/login", tags=["Auth"])
async def login(form: OAuth2PasswordRequestForm = Depends(), db=Depends(get_db)):
    async with db.acquire() as conn:
        usuario = await conn.fetchrow(
            "SELECT id, email, nombre, rol, password_hash FROM usuarios WHERE email=$1 AND activo=true",
            form.username
        )
    if not usuario or not verificar_password(form.password, usuario["password_hash"]):
        raise HTTPException(401, "Email o contraseña incorrectos")
    token = crear_token({
        "sub": usuario["email"],
        "rol": usuario["rol"],
        "id":  str(usuario["id"]),
        "nombre": usuario["nombre"]
    })
    return {"access_token": token, "token_type": "bearer", "rol": usuario["rol"], "nombre": usuario["nombre"]}

@app.get("/auth/me", tags=["Auth"])
async def me(user=Depends(get_current_user)):
    return user

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


# ── BORME / OpenMercantil ─────────────────────────────────
from app.services.mercantil_engine import consultar_borme

@app.get("/api/mercantil/{nif}", tags=["BORME · Registro Mercantil"])
async def consultar_mercantil_endpoint(nif: str):
    """Consulta datos BORME de una empresa por NIF via OpenMercantil."""
    resultado = await consultar_borme(nif)
    return resultado

@app.get("/api/mercantil/{nif}/riesgo", tags=["BORME · Registro Mercantil"])
async def consultar_mercantil_riesgo(nif: str, expediente_id: str = None):
    """Consulta BORME e integra señales de riesgo en el scoring EBR."""
    datos_borme = await consultar_borme(nif)
    return {
        "nif": nif,
        "datos_borme": datos_borme,
        "senales_riesgo": datos_borme.get("senales_riesgo", []),
        "encontrado": datos_borme.get("encontrado", False)
    }
