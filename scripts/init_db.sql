-- ============================================================
-- Compliance Lab — Modelo de datos KYC
-- Se ejecuta automáticamente al arrancar Docker por primera vez
-- ============================================================

-- Tipos enumerados (valores fijos que puede tener un campo)
-- ============================================================

-- Estados posibles de un expediente
CREATE TYPE estado_expediente AS ENUM (
  'borrador',           -- recién creado, sin documentos
  'en_revision',        -- AML Officer lo está revisando
  'pendiente_info',     -- falta documentación al cliente
  'aprobado',           -- KYC superado
  'rechazado',          -- KYC no supera los controles
  'sar_emitido'         -- se ha enviado SAR al SEPBLAC
);

-- Tipos de documento que maneja el sistema (los 8 del roadmap)
CREATE TYPE tipo_documento AS ENUM (
  'nota_simple',                    -- del Registro Mercantil
  'certificado_vigencia',           -- certifica que la empresa existe
  'certificado_administrador',      -- quién administra la empresa
  'escritura_constitucion',         -- escritura fundacional
  'acta_titularidad_real',          -- quiénes son los dueños reales
  'documento_identidad',            -- DNI / pasaporte
  'declaracion_actividad',          -- a qué se dedica la empresa
  'otros'                           -- documentos no clasificados
);

-- Nivel de riesgo calculado por el motor EBR
CREATE TYPE nivel_riesgo AS ENUM (
  'bajo',
  'medio',
  'alto',
  'muy_alto'
);


-- TABLA PRINCIPAL: Expedientes KYC
-- ============================================================
-- Cada expediente = una empresa que queremos verificar
CREATE TABLE expedientes (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  codigo            VARCHAR(20) UNIQUE NOT NULL,     -- ej: "EXP-2026-001"
  
  -- Datos de la empresa investigada
  denominacion      VARCHAR(500) NOT NULL,            -- nombre de la empresa
  nif               VARCHAR(20),                      -- NIF / CIF
  
  -- Estado y riesgo
  estado            estado_expediente DEFAULT 'borrador',
  nivel_riesgo      nivel_riesgo DEFAULT 'bajo',
  score_ebr         INTEGER DEFAULT 0,               -- puntuación 0-100
  
  -- AML Officer asignado
  aml_officer_id    UUID,
  
  -- Fechas
  creado_en         TIMESTAMPTZ DEFAULT NOW(),
  actualizado_en    TIMESTAMPTZ DEFAULT NOW(),
  fecha_aprobacion  TIMESTAMPTZ,
  
  -- Notas internas
  notas             TEXT
);

-- Índices para búsqueda rápida
CREATE INDEX idx_expedientes_nif ON expedientes(nif);
CREATE INDEX idx_expedientes_estado ON expedientes(estado);
CREATE INDEX idx_expedientes_codigo ON expedientes(codigo);


-- TABLA: Documentos de cada expediente
-- ============================================================
-- Cada PDF / imagen que se sube a un expediente
CREATE TABLE documentos (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  expediente_id     UUID NOT NULL REFERENCES expedientes(id) ON DELETE CASCADE,
  
  -- Información del archivo
  nombre_archivo    VARCHAR(500) NOT NULL,
  tipo              tipo_documento DEFAULT 'otros',
  ruta_almacenamiento VARCHAR(1000),                 -- dónde está guardado el PDF
  tamanio_bytes     BIGINT,
  
  -- Resultado del análisis IA (LegNER)
  confianza_clasificacion DECIMAL(5,2),              -- 0-100% de seguridad de la IA
  campos_extraidos  JSONB,                           -- los 47 campos en formato JSON
  texto_ocr         TEXT,                            -- texto extraído del PDF
  
  -- Estado del procesamiento
  procesado         BOOLEAN DEFAULT FALSE,
  error_procesado   TEXT,
  
  -- Fechas
  subido_en         TIMESTAMPTZ DEFAULT NOW(),
  procesado_en      TIMESTAMPTZ
);

CREATE INDEX idx_documentos_expediente ON documentos(expediente_id);
CREATE INDEX idx_documentos_tipo ON documentos(tipo);
-- Índice especial para buscar dentro del JSON de campos
CREATE INDEX idx_documentos_campos ON documentos USING GIN(campos_extraidos);


-- TABLA: Blockchain audit trail
-- ============================================================
-- Registro inmutable de todo lo que ocurre en cada expediente
-- Una vez insertado, NUNCA se modifica ni borra
CREATE TABLE audit_trail (
  id                BIGSERIAL PRIMARY KEY,
  expediente_id     UUID NOT NULL REFERENCES expedientes(id),
  
  -- Qué pasó
  evento            VARCHAR(100) NOT NULL,           -- ej: "documento_subido"
  descripcion       TEXT,
  actor             VARCHAR(200),                    -- quién lo hizo
  
  -- Datos del evento
  datos             JSONB,
  
  -- Encadenamiento blockchain
  hash_evento       VARCHAR(64) NOT NULL,            -- SHA-256 de este evento
  hash_anterior     VARCHAR(64),                     -- SHA-256 del evento previo
  
  -- Timestamp inmutable
  timestamp_utc     TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

CREATE INDEX idx_audit_expediente ON audit_trail(expediente_id);
CREATE INDEX idx_audit_timestamp ON audit_trail(timestamp_utc);


-- TABLA: Resultados del screening AML
-- ============================================================
-- Cruces contra listas OFAC, ONU, PEPs, Adverse Media
CREATE TABLE screening_resultados (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  expediente_id     UUID NOT NULL REFERENCES expedientes(id),
  
  -- Qué se buscó
  nombre_buscado    VARCHAR(500) NOT NULL,
  tipo_busqueda     VARCHAR(50),                     -- 'OFAC', 'ONU', 'PEP', 'ADVERSE_MEDIA'
  
  -- Qué se encontró
  hay_coincidencia  BOOLEAN DEFAULT FALSE,
  nivel_confianza   DECIMAL(5,2),
  detalles          JSONB,                           -- datos completos del resultado
  
  -- Fuente
  fuente            VARCHAR(200),                    -- 'OpenSanctions', 'ICIJ', etc.
  
  -- Fecha
  consultado_en     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_screening_expediente ON screening_resultados(expediente_id);


-- TABLA: Usuarios del sistema (AML Officers)
-- ============================================================
CREATE TABLE usuarios (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email             VARCHAR(255) UNIQUE NOT NULL,
  nombre            VARCHAR(500) NOT NULL,
  rol               VARCHAR(50) DEFAULT 'aml_officer', -- 'admin', 'aml_officer', 'viewer'
  activo            BOOLEAN DEFAULT TRUE,
  creado_en         TIMESTAMPTZ DEFAULT NOW()
);


-- DATOS DE PRUEBA (los borramos en producción)
-- ============================================================
INSERT INTO usuarios (id, email, nombre, rol) VALUES
  ('00000000-0000-0000-0000-000000000001', 'admin@jrvlab.com', 'Admin JRV Lab', 'admin'),
  ('00000000-0000-0000-0000-000000000002', 'aml@jrvlab.com', 'AML Officer Principal', 'aml_officer');

-- Expediente de prueba (Naper Brands, que tenemos documentos reales)
INSERT INTO expedientes (codigo, denominacion, nif, estado, aml_officer_id) VALUES
  ('EXP-2026-001', 'NAPER BRANDS S.L.', 'B-XXXXXXXX', 'en_revision', '00000000-0000-0000-0000-000000000002'),
  ('EXP-2026-002', 'NAMOZUL SPAIN S.L.', 'B-XXXXXXXY', 'borrador', '00000000-0000-0000-0000-000000000002'),
  ('EXP-2026-003', 'MARMENA INVESTMENTS S.L.', 'B-XXXXXXXZ', 'borrador', '00000000-0000-0000-0000-000000000002');

SELECT 'Base de datos Compliance Lab iniciada correctamente ✅' AS resultado;
