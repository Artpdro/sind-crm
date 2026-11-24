"""Schema do banco (SQLite). Aplicado por `python -m scripts.init_db` (idempotente).
Regras de integridade ficam no BANCO: PK interna, CNPJ UNIQUE, CHECKs, chaves estrangeiras,
histórico e auditoria imutáveis por trigger."""
from sqlalchemy.engine import Engine

NOW = "strftime('%Y-%m-%d %H:%M:%f','now')"

_SCHEMA_TEMPLATE = """
BEGIN;
CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (__NOW__));

-- ───────── Usuários e sessões ─────────
CREATE TABLE users (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    username              TEXT NOT NULL UNIQUE COLLATE NOCASE
                          CHECK (length(username) BETWEEN 3 AND 50 AND username NOT GLOB '*[^A-Za-z0-9._-]*'),
    email                 TEXT UNIQUE COLLATE NOCASE CHECK (email IS NULL OR length(email) <= 320),
    full_name             TEXT,
    password_hash         TEXT NOT NULL,
    role                  TEXT NOT NULL CHECK (role IN ('admin','operador','visualizador')),
    is_active             INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    must_change_password  INTEGER NOT NULL DEFAULT 0 CHECK (must_change_password IN (0,1)),
    failed_attempts       INTEGER NOT NULL DEFAULT 0,
    locked_until          TEXT,
    last_login_at         TEXT,
    created_at            TEXT NOT NULL DEFAULT (__NOW__),
    updated_at            TEXT NOT NULL DEFAULT (__NOW__)
);
CREATE TRIGGER users_updated AFTER UPDATE ON users
BEGIN UPDATE users SET updated_at = __NOW__ WHERE id = NEW.id; END;

CREATE TABLE sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash    TEXT NOT NULL UNIQUE,      -- SHA-256 do token; o token em si nunca é gravado
    csrf_token    TEXT NOT NULL,
    ip            TEXT,
    user_agent    TEXT,
    created_at    TEXT NOT NULL DEFAULT (__NOW__),
    last_seen_at  TEXT NOT NULL DEFAULT (__NOW__),
    expires_at    TEXT NOT NULL,             -- limite absoluto
    revoked_at    TEXT
);
CREATE INDEX sessions_user_idx ON sessions(user_id);

CREATE TABLE password_reset_tokens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL UNIQUE,
    expires_at  TEXT NOT NULL,
    used_at     TEXT,
    created_at  TEXT NOT NULL DEFAULT (__NOW__)
);

CREATE TABLE rate_limit_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bucket      TEXT NOT NULL,
    key         TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (__NOW__)
);
CREATE INDEX rate_limit_lookup ON rate_limit_events(bucket, key, created_at);

CREATE TABLE audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at  TEXT NOT NULL DEFAULT (__NOW__),
    user_id      INTEGER,
    username     TEXT,
    action       TEXT NOT NULL,
    entity_type  TEXT,
    entity_id    INTEGER,
    ip           TEXT,
    details      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX audit_log_time_idx ON audit_log(occurred_at);
CREATE INDEX audit_log_entity_idx ON audit_log(entity_type, entity_id);
CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'A trilha de auditoria é imutável.'); END;
CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'A trilha de auditoria é imutável.'); END;

-- ───────── Tabelas de apoio configuráveis pelo administrador ─────────
CREATE TABLE situacoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK (length(nome) BETWEEN 1 AND 80),
    cor TEXT NOT NULL DEFAULT '#6b7280' CHECK (length(cor) = 7 AND cor GLOB '#[0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]'),
    ativa INTEGER NOT NULL DEFAULT 1 CHECK (ativa IN (0,1)),
    ordem INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE categorias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK (length(nome) BETWEEN 1 AND 80),
    ativa INTEGER NOT NULL DEFAULT 1 CHECK (ativa IN (0,1))
);
CREATE TABLE portes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK (length(nome) BETWEEN 1 AND 80),
    ativa INTEGER NOT NULL DEFAULT 1 CHECK (ativa IN (0,1))
);
CREATE TABLE tipos_empresa (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK (length(nome) BETWEEN 1 AND 80),
    ativa INTEGER NOT NULL DEFAULT 1 CHECK (ativa IN (0,1))
);

-- ───────── Empresas ─────────
CREATE TABLE empresas (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,          -- ID interno (chave primária)
    nome                   TEXT NOT NULL CHECK (length(nome) BETWEEN 1 AND 300),
    nome_fantasia          TEXT CHECK (length(nome_fantasia) <= 300),
    -- CNPJ normalizado: 14 dígitos, texto. NULL permitido (a planilha tem linhas sem CNPJ);
    -- UNIQUE aceita vários NULL e impede dois CNPJ iguais.
    cnpj                   TEXT CONSTRAINT empresas_cnpj_key UNIQUE
                           CHECK (cnpj IS NULL OR (length(cnpj) = 14 AND cnpj NOT GLOB '*[^0-9]*')),
    email                  TEXT CHECK (length(email) <= 320),
    emails_adicionais      TEXT NOT NULL DEFAULT '[]',                 -- lista em JSON
    celular                TEXT CHECK (celular IS NULL OR (length(celular) BETWEEN 8 AND 13 AND celular NOT GLOB '*[^0-9]*')),
    fone                   TEXT CHECK (fone IS NULL OR (length(fone) BETWEEN 8 AND 13 AND fone NOT GLOB '*[^0-9]*')),
    cep                    TEXT CHECK (cep IS NULL OR (length(cep) BETWEEN 1 AND 8 AND cep NOT GLOB '*[^0-9]*')),
    rua                    TEXT CHECK (length(rua) <= 300),
    numero                 TEXT CHECK (length(numero) <= 60),          -- texto: 10A, S/N, Loja 01...
    numero_base            INTEGER,                                    -- parte numérica (preenchida pela aplicação); NULL p/ S/N
    complemento            TEXT CHECK (length(complemento) <= 300),
    bairro                 TEXT CHECK (length(bairro) <= 150),
    cidade                 TEXT CHECK (length(cidade) <= 150),
    estado                 TEXT CHECK (estado IS NULL OR (length(estado) = 2 AND estado NOT GLOB '*[^A-Z]*')),
    situacao_id            INTEGER REFERENCES situacoes(id),
    porte_id               INTEGER REFERENCES portes(id),
    tipo_empresa_id        INTEGER REFERENCES tipos_empresa(id),
    informacoes_adicionais TEXT CHECK (length(informacoes_adicionais) <= 20000),

    -- Geolocalização persistida (o mapa NUNCA geocodifica ao abrir)
    latitude               REAL CHECK (latitude  BETWEEN -90  AND 90),
    longitude              REAL CHECK (longitude BETWEEN -180 AND 180),
    geocode_status         TEXT NOT NULL DEFAULT 'pendente'
                           CHECK (geocode_status IN ('localizado','pendente','nao_encontrado','revisao')),
    geocode_endereco_usado TEXT,
    geocoded_at            TEXT,
    geocode_desatualizada  INTEGER NOT NULL DEFAULT 0 CHECK (geocode_desatualizada IN (0,1)),

    version                INTEGER NOT NULL DEFAULT 1,                 -- bloqueio otimista
    deleted_at             TEXT,                                       -- exclusão lógica
    created_by             INTEGER REFERENCES users(id),
    updated_by             INTEGER REFERENCES users(id),
    created_at             TEXT NOT NULL DEFAULT (__NOW__),
    updated_at             TEXT NOT NULL DEFAULT (__NOW__),
    CONSTRAINT empresas_lat_lon_juntos CHECK ((latitude IS NULL) = (longitude IS NULL))
);
CREATE TRIGGER empresas_updated AFTER UPDATE ON empresas
BEGIN UPDATE empresas SET updated_at = __NOW__ WHERE id = NEW.id; END;
CREATE INDEX empresas_geo_idx ON empresas (latitude, longitude);
CREATE INDEX empresas_cidade_bairro_idx ON empresas (cidade, bairro);
CREATE INDEX empresas_rua_idx ON empresas (rua COLLATE NOCASE);
CREATE INDEX empresas_situacao_idx ON empresas (situacao_id);
CREATE INDEX empresas_porte_idx ON empresas (porte_id);
CREATE INDEX empresas_tipo_idx ON empresas (tipo_empresa_id);
CREATE INDEX empresas_nome_idx ON empresas (nome COLLATE NOCASE);

CREATE TABLE empresa_categorias (
    empresa_id   INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    categoria_id INTEGER NOT NULL REFERENCES categorias(id) ON DELETE RESTRICT,
    PRIMARY KEY (empresa_id, categoria_id)
);
CREATE INDEX empresa_categorias_cat_idx ON empresa_categorias(categoria_id);

-- ───────── Campos personalizados ─────────
CREATE TABLE campos_personalizados (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chave TEXT NOT NULL UNIQUE CHECK (length(chave) BETWEEN 2 AND 50 AND chave GLOB '[a-z]*' AND chave NOT GLOB '*[^a-z0-9_]*'),
    rotulo TEXT NOT NULL CHECK (length(rotulo) BETWEEN 1 AND 120),
    tipo TEXT NOT NULL CHECK (tipo IN ('booleano','data','texto','numero','selecao')),
    opcoes TEXT,
    obrigatorio INTEGER NOT NULL DEFAULT 0 CHECK (obrigatorio IN (0,1)),
    ativo INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0,1)),
    ordem INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE empresa_campos_valores (
    empresa_id   INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    campo_id     INTEGER NOT NULL REFERENCES campos_personalizados(id) ON DELETE CASCADE,
    valor_texto  TEXT CHECK (length(valor_texto) <= 5000),
    valor_numero REAL,
    valor_data   TEXT,
    valor_bool   INTEGER CHECK (valor_bool IN (0,1)),
    PRIMARY KEY (empresa_id, campo_id)
);

-- ───────── Histórico de interações (append-only) ─────────
CREATE TABLE interacoes (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id             INTEGER NOT NULL REFERENCES empresas(id) ON DELETE RESTRICT,
    usuario_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    tipo                   TEXT NOT NULL CHECK (tipo IN ('ligacao','whatsapp','email','visita','reuniao','atendimento','outro')),
    descricao              TEXT NOT NULL CHECK (length(descricao) BETWEEN 1 AND 5000),
    situacao_resultante_id INTEGER REFERENCES situacoes(id),
    observacoes            TEXT CHECK (length(observacoes) <= 5000),
    data_interacao         TEXT NOT NULL DEFAULT (__NOW__),
    created_at             TEXT NOT NULL DEFAULT (__NOW__)
);
CREATE INDEX interacoes_empresa_idx ON interacoes(empresa_id, data_interacao);
CREATE TRIGGER interacoes_no_update BEFORE UPDATE ON interacoes
BEGIN SELECT RAISE(ABORT, 'O histórico de interações é imutável (append-only).'); END;
CREATE TRIGGER interacoes_no_delete BEFORE DELETE ON interacoes
BEGIN SELECT RAISE(ABORT, 'O histórico de interações é imutável (append-only).'); END;

-- ───────── Dados iniciais (editáveis pelo administrador; não ficam fixos no código) ─────────
INSERT INTO situacoes (nome, cor, ordem) VALUES
 ('Ativa','#15803d',1),('Inativa','#6b7280',2),('Em acompanhamento','#b45309',3),
 ('Visitada','#1d4ed8',4),('Contato realizado','#0e7490',5),('Sem contato','#b91c1c',6),('Pendente','#a16207',7);
INSERT INTO portes (nome) VALUES ('Microempresa'),('Pequena'),('Média'),('Grande');
INSERT INTO tipos_empresa (nome) VALUES ('Matriz'),('Filial'),('MEI');

INSERT INTO schema_migrations (version) VALUES (1);
COMMIT;
"""
SCHEMA_SQL = _SCHEMA_TEMPLATE.replace("__NOW__", NOW)


_SCHEMA_V2_TEMPLATE = """
BEGIN;
-- Importação do Excel: cada envio gera uma "prévia" que só vira dado real após confirmação.
CREATE TABLE importacoes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    nome_arquivo  TEXT NOT NULL CHECK (length(nome_arquivo) <= 120),
    sha256        TEXT NOT NULL,
    tamanho       INTEGER NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('previa','executando','concluida','cancelada','falhou')),
    modo          TEXT CHECK (modo IN ('novos','atualizar','ambos')),
    resumo        TEXT NOT NULL DEFAULT '{}',
    resultado     TEXT,
    criada_em     TEXT NOT NULL DEFAULT (__NOW__),
    concluida_em  TEXT
);
-- Linhas ficam guardadas só enquanto a prévia está aberta (minimização de dados / LGPD).
CREATE TABLE importacao_linhas (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    importacao_id  INTEGER NOT NULL REFERENCES importacoes(id) ON DELETE CASCADE,
    linha          INTEGER NOT NULL,
    acao           TEXT NOT NULL CHECK (acao IN ('novo','atualizar','sem_alteracao','duplicada','rejeitada','ja_existente')),
    revisao        INTEGER NOT NULL DEFAULT 0 CHECK (revisao IN (0,1)),
    dados          TEXT NOT NULL,
    motivos        TEXT NOT NULL DEFAULT '[]',
    avisos         TEXT NOT NULL DEFAULT '[]',
    erros          TEXT NOT NULL DEFAULT '[]',
    alteracoes     TEXT NOT NULL DEFAULT '[]',
    mantida_linha  INTEGER,
    empresa_id     INTEGER
);
CREATE INDEX importacao_linhas_idx ON importacao_linhas(importacao_id, acao, linha);

ALTER TABLE empresas ADD COLUMN precisa_revisao INTEGER NOT NULL DEFAULT 0 CHECK (precisa_revisao IN (0,1));
ALTER TABLE empresas ADD COLUMN motivos_revisao TEXT NOT NULL DEFAULT '[]';
ALTER TABLE empresas ADD COLUMN cnpj_original TEXT CHECK (cnpj_original IS NULL OR length(cnpj_original) <= 100);
ALTER TABLE empresas ADD COLUMN importacao_id INTEGER REFERENCES importacoes(id);
CREATE INDEX empresas_revisao_idx ON empresas (precisa_revisao);

INSERT INTO schema_migrations (version) VALUES (2);
COMMIT;
"""
SCHEMA_V2_SQL = _SCHEMA_V2_TEMPLATE.replace("__NOW__", NOW)

_SCHEMA_V3_TEMPLATE = """
BEGIN;
-- Cache: a mesma consulta nunca é enviada duas vezes ao serviço externo (inclusive resultados "não encontrado").
CREATE TABLE geocode_cache (
    chave      TEXT PRIMARY KEY,
    encontrado INTEGER NOT NULL CHECK (encontrado IN (0,1)),
    resultado  TEXT,
    criado_em  TEXT NOT NULL DEFAULT (__NOW__)
);
-- Trava de execução única: só UM processo geocodifica por vez (a API e o script dividem o limite de 1 consulta/s).
CREATE TABLE geocode_lease (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    holder     TEXT,
    expires_at TEXT
);
INSERT INTO geocode_lease (id) VALUES (1);

ALTER TABLE empresas ADD COLUMN geocode_tentativas INTEGER NOT NULL DEFAULT 0;
ALTER TABLE empresas ADD COLUMN geocode_precisao TEXT CHECK (geocode_precisao IS NULL OR geocode_precisao IN ('endereco','rua','cep','manual'));
ALTER TABLE empresas ADD COLUMN geocode_erro TEXT CHECK (geocode_erro IS NULL OR length(geocode_erro) <= 200);
CREATE INDEX empresas_geocode_status_idx ON empresas (geocode_status);

INSERT INTO schema_migrations (version) VALUES (3);
COMMIT;
"""
SCHEMA_V3_SQL = _SCHEMA_V3_TEMPLATE.replace("__NOW__", NOW)

_SCHEMA_V4_TEMPLATE = """
BEGIN;
-- Novo nível de precisão: 'bairro' (aproximada pelo bairro). O SQLite não altera CHECK; troca-se a coluna preservando os valores.
ALTER TABLE empresas ADD COLUMN geocode_precisao_novo TEXT CHECK (geocode_precisao_novo IS NULL OR geocode_precisao_novo IN ('endereco','rua','cep','bairro','manual'));
UPDATE empresas SET geocode_precisao_novo = geocode_precisao;
ALTER TABLE empresas DROP COLUMN geocode_precisao;
ALTER TABLE empresas RENAME COLUMN geocode_precisao_novo TO geocode_precisao;
INSERT INTO schema_migrations (version) VALUES (4);
COMMIT;
"""
SCHEMA_V4_SQL = _SCHEMA_V4_TEMPLATE

SCHEMA_V5_SQL = """
BEGIN;
-- Nível 'interpolado' (estimada entre números vizinhos) e a FONTE da coordenada (cnefe = IBGE, osm = OpenStreetMap, manual).
ALTER TABLE empresas ADD COLUMN geocode_fonte TEXT CHECK (geocode_fonte IS NULL OR geocode_fonte IN ('cnefe','osm','manual'));
UPDATE empresas SET geocode_fonte = CASE WHEN geocode_precisao = 'manual' THEN 'manual' WHEN latitude IS NOT NULL THEN 'osm' END;
ALTER TABLE empresas ADD COLUMN geocode_precisao_novo TEXT CHECK (geocode_precisao_novo IS NULL OR geocode_precisao_novo IN ('endereco','interpolado','rua','cep','bairro','manual'));
UPDATE empresas SET geocode_precisao_novo = geocode_precisao;
ALTER TABLE empresas DROP COLUMN geocode_precisao;
ALTER TABLE empresas RENAME COLUMN geocode_precisao_novo TO geocode_precisao;
INSERT INTO schema_migrations (version) VALUES (5);
COMMIT;
"""

MIGRACOES = [(1, SCHEMA_SQL), (2, SCHEMA_V2_SQL), (3, SCHEMA_V3_SQL), (4, SCHEMA_V4_SQL), (5, SCHEMA_V5_SQL)]


def init_db(engine: Engine) -> bool:
    """Aplica as migrações que faltam, em ordem, sem tocar nos dados. Retorna True se aplicou alguma."""
    raw = engine.raw_connection()
    try:
        drv = raw.driver_connection
        tem = drv.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
        feitas = {r[0] for r in drv.execute("SELECT version FROM schema_migrations")} if tem else set()
        aplicou = False
        for versao, sql in MIGRACOES:
            if versao not in feitas:
                drv.executescript(sql)
                aplicou = True
        return aplicou
    finally:
        raw.close()
