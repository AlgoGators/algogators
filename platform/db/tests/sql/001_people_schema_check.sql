-- Sanity checks against a freshly migrated database. Every block raises on failure.
\set ON_ERROR_STOP on

DO $$
DECLARE
    n INT;
    v INT;
    pid BIGINT;
    sid BIGINT;
    mid BIGINT;
    aid BIGINT;
    tid BIGINT;
BEGIN
    -- 13 tables
    SELECT COUNT(*) INTO n FROM information_schema.tables
     WHERE table_schema = 'people' AND table_type = 'BASE TABLE';
    IF n <> 13 THEN RAISE EXCEPTION 'expected 13 tables, got %', n; END IF;

    -- 2 views
    SELECT COUNT(*) INTO n FROM information_schema.views WHERE table_schema = 'people';
    IF n <> 2 THEN RAISE EXCEPTION 'expected 2 views, got %', n; END IF;

    -- 4 seeded teams
    SELECT COUNT(*) INTO n FROM people.team;
    IF n <> 4 THEN RAISE EXCEPTION 'expected 4 teams, got %', n; END IF;

    -- 19 triggers (10 bump + 9 log)
    SELECT COUNT(*) INTO n FROM information_schema.triggers WHERE trigger_schema = 'people';
    -- information_schema.triggers lists one row per event; AFTER INSERT OR UPDATE = 2 rows.
    IF n <> 10 + 18 THEN RAISE EXCEPTION 'expected 28 trigger-event rows, got %', n; END IF;
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'people' AND table_name = 'application_score') THEN
        RAISE EXCEPTION 'application_score must not exist';
    END IF;

    -- person: insert, generated name_key, change log written with actor
    PERFORM set_config('people.actor', 'tester', true);
    INSERT INTO people.person (first_name, last_name, email)
    VALUES ('  Jane ', 'Doe', 'Jane@ufl.edu') RETURNING id INTO pid;
    IF (SELECT name_key FROM people.person WHERE id = pid) <> 'jane doe' THEN
        RAISE EXCEPTION 'name_key not generated';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM people.change_log
                   WHERE table_name = 'person' AND row_id = pid AND operation = 'insert' AND actor = 'tester') THEN
        RAISE EXCEPTION 'insert not logged';
    END IF;

    -- case-insensitive email uniqueness
    BEGIN
        INSERT INTO people.person (first_name, last_name, email) VALUES ('J', 'D', 'jane@UFL.edu');
        RAISE EXCEPTION 'duplicate email accepted';
    EXCEPTION WHEN unique_violation THEN NULL;
    END;

    -- row_version bumped by trigger, not caller
    UPDATE people.person SET last_name = 'Roe' WHERE id = pid;
    SELECT row_version INTO v FROM people.person WHERE id = pid;
    IF v <> 2 THEN RAISE EXCEPTION 'row_version not bumped, got %', v; END IF;

    -- soft delete frees the email, is logged as soft_delete
    UPDATE people.person SET deleted_at = now() WHERE id = pid;
    IF NOT EXISTS (SELECT 1 FROM people.change_log WHERE row_id = pid AND operation = 'soft_delete') THEN
        RAISE EXCEPTION 'soft delete not logged';
    END IF;
    INSERT INTO people.person (first_name, last_name, email) VALUES ('Jane', 'Doe', 'jane@ufl.edu') RETURNING id INTO pid;

    -- student + grad_sort
    INSERT INTO people.student (person_id, grad_term, grad_year) VALUES (pid, 'fall', 2027) RETURNING id INTO sid;
    IF (SELECT grad_sort FROM people.student WHERE id = sid) <> 20273 THEN RAISE EXCEPTION 'grad_sort wrong'; END IF;

    -- composite FK: member cannot point at another person's student row
    INSERT INTO people.person (first_name, last_name, email) VALUES ('Other', 'Person', 'other@ufl.edu');
    BEGIN
        INSERT INTO people.member (person_id, student_id)
        VALUES ((SELECT id FROM people.person WHERE email = 'other@ufl.edu'), sid);
        RAISE EXCEPTION 'composite FK not enforced';
    EXCEPTION WHEN foreign_key_violation THEN NULL;
    END;
    INSERT INTO people.member (person_id, student_id) VALUES (pid, sid) RETURNING id INTO mid;

    -- one current team per member
    SELECT id INTO tid FROM people.team WHERE slug = 'quant_dev';
    INSERT INTO people.member_team (member_id, team_id) VALUES (mid, tid);
    BEGIN
        INSERT INTO people.member_team (member_id, team_id) VALUES (mid, (SELECT id FROM people.team WHERE slug = 'quant_research'));
        RAISE EXCEPTION 'second current team accepted';
    EXCEPTION WHEN unique_violation THEN NULL;
    END;

    -- applications: two cycles, count of 2, one per cycle enforced
    INSERT INTO people.application (person_id, cycle_term, cycle_year, submitted_at)
    VALUES (pid, 'fall', 2025, '2025-09-01') RETURNING id INTO aid;
    INSERT INTO people.application (person_id, cycle_term, cycle_year, outcome, submitted_at)
    VALUES (pid, 'spring', 2026, 'accepted', '2026-01-15');
    BEGIN
        INSERT INTO people.application (person_id, cycle_term, cycle_year) VALUES (pid, 'fall', 2025);
        RAISE EXCEPTION 'duplicate application per cycle accepted';
    EXCEPTION WHEN unique_violation THEN NULL;
    END;
    IF (SELECT stage_rank FROM people.application WHERE id = aid) <> 1 THEN RAISE EXCEPTION 'stage_rank wrong'; END IF;

    -- team preference: no two teams share a rank
    INSERT INTO people.application_team_preference (application_id, team_id, rank) VALUES (aid, tid, 1);
    BEGIN
        INSERT INTO people.application_team_preference (application_id, team_id, rank)
        VALUES (aid, (SELECT id FROM people.team WHERE slug = 'quant_research'), 1);
        RAISE EXCEPTION 'duplicate rank accepted';
    EXCEPTION WHEN unique_violation THEN NULL;
    END;

    -- applicant_history
    IF (SELECT times_applied FROM people.applicant_history WHERE person_id = pid) <> 2 THEN RAISE EXCEPTION 'times_applied wrong'; END IF;
    IF (SELECT latest_outcome FROM people.applicant_history WHERE person_id = pid) <> 'accepted' THEN RAISE EXCEPTION 'latest_outcome wrong'; END IF;
    IF NOT (SELECT is_current_member FROM people.applicant_history WHERE person_id = pid) THEN RAISE EXCEPTION 'is_current_member wrong'; END IF;

    -- roster_public
    IF (SELECT team FROM people.roster_public WHERE id = mid) <> 'Quantitative Development' THEN RAISE EXCEPTION 'roster team wrong'; END IF;

    -- investor stub logs with person_id as row_id
    INSERT INTO people.investor (person_id) VALUES (pid);
    IF NOT EXISTS (SELECT 1 FROM people.change_log WHERE table_name = 'investor' AND row_id = pid) THEN
        RAISE EXCEPTION 'investor insert not logged';
    END IF;

    -- attachment: content/fetched_at pairing
    BEGIN
        INSERT INTO people.attachment (person_id, kind, original_filename, content)
        VALUES (pid, 'resume', 'resume_Jane Doe.pdf', '\x00'::bytea);
        RAISE EXCEPTION 'content without fetched_at accepted';
    EXCEPTION WHEN check_violation THEN NULL;
    END;

    RAISE NOTICE 'ALL CHECKS PASSED';
END;
$$;
