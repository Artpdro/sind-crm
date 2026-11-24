"""Schema inicial: usuários/sessões/auditoria, empresas (PostGIS), situações, categorias,
campos personalizados e histórico de interações (append-only).

Revision ID: 0001
Revises:
"""
import os
import re

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "sindnorte_app")
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", APP_ROLE):
    raise SystemExit("APP_DB_ROLE inválido")

SCHEMA = r"""
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS citext;

CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END $$ LANGUAGE plpgsql;

-- ───────── Usuários e sessões ─────────
CREATE TABLE users (
    id                    BIGSERIAL PRIMARY KEY,
    username              CITEXT NOT NULL UNIQUE CHECK (username ~ '^[A-Za-z0-9._-]{3,50}$'),
    email                 CITEXT UNIQUE,
    full_name             TEXT,
    password_hash         TEXT NOT NULL,
    role                  TEXT NOT NULL CHECK (role IN ('admin','operador','visualizador')),
    is_active             BOOLEAN NOT NULL DEFAULT TRUE,
    must_change_password  BOOLEAN NOT NULL DEFAULT FALSE,
    failed_attempts       INTEGER NOT NULL DEFAULT 0,
    locked_until          TIMESTAMPTZ,
    last_login_at         TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TRIGGER users_updated BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE sessions (
    id            BIGSERIAL PRIMARY KEY,
    user_id       BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash    TEXT NOT NULL UNIQUE,          -- SHA-256 do token; o token em si nunca é gravado
    csrf_token    TEXT NOT NULL,
    ip            TEXT,
    user_agent    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ NOT NULL,          -- limite absoluto
    revoked_at    TIMESTAMPTZ
);
CREATE INDEX sessions_user_idx ON sessions(user_id) WHERE revoked_at IS NULL;

CREATE TABLE password_reset_tokens (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL UNIQUE,
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE rate_limit_events (
    id          BIGSERIAL PRIMARY KEY,
    bucket      TEXT NOT NULL,
    key         TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX rate_limit_lookup ON rate_limit_events(bucket, key, created_at);

CREATE TABLE audit_log (
    id           BIGSERIAL PRIMARY KEY,
    occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id      BIGINT REFERENCES users(id) ON DELETE SET NULL,
    username     TEXT,
    action       TEXT NOT NULL,
    entity_type  TEXT,
    entity_id    BIGINT,
    ip           TEXT,
    details      JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX audit_log_time_idx ON audit_log(occurred_at DESC);
CREATE INDEX audit_log_entity_idx ON audit_log(entity_type, entity_id);

-- ───────── Tabelas de apoio configuráveis pelo administrador ─────────
CREATE TABLE situacoes (
    id      BIGSERIAL PRIMARY KEY,
    nome    CITEXT NOT NULL UNIQUE CHECK (length(nome) BETWEEN 1 AND 80),
    cor     TEXT NOT NULL DEFAULT '#6b7280' CHECK (cor ~ '^#[0-9a-fA-F]{6}$'),
    ativa   BOOLEAN NOT NULL DEFAULT TRUE,
    ordem   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE categorias (
    id     BIGSERIAL PRIMARY KEY,
    nome   CITEXT NOT NULL UNIQUE CHECK (length(nome) BETWEEN 1 AND 80),
    ativa  BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE TABLE portes (
    id     BIGSERIAL PRIMARY KEY,
    nome   CITEXT NOT NULL UNIQUE CHECK (length(nome) BETWEEN 1 AND 80),
    ativa  BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE TABLE tipos_empresa (
    id     BIGSERIAL PRIMARY KEY,
    nome   CITEXT NOT NULL UNIQUE CHECK (length(nome) BETWEEN 1 AND 80),
    ativa  BOOLEAN NOT NULL DEFAULT TRUE
);

-- ───────── Empresas ─────────
CREATE TABLE empresas (
    id                     BIGSERIAL PRIMARY KEY,                       -- ID interno (chave primária)
    nome                   TEXT NOT NULL CHECK (length(nome) BETWEEN 1 AND 300),
    nome_fantasia          TEXT CHECK (length(nome_fantasia) <= 300),
    -- CNPJ normalizado (14 dígitos, texto). NULL permitido (a planilha tem linhas sem CNPJ);
    -- UNIQUE do PostgreSQL aceita vários NULL e impede dois CNPJ iguais.
    cnpj                   TEXT CONSTRAINT empresas_cnpj_key UNIQUE
                                CONSTRAINT empresas_cnpj_formato CHECK (cnpj ~ '^[0-9]{14}$'),
    email                  TEXT CHECK (length(email) <= 320),
    emails_adicionais      TEXT[] NOT NULL DEFAULT '{}',
    celular                TEXT CHECK (celular ~ '^[0-9]{8,13}$'),
    fone                   TEXT CHECK (fone ~ '^[0-9]{8,13}$'),
    cep                    TEXT CHECK (cep ~ '^[0-9]{1,8}$'),           -- texto; valores incompletos ficam p/ revisão
    rua                    TEXT CHECK (length(rua) <= 300),
    numero                 TEXT CHECK (length(numero) <= 60),           -- texto: 10A, S/N, Loja 01, 100/102...
    -- componente numérico extraído do número (NULL quando não identificável, ex.: S/N)
    numero_base            INTEGER GENERATED ALWAYS AS (
        CASE WHEN substring(numero from '[0-9]+') ~ '^[0-9]{1,9}$'
             THEN substring(numero from '[0-9]+')::integer END) STORED,
    complemento            TEXT CHECK (length(complemento) <= 300),
    bairro                 TEXT CHECK (length(bairro) <= 150),
    cidade                 TEXT CHECK (length(cidade) <= 150),
    estado                 CHAR(2) CHECK (estado ~ '^[A-Z]{2}$'),
    situacao_id            BIGINT REFERENCES situacoes(id),
    porte_id               BIGINT REFERENCES portes(id),
    tipo_empresa_id        BIGINT REFERENCES tipos_empresa(id),
    informacoes_adicionais TEXT CHECK (length(informacoes_adicionais) <= 20000),

    -- Geolocalização persistida (o mapa NUNCA geocodifica ao abrir)
    latitude               DOUBLE PRECISION CHECK (latitude  BETWEEN -90  AND 90),
    longitude              DOUBLE PRECISION CHECK (longitude BETWEEN -180 AND 180),
    location               geography(Point,4326) GENERATED ALWAYS AS (
        CASE WHEN latitude IS NOT NULL AND longitude IS NOT NULL
             THEN ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography END) STORED,
    geocode_status         TEXT NOT NULL DEFAULT 'pendente'
                           CHECK (geocode_status IN ('localizado','pendente','nao_encontrado','revisao')),
    geocode_endereco_usado TEXT,
    geocoded_at            TIMESTAMPTZ,
    -- endereço mudou: coordenada anterior é preservada até uma nova ser validada
    geocode_desatualizada  BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT empresas_lat_lon_juntos CHECK ((latitude IS NULL) = (longitude IS NULL)),

    version                INTEGER NOT NULL DEFAULT 1,                  -- bloqueio otimista (concorrência)
    deleted_at             TIMESTAMPTZ,                                 -- exclusão lógica (preserva histórico)
    created_by             BIGINT REFERENCES users(id),
    updated_by             BIGINT REFERENCES users(id),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TRIGGER empresas_updated BEFORE UPDATE ON empresas FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE INDEX empresas_location_gix ON empresas USING GIST (location);
CREATE INDEX empresas_cidade_bairro_idx ON empresas (cidade, bairro);
CREATE INDEX empresas_situacao_idx ON empresas (situacao_id);
CREATE INDEX empresas_porte_idx ON empresas (porte_id);
CREATE INDEX empresas_tipo_idx ON empresas (tipo_empresa_id);
CREATE INDEX empresas_rua_trgm ON empresas USING GIN (rua gin_trgm_ops);
CREATE INDEX empresas_nome_trgm ON empresas USING GIN (nome gin_trgm_ops);
CREATE INDEX empresas_geocode_pendente_idx ON empresas (id) WHERE geocode_status <> 'localizado' OR geocode_desatualizada;

CREATE TABLE empresa_categorias (
    empresa_id    BIGINT NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    categoria_id  BIGINT NOT NULL REFERENCES categorias(id) ON DELETE RESTRICT,
    PRIMARY KEY (empresa_id, categoria_id)
);
CREATE INDEX empresa_categorias_cat_idx ON empresa_categorias(categoria_id);

-- ───────── Campos personalizados ─────────
CREATE TABLE campos_personalizados (
    id           BIGSERIAL PRIMARY KEY,
    chave        TEXT NOT NULL UNIQUE CHECK (chave ~ '^[a-z][a-z0-9_]{1,49}$'),
    rotulo       TEXT NOT NULL CHECK (length(rotulo) BETWEEN 1 AND 120),
    tipo         TEXT NOT NULL CHECK (tipo IN ('booleano','data','texto','numero','selecao')),
    opcoes       JSONB,                                  -- para tipo 'selecao'
    obrigatorio  BOOLEAN NOT NULL DEFAULT FALSE,
    ativo        BOOLEAN NOT NULL DEFAULT TRUE,
    ordem        INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE empresa_campos_valores (
    empresa_id    BIGINT NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    campo_id      BIGINT NOT NULL REFERENCES campos_personalizados(id) ON DELETE CASCADE,
    valor_texto   TEXT CHECK (length(valor_texto) <= 5000),
    valor_numero  NUMERIC,
    valor_data    DATE,
    valor_bool    BOOLEAN,
    PRIMARY KEY (empresa_id, campo_id)
);

-- ───────── Histórico de interações (append-only) ─────────
CREATE TABLE interacoes (
    id                     BIGSERIAL PRIMARY KEY,
    empresa_id             BIGINT NOT NULL REFERENCES empresas(id) ON DELETE RESTRICT,
    usuario_id             BIGINT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    tipo                   TEXT NOT NULL CHECK (tipo IN ('ligacao','whatsapp','email','visita','reuniao','atendimento','outro')),
    descricao              TEXT NOT NULL CHECK (length(descricao) BETWEEN 1 AND 5000),
    situacao_resultante_id BIGINT REFERENCES situacoes(id),
    observacoes            TEXT CHECK (length(observacoes) <= 5000),
    data_interacao         TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX interacoes_empresa_idx ON interacoes(empresa_id, data_interacao DESC);

CREATE FUNCTION interacoes_imutavel() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'O histórico de interações é imutável (append-only).'; END $$ LANGUAGE plpgsql;
CREATE TRIGGER interacoes_no_update BEFORE UPDATE OR DELETE ON interacoes
    FOR EACH ROW EXECUTE FUNCTION interacoes_imutavel();

-- ───────── Dados iniciais (editáveis pelo administrador; não ficam fixos no código) ─────────
INSERT INTO situacoes (nome, cor, ordem) VALUES
 ('Ativa','#15803d',1),('Inativa','#6b7280',2),('Em acompanhamento','#b45309',3),
 ('Visitada','#1d4ed8',4),('Contato realizado','#0e7490',5),('Sem contato','#b91c1c',6),('Pendente','#a16207',7);
INSERT INTO portes (nome) VALUES ('Microempresa'),('Pequena'),('Média'),('Grande');
INSERT INTO tipos_empresa (nome) VALUES ('Matriz'),('Filial'),('MEI');

-- ───────── Menor privilégio para o usuário da aplicação ─────────
GRANT USAGE ON SCHEMA public TO "{role}";
GRANT SELECT, INSERT, UPDATE, DELETE ON
    users, sessions, password_reset_tokens, rate_limit_events, situacoes, categorias, portes,
    tipos_empresa, empresas, empresa_categorias, campos_personalizados, empresa_campos_valores
    TO "{role}";
GRANT SELECT, INSERT ON audit_log, interacoes TO "{role}";   -- sem UPDATE/DELETE: trilha imutável
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{role}";
""".replace("{role}", APP_ROLE)


def upgrade() -> None:
    op.execute(SCHEMA)


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS interacoes, empresa_campos_valores, campos_personalizados, empresa_categorias,
            empresas, tipos_empresa, portes, categorias, situacoes, audit_log, rate_limit_events,
            password_reset_tokens, sessions, users CASCADE;
        DROP FUNCTION IF EXISTS interacoes_imutavel();
        DROP FUNCTION IF EXISTS set_updated_at();
        """
    )
