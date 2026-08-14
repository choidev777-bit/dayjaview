BEGIN;

CREATE SCHEMA IF NOT EXISTS identity;
CREATE SCHEMA IF NOT EXISTS library;

CREATE TABLE identity.users (
    user_id text PRIMARY KEY,
    google_subject text NOT NULL UNIQUE,
    display_name text NOT NULL,
    email text,
    email_verified boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL,
    last_authenticated_at timestamptz NOT NULL,
    CONSTRAINT identity_users_user_id_length CHECK (char_length(user_id) BETWEEN 1 AND 128),
    CONSTRAINT identity_users_google_subject_length
        CHECK (char_length(google_subject) BETWEEN 1 AND 255),
    CONSTRAINT identity_users_display_name_length
        CHECK (char_length(display_name) BETWEEN 1 AND 200),
    CONSTRAINT identity_users_email_length
        CHECK (email IS NULL OR char_length(email) BETWEEN 1 AND 320)
);

CREATE TABLE identity.user_roles (
    user_id text NOT NULL REFERENCES identity.users(user_id) ON DELETE CASCADE,
    role text NOT NULL,
    granted_at timestamptz NOT NULL,
    PRIMARY KEY (user_id, role),
    CONSTRAINT identity_user_roles_role
        CHECK (role IN ('USER', 'HISTORICAL_PILOT', 'OPERATOR'))
);

CREATE TABLE identity.oauth_states (
    state_hash char(64) PRIMARY KEY,
    browser_nonce_hash char(64) NOT NULL,
    return_to text NOT NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    CONSTRAINT identity_oauth_states_return_to
        CHECK (
            char_length(return_to) BETWEEN 1 AND 2048
            AND left(return_to, 1) = '/'
            AND left(return_to, 2) <> '//'
            AND position(chr(92) IN return_to) = 0
        ),
    CONSTRAINT identity_oauth_states_expiry CHECK (expires_at > created_at),
    CONSTRAINT identity_oauth_states_consumed_at
        CHECK (consumed_at IS NULL OR consumed_at >= created_at)
);

CREATE INDEX identity_oauth_states_expiry_idx
    ON identity.oauth_states (expires_at)
    WHERE consumed_at IS NULL;

CREATE TABLE identity.sessions (
    token_hash char(64) PRIMARY KEY,
    user_id text NOT NULL REFERENCES identity.users(user_id) ON DELETE CASCADE,
    csrf_token_hash char(64) NOT NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    authenticated_at timestamptz NOT NULL,
    revoked_at timestamptz,
    CONSTRAINT identity_sessions_expiry CHECK (expires_at > created_at),
    CONSTRAINT identity_sessions_authenticated_at
        CHECK (authenticated_at >= created_at AND authenticated_at <= expires_at),
    CONSTRAINT identity_sessions_revoked_at
        CHECK (revoked_at IS NULL OR revoked_at >= created_at)
);

CREATE INDEX identity_sessions_user_active_idx
    ON identity.sessions (user_id, expires_at)
    WHERE revoked_at IS NULL;

CREATE TABLE identity.realtime_tickets (
    ticket_hash char(64) PRIMARY KEY,
    session_token_hash char(64) NOT NULL
        REFERENCES identity.sessions(token_hash) ON DELETE CASCADE,
    user_id text NOT NULL REFERENCES identity.users(user_id) ON DELETE CASCADE,
    origin text NOT NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    CONSTRAINT identity_realtime_tickets_expiry CHECK (expires_at > created_at),
    CONSTRAINT identity_realtime_tickets_consumed_at
        CHECK (consumed_at IS NULL OR consumed_at >= created_at)
);

CREATE INDEX identity_realtime_tickets_expiry_idx
    ON identity.realtime_tickets (expires_at)
    WHERE consumed_at IS NULL;

CREATE TABLE library.saved_items (
    user_id text NOT NULL REFERENCES identity.users(user_id) ON DELETE CASCADE,
    saved_type text NOT NULL,
    target_id text NOT NULL,
    display_name_snapshot text NOT NULL,
    saved_at timestamptz NOT NULL,
    PRIMARY KEY (user_id, saved_type, target_id),
    CONSTRAINT library_saved_items_type CHECK (saved_type IN ('THEME', 'STOCK', 'EVENT')),
    CONSTRAINT library_saved_items_target_id_length
        CHECK (char_length(target_id) BETWEEN 1 AND 128),
    CONSTRAINT library_saved_items_display_name_length
        CHECK (char_length(display_name_snapshot) BETWEEN 1 AND 500)
);

CREATE INDEX library_saved_items_owner_order_idx
    ON library.saved_items (user_id, saved_at DESC, target_id, saved_type);

COMMENT ON COLUMN identity.oauth_states.state_hash IS
    'SHA-256 digest only; raw OAuth state is never persisted';
COMMENT ON COLUMN identity.sessions.token_hash IS
    'SHA-256 digest only; raw session cookie is never persisted';
COMMENT ON COLUMN identity.sessions.csrf_token_hash IS
    'SHA-256 digest only; raw CSRF token is never persisted';
COMMENT ON TABLE library.saved_items IS
    'Identity/library writer must scope every query and mutation to the authenticated user_id';

COMMIT;
