-- ══════════════════════════════════════════════════════════════════════════════
-- WARBOT  —  Full Schema
-- Two-level hex addressing: outer "q,r" + mid "q,r:mq,mr"
-- Multi-planet support with 5 defaults per guild
-- ══════════════════════════════════════════════════════════════════════════════

-- ── Guild configuration ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS guild_config (
    guild_id              BIGINT PRIMARY KEY,
    game_started          BOOLEAN      NOT NULL DEFAULT FALSE,
    active_planet_id      INT          DEFAULT 1,
    turn_interval_hours   INT          NOT NULL DEFAULT 8,
    last_turn_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    report_channel_id     BIGINT       DEFAULT NULL,
    map_channel_id        BIGINT       DEFAULT NULL,
    map_message_id        BIGINT       DEFAULT NULL,
    overview_channel_id   BIGINT       DEFAULT NULL,
    overview_message_id   BIGINT       DEFAULT NULL,
    reg_channel_id        BIGINT       DEFAULT NULL,
    reg_message_id        BIGINT       DEFAULT NULL,
    admin_role_id         BIGINT       DEFAULT NULL,
    player_role_id        BIGINT       DEFAULT NULL,
    gamemaster_role_id    BIGINT       DEFAULT NULL,

    -- Theme strings
    theme_bot_name        TEXT NOT NULL DEFAULT 'IRON PACT',
    theme_player_faction  TEXT NOT NULL DEFAULT 'Iron Pact PMC',
    theme_enemy_faction   TEXT NOT NULL DEFAULT 'Enemy',
    theme_player_unit     TEXT NOT NULL DEFAULT 'Unit',
    theme_enemy_unit      TEXT NOT NULL DEFAULT 'Enemy Unit',
    theme_safe_zone       TEXT NOT NULL DEFAULT 'FOB Alpha',
    theme_flavor_text     TEXT NOT NULL DEFAULT 'The contract must be fulfilled.',
    theme_color           INT  NOT NULL DEFAULT 11141120,

    citadel_besieged      BOOLEAN NOT NULL DEFAULT FALSE
);

-- ── Planets ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS planets (
    id            SERIAL       PRIMARY KEY,
    guild_id      BIGINT       NOT NULL,
    name          TEXT         NOT NULL,
    contractor    TEXT         NOT NULL DEFAULT 'Uncontracted',
    enemy_type    TEXT         NOT NULL DEFAULT 'Unknown',
    description   TEXT         DEFAULT NULL,
    is_unlocked   BOOLEAN      NOT NULL DEFAULT TRUE,
    sort_order    INT          NOT NULL DEFAULT 0,
    UNIQUE(guild_id, name)
);

-- ── Hex map  (outer level=1, mid level=2) ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS hexes (
    id             SERIAL      PRIMARY KEY,
    guild_id       BIGINT      NOT NULL,
    planet_id      INT         NOT NULL,
    address        TEXT        NOT NULL,
    level          INT         NOT NULL,
    parent_address TEXT        DEFAULT NULL,
    controller     TEXT        NOT NULL DEFAULT 'neutral',
    status         TEXT        NOT NULL DEFAULT 'neutral',
    UNIQUE(guild_id, planet_id, address)
);

-- ── Per-hex terrain  (separate table so terrain survives game resets) ─────────
CREATE TABLE IF NOT EXISTS hex_terrain (
    id         SERIAL PRIMARY KEY,
    guild_id   BIGINT NOT NULL,
    planet_id  INT    NOT NULL,
    address    TEXT   NOT NULL,
    terrain    TEXT   NOT NULL DEFAULT 'flat',
    UNIQUE(guild_id, planet_id, address)
);

-- ── Player units (squadrons) ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS squadrons (
    id                  SERIAL      PRIMARY KEY,
    guild_id            BIGINT      NOT NULL,
    planet_id           INT         NOT NULL DEFAULT 1,
    owner_id            BIGINT      NOT NULL,
    owner_name          TEXT        NOT NULL DEFAULT 'Handler',
    name                TEXT        NOT NULL,
    hex_address         TEXT        NOT NULL,
    deploy_hex          TEXT        DEFAULT NULL,
    in_transit          BOOLEAN     NOT NULL DEFAULT FALSE,
    transit_destination TEXT        DEFAULT NULL,
    transit_step        INT         NOT NULL DEFAULT 0,
    attack              INT         NOT NULL DEFAULT 10,
    defense             INT         NOT NULL DEFAULT 10,
    speed               INT         NOT NULL DEFAULT 10,
    morale              INT         NOT NULL DEFAULT 10,
    supply              INT         NOT NULL DEFAULT 10,
    recon               INT         NOT NULL DEFAULT 10,
    is_active           BOOLEAN     NOT NULL DEFAULT TRUE,
    last_scavenged_turn INT         NOT NULL DEFAULT -1,
    last_combat_turn    INT         NOT NULL DEFAULT -1,
    UNIQUE(guild_id, planet_id, owner_id, name)
);

-- ── Enemy units ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS enemy_units (
    id             SERIAL  PRIMARY KEY,
    guild_id       BIGINT  NOT NULL,
    planet_id      INT     NOT NULL DEFAULT 1,
    unit_type      TEXT    NOT NULL DEFAULT 'Scout',
    hex_address    TEXT    NOT NULL,
    attack         INT     NOT NULL DEFAULT 10,
    defense        INT     NOT NULL DEFAULT 10,
    speed          INT     NOT NULL DEFAULT 10,
    morale         INT     NOT NULL DEFAULT 10,
    supply         INT     NOT NULL DEFAULT 10,
    recon          INT     NOT NULL DEFAULT 10,
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    manually_moved BOOLEAN NOT NULL DEFAULT FALSE
);

-- ── GM queued enemy moves ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS enemy_gm_moves (
    id             SERIAL      PRIMARY KEY,
    guild_id       BIGINT      NOT NULL,
    planet_id      INT         NOT NULL DEFAULT 1,
    enemy_unit_id  INT         NOT NULL,
    target_address TEXT        NOT NULL,
    queued_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(guild_id, enemy_unit_id)
);

-- ── Combat log ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS combat_log (
    id             SERIAL      PRIMARY KEY,
    guild_id       BIGINT      NOT NULL,
    planet_id      INT         NOT NULL DEFAULT 1,
    turn_number    INT         NOT NULL,
    hex_address    TEXT        NOT NULL,
    attacker       TEXT        NOT NULL,
    defender       TEXT        NOT NULL,
    attacker_roll  INT         NOT NULL,
    defender_roll  INT         NOT NULL,
    outcome        TEXT        NOT NULL,
    occurred_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Turn history ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS turn_history (
    id          SERIAL      PRIMARY KEY,
    guild_id    BIGINT      NOT NULL,
    planet_id   INT         NOT NULL DEFAULT 1,
    turn_number INT         NOT NULL,
    resolved_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Player economy ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS player_economy (
    id             SERIAL PRIMARY KEY,
    guild_id       BIGINT NOT NULL,
    owner_id       BIGINT NOT NULL,
    raw_materials  INT    NOT NULL DEFAULT 0,
    credits        INT    NOT NULL DEFAULT 0,
    UNIQUE(guild_id, owner_id)
);

-- ── Indexes ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_hexes_guild_planet   ON hexes(guild_id, planet_id);
CREATE INDEX IF NOT EXISTS idx_sq_guild_planet      ON squadrons(guild_id, planet_id);
CREATE INDEX IF NOT EXISTS idx_enemy_guild_planet   ON enemy_units(guild_id, planet_id);
CREATE INDEX IF NOT EXISTS idx_combat_guild         ON combat_log(guild_id);
CREATE INDEX IF NOT EXISTS idx_planets_guild        ON planets(guild_id);

-- ── Live migration guards ─────────────────────────────────────────────────────
DO $$ BEGIN ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS active_planet_id    INT     DEFAULT 1;          END $$;
DO $$ BEGIN ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS overview_channel_id BIGINT  DEFAULT NULL;       END $$;
DO $$ BEGIN ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS overview_message_id BIGINT  DEFAULT NULL;       END $$;
DO $$ BEGIN ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS theme_bot_name       TEXT NOT NULL DEFAULT 'IRON PACT'; END $$;
DO $$ BEGIN ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS theme_player_faction TEXT NOT NULL DEFAULT 'Iron Pact PMC'; END $$;
DO $$ BEGIN ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS theme_enemy_faction  TEXT NOT NULL DEFAULT 'Enemy'; END $$;
DO $$ BEGIN ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS theme_player_unit    TEXT NOT NULL DEFAULT 'Unit'; END $$;
DO $$ BEGIN ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS theme_enemy_unit     TEXT NOT NULL DEFAULT 'Enemy Unit'; END $$;
DO $$ BEGIN ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS theme_safe_zone      TEXT NOT NULL DEFAULT 'FOB Alpha'; END $$;
DO $$ BEGIN ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS theme_flavor_text    TEXT NOT NULL DEFAULT 'The contract must be fulfilled.'; END $$;
DO $$ BEGIN ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS theme_color          INT  NOT NULL DEFAULT 11141120; END $$;
DO $$ BEGIN ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS citadel_besieged     BOOLEAN NOT NULL DEFAULT FALSE; END $$;
DO $$ BEGIN ALTER TABLE squadrons    ADD COLUMN IF NOT EXISTS planet_id            INT NOT NULL DEFAULT 1; END $$;
DO $$ BEGIN ALTER TABLE enemy_units  ADD COLUMN IF NOT EXISTS planet_id            INT NOT NULL DEFAULT 1; END $$;
DO $$ BEGIN ALTER TABLE combat_log   ADD COLUMN IF NOT EXISTS planet_id            INT NOT NULL DEFAULT 1; END $$;
DO $$ BEGIN ALTER TABLE turn_history ADD COLUMN IF NOT EXISTS planet_id            INT NOT NULL DEFAULT 1; END $$;
DO $$ BEGIN ALTER TABLE enemy_gm_moves ADD COLUMN IF NOT EXISTS planet_id          INT NOT NULL DEFAULT 1; END $$;
