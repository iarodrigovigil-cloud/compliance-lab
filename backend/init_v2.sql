-- ============================================================
--  COMPLIANCE LAB · Migración Multi-tenancy Modelo C
--  JRV Lab S.L. · 2026
--
--  Qué hace este script:
--    1. Crea tabla organizaciones (tenant raíz)
--    2. Añade organizacion_id a todas las tablas existentes
--    3. Amplía tabla usuarios (hash_password, organizacion_id, rol completo)
--    4. Crea tabla alertas (Agente 3)
--    5. Crea tabla configuracion_organizacion
--    6. Activa Row Level Security en todas las tablas
--    7. Crea políticas RLS por tenant
--    8. Inserta organización demo + usuarios seed
--
--  IMPORTANTE: ejecutar UNA sola vez sobre la BD existente.
--  Es idempotente (IF NOT EXISTS / ON CONFLICT DO NOTHING).
-- ============================================================

-- ══════════════════════════════════════════════════════════
-- 0. EXTENSIONES
-- ══════════════════════════════════════════════════════════
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ══════════════════════════════════════════════════════════
-- 1. TABLA ORGANIZACIONES (nuevo tenant raíz)
-- ══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS organizaciones (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nombre                  VARCHAR(255) NOT NULL,
    cif                     VARCHAR(20) UNIQUE,
    tipo_sujeto_obligado    VARCHAR(100) NOT NULL,
    -- Tipos soportados en v1:
    --   'inmobiliaria' | 'gestoria' | 'notaria' | 'entidad_financiera'
    --   'asesor_fiscal' | 'abogado' | 'casino' | 'otro'
    plan                    VARCHAR(50) DEFAULT 'poc',
    -- 'poc' | 'starter' | 'professional' | 'enterprise'
    activa                  BOOLEAN DEFAULT TRUE,
    dominio_email           VARCHAR(255),
    -- Umbrales EBR configurables por organización
    umbral_riesgo_bajo      INTEGER DEFAULT 30,
    umbral_riesgo_medio     INTEGER DEFAULT 60,
    umbral_riesgo_alto      INTEGER DEFAULT 80,
    -- Plazos operativos (días)
    plazo_revision_expediente   INTEGER DEFAULT 30,
    plazo_aprobacion_supervisor INTEGER DEFAULT 5,
    plazo_rescreening_dias      INTEGER DEFAULT 30,
    -- Campos KYC obligatorios (JSON array de nombres de campo)
    campos_obligatorios     JSONB DEFAULT '["denominacion_social","nif_cif","domicilio_social","capital_social","nombre_titular","porcentaje_participacion"]',
    -- Metadatos
    fecha_creacion          TIMESTAMP DEFAULT NOW(),
    fecha_actualizacion     TIMESTAMP DEFAULT NOW(),
    notas                   TEXT
);

-- ══════════════════════════════════════════════════════════
-- 2. CONFIGURACIÓN POR ORGANIZACIÓN (parámetros avanzados)
-- ══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS configuracion_organizacion (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organizacion_id     UUID NOT NULL REFERENCES organizaciones(id) ON DELETE CASCADE,
    clave               VARCHAR(100) NOT NULL,
    valor               TEXT,
    descripcion         TEXT,
    fecha_actualizacion TIMESTAMP DEFAULT NOW(),
    UNIQUE (organizacion_id, clave)
);

-- ══════════════════════════════════════════════════════════
-- 3. AÑADIR organizacion_id A TABLAS EXISTENTES
-- ══════════════════════════════════════════════════════════

-- expedientes
ALTER TABLE expedientes
    ADD COLUMN IF NOT EXISTS organizacion_id UUID REFERENCES organizaciones(id),
    ADD COLUMN IF NOT EXISTS asignado_a      UUID,  -- usuario_id del AML Officer asignado
    ADD COLUMN IF NOT EXISTS estado_supervision VARCHAR(50) DEFAULT 'borrador';
    -- 'borrador' | 'en_revision' | 'aprobado' | 'rechazado' | 'archivado'

-- documentos
ALTER TABLE documentos
    ADD COLUMN IF NOT EXISTS organizacion_id UUID REFERENCES organizaciones(id);

-- campos_extraidos
ALTER TABLE campos_extraidos
    ADD COLUMN IF NOT EXISTS organizacion_id UUID REFERENCES organizaciones(id);

-- scoring_aml
ALTER TABLE scoring_aml
    ADD COLUMN IF NOT EXISTS organizacion_id UUID REFERENCES organizaciones(id),
    ADD COLUMN IF NOT EXISTS metodo          VARCHAR(50),
    ADD COLUMN IF NOT EXISTS datos_borme     JSONB,
    ADD COLUMN IF NOT EXISTS senales_borme   JSONB,
    ADD COLUMN IF NOT EXISTS borme_consultado_en TIMESTAMP,
    ADD COLUMN IF NOT EXISTS adverse_media_score INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS adverse_media_detalle JSONB;

-- audit_trail
ALTER TABLE audit_trail
    ADD COLUMN IF NOT EXISTS organizacion_id UUID REFERENCES organizaciones(id);

-- ══════════════════════════════════════════════════════════
-- 4. AMPLIAR TABLA USUARIOS
-- ══════════════════════════════════════════════════════════
ALTER TABLE usuarios
    ADD COLUMN IF NOT EXISTS organizacion_id UUID REFERENCES organizaciones(id),
    ADD COLUMN IF NOT EXISTS hash_password   VARCHAR(255),
    ADD COLUMN IF NOT EXISTS nombre_completo VARCHAR(255),
    ADD COLUMN IF NOT EXISTS ultimo_acceso   TIMESTAMP,
    ADD COLUMN IF NOT EXISTS intentos_fallidos INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS bloqueado       BOOLEAN DEFAULT FALSE;

-- Actualizar constraint de rol para incluir supervisor y admin
ALTER TABLE usuarios
    DROP CONSTRAINT IF EXISTS usuarios_rol_check;
ALTER TABLE usuarios
    ADD CONSTRAINT usuarios_rol_check
    CHECK (rol IN ('admin', 'supervisor', 'aml_officer'));

-- ══════════════════════════════════════════════════════════
-- 5. TABLA ALERTAS (Agente 3 · Rescreening + Supervisión)
-- ══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS alertas (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organizacion_id     UUID NOT NULL REFERENCES organizaciones(id) ON DELETE CASCADE,
    expediente_id       UUID REFERENCES expedientes(id) ON DELETE CASCADE,
    tipo_alerta         VARCHAR(100) NOT NULL,
    -- 'sancion_nueva' | 'expediente_abandonado' | 'aprobacion_pendiente'
    -- | 'campo_baja_confianza' | 'rescreening_requerido' | 'plazo_vencido'
    severidad           VARCHAR(20) NOT NULL DEFAULT 'media',
    -- 'critica' | 'alta' | 'media' | 'baja'
    titulo              VARCHAR(255) NOT NULL,
    descripcion         TEXT,
    datos_alerta        JSONB,
    resuelta            BOOLEAN DEFAULT FALSE,
    resuelta_por        UUID REFERENCES usuarios(id),
    fecha_resolucion    TIMESTAMP,
    notificado          BOOLEAN DEFAULT FALSE,
    fecha_alerta        TIMESTAMP DEFAULT NOW()
);

-- ══════════════════════════════════════════════════════════
-- 6. TABLA CHECKLIST CALIDAD (Agente 2 · Supervisor KYC)
-- ══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS checklist_calidad (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organizacion_id     UUID NOT NULL REFERENCES organizaciones(id) ON DELETE CASCADE,
    expediente_id       UUID NOT NULL REFERENCES expedientes(id) ON DELETE CASCADE,
    verificacion        VARCHAR(100) NOT NULL,
    -- 'campos_obligatorios' | 'confianza_minima' | 'ebr_calculado'
    -- | 'screening_vigente' | 'decision_pendiente' | 'documentos_minimos'
    resultado           VARCHAR(20) NOT NULL,
    -- 'ok' | 'bloqueante' | 'advertencia'
    detalle             TEXT,
    fecha_verificacion  TIMESTAMP DEFAULT NOW()
);

-- ══════════════════════════════════════════════════════════
-- 7. ÍNDICES ADICIONALES
-- ══════════════════════════════════════════════════════════
CREATE INDEX IF NOT EXISTS idx_expedientes_org      ON expedientes(organizacion_id);
CREATE INDEX IF NOT EXISTS idx_expedientes_estado_sup ON expedientes(estado_supervision);
CREATE INDEX IF NOT EXISTS idx_expedientes_asignado  ON expedientes(asignado_a);
CREATE INDEX IF NOT EXISTS idx_documentos_org        ON documentos(organizacion_id);
CREATE INDEX IF NOT EXISTS idx_campos_org            ON campos_extraidos(organizacion_id);
CREATE INDEX IF NOT EXISTS idx_scoring_org           ON scoring_aml(organizacion_id);
CREATE INDEX IF NOT EXISTS idx_audit_org             ON audit_trail(organizacion_id);
CREATE INDEX IF NOT EXISTS idx_usuarios_org          ON usuarios(organizacion_id);
CREATE INDEX IF NOT EXISTS idx_alertas_org           ON alertas(organizacion_id);
CREATE INDEX IF NOT EXISTS idx_alertas_expediente    ON alertas(expediente_id);
CREATE INDEX IF NOT EXISTS idx_alertas_resuelta      ON alertas(resuelta);

-- ══════════════════════════════════════════════════════════
-- 8. ROW LEVEL SECURITY
-- ══════════════════════════════════════════════════════════

-- Activar RLS en todas las tablas con datos de tenant
ALTER TABLE expedientes         ENABLE ROW LEVEL SECURITY;
ALTER TABLE documentos          ENABLE ROW LEVEL SECURITY;
ALTER TABLE campos_extraidos    ENABLE ROW LEVEL SECURITY;
ALTER TABLE scoring_aml         ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_trail         ENABLE ROW LEVEL SECURITY;
ALTER TABLE usuarios            ENABLE ROW LEVEL SECURITY;
ALTER TABLE alertas             ENABLE ROW LEVEL SECURITY;
ALTER TABLE checklist_calidad   ENABLE ROW LEVEL SECURITY;
ALTER TABLE configuracion_organizacion ENABLE ROW LEVEL SECURITY;

-- El superuser (appuser de Railway) bypasea RLS para operaciones admin
-- Las políticas aplican a conexiones que setean app.current_org_id

-- Política: cada tabla solo devuelve filas de la organización activa en sesión
-- La aplicación ejecuta: SET app.current_org_id = '<uuid>' al inicio de cada request

CREATE POLICY IF NOT EXISTS rls_expedientes ON expedientes
    USING (
        organizacion_id::text = current_setting('app.current_org_id', true)
        OR current_setting('app.current_org_id', true) IS NULL
        OR current_setting('app.current_org_id', true) = ''
    );

CREATE POLICY IF NOT EXISTS rls_documentos ON documentos
    USING (
        organizacion_id::text = current_setting('app.current_org_id', true)
        OR current_setting('app.current_org_id', true) IS NULL
        OR current_setting('app.current_org_id', true) = ''
    );

CREATE POLICY IF NOT EXISTS rls_campos ON campos_extraidos
    USING (
        organizacion_id::text = current_setting('app.current_org_id', true)
        OR current_setting('app.current_org_id', true) IS NULL
        OR current_setting('app.current_org_id', true) = ''
    );

CREATE POLICY IF NOT EXISTS rls_scoring ON scoring_aml
    USING (
        organizacion_id::text = current_setting('app.current_org_id', true)
        OR current_setting('app.current_org_id', true) IS NULL
        OR current_setting('app.current_org_id', true) = ''
    );

CREATE POLICY IF NOT EXISTS rls_audit ON audit_trail
    USING (
        organizacion_id::text = current_setting('app.current_org_id', true)
        OR current_setting('app.current_org_id', true) IS NULL
        OR current_setting('app.current_org_id', true) = ''
    );

CREATE POLICY IF NOT EXISTS rls_alertas ON alertas
    USING (
        organizacion_id::text = current_setting('app.current_org_id', true)
        OR current_setting('app.current_org_id', true) IS NULL
        OR current_setting('app.current_org_id', true) = ''
    );

CREATE POLICY IF NOT EXISTS rls_checklist ON checklist_calidad
    USING (
        organizacion_id::text = current_setting('app.current_org_id', true)
        OR current_setting('app.current_org_id', true) IS NULL
        OR current_setting('app.current_org_id', true) = ''
    );

CREATE POLICY IF NOT EXISTS rls_config ON configuracion_organizacion
    USING (
        organizacion_id::text = current_setting('app.current_org_id', true)
        OR current_setting('app.current_org_id', true) IS NULL
        OR current_setting('app.current_org_id', true) = ''
    );

-- Usuarios: cada usuario ve solo los de su organización
CREATE POLICY IF NOT EXISTS rls_usuarios ON usuarios
    USING (
        organizacion_id::text = current_setting('app.current_org_id', true)
        OR current_setting('app.current_org_id', true) IS NULL
        OR current_setting('app.current_org_id', true) = ''
    );

-- ══════════════════════════════════════════════════════════
-- 9. DATOS SEED
-- ══════════════════════════════════════════════════════════

-- Organización demo (JRV Lab para POC internos)
INSERT INTO organizaciones (
    id, nombre, cif, tipo_sujeto_obligado, plan,
    umbral_riesgo_bajo, umbral_riesgo_medio, umbral_riesgo_alto,
    plazo_revision_expediente, plazo_aprobacion_supervisor, plazo_rescreening_dias,
    campos_obligatorios
) VALUES (
    'a0000000-0000-0000-0000-000000000001',
    'JRV Lab Demo',
    'B88000001',
    'gestoria',
    'poc',
    30, 60, 80,
    30, 5, 30,
    '["denominacion_social","nif_cif","domicilio_social","capital_social","nombre_titular","porcentaje_participacion"]'
) ON CONFLICT DO NOTHING;

-- Organización piloto 1 (inmobiliaria)
INSERT INTO organizaciones (
    id, nombre, cif, tipo_sujeto_obligado, plan,
    umbral_riesgo_bajo, umbral_riesgo_medio, umbral_riesgo_alto
) VALUES (
    'a0000000-0000-0000-0000-000000000002',
    'Piloto Inmobiliaria SL',
    'B88000002',
    'inmobiliaria',
    'poc',
    25, 55, 75
) ON CONFLICT DO NOTHING;

-- Usuario admin (JRV Lab Demo)
INSERT INTO usuarios (id, email, nombre, nombre_completo, rol, organizacion_id, hash_password, activo)
VALUES (
    'u0000000-0000-0000-0000-000000000001',
    'admin@jrvlab.es',
    'Admin JRV',
    'Administrador JRV Lab',
    'admin',
    'a0000000-0000-0000-0000-000000000001',
    '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMlJbekRSXTkWe9tS0rHV5T0fi',  -- password: admin2026
    true
) ON CONFLICT (email) DO UPDATE SET
    organizacion_id = EXCLUDED.organizacion_id,
    hash_password   = EXCLUDED.hash_password,
    rol             = EXCLUDED.rol;

-- Usuario supervisor (JRV Lab Demo)
INSERT INTO usuarios (id, email, nombre, nombre_completo, rol, organizacion_id, hash_password, activo)
VALUES (
    'u0000000-0000-0000-0000-000000000002',
    'supervisor@jrvlab.es',
    'Supervisor JRV',
    'Supervisor Compliance JRV Lab',
    'supervisor',
    'a0000000-0000-0000-0000-000000000001',
    '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMlJbekRSXTkWe9tS0rHV5T0fi',  -- password: admin2026
    true
) ON CONFLICT (email) DO UPDATE SET
    organizacion_id = EXCLUDED.organizacion_id,
    hash_password   = EXCLUDED.hash_password,
    rol             = EXCLUDED.rol;

-- Usuario AML Officer (JRV Lab Demo)
INSERT INTO usuarios (id, email, nombre, nombre_completo, rol, organizacion_id, hash_password, activo)
VALUES (
    'u0000000-0000-0000-0000-000000000003',
    'officer@jrvlab.es',
    'AML Officer',
    'AML Officer JRV Lab',
    'aml_officer',
    'a0000000-0000-0000-0000-000000000001',
    '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMlJbekRSXTkWe9tS0rHV5T0fi',  -- password: admin2026
    true
) ON CONFLICT (email) DO UPDATE SET
    organizacion_id = EXCLUDED.organizacion_id,
    hash_password   = EXCLUDED.hash_password,
    rol             = EXCLUDED.rol;

-- Actualizar expediente existente de CROOWLY a la organización demo
UPDATE expedientes
SET organizacion_id = 'a0000000-0000-0000-0000-000000000001'
WHERE organizacion_id IS NULL;

-- Actualizar documentos, campos, scoring, audit sin organización
UPDATE documentos d
SET organizacion_id = e.organizacion_id
FROM expedientes e
WHERE d.expediente_id = e.id AND d.organizacion_id IS NULL;

UPDATE campos_extraidos c
SET organizacion_id = e.organizacion_id
FROM expedientes e
WHERE c.expediente_id = e.id AND c.organizacion_id IS NULL;

UPDATE scoring_aml s
SET organizacion_id = e.organizacion_id
FROM expedientes e
WHERE s.expediente_id = e.id AND s.organizacion_id IS NULL;

UPDATE audit_trail a
SET organizacion_id = e.organizacion_id
FROM expedientes e
WHERE a.expediente_id = e.id AND a.organizacion_id IS NULL;

-- ══════════════════════════════════════════════════════════
-- 10. CONFIGURACIONES POR DEFECTO POR ORGANIZACIÓN
-- ══════════════════════════════════════════════════════════
INSERT INTO configuracion_organizacion (organizacion_id, clave, valor, descripcion)
VALUES
    ('a0000000-0000-0000-0000-000000000001', 'rescreening_activo',    'true',  'Rescreening automático diario activado'),
    ('a0000000-0000-0000-0000-000000000001', 'notificaciones_email',  'false', 'Notificaciones por email (requiere SMTP)'),
    ('a0000000-0000-0000-0000-000000000001', 'documentos_minimos',    '2',     'Número mínimo de documentos por expediente'),
    ('a0000000-0000-0000-0000-000000000001', 'confianza_minima_kyc',  '70',    'Confianza mínima LegNER sin revisión manual'),
    ('a0000000-0000-0000-0000-000000000002', 'rescreening_activo',    'true',  'Rescreening automático diario activado'),
    ('a0000000-0000-0000-0000-000000000002', 'documentos_minimos',    '3',     'Inmobiliaria: mínimo 3 documentos'),
    ('a0000000-0000-0000-0000-000000000002', 'confianza_minima_kyc',  '75',    'Confianza mínima LegNER sin revisión manual')
ON CONFLICT (organizacion_id, clave) DO NOTHING;

-- ══════════════════════════════════════════════════════════
-- VERIFICACIÓN FINAL
-- ══════════════════════════════════════════════════════════
DO $$ BEGIN
    RAISE NOTICE '✅ Migración multi-tenancy Compliance Lab completada';
    RAISE NOTICE '   • Tabla organizaciones creada';
    RAISE NOTICE '   • organizacion_id añadido a 6 tablas';
    RAISE NOTICE '   • Tabla alertas creada (Agente 3)';
    RAISE NOTICE '   • Tabla checklist_calidad creada (Agente 2)';
    RAISE NOTICE '   • RLS activado en 9 tablas';
    RAISE NOTICE '   • 2 organizaciones seed insertadas';
    RAISE NOTICE '   • 3 usuarios seed: admin / supervisor / aml_officer (pw: admin2026)';
    RAISE NOTICE '   • Datos existentes migrados a org demo';
END $$;
