# ── agent_tools.py ──────────────────────────────────────
import os

# ── 1. SCHEMA: las tools que Claude puede usar
TOOLS_SCHEMA = [
    {
        "name": "clasificar_documento",
        "description": "Clasifica un documento KYC y extrae sus campos usando LegNER.",
        "input_schema": {
            "type": "object",
            "properties": {
                "documento_id": {"type": "string", "description": "UUID del documento en BD"},
                "texto":        {"type": "string", "description": "Texto extraído del PDF"}
            },
            "required": ["documento_id", "texto"]
        }
    },
    {
        "name": "calcular_riesgo_ebr",
        "description": "Calcula el scoring EBR (riesgo AML) de un expediente.",
        "input_schema": {
            "type": "object",
            "properties": {
                "expediente_id": {"type": "string"}
            },
            "required": ["expediente_id"]
        }
    },
    {
        "name": "generar_sar_borrador",
        "description": "Genera un borrador SAR Art. 18 Ley 10/2010 para un expediente.",
        "input_schema": {
            "type": "object",
            "properties": {
                "expediente_id": {"type": "string"},
                "motivo":        {"type": "string", "description": "Motivo de sospecha"}
            },
            "required": ["expediente_id"]
        }
    },
    {
        "name": "consultar_expediente",
        "description": "Consulta la BD y devuelve datos del expediente: documentos, campos extraídos, scoring.",
        "input_schema": {
            "type": "object",
            "properties": {
                "expediente_id": {"type": "string"}
            },
            "required": ["expediente_id"]
        }
    },
    {
        "name": "guardar_resultado",
        "description": "Guarda una nota o resultado en el audit_trail del expediente.",
        "input_schema": {
            "type": "object",
            "properties": {
                "expediente_id": {"type": "string"},
                "accion":        {"type": "string"},
                "detalle":       {"type": "string"}
            },
            "required": ["expediente_id", "accion"]
        }
    }
]


# ── 2. EJECUTOR: cuando Claude elige una tool, aquí se llama
async def ejecutar_tool(nombre: str, inputs: dict, db_pool) -> str:

    if nombre == "clasificar_documento":
        from app.services.legner_engine import extraer_campos
        tipo = inputs.get("tipo_documento", "documento_generico")
        resultado = extraer_campos(inputs["texto"], tipo)
        return str(resultado)

    elif nombre == "calcular_riesgo_ebr":
        from app.services.ebr_engine import calcular_ebr
        # Obtener campos y denominacion de la BD antes de llamar a EBR
        async with db_pool.acquire() as conn:
            exp = await conn.fetchrow(
                "SELECT denominacion, nif FROM expedientes WHERE id=$1",
                inputs["expediente_id"]
            )
            campos = await conn.fetch(
                "SELECT nombre_campo, valor, confianza FROM campos_extraidos WHERE expediente_id=$1",
                inputs["expediente_id"]
            )
        campos_lista = [dict(c) for c in campos]
        denominacion = dict(exp)["denominacion"] if exp else "Desconocido"
        nif = dict(exp).get("nif") if exp else None
        resultado = calcular_ebr(
            inputs["expediente_id"], campos_lista, denominacion, nif
        )
        return str(resultado)

    elif nombre == "generar_sar_borrador":
        from app.services.sar_engine import generar_sar
        resultado = await generar_sar(
            inputs["expediente_id"],
            inputs.get("motivo", ""),
            db_pool
        )
        return str(resultado)

    elif nombre == "consultar_expediente":
        async with db_pool.acquire() as conn:
            exp = await conn.fetchrow(
                "SELECT * FROM expedientes WHERE id=$1",
                inputs["expediente_id"]
            )
            docs = await conn.fetch(
                "SELECT tipo_documento, estado_procesamiento FROM documentos WHERE expediente_id=$1",
                inputs["expediente_id"]
            )
        return f"Expediente: {dict(exp)}, Documentos: {[dict(d) for d in docs]}"

    elif nombre == "guardar_resultado":
        import uuid as _uuid
        async with db_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO audit_trail
                   (expediente_id, tipo_evento, descripcion, actor, hash_evento)
                   VALUES ($1,$2,$3,'agente-kyc',$4)""",
                inputs["expediente_id"],
                inputs["accion"],
                inputs.get("detalle", ""),
                str(_uuid.uuid4()).replace("-", "")[:64]
            )
        return "Guardado en audit_trail"

    return f"Tool '{nombre}' no reconocida"