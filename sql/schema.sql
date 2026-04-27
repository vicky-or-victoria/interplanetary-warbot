-- ══════════════════════════════════════════════════════════════════════════════
-- WARBOT  —  Schema v3
-- Flat global hex addressing: every hex is "gq,gr"
-- No outer/mid/level split. 703 hexes per planet.
-- Brigade system with full mechanical differences.
-- ══════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS guild_config (
    guild_id              BIGINT       PRIMARY KEY,
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
    enlist_channel_id     BIGINT       DEFAULT NULL,
    enlist_message_id     BIGINT       DEFAULT NULL,
    admin_role_id         BIGINT       DEFAULT NULL,
    player_role_id        BIGINT       DEFAULT NULL,
    gamemaster_role_id    BIGINT       DEFAULT NULL,
    theme_bot_name        TEXT NOT NULL DEFAULT 'IRON PACT',
    theme_player_faction  TEXT NOT NULL DEFAULT 'Iron Pact PMC',
    theme_enemy_faction   TEXT NOT NULL DEFAULT 'Enemy',
    theme_player_unit     TEXT NOT NULL DEFAULT 'Unit',
    theme_enemy_unit      TEXT NOT NULL DEFAULT 'Enemy Unit',
    theme_safe_zone       TEXT NOT NULL DEFAULT 'Deployment Zone',
    theme_flavor_text     TEXT NOT NULL DEFAULT 'The contract must be fulfilled.',
    theme_color           INT  NOT NULL DEFAULT 11141120,
    contract_name         TEXT         DEFAULT NULL,
    announcement_channel_id BIGINT     DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS planets (
    id          SERIAL   PRIMARY KEY,
    guild_id    BIGINT   NOT NULL,
    name        TEXT     NOT NULL,
    contractor  TEXT     NOT NULL DEFAULT 'Uncontracted',
    enemy_type  TEXT     NOT NULL DEFAULT 'Unknown',
    description TEXT     DEFAULT NULL,
    is_unlocked BOOLEAN  NOT NULL DEFAULT TRUE,
    sort_order  INT      NOT NULL DEFAULT 0,
    UNIQUE(guild_id, name)
);

-- Flat hex map — address is global axial "gq,gr"
CREATE TABLE IF NOT EXISTS hexes (
    id         SERIAL  PRIMARY KEY,
    guild_id   BIGINT  NOT NULL,
    planet_id  INT     NOT NULL,
    address    TEXT    NOT NULL,
    controller TEXT    NOT NULL DEFAULT 'neutral',
    status     TEXT    NOT NULL DEFAULT 'neutral',
    UNIQUE(guild_id, planet_id, address)
);

CREATE TABLE IF NOT EXISTS hex_terrain (
    id        SERIAL PRIMARY KEY,
    guild_id  BIGINT NOT NULL,
    planet_id INT    NOT NULL,
    address   TEXT   NOT NULL,
    terrain   TEXT   NOT NULL DEFAULT 'flat',
    UNIQUE(guild_id, planet_id, address)
);

CREATE TABLE IF NOT EXISTS squadrons (
    id                  SERIAL      PRIMARY KEY,
    guild_id            BIGINT      NOT NULL,
    planet_id           INT         NOT NULL DEFAULT 1,
    owner_id            BIGINT      NOT NULL,
    owner_name          TEXT        NOT NULL DEFAULT 'Operative',
    name                TEXT        NOT NULL,
    brigade             TEXT        NOT NULL DEFAULT 'infantry',
    hex_address         TEXT        NOT NULL,
    in_transit          BOOLEAN     NOT NULL DEFAULT FALSE,
    transit_destination TEXT        DEFAULT NULL,
    transit_turns_left  INT         NOT NULL DEFAULT 0,
    attack              INT         NOT NULL DEFAULT 10,
    defense             INT         NOT NULL DEFAULT 10,
    speed               INT         NOT NULL DEFAULT 10,
    morale              INT         NOT NULL DEFAULT 10,
    supply              INT         NOT NULL DEFAULT 10,
    recon               INT         NOT NULL DEFAULT 10,
    is_active           BOOLEAN     NOT NULL DEFAULT TRUE,
    last_scavenged_turn INT         NOT NULL DEFAULT -1,
    last_moved_turn     INT         NOT NULL DEFAULT -1,
    is_dug_in           BOOLEAN     NOT NULL DEFAULT FALSE,
    artillery_armed     BOOLEAN     NOT NULL DEFAULT FALSE,
    hp                  INT         NOT NULL DEFAULT 3,
    UNIQUE(guild_id, planet_id, owner_id, name)
);

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

CREATE TABLE IF NOT EXISTS enemy_gm_moves (
    id             SERIAL      PRIMARY KEY,
    guild_id       BIGINT      NOT NULL,
    planet_id      INT         NOT NULL DEFAULT 1,
    enemy_unit_id  INT         NOT NULL,
    target_address TEXT        NOT NULL,
    queued_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(guild_id, enemy_unit_id)
);

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

CREATE TABLE IF NOT EXISTS turn_history (
    id          SERIAL      PRIMARY KEY,
    guild_id    BIGINT      NOT NULL,
    planet_id   INT         NOT NULL DEFAULT 1,
    turn_number INT         NOT NULL,
    resolved_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS player_economy (
    id            SERIAL PRIMARY KEY,
    guild_id      BIGINT NOT NULL,
    owner_id      BIGINT NOT NULL,
    raw_materials INT    NOT NULL DEFAULT 0,
    credits       INT    NOT NULL DEFAULT 0,
    UNIQUE(guild_id, owner_id)
);

CREATE INDEX IF NOT EXISTS idx_hexes_gp    ON hexes(guild_id, planet_id);
CREATE INDEX IF NOT EXISTS idx_sq_gp       ON squadrons(guild_id, planet_id);
CREATE INDEX IF NOT EXISTS idx_enemy_gp    ON enemy_units(guild_id, planet_id);
CREATE INDEX IF NOT EXISTS idx_combat_g    ON combat_log(guild_id);
CREATE INDEX IF NOT EXISTS idx_planets_g   ON planets(guild_id);
CREATE INDEX IF NOT EXISTS idx_terrain_gp  ON hex_terrain(guild_id, planet_id);

-- Migration guards
DO $$ BEGIN ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS enlist_channel_id  BIGINT DEFAULT NULL; END $$;
DO $$ BEGIN ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS enlist_message_id  BIGINT DEFAULT NULL; END $$;
DO $$ BEGIN ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS active_planet_id   INT    DEFAULT 1;    END $$;
DO $$ BEGIN ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS overview_channel_id BIGINT DEFAULT NULL; END $$;
DO $$ BEGIN ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS overview_message_id BIGINT DEFAULT NULL; END $$;
DO $$ BEGIN ALTER TABLE squadrons    ADD COLUMN IF NOT EXISTS brigade            TEXT    NOT NULL DEFAULT 'infantry'; END $$;
DO $$ BEGIN ALTER TABLE squadrons    ADD COLUMN IF NOT EXISTS transit_turns_left INT     NOT NULL DEFAULT 0; END $$;
DO $$ BEGIN ALTER TABLE squadrons    ADD COLUMN IF NOT EXISTS last_moved_turn    INT     NOT NULL DEFAULT -1; END $$;
DO $$ BEGIN ALTER TABLE squadrons    ADD COLUMN IF NOT EXISTS is_dug_in          BOOLEAN NOT NULL DEFAULT FALSE; END $$;
DO $$ BEGIN ALTER TABLE squadrons    ADD COLUMN IF NOT EXISTS artillery_armed    BOOLEAN NOT NULL DEFAULT FALSE; END $$;
DO $$ BEGIN ALTER TABLE squadrons    ADD COLUMN IF NOT EXISTS planet_id          INT     NOT NULL DEFAULT 1; END $$;
DO $$ BEGIN ALTER TABLE enemy_units  ADD COLUMN IF NOT EXISTS planet_id          INT     NOT NULL DEFAULT 1; END $$;
DO $$ BEGIN ALTER TABLE combat_log   ADD COLUMN IF NOT EXISTS planet_id          INT     NOT NULL DEFAULT 1; END $$;
DO $$ BEGIN ALTER TABLE turn_history ADD COLUMN IF NOT EXISTS planet_id          INT     NOT NULL DEFAULT 1; END $$;
DO $$ BEGIN ALTER TABLE enemy_gm_moves ADD COLUMN IF NOT EXISTS planet_id        INT    NOT NULL DEFAULT 1; END $$;

-- v3 hex schema migration: drop old two-level columns if they exist (makes level NOT NULL safe to remove)
DO $$ BEGIN
    ALTER TABLE hexes ALTER COLUMN level DROP NOT NULL;
EXCEPTION WHEN undefined_column THEN NULL; END $$;
DO $$ BEGIN
    ALTER TABLE hexes ALTER COLUMN parent_address DROP NOT NULL;
EXCEPTION WHEN undefined_column THEN NULL; END $$;
-- Drop old transit_step column replaced by transit_turns_left
DO $$ BEGIN
    ALTER TABLE squadrons DROP COLUMN IF EXISTS transit_step;
EXCEPTION WHEN undefined_column THEN NULL; END $$;
-- Drop old deploy_hex column
DO $$ BEGIN
    ALTER TABLE squadrons DROP COLUMN IF EXISTS deploy_hex;
EXCEPTION WHEN undefined_column THEN NULL; END $$;
-- Drop old last_combat_turn column replaced by last_moved_turn
DO $$ BEGIN
    ALTER TABLE squadrons DROP COLUMN IF EXISTS last_combat_turn;
EXCEPTION WHEN undefined_column THEN NULL; END $$;
-- Drop old citadel_besieged column from guild_config
DO $$ BEGIN
    ALTER TABLE guild_config DROP COLUMN IF EXISTS citadel_besieged;
EXCEPTION WHEN undefined_column THEN NULL; END $$;

-- v4 additions
DO $$ BEGIN ALTER TABLE squadrons    ADD COLUMN IF NOT EXISTS hp                    INT    NOT NULL DEFAULT 3; END $$;
DO $$ BEGIN ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS contract_name         TEXT   DEFAULT NULL; END $$;
DO $$ BEGIN ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS announcement_channel_id BIGINT DEFAULT NULL; END $$;
