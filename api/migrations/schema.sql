\restrict dbmate

-- Dumped from database version 16.15 (Debian 16.15-1.pgdg13+2)
-- Dumped by pg_dump version 18.6

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: admin_reset_drop(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.admin_reset_drop(p_drop_id uuid) RETURNS integer
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public'
    AS $$
DECLARE
    v_run_no int;
BEGIN
    PERFORM 1 FROM drops WHERE id = p_drop_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'drop % not found', p_drop_id USING ERRCODE = 'no_data_found';
    END IF;

    PERFORM set_config('fairdrop.resetting', 'on', true);  -- true = local to this transaction
    DELETE FROM allocations WHERE drop_id = p_drop_id;
    PERFORM set_config('fairdrop.resetting', 'off', true);

    UPDATE seats SET status = 'free', entry_id = NULL, sold_at = NULL
    WHERE drop_id = p_drop_id AND status <> 'free';
    DELETE FROM entries WHERE drop_id = p_drop_id;
    DELETE FROM idempotency_records WHERE drop_id = p_drop_id;

    UPDATE drops
    SET run_no = run_no + 1, drawn_at = NULL, closed_at = NULL, done_at = NULL, entry_set_hash = NULL
    WHERE id = p_drop_id
    RETURNING run_no INTO v_run_no;

    RETURN v_run_no;
END;
$$;


--
-- Name: allocations_append_only(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.allocations_append_only() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
BEGIN
    IF TG_OP = 'DELETE'
       AND coalesce(current_setting('fairdrop.resetting', true), '') = 'on'
       AND current_user <> 'fairdrop_app' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'allocations is append-only (% refused)', TG_OP
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;


--
-- Name: allocations_no_truncate(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.allocations_no_truncate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
BEGIN
    IF coalesce(current_setting('fairdrop.resetting', true), '') = 'on'
       AND current_user <> 'fairdrop_app' THEN
        RETURN NULL;
    END IF;
    RAISE EXCEPTION 'allocations is append-only (TRUNCATE refused)'
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;


--
-- Name: create_drop(text, integer, text, integer, integer, text, text, timestamp with time zone, timestamp with time zone); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.create_drop(p_name text, p_capacity integer, p_mode text, p_window_s integer, p_claim_window_s integer, p_seed_commit text, p_seed text DEFAULT NULL::text, p_reg_opens_at timestamp with time zone DEFAULT NULL::timestamp with time zone, p_reg_closes_at timestamp with time zone DEFAULT NULL::timestamp with time zone) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public'
    AS $$
DECLARE
    v_drop_id uuid;
BEGIN
    INSERT INTO drops (name, capacity, mode, phase, window_s, claim_window_s,
                       seed_commit, seed, reg_opens_at, reg_closes_at)
    VALUES (p_name, p_capacity, p_mode, 'SCHEDULED', p_window_s, p_claim_window_s,
            p_seed_commit, p_seed, p_reg_opens_at, p_reg_closes_at)
    RETURNING id INTO v_drop_id;

    INSERT INTO seats (drop_id, capacity, seat_no, status)
    SELECT v_drop_id, p_capacity, n, 'free'
    FROM generate_series(1, p_capacity) AS n;

    RETURN v_drop_id;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: abuse_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.abuse_events (
    id bigint NOT NULL,
    ts timestamp with time zone DEFAULT now() NOT NULL,
    drop_id uuid,
    layer text,
    action text,
    key_type text,
    key_value text,
    user_id uuid,
    detail jsonb
);


--
-- Name: abuse_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.abuse_events ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.abuse_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: allocations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.allocations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    drop_id uuid NOT NULL,
    entry_id uuid NOT NULL,
    seat_id bigint NOT NULL,
    idempotency_key uuid NOT NULL,
    run_no integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT allocations_run_no_positive CHECK ((run_no >= 1))
);


--
-- Name: app_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.app_settings (
    key text NOT NULL,
    value jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: drop_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.drop_runs (
    id bigint NOT NULL,
    drop_id uuid NOT NULL,
    run_no integer NOT NULL,
    mode text NOT NULL,
    started_at timestamp with time zone,
    ended_at timestamp with time zone,
    summary jsonb,
    scorecard jsonb,
    CONSTRAINT drop_runs_mode_valid CHECK ((mode = ANY (ARRAY['fair'::text, 'fifo'::text]))),
    CONSTRAINT drop_runs_run_no_positive CHECK ((run_no >= 1))
);


--
-- Name: drop_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.drop_runs ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.drop_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: drops; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.drops (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    capacity integer NOT NULL,
    mode text NOT NULL,
    phase text NOT NULL,
    reg_opens_at timestamp with time zone,
    reg_closes_at timestamp with time zone,
    window_s integer NOT NULL,
    claim_window_s integer DEFAULT 120 NOT NULL,
    run_no integer DEFAULT 1 NOT NULL,
    seed_commit text NOT NULL,
    seed text,
    entry_set_hash text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    drawn_at timestamp with time zone,
    closed_at timestamp with time zone,
    done_at timestamp with time zone,
    CONSTRAINT drops_capacity_positive CHECK ((capacity > 0)),
    CONSTRAINT drops_claim_window_positive CHECK ((claim_window_s > 0)),
    CONSTRAINT drops_mode_valid CHECK ((mode = ANY (ARRAY['fair'::text, 'fifo'::text]))),
    CONSTRAINT drops_phase_valid CHECK ((phase = ANY (ARRAY['SCHEDULED'::text, 'OPEN'::text, 'CLOSED'::text, 'DRAWN'::text, 'CLAIMING'::text, 'DONE'::text]))),
    CONSTRAINT drops_reg_window_ordered CHECK (((reg_opens_at IS NULL) OR (reg_closes_at IS NULL) OR (reg_closes_at > reg_opens_at))),
    CONSTRAINT drops_run_no_positive CHECK ((run_no >= 1)),
    CONSTRAINT drops_window_positive CHECK ((window_s > 0))
);


--
-- Name: entries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.entries (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    drop_id uuid NOT NULL,
    user_id uuid NOT NULL,
    status text NOT NULL,
    entered_at timestamp with time zone DEFAULT now() NOT NULL,
    client_ip inet,
    device_id text,
    risk_score integer DEFAULT 0 NOT NULL,
    risk_flags jsonb DEFAULT '[]'::jsonb NOT NULL,
    draw_rank integer,
    offer_expires_at timestamp with time zone,
    offered_at timestamp with time zone,
    allocated_at timestamp with time zone,
    step_up_passed_at timestamp with time zone,
    status_changed_at timestamp with time zone DEFAULT now() NOT NULL,
    run_no integer NOT NULL,
    CONSTRAINT entries_draw_rank_positive CHECK ((draw_rank >= 1)),
    CONSTRAINT entries_run_no_positive CHECK ((run_no >= 1)),
    CONSTRAINT entries_status_valid CHECK ((status = ANY (ARRAY['REGISTERED'::text, 'OFFERED'::text, 'STEP_UP_REQUIRED'::text, 'WAITLISTED'::text, 'NOT_SELECTED'::text, 'OFFER_EXPIRED'::text, 'ALLOCATED'::text, 'DISQUALIFIED'::text])))
);


--
-- Name: idempotency_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.idempotency_records (
    user_id uuid NOT NULL,
    key uuid NOT NULL,
    drop_id uuid NOT NULL,
    endpoint text NOT NULL,
    request_hash text NOT NULL,
    response jsonb NOT NULL,
    status_code integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: schema_migrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schema_migrations (
    version character varying NOT NULL
);


--
-- Name: seats; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.seats (
    id bigint NOT NULL,
    drop_id uuid NOT NULL,
    capacity integer NOT NULL,
    seat_no integer NOT NULL,
    status text NOT NULL,
    entry_id uuid,
    sold_at timestamp with time zone,
    CONSTRAINT seats_free_iff_no_entry CHECK (((status = 'free'::text) = (entry_id IS NULL))),
    CONSTRAINT seats_seat_no_within_capacity CHECK (((seat_no >= 1) AND (seat_no <= capacity))),
    CONSTRAINT seats_sold_iff_sold_at CHECK (((status = 'sold'::text) = (sold_at IS NOT NULL))),
    CONSTRAINT seats_status_valid CHECK ((status = ANY (ARRAY['free'::text, 'sold'::text])))
);


--
-- Name: seats_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.seats ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.seats_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sessions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    device_id text,
    ip inet,
    ua_hash text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    revoked_at timestamp with time zone
);


--
-- Name: system_state; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.system_state (
    key text NOT NULL,
    value jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    public_id text NOT NULL,
    phone_hash text NOT NULL,
    first_device_id text,
    first_ip inet,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: v_drop_integrity; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_drop_integrity AS
 SELECT drop_id,
    seats_total,
    sold,
    free,
    capacity,
    oversold,
    duplicate_entries_with_seats,
    allocations_count,
    sold_without_allocation,
    allocation_without_sold_seat,
    entries_allocated_count,
    entries_allocated_mismatch,
    COALESCE(((seats_total = capacity) AND (oversold = 0) AND (duplicate_entries_with_seats = 0) AND (sold = allocations_count) AND (sold = entries_allocated_count) AND (sold_without_allocation = 0) AND (allocation_without_sold_seat = 0) AND (entries_allocated_mismatch = 0) AND (sold_seat_entry_not_allocated = 0) AND (free_seat_with_sold_at = 0)), false) AS invariant_ok,
    sold_seat_entry_not_allocated,
    free_seat_with_sold_at
   FROM ( SELECT d.id AS drop_id,
            d.capacity,
            COALESCE(x.seats_total, 0) AS seats_total,
            COALESCE(x.sold, 0) AS sold,
            COALESCE(x.free, 0) AS free,
            GREATEST(0, (COALESCE(x.sold, 0) - d.capacity)) AS oversold,
            COALESCE(x.duplicate_entries_with_seats, 0) AS duplicate_entries_with_seats,
            COALESCE(x.allocations_count, 0) AS allocations_count,
            COALESCE(x.sold_without_allocation, 0) AS sold_without_allocation,
            COALESCE(x.allocation_without_sold_seat, 0) AS allocation_without_sold_seat,
            COALESCE(x.entries_allocated_count, 0) AS entries_allocated_count,
            (COALESCE(x.allocated_without_ledger_row, 0) + COALESCE(x.ledger_entry_not_allocated, 0)) AS entries_allocated_mismatch,
            COALESCE(x.sold_seat_entry_not_allocated, 0) AS sold_seat_entry_not_allocated,
            COALESCE(x.free_seat_with_sold_at, 0) AS free_seat_with_sold_at
           FROM (public.drops d
             CROSS JOIN LATERAL ( WITH e AS MATERIALIZED (
                         SELECT en.id
                           FROM public.entries en
                          WHERE ((en.drop_id = d.id) AND (en.status = 'ALLOCATED'::text))
                        ), s AS MATERIALIZED (
                         SELECT se.id,
                            se.status,
                            se.entry_id,
                            se.sold_at
                           FROM public.seats se
                          WHERE (se.drop_id = d.id)
                        ), a AS MATERIALIZED (
                         SELECT al.seat_id,
                            al.entry_id
                           FROM public.allocations al
                          WHERE (al.drop_id = d.id)
                        )
                 SELECT (( SELECT count(*) AS count
                           FROM s))::integer AS seats_total,
                    (( SELECT count(*) AS count
                           FROM s
                          WHERE (s.status = 'sold'::text)))::integer AS sold,
                    (( SELECT count(*) AS count
                           FROM s
                          WHERE (s.status = 'free'::text)))::integer AS free,
                    (( SELECT count(*) AS count
                           FROM ( SELECT 1 AS "?column?"
                                   FROM s
                                  WHERE (s.entry_id IS NOT NULL)
                                  GROUP BY s.entry_id
                                 HAVING (count(*) > 1)) multi))::integer AS duplicate_entries_with_seats,
                    (( SELECT count(*) AS count
                           FROM a))::integer AS allocations_count,
                    (( SELECT count(*) AS count
                           FROM s
                          WHERE ((s.status = 'sold'::text) AND (NOT (EXISTS ( SELECT 1
                                   FROM a
                                  WHERE (a.seat_id = s.id)))))))::integer AS sold_without_allocation,
                    (( SELECT count(*) AS count
                           FROM a
                          WHERE (NOT (EXISTS ( SELECT 1
                                   FROM s
                                  WHERE ((s.id = a.seat_id) AND (s.status = 'sold'::text)))))))::integer AS allocation_without_sold_seat,
                    (( SELECT count(*) AS count
                           FROM e))::integer AS entries_allocated_count,
                    (( SELECT count(*) AS count
                           FROM e
                          WHERE (NOT (EXISTS ( SELECT 1
                                   FROM a
                                  WHERE (a.entry_id = e.id))))))::integer AS allocated_without_ledger_row,
                    (( SELECT count(*) AS count
                           FROM a
                          WHERE (NOT (EXISTS ( SELECT 1
                                   FROM e
                                  WHERE (e.id = a.entry_id))))))::integer AS ledger_entry_not_allocated,
                    (( SELECT count(*) AS count
                           FROM s
                          WHERE ((s.status = 'sold'::text) AND (NOT (EXISTS ( SELECT 1
                                   FROM e
                                  WHERE (e.id = s.entry_id)))))))::integer AS sold_seat_entry_not_allocated,
                    (( SELECT count(*) AS count
                           FROM s
                          WHERE ((s.status = 'free'::text) AND (s.sold_at IS NOT NULL))))::integer AS free_seat_with_sold_at) x)) m;


--
-- Name: abuse_events abuse_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.abuse_events
    ADD CONSTRAINT abuse_events_pkey PRIMARY KEY (id);


--
-- Name: allocations allocations_entry_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.allocations
    ADD CONSTRAINT allocations_entry_id_key UNIQUE (entry_id);


--
-- Name: allocations allocations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.allocations
    ADD CONSTRAINT allocations_pkey PRIMARY KEY (id);


--
-- Name: allocations allocations_seat_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.allocations
    ADD CONSTRAINT allocations_seat_id_key UNIQUE (seat_id);


--
-- Name: app_settings app_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_settings
    ADD CONSTRAINT app_settings_pkey PRIMARY KEY (key);


--
-- Name: drop_runs drop_runs_drop_run_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drop_runs
    ADD CONSTRAINT drop_runs_drop_run_key UNIQUE (drop_id, run_no);


--
-- Name: drop_runs drop_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drop_runs
    ADD CONSTRAINT drop_runs_pkey PRIMARY KEY (id);


--
-- Name: drops drops_id_capacity_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drops
    ADD CONSTRAINT drops_id_capacity_key UNIQUE (id, capacity);


--
-- Name: drops drops_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drops
    ADD CONSTRAINT drops_pkey PRIMARY KEY (id);


--
-- Name: entries entries_drop_draw_rank_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entries
    ADD CONSTRAINT entries_drop_draw_rank_key UNIQUE (drop_id, draw_rank);


--
-- Name: entries entries_drop_user_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entries
    ADD CONSTRAINT entries_drop_user_key UNIQUE (drop_id, user_id);


--
-- Name: entries entries_id_drop_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entries
    ADD CONSTRAINT entries_id_drop_key UNIQUE (id, drop_id);


--
-- Name: entries entries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entries
    ADD CONSTRAINT entries_pkey PRIMARY KEY (id);


--
-- Name: idempotency_records idempotency_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.idempotency_records
    ADD CONSTRAINT idempotency_records_pkey PRIMARY KEY (user_id, key);


--
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (version);


--
-- Name: seats seats_drop_entry_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.seats
    ADD CONSTRAINT seats_drop_entry_key UNIQUE (drop_id, entry_id);


--
-- Name: seats seats_drop_seat_no_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.seats
    ADD CONSTRAINT seats_drop_seat_no_key UNIQUE (drop_id, seat_no);


--
-- Name: seats seats_id_entry_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.seats
    ADD CONSTRAINT seats_id_entry_key UNIQUE (id, entry_id);


--
-- Name: seats seats_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.seats
    ADD CONSTRAINT seats_pkey PRIMARY KEY (id);


--
-- Name: sessions sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_pkey PRIMARY KEY (id);


--
-- Name: system_state system_state_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_state
    ADD CONSTRAINT system_state_pkey PRIMARY KEY (key);


--
-- Name: users users_phone_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_phone_hash_key UNIQUE (phone_hash);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: users users_public_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_public_id_key UNIQUE (public_id);


--
-- Name: abuse_events_drop_ts_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX abuse_events_drop_ts_idx ON public.abuse_events USING btree (drop_id, ts);


--
-- Name: allocations_drop_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX allocations_drop_idx ON public.allocations USING btree (drop_id);


--
-- Name: entries_drop_status_rank_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX entries_drop_status_rank_idx ON public.entries USING btree (drop_id, status, draw_rank);


--
-- Name: entries_offer_expiry_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX entries_offer_expiry_idx ON public.entries USING btree (drop_id, offer_expires_at) WHERE (status = 'OFFERED'::text);


--
-- Name: idempotency_records_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idempotency_records_created_at_idx ON public.idempotency_records USING btree (created_at);


--
-- Name: idempotency_records_drop_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idempotency_records_drop_idx ON public.idempotency_records USING btree (drop_id);


--
-- Name: seats_free_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX seats_free_idx ON public.seats USING btree (drop_id, seat_no) WHERE (status = 'free'::text);


--
-- Name: sessions_user_device_active_uidx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX sessions_user_device_active_uidx ON public.sessions USING btree (user_id, device_id) WHERE (revoked_at IS NULL);


--
-- Name: sessions_user_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX sessions_user_id_idx ON public.sessions USING btree (user_id);


--
-- Name: allocations allocations_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER allocations_no_truncate BEFORE TRUNCATE ON public.allocations FOR EACH STATEMENT EXECUTE FUNCTION public.allocations_no_truncate();


--
-- Name: allocations allocations_no_update_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER allocations_no_update_delete BEFORE DELETE OR UPDATE ON public.allocations FOR EACH ROW EXECUTE FUNCTION public.allocations_append_only();


--
-- Name: allocations allocations_entry_in_drop_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.allocations
    ADD CONSTRAINT allocations_entry_in_drop_fk FOREIGN KEY (entry_id, drop_id) REFERENCES public.entries(id, drop_id);


--
-- Name: allocations allocations_seat_entry_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.allocations
    ADD CONSTRAINT allocations_seat_entry_fk FOREIGN KEY (seat_id, entry_id) REFERENCES public.seats(id, entry_id);


--
-- Name: drop_runs drop_runs_drop_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drop_runs
    ADD CONSTRAINT drop_runs_drop_id_fkey FOREIGN KEY (drop_id) REFERENCES public.drops(id);


--
-- Name: entries entries_drop_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entries
    ADD CONSTRAINT entries_drop_id_fkey FOREIGN KEY (drop_id) REFERENCES public.drops(id);


--
-- Name: entries entries_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entries
    ADD CONSTRAINT entries_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: idempotency_records idempotency_records_drop_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.idempotency_records
    ADD CONSTRAINT idempotency_records_drop_id_fkey FOREIGN KEY (drop_id) REFERENCES public.drops(id);


--
-- Name: idempotency_records idempotency_records_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.idempotency_records
    ADD CONSTRAINT idempotency_records_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: seats seats_drop_capacity_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.seats
    ADD CONSTRAINT seats_drop_capacity_fk FOREIGN KEY (drop_id, capacity) REFERENCES public.drops(id, capacity);


--
-- Name: seats seats_entry_in_drop_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.seats
    ADD CONSTRAINT seats_entry_in_drop_fk FOREIGN KEY (entry_id, drop_id) REFERENCES public.entries(id, drop_id);


--
-- Name: sessions sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- PostgreSQL database dump complete
--

\unrestrict dbmate


--
-- Dbmate schema migrations
--

INSERT INTO public.schema_migrations (version) VALUES
    ('20261004100000'),
    ('20261004100100'),
    ('20261004100200'),
    ('20261004100300'),
    ('20261004100400');
