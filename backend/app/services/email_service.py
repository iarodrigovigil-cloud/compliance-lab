# ── app/services/email_service.py ──────────────────────────────────
#  COMPLIANCE LAB · Servicio de notificaciones por email (Agente 3)
#  JRV Lab S.L.
#
#  Envía resúmenes de alertas agrupados por organización:
#   - Admin + Supervisor: reciben TODAS las alertas de su organización
#   - AML Officer: recibe solo las alertas de expedientes asignados a
#     él (o sin asignar, ya que puede verlos igualmente)
# ─────────────────────────────────────────────────────────────────

import os
import asyncio
import aiosmtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")

SEVERIDAD_COLOR = {
    "critica": "#dc2626",
    "alta":    "#ea580c",
    "media":   "#ca8a04",
    "baja":    "#65a30d",
}

SEVERIDAD_ETIQUETA = {
    "critica": "CRÍTICA",
    "alta":    "ALTA",
    "media":   "MEDIA",
    "baja":    "BAJA",
}


async def _enviar_email(destinatario: str, asunto: str, html: str) -> bool:
    """Envía un email individual vía SMTP. Devuelve True/False, nunca lanza excepción."""
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASSWORD:
        print(f"[email_service] SMTP no configurado, omitiendo envío a {destinatario}")
        return False
    try:
        mensaje = MIMEMultipart("alternative")
        mensaje["Subject"] = asunto
        mensaje["From"] = f"Compliance Lab <{SMTP_USER}>"
        mensaje["To"] = destinatario
        mensaje.attach(MIMEText(html, "html", "utf-8"))

        await aiosmtplib.send(
            mensaje,
            hostname=SMTP_HOST,
            port=SMTP_PORT,
            username=SMTP_USER,
            password=SMTP_PASSWORD,
            start_tls=True,
        )
        return True
    except Exception as e:
        print(f"[email_service] Error enviando a {destinatario}: {e}")
        return False


def _construir_html(nombre_destinatario: str, org_nombre: str, alertas: list) -> str:
    filas = ""
    for a in alertas:
        color = SEVERIDAD_COLOR.get(a["severidad"], "#64748b")
        etiqueta = SEVERIDAD_ETIQUETA.get(a["severidad"], a["severidad"].upper())
        filas += f"""
        <tr>
            <td style="padding:12px 16px;border-bottom:1px solid #e2e8f0;">
                <span style="background:{color};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">{etiqueta}</span>
            </td>
            <td style="padding:12px 16px;border-bottom:1px solid #e2e8f0;">
                <strong>{a['titulo']}</strong><br>
                <span style="color:#64748b;font-size:13px;">{a['descripcion']}</span>
            </td>
        </tr>"""

    return f"""
    <html><body style="font-family:Arial,sans-serif;background:#f1f5f9;padding:24px;margin:0;">
        <div style="max-width:600px;margin:0 auto;background:#fff;border-radius:8px;overflow:hidden;">
            <div style="background:#0D1B2A;padding:20px 24px;">
                <h2 style="color:#fff;margin:0;font-size:18px;">🛡️ Compliance Lab</h2>
                <p style="color:#94a3b8;margin:4px 0 0;font-size:13px;">Resumen de alertas · Agente 3 · {org_nombre}</p>
            </div>
            <div style="padding:20px 24px;">
                <p style="color:#334155;">Hola {nombre_destinatario},</p>
                <p style="color:#334155;">El ciclo de supervisión automática ha detectado <strong>{len(alertas)} alerta(s)</strong> que requieren tu atención:</p>
            </div>
            <table style="width:100%;border-collapse:collapse;">{filas}</table>
            <div style="padding:20px 24px;">
                <a href="https://compliancelab.jrvlab.com" style="background:#003C96;color:#fff;padding:10px 20px;border-radius:6px;text-decoration:none;font-size:14px;">Ver en Compliance Lab →</a>
            </div>
            <div style="padding:16px 24px;background:#f8fafc;color:#94a3b8;font-size:11px;">
                JRV Lab S.L. · Compliance Lab · Notificación automática, no responder a este correo.
            </div>
        </div>
    </body></html>"""


async def notificar_resumen_alertas(conn, org_id: str, org_nombre: str, alertas_org: list):
    """
    Envía emails resumen a los destinatarios correspondientes de una organización.

    alertas_org: lista de dicts con las claves:
        titulo, descripcion, severidad, expediente_id, asignado_a (puede ser None)
    """
    if not alertas_org:
        return

    # ── Admin + Supervisor: reciben TODAS las alertas de la org ──
    destinatarios_generales = await conn.fetch(
        """SELECT email, nombre FROM usuarios
           WHERE organizacion_id=$1::uuid AND rol IN ('admin','supervisor')
           AND activo=true""",
        org_id)

    for dest in destinatarios_generales:
        html = _construir_html(dest["nombre"] or dest["email"], org_nombre, alertas_org)
        asunto = f"⚠️ Compliance Lab · {len(alertas_org)} alerta(s) en {org_nombre}"
        await _enviar_email(dest["email"], asunto, html)

    # ── AML Officers: solo alertas de sus expedientes asignados (o sin asignar) ──
    officers = await conn.fetch(
        """SELECT id, email, nombre FROM usuarios
           WHERE organizacion_id=$1::uuid AND rol='aml_officer' AND activo=true""",
        org_id)

    for officer in officers:
        officer_id = str(officer["id"])
        alertas_officer = [
            a for a in alertas_org
            if a.get("asignado_a") is None or str(a.get("asignado_a")) == officer_id
        ]
        if alertas_officer:
            html = _construir_html(officer["nombre"] or officer["email"], org_nombre, alertas_officer)
            asunto = f"⚠️ Compliance Lab · {len(alertas_officer)} alerta(s) en tus expedientes"
            await _enviar_email(officer["email"], asunto, html)
