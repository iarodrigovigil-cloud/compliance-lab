#!/usr/bin/env python3
"""
Compliance Lab — Script de prueba
Ejecuta esto para comprobar que la API y LegNER funcionan.
Uso: python test_rapido.py

IMPORTANTE: La API debe estar arrancada antes de ejecutar esto.
  uvicorn app.main:app --reload
"""
import httpx
import json
import asyncio

BASE_URL = "http://localhost:8000"

# Texto de prueba — simula el contenido de una Nota Simple real
TEXTO_NOTA_SIMPLE = """
REGISTRO MERCANTIL DE SEVILLA
NOTA SIMPLE INFORMATIVA

DENOMINACIÓN SOCIAL: NAPER BRANDS, S.L.
CIF: B-41234567
DOMICILIO SOCIAL: Avenida de la Constitución 15, 41001 Sevilla
OBJETO SOCIAL: Comercio al por mayor de productos de alimentación y bebidas
CAPITAL SOCIAL: 10.000,00 euros
FECHA DE CONSTITUCIÓN: 15 de marzo de 2018

ADMINISTRADOR ÚNICO: 
  Nombre: Juan Rodríguez Vázquez
  DNI: 28.456.789-A
  Cargo: Administrador Único
  Fecha de nombramiento: 15 de marzo de 2018

INSCRIPCIÓN: Tomo 5234, Folio 45, Sección 8ª, Hoja SE-89234
SITUACIÓN: Activa
"""

TEXTO_ACTA_TITULARIDAD = """
ACTA DE MANIFESTACIONES SOBRE TITULARIDAD REAL

En Sevilla, a 20 de abril de 2021, ante mí, Don Carlos Martínez López, 
Notario del Ilustre Colegio de Andalucía.

COMPARECE: Don Juan Rodríguez Vázquez, mayor de edad, DNI 28.456.789-A,
en nombre y representación de NAPER BRANDS S.L.

MANIFIESTA que los titulares reales de la sociedad son:

1. TITULAR REAL DIRECTO:
   Nombre: Juan Rodríguez Vázquez
   DNI: 28.456.789-A
   Porcentaje de participación: 60%
   Tipo de control: Participación directa
   Persona Políticamente Expuesta: NO

2. TITULAR REAL DIRECTO:
   Nombre: María González Pérez  
   DNI: 52.347.891-B
   Porcentaje de participación: 40%
   Tipo de control: Participación directa
   Persona Políticamente Expuesta: NO

Número de protocolo: 1234/2021
"""


async def probar_api():
    """Ejecuta todas las pruebas en orden"""
    
    print("\n" + "="*60)
    print("  COMPLIANCE LAB — PRUEBA DE SISTEMA")
    print("="*60)
    
    async with httpx.AsyncClient(timeout=60.0) as cliente:
        
        # ---- PRUEBA 1: ¿Está la API activa? ----
        print("\n📡 PRUEBA 1: Verificar que la API funciona...")
        try:
            resp = await cliente.get(f"{BASE_URL}/")
            if resp.status_code == 200:
                datos = resp.json()
                print(f"  ✅ API activa: {datos['producto']} v{datos['version']}")
            else:
                print(f"  ❌ Error: {resp.status_code}")
                return
        except httpx.ConnectError:
            print("  ❌ No se puede conectar. ¿Está arrancado el servidor?")
            print("     Ejecuta: uvicorn app.main:app --reload")
            return
        
        # ---- PRUEBA 2: Crear un expediente ----
        print("\n📁 PRUEBA 2: Crear expediente KYC...")
        resp = await cliente.post(f"{BASE_URL}/expedientes", json={
            "denominacion": "NAPER BRANDS S.L.",
            "nif": "B-41234567",
            "notas": "Expediente de prueba automatizado"
        })
        datos = resp.json()
        exp_id = datos["expediente"]["id"]
        exp_codigo = datos["expediente"]["codigo"]
        print(f"  ✅ Expediente creado: {exp_codigo} (ID: {exp_id[:8]}...)")
        
        # ---- PRUEBA 3: LegNER con Nota Simple ----
        print("\n🤖 PRUEBA 3: LegNER analizando Nota Simple...")
        resp = await cliente.post(f"{BASE_URL}/test/analizar-texto", json={
            "texto": TEXTO_NOTA_SIMPLE
        })
        resultado = resp.json()
        if resultado.get("exito"):
            print(f"  ✅ Tipo detectado: {resultado['tipo']}")
            print(f"  ✅ Confianza: {resultado['confianza']}%")
            print(f"  ✅ Empresa: {resultado.get('empresa_detectada', 'N/A')}")
            campos = resultado.get("campos", {})
            if "denominacion_social" in campos:
                print(f"  ✅ Denominación extraída: {campos['denominacion_social']}")
            if "nif_cif" in campos:
                print(f"  ✅ NIF extraído: {campos['nif_cif']}")
        else:
            print(f"  ❌ Error en LegNER: {resultado.get('error', 'desconocido')}")
        
        # ---- PRUEBA 4: LegNER con Acta de Titularidad ----
        print("\n🤖 PRUEBA 4: LegNER analizando Acta de Titularidad Real...")
        resp = await cliente.post(f"{BASE_URL}/test/analizar-texto", json={
            "texto": TEXTO_ACTA_TITULARIDAD
        })
        resultado = resp.json()
        if resultado.get("exito"):
            print(f"  ✅ Tipo detectado: {resultado['tipo']}")
            print(f"  ✅ Confianza: {resultado['confianza']}%")
            campos = resultado.get("campos", {})
            titulares = campos.get("titulares_reales", [])
            print(f"  ✅ Titulares reales encontrados: {len(titulares)}")
            for t in titulares:
                print(f"     → {t.get('nombre', 'N/A')}: {t.get('porcentaje_participacion', '?')}%")
        else:
            print(f"  ❌ Error: {resultado.get('error')}")
        
        # ---- PRUEBA 5: Audit Trail Blockchain ----
        print("\n🔗 PRUEBA 5: Verificar blockchain audit trail...")
        resp = await cliente.get(f"{BASE_URL}/expedientes/{exp_id}/audit-trail")
        audit = resp.json()
        print(f"  ✅ Eventos registrados: {audit['total_eventos']}")
        print(f"  ✅ Cadena blockchain válida: {audit['cadena_valida']}")
        for ev in audit["eventos"]:
            print(f"     → [{ev['evento']}] {ev['descripcion'][:50]}...")
        
        print("\n" + "="*60)
        print("  ✅ TODAS LAS PRUEBAS COMPLETADAS")
        print("="*60)
        print(f"\n📖 Documentación interactiva: {BASE_URL}/docs")
        print(f"🗄️  Panel de base de datos: http://localhost:8080\n")


if __name__ == "__main__":
    asyncio.run(probar_api())
