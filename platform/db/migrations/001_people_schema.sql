-- =============================================================================
-- 001_people_schema.sql — the `people` schema: applicants, members, investors.
--
-- The design is argued in platform/db/docs/people-schema-reference.md. This
-- file is the executable version of that document. Read the doc first.
--
-- Idempotent: every statement is IF NOT EXISTS / OR REPLACE / DROP-then-CREATE,
-- so re-running the file is safe. Runs as one transaction: either the whole
-- schema lands or none of it does.
--
-- Apply with pgAdmin (open this file in the Query Tool against the target
-- database and execute) or with psql:
--
--     psql -U postgres -d new_algo_data -v ON_ERROR_STOP=1 -f 001_people_schema.sql
--
-- Requires Postgres 12+ (generated columns).
-- =============================================================================

BEGIN;

CREATE SCHEMA IF NOT EXISTS people;

-- -----------------------------------------------------------------------------
-- people.team — lookup of teams.
--
-- A table rather than a CHECK constraint because team names change, and a
-- CHECK would turn every rename into a migration. `slug` is the stable handle.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS people.team (
    id           BIGSERIAL PRIMARY KEY,
    name         TEXT        NOT NULL CHECK (btrim(name) <> ''),
    -- Inline UNIQUE, deliberately NOT partial on deleted_at: a retired team keeps
    -- its slug reserved, because reusing one would silently re-point historical
    -- assignments at a different team.
    slug         TEXT        NOT NULL UNIQUE CHECK (slug ~ '^[a-z][a-z0-9_]*$'),
    active       BOOLEAN     NOT NULL DEFAULT TRUE,
    deleted_at   TIMESTAMPTZ,
    row_version  INTEGER     NOT NULL DEFAULT 1,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A retired team's *display name* can be reused; its slug cannot.
CREATE UNIQUE INDEX IF NOT EXISTS team_name_lower_idx
    ON people.team (lower(name)) WHERE deleted_at IS NULL;

INSERT INTO people.team (name, slug) VALUES
    ('Quantitative Research',   'quant_research'),
    ('Quantitative Development', 'quant_dev'),
    ('Quantitative Trading',    'quant_trading'),
    ('Investor Relations',      'investor_relations')
ON CONFLICT (slug) DO NOTHING;

-- -----------------------------------------------------------------------------
-- people.person — one row per human. Everything else hangs off it.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS people.person (
    id           BIGSERIAL PRIMARY KEY,
    first_name   TEXT        NOT NULL CHECK (btrim(first_name) <> ''),
    last_name    TEXT        NOT NULL CHECK (btrim(last_name) <> ''),
    -- The applicant's UF email, which is what the forms ask for.
    email        TEXT        NOT NULL CHECK (btrim(email) <> ''),
    -- Nullable on purpose: an applicant is neither a member nor an investor.
    -- Denormalized and NOT the source of truth — membership is the existence
    -- of a people.member row.
    person_type  TEXT        CHECK (person_type IN ('member', 'investor')),
    -- Pairs manually uploaded resume files back to people. Deliberately not
    -- unique: two people can share a name. Email is the identity.
    name_key     TEXT        GENERATED ALWAYS AS
                     (lower(btrim(first_name) || ' ' || btrim(last_name))) STORED,
    deleted_at   TIMESTAMPTZ,
    row_version  INTEGER     NOT NULL DEFAULT 1,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Case-insensitive so Jane@ufl.edu and jane@ufl.edu cannot become two people.
-- Partial so a soft-deleted person does not own their address forever.
CREATE UNIQUE INDEX IF NOT EXISTS person_email_lower_idx
    ON people.person (lower(email)) WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS person_name_key_idx
    ON people.person (name_key);

-- -----------------------------------------------------------------------------
-- people.student — the academic record.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS people.student (
    id             BIGSERIAL PRIMARY KEY,
    person_id      BIGINT       NOT NULL REFERENCES people.person (id) ON DELETE CASCADE,
    -- Nullable: neither form asks. NUMERIC so 3.85 round-trips exactly; ceiling
    -- 4.5 because weighted scales exceed 4.
    gpa            NUMERIC(3,2) CHECK (gpa IS NULL OR (gpa >= 0 AND gpa <= 4.5)),
    class_standing TEXT,
    -- A term and a year, not a date. There is no winter term.
    grad_term      TEXT         NOT NULL CHECK (grad_term IN ('spring', 'summer', 'fall')),
    grad_year      SMALLINT     NOT NULL CHECK (grad_year BETWEEN 1900 AND 2200),
    -- "graduating before X" as one comparison.
    grad_sort      INTEGER      GENERATED ALWAYS AS (
                       grad_year * 10 + CASE grad_term
                           WHEN 'spring' THEN 1
                           WHEN 'summer' THEN 2
                           WHEN 'fall'   THEN 3
                       END) STORED,
    deleted_at     TIMESTAMPTZ,
    row_version    INTEGER      NOT NULL DEFAULT 1,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    -- So member can reference the (person, student) pair as one FK.
    CONSTRAINT student_person_id_id_key UNIQUE (person_id, id)
);

CREATE UNIQUE INDEX IF NOT EXISTS student_person_live_idx
    ON people.student (person_id) WHERE deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- people.student_major — majors and minors, any number of each.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS people.student_major (
    id           BIGSERIAL PRIMARY KEY,
    student_id   BIGINT      NOT NULL REFERENCES people.student (id) ON DELETE CASCADE,
    field        TEXT        NOT NULL CHECK (btrim(field) <> ''),
    kind         TEXT        NOT NULL CHECK (kind IN ('major', 'minor')),
    deleted_at   TIMESTAMPTZ,
    row_version  INTEGER     NOT NULL DEFAULT 1,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS student_major_live_idx
    ON people.student_major (student_id, lower(field), kind) WHERE deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- people.attachment — resumes and strategy write-ups.
--
-- Anchored to person, not student: an applicant submits a resume before there
-- is any reason to have a student row. `content` is NULL after ingest and is
-- filled by a human upload later.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS people.attachment (
    id                BIGSERIAL PRIMARY KEY,
    person_id         BIGINT      NOT NULL REFERENCES people.person (id),
    kind              TEXT        NOT NULL CHECK (kind IN ('resume', 'trading_strategy')),
    source_url        TEXT,
    -- Exactly as Forms produced it. Never normalized: the respondent name
    -- Forms appends before the extension is the only link back to a person.
    original_filename TEXT        NOT NULL CHECK (btrim(original_filename) <> ''),
    content           BYTEA,
    content_type      TEXT        CHECK (content_type IS NULL OR content_type = 'application/pdf'),
    -- 10 MB: Microsoft Forms' smallest per-file setting. Not stricter, because
    -- a lower ceiling would reject a file the form accepted.
    size_bytes        BIGINT      CHECK (size_bytes IS NULL OR (size_bytes > 0 AND size_bytes <= 10485760)),
    sha256            TEXT        CHECK (sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'),
    fetched_at        TIMESTAMPTZ,
    deleted_at        TIMESTAMPTZ,
    row_version       INTEGER     NOT NULL DEFAULT 1,
    uploaded_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- A row cannot claim a fetch that produced nothing, or hold bytes with no
    -- record of when they arrived.
    CONSTRAINT attachment_content_fetched_chk CHECK ((content IS NULL) = (fetched_at IS NULL)),
    -- So application can reference the (person, attachment) pair as one FK.
    CONSTRAINT attachment_person_id_id_key UNIQUE (person_id, id)
);

-- "Who to chase before a review round."
CREATE INDEX IF NOT EXISTS attachment_unfetched_idx
    ON people.attachment (person_id) WHERE content IS NULL AND deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- people.member — who is in the club.
--
-- No team column: people move teams and a column would lose the history. See
-- member_team. Alumni stay here with left_on set.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS people.member (
    id            BIGSERIAL PRIMARY KEY,
    person_id     BIGINT      NOT NULL REFERENCES people.person (id),
    -- Nullable for members who are not students (alum, staff, advisor). A
    -- composite FK with a NULL column is satisfied, so no exemption is needed.
    student_id    BIGINT,
    -- Orthogonal to team: a lead belongs to a real team as well.
    is_leadership BOOLEAN     NOT NULL DEFAULT FALSE,
    joined_on     DATE        NOT NULL DEFAULT CURRENT_DATE,
    left_on       DATE        CHECK (left_on IS NULL OR left_on >= joined_on),
    deleted_at    TIMESTAMPTZ,
    row_version   INTEGER     NOT NULL DEFAULT 1,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Composite: a member row cannot name person 7 and a student record that
    -- belongs to person 19.
    CONSTRAINT member_student_fkey
        FOREIGN KEY (person_id, student_id) REFERENCES people.student (person_id, id)
);

CREATE UNIQUE INDEX IF NOT EXISTS member_person_live_idx
    ON people.member (person_id) WHERE deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- people.member_team — team assignment history.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS people.member_team (
    id           BIGSERIAL PRIMARY KEY,
    member_id    BIGINT      NOT NULL REFERENCES people.member (id) ON DELETE CASCADE,
    team_id      BIGINT      NOT NULL REFERENCES people.team (id),
    started_on   DATE        NOT NULL DEFAULT CURRENT_DATE,
    ended_on     DATE        CHECK (ended_on IS NULL OR ended_on >= started_on),
    deleted_at   TIMESTAMPTZ,
    row_version  INTEGER     NOT NULL DEFAULT 1,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One *current* team per member; past assignments unconstrained. A member with
-- no open row is "accepted, not yet placed".
CREATE UNIQUE INDEX IF NOT EXISTS member_team_current_idx
    ON people.member_team (member_id) WHERE ended_on IS NULL AND deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS member_team_team_idx
    ON people.member_team (team_id);

-- -----------------------------------------------------------------------------
-- people.application_form — the form of record, per cycle per track.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS people.application_form (
    id               BIGSERIAL PRIMARY KEY,
    -- Narrower than student.grad_term: recruiting runs fall and spring only.
    cycle_term       TEXT        NOT NULL CHECK (cycle_term IN ('fall', 'spring')),
    cycle_year       SMALLINT    NOT NULL CHECK (cycle_year BETWEEN 1900 AND 2200),
    -- Not a team: one 'analyst' form covers the three quant teams.
    track            TEXT        NOT NULL CHECK (track IN ('analyst', 'relations')),
    form_url         TEXT        NOT NULL,
    external_form_id TEXT,
    -- Which question feeds which column, for THIS cycle's form. Data, not code.
    question_map     JSONB       NOT NULL DEFAULT '{}'::jsonb,
    opened_at        TIMESTAMPTZ,
    closed_at        TIMESTAMPTZ,
    deleted_at       TIMESTAMPTZ,
    row_version      INTEGER     NOT NULL DEFAULT 1,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT application_form_window_chk
        CHECK (opened_at IS NULL OR closed_at IS NULL OR closed_at >= opened_at)
);

CREATE UNIQUE INDEX IF NOT EXISTS application_form_cycle_track_idx
    ON people.application_form (cycle_term, cycle_year, track) WHERE deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- people.application — one submission.
--
-- `stage` is the furthest stage reached and only moves forward; `outcome` is
-- the decision. Separate so "rejected at interview" keeps both halves.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS people.application (
    id                   BIGSERIAL PRIMARY KEY,
    person_id            BIGINT      NOT NULL REFERENCES people.person (id),
    cycle_term           TEXT        NOT NULL CHECK (cycle_term IN ('fall', 'spring')),
    cycle_year           SMALLINT    NOT NULL CHECK (cycle_year BETWEEN 1900 AND 2200),
    form_id              BIGINT      REFERENCES people.application_form (id),
    -- The export's `Id` column. Per form, starting at 1 — not globally unique.
    external_response_id TEXT,
    -- The whole response, verbatim. Never parsed at query time; exists so a
    -- question can be backfilled later. Also holds date of birth on purpose.
    raw_response         JSONB,
    stage                TEXT        NOT NULL DEFAULT 'submitted'
                             CHECK (stage IN ('submitted', 'screen', 'interview', 'final')),
    stage_rank           SMALLINT    GENERATED ALWAYS AS (
                             CASE stage
                                 WHEN 'submitted' THEN 1
                                 WHEN 'screen'    THEN 2
                                 WHEN 'interview' THEN 3
                                 WHEN 'final'     THEN 4
                             END) STORED,
    outcome              TEXT        NOT NULL DEFAULT 'pending'
                             CHECK (outcome IN ('pending', 'accepted', 'rejected', 'withdrawn')),
    -- Pins the resume this application was judged on.
    resume_attachment_id BIGINT,
    submitted_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at           TIMESTAMPTZ,
    row_version          INTEGER     NOT NULL DEFAULT 1,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT application_resume_fkey
        FOREIGN KEY (person_id, resume_attachment_id)
        REFERENCES people.attachment (person_id, id)
);

-- Deliberately NOT partial on deleted_at: a response id names one event that
-- happened once, so re-importing must find a soft-deleted row and restore it.
CREATE UNIQUE INDEX IF NOT EXISTS application_form_response_idx
    ON people.application (form_id, external_response_id)
    WHERE external_response_id IS NOT NULL;

-- One live application per person per cycle; withdraw-and-reapply stays possible.
CREATE UNIQUE INDEX IF NOT EXISTS application_person_cycle_idx
    ON people.application (person_id, cycle_term, cycle_year) WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS application_person_idx
    ON people.application (person_id);

-- -----------------------------------------------------------------------------
-- people.application_team_preference — the ranked answer.
--
-- Relations applications get no rows here. A preference is not a placement.
-- No deleted_at / row_version: rows live and die with their application.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS people.application_team_preference (
    application_id BIGINT      NOT NULL REFERENCES people.application (id) ON DELETE CASCADE,
    team_id        BIGINT      NOT NULL REFERENCES people.team (id),
    rank           SMALLINT    NOT NULL CHECK (rank >= 1),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (application_id, team_id),
    CONSTRAINT application_team_preference_rank_key UNIQUE (application_id, rank)
);

-- people.application_score is gone. Grades are not stored in the database:
-- leadership keeps them in the grading workbook. The DROP tidies up any
-- database that ran an earlier version of this file; it never held data.
DROP TABLE IF EXISTS people.application_score;

-- -----------------------------------------------------------------------------
-- people.investor — a deliberate stub. Shape decided in a later migration.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS people.investor (
    person_id    BIGINT      PRIMARY KEY REFERENCES people.person (id),
    deleted_at   TIMESTAMPTZ,
    row_version  INTEGER     NOT NULL DEFAULT 1,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- people.import_batch — one row per write event (an upload, a sync, an edit).
-- What makes "undo that upload" a real operation.
--
-- `actor` must come from the authenticated session, never from a field in the
-- uploaded workbook.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS people.import_batch (
    id           BIGSERIAL PRIMARY KEY,
    source       TEXT        NOT NULL CHECK (source IN ('form_sync', 'excel_upload', 'app_edit', 'api')),
    filename     TEXT,
    -- "Did I already upload this exact sheet" before any row is touched.
    file_sha256  TEXT        CHECK (file_sha256 IS NULL OR file_sha256 ~ '^[0-9a-f]{64}$'),
    actor        TEXT        NOT NULL CHECK (btrim(actor) <> ''),
    note         TEXT,
    row_count    INTEGER     NOT NULL DEFAULT 0 CHECK (row_count >= 0),
    status       TEXT        NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'applied', 'failed', 'reverted')),
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    reverted_at  TIMESTAMPTZ,
    reverted_by  TEXT
);

-- -----------------------------------------------------------------------------
-- people.change_log — row-level history, whole-row snapshots before and after.
-- Reverting a batch is reading this newest-first and applying each `before`.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS people.change_log (
    id           BIGSERIAL PRIMARY KEY,
    batch_id     BIGINT      REFERENCES people.import_batch (id),
    table_name   TEXT        NOT NULL,
    row_id       BIGINT      NOT NULL,
    operation    TEXT        NOT NULL CHECK (operation IN ('insert', 'update', 'soft_delete', 'restore')),
    before       JSONB,
    after        JSONB,
    actor        TEXT        NOT NULL,
    occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS change_log_row_idx   ON people.change_log (table_name, row_id);
CREATE INDEX IF NOT EXISTS change_log_batch_idx ON people.change_log (batch_id);

-- =============================================================================
-- Triggers.
--
-- History and row_version are maintained by the database, not application
-- code, so a psql session, a migration, or a second service cannot skip them.
-- A writer that can only speak SQL sets two session settings per transaction:
--     SET LOCAL people.actor    = 'jane';
--     SET LOCAL people.batch_id = '42';
-- =============================================================================

CREATE OR REPLACE FUNCTION people.bump_version() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    NEW.row_version := OLD.row_version + 1;
    NEW.updated_at  := now();
    RETURN NEW;
END;
$fn$;

CREATE OR REPLACE FUNCTION people.log_change() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE
    v_op       TEXT;
    v_actor    TEXT;
    v_batch    BIGINT;
    v_after    JSONB;
    v_before   JSONB;
    v_row_id   BIGINT;
BEGIN
    v_after := to_jsonb(NEW);
    -- investor's primary key is person_id; every other logged table has id.
    v_row_id := COALESCE((v_after ->> 'id')::BIGINT, (v_after ->> 'person_id')::BIGINT);

    IF TG_OP = 'INSERT' THEN
        v_op := 'insert';
    ELSE
        v_before := to_jsonb(OLD);
        IF OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL THEN
            v_op := 'soft_delete';
        ELSIF OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS NULL THEN
            v_op := 'restore';
        ELSE
            v_op := 'update';
        END IF;
    END IF;

    v_actor := NULLIF(current_setting('people.actor', true), '');
    IF v_actor IS NULL THEN
        v_actor := current_user;
    END IF;
    v_batch := NULLIF(current_setting('people.batch_id', true), '')::BIGINT;

    INSERT INTO people.change_log (batch_id, table_name, row_id, operation, before, after, actor)
    VALUES (v_batch, TG_TABLE_NAME, v_row_id, v_op, v_before, v_after, v_actor);

    RETURN NEW;
END;
$fn$;

-- CREATE TRIGGER has no IF NOT EXISTS, and CREATE OR REPLACE TRIGGER is 14+,
-- so: DROP then CREATE. Idempotent on every version.
DO $do$
DECLARE
    t TEXT;
BEGIN
    -- row_version: the 9 logged tables plus attachment.
    FOREACH t IN ARRAY ARRAY[
        'person', 'student', 'student_major', 'member', 'member_team', 'team',
        'application', 'application_form', 'investor', 'attachment'
    ] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON people.%I', t || '_bump_version', t);
        EXECUTE format(
            'CREATE TRIGGER %I BEFORE UPDATE ON people.%I
             FOR EACH ROW EXECUTE FUNCTION people.bump_version()',
            t || '_bump_version', t);
    END LOOP;

    -- change_log: attachment excluded, because to_jsonb would base64 the PDF
    -- into a history row on every write.
    FOREACH t IN ARRAY ARRAY[
        'person', 'student', 'student_major', 'member', 'member_team', 'team',
        'application', 'application_form', 'investor'
    ] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON people.%I', t || '_log_change', t);
        EXECUTE format(
            'CREATE TRIGGER %I AFTER INSERT OR UPDATE ON people.%I
             FOR EACH ROW EXECUTE FUNCTION people.log_change()',
            t || '_log_change', t);
    END LOOP;
END;
$do$;

-- =============================================================================
-- Views.
-- =============================================================================

-- Every live member, past and present, with no email, GPA, resume or DOB.
-- Alumni appear with active = false rather than being filtered out.
CREATE OR REPLACE VIEW people.roster_public AS
SELECT
    m.id,
    p.first_name,
    p.last_name,
    t.name                AS team,
    m.is_leadership,
    m.joined_on,
    (m.left_on IS NULL)   AS active
FROM people.member m
JOIN people.person p
  ON p.id = m.person_id AND p.deleted_at IS NULL
LEFT JOIN people.member_team mt
  ON mt.member_id = m.id AND mt.ended_on IS NULL AND mt.deleted_at IS NULL
LEFT JOIN people.team t
  ON t.id = mt.team_id
WHERE m.deleted_at IS NULL;

-- One row per person who has ever applied: how many times, which cycles, and
-- whether they are a current member. The count is derived from application
-- rows rather than stored, so it cannot drift from the applications themselves.
CREATE OR REPLACE VIEW people.applicant_history AS
SELECT
    p.id                                   AS person_id,
    p.first_name,
    p.last_name,
    p.email,
    COUNT(a.id)::INTEGER                   AS times_applied,
    ARRAY_AGG(a.cycle_term || ' ' || a.cycle_year::TEXT
              ORDER BY a.cycle_year, CASE a.cycle_term WHEN 'spring' THEN 1 ELSE 3 END) AS cycles,
    MIN(a.submitted_at)                    AS first_applied_at,
    MAX(a.submitted_at)                    AS last_applied_at,
    (ARRAY_AGG(a.outcome ORDER BY a.submitted_at DESC))[1] AS latest_outcome,
    EXISTS (
        SELECT 1 FROM people.member m
        WHERE m.person_id = p.id AND m.deleted_at IS NULL AND m.left_on IS NULL
    )                                      AS is_current_member
FROM people.person p
JOIN people.application a
  ON a.person_id = p.id AND a.deleted_at IS NULL
WHERE p.deleted_at IS NULL
GROUP BY p.id, p.first_name, p.last_name, p.email;

-- =============================================================================
-- Grants. Guarded: an unguarded GRANT to a missing role aborts the migration.
-- Until these roles exist the schema is reachable only by the database owner,
-- which is the intended interim state.
-- =============================================================================
DO $do$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'db_readwrite_all') THEN
        GRANT USAGE ON SCHEMA people TO db_readwrite_all;
        GRANT ALL ON ALL TABLES IN SCHEMA people TO db_readwrite_all;
        GRANT USAGE ON ALL SEQUENCES IN SCHEMA people TO db_readwrite_all;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'db_readonly') THEN
        GRANT USAGE ON SCHEMA people TO db_readonly;
        GRANT SELECT ON people.roster_public TO db_readonly;
    END IF;
END;
$do$;

COMMIT;
