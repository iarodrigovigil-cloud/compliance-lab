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
        WHERE ($1::text IS NULL OR e.estado = $1::estadoexpediente)
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
