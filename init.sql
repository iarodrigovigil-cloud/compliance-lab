-- ============================================================
--  COMPLIANCE LAB · Modelo de datos KYC
--  JRV Lab S.L. · 2026
--  Se ejecuta automáticamente cuando Docker arranca PostgreSQL
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- TABLA 1: EXPEDIENTES KYC
CREATE TABLE IF NOT EXISTS expedientes (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    codigo              VARCHAR(20) UNIQUE NOT NULL,
    denominacion        VARCHAR(255) NOT NULL,
    nif                 VARCHAR(20),
    tipo_entidad        VARCHAR(50) DEFAULT 'persona_juridica',
    estado              VARCHAR(50) DEFAULT 'pendiente',
    score_ebr           INTEGER DEFAULT 0,
    nivel_riesgo        VARCHAR(20) DEFAULT 'sin_calcular',
    aml_officer_id      UUID,
    aml_officer_nombre  VARCHAR(100),
    fecha_creacion      TIMESTAMP DEFAULT NOW(),
    fecha_actualizacion TIMESTAMP DEFAULT NOW(),
    fecha_cierre        TIMESTAMP,
    notas               TEXT
);

-- TABLA 2: DOCUMENTOS
CREATE TABLE IF NOT EXISTS documentos (
    id                          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    expediente_id               UUID NOT NULL REFERENCES expedientes(id) ON DELETE CASCADE,
    nombre_archivo              VARCHAR(255) NOT NULL,
    ruta_archivo                VARCHAR(500),
    tamanio_bytes               INTEGER,
    formato                     VARCHAR(20),
    tipo_documento              VARCHAR(100),
    confianza_clasificacion     DECIMAL(5,2),
    estado_procesamiento        VARCHAR(50) DEFAULT 'subido',
    requiere_revision_manual    BOOLEAN DEFAULT FALSE,
    motivo_revision             VARCHAR(255),
    texto_extraido              TEXT,
    fecha_subida                TIMESTAMP DEFAULT NOW(),
    fecha_procesado             TIMESTAMP
);

-- TABLA 3: CAMPOS EXTRAÍDOS (los 47 campos de LegNER)
CREATE TABLE IF NOT EXISTS campos_extraidos (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    documento_id            UUID NOT NULL REFERENCES documentos(id) ON DELETE CASCADE,
    expediente_id           UUID NOT NULL REFERENCES expedientes(id) ON DELETE CASCADE,
    nombre_campo            VARCHAR(100) NOT NULL,
    valor                   TEXT,
    tipo_campo              VARCHAR(20),
    confianza               DECIMAL(5,2),
    revisado_manualmente    BOOLEAN DEFAULT FALSE,
    valor_corregido         TEXT,
    indice_repeticion       INTEGER DEFAULT 0,
    fecha_extraccion        TIMESTAMP DEFAULT NOW()
);

-- TABLA 4: SCORING AML / EBR (5 dimensiones de riesgo)
CREATE TABLE IF NOT EXISTS scoring_aml (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    expediente_id           UUID NOT NULL REFERENCES expedientes(id) ON DELETE CASCADE,
    riesgo_cliente          INTEGER DEFAULT 0,
    riesgo_geografico       INTEGER DEFAULT 0,
    riesgo_producto         INTEGER DEFAULT 0,
    riesgo_canal            INTEGER DEFAULT 0,
    riesgo_otro             INTEGER DEFAULT 0,
    score_inherente         INTEGER DEFAULT 0,
    controles_aplicados     INTEGER DEFAULT 0,
    score_residual          INTEGER DEFAULT 0,
    nivel_riesgo            VARCHAR(20),
    umbral_sar              BOOLEAN DEFAULT FALSE,
    screening_ofac          BOOLEAN DEFAULT FALSE,
    screening_ue_onu        BOOLEAN DEFAULT FALSE,
    screening_pep           BOOLEAN DEFAULT FALSE,
    screening_adverse       BOOLEAN DEFAULT FALSE,
    hits_encontrados        INTEGER DEFAULT 0,
    detalle_hits            JSONB,
    fecha_calculo           TIMESTAMP DEFAULT NOW()
);

-- TABLA 5: BLOCKCHAIN / AUDIT TRAIL (registro inmutable AMLR 2024)
CREATE TABLE IF NOT EXISTS audit_trail (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    expediente_id   UUID REFERENCES expedientes(id),
    documento_id    UUID REFERENCES documentos(id),
    tipo_evento     VARCHAR(100) NOT NULL,
    descripcion     TEXT,
    datos_evento    JSONB,
    actor           VARCHAR(100),
    ip_origen       INET,
    hash_evento     VARCHAR(64) NOT NULL,
    hash_anterior   VARCHAR(64),
    numero_bloque   INTEGER,
    fecha_evento    TIMESTAMP DEFAULT NOW()
);

-- TABLA 6: USUARIOS / AML OFFICERS
CREATE TABLE IF NOT EXISTS usuarios (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email           VARCHAR(255) UNIQUE NOT NULL,
    nombre          VARCHAR(100) NOT NULL,
    rol             VARCHAR(50) DEFAULT 'aml_officer',
    activo          BOOLEAN DEFAULT TRUE,
    fecha_creacion  TIMESTAMP DEFAULT NOW()
);

-- ÍNDICES
CREATE INDEX IF NOT EXISTS idx_expedientes_estado ON expedientes(estado);
CREATE INDEX IF NOT EXISTS idx_expedientes_nif ON expedientes(nif);
CREATE INDEX IF NOT EXISTS idx_documentos_expediente ON documentos(expediente_id);
CREATE INDEX IF NOT EXISTS idx_campos_documento ON campos_extraidos(documento_id);
CREATE INDEX IF NOT EXISTS idx_audit_expediente ON audit_trail(expediente_id);

-- DATOS DE PRUEBA (basados en los documentos reales del proyecto)
INSERT INTO expedientes (codigo, denominacion, nif, estado, nivel_riesgo, aml_officer_nombre) VALUES
    ('EXP-2026-001', 'NAPER BRANDS SL',       'B-XXXXXXXX', 'en_proceso', 'medio',         'Officer Demo'),
    ('EXP-2026-002', 'NAMOZUL SPAIN SL',       'B-XXXXXXXY', 'pendiente',  'sin_calcular',  'Officer Demo'),
    ('EXP-2026-003', 'MARMENA INVESTMENTS SL', 'B-XXXXXXXZ', 'pendiente',  'sin_calcular',  NULL)
ON CONFLICT DO NOTHING;

DO $$ BEGIN
    RAISE NOTICE '✅ Base de datos Compliance Lab inicializada';
    RAISE NOTICE '   6 tablas KYC creadas: expedientes, documentos, campos_extraidos, scoring_aml, audit_trail, usuarios';
    RAISE NOTICE '   3 expedientes de prueba insertados';
END $$;
