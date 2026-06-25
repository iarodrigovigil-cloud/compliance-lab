# ── agent_loop.py ────────────────────────────────────────
import anthropic
import os
from app.agent_tools import TOOLS_SCHEMA, ejecutar_tool

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """Eres el Agente KYC de Compliance Lab, sistema AML español.
Tienes herramientas para analizar expedientes según Ley 10/2010 y SEPBLAC.
Orden lógico: primero consulta el expediente, luego clasifica documentos,
luego calcula EBR, y si el riesgo es alto genera borrador SAR.
Entrega un resumen estructurado con: documentos encontrados, campos clave
extraídos, nivel de riesgo EBR y recomendación final."""

async def ejecutar_agente(prompt_usuario: str, db_pool) -> dict:
    mensajes = [{"role": "user", "content": prompt_usuario}]
    tools_usadas = []
    pasos = []
    max_iteraciones = 10

    for iteracion in range(max_iteraciones):

        respuesta = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS_SCHEMA,
            messages=mensajes
        )

        if respuesta.stop_reason == "end_turn":
            texto_final = "".join(
                b.text for b in respuesta.content
                if hasattr(b, "text")
            )
            return {
                "respuesta_final": texto_final,
                "tools_usadas": tools_usadas,
                "pasos": pasos
            }

        if respuesta.stop_reason == "tool_use":
            mensajes.append({
                "role": "assistant",
                "content": respuesta.content
            })

            resultados_tools = []

            for bloque in respuesta.content:
                if bloque.type != "tool_use":
                    continue

                nombre_tool = bloque.name
                inputs_tool  = bloque.input
                tools_usadas.append(nombre_tool)

                resultado = await ejecutar_tool(nombre_tool, inputs_tool, db_pool)

                pasos.append({
                    "tool": nombre_tool,
                    "inputs": inputs_tool,
                    "resultado_resumen": str(resultado)[:200]
                })

                resultados_tools.append({
                    "type": "tool_result",
                    "tool_use_id": bloque.id,
                    "content": str(resultado)
                })

            mensajes.append({
                "role": "user",
                "content": resultados_tools
            })

    return {
        "respuesta_final": "Límite de iteraciones alcanzado.",
        "tools_usadas": tools_usadas,
        "pasos": pasos
    }