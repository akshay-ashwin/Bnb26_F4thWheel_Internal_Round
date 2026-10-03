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
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--
-- Name: EXTENSION pgcrypto; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';


--
-- Name: admin_reset_drop(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.admin_reset_drop(p_drop_id uuid) RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE new_run int;
BEGIN
  DELETE FROM allocations WHERE drop_id = p_drop_id;
  UPDATE seats SET status = 'free', entry_id = NULL, sold_at = NULL WHERE drop_id = p_drop_id;
  DELETE FROM entries WHERE drop_id = p_drop_id;
  DELETE FROM idempotency_records WHERE drop_id = p_drop_id;
  UPDATE drops SET run_no = run_no + 1 WHERE id = p_drop_id RETURNING run_no INTO new_run;
  RETURN new_run;
END $$;


--
-- Name: create_drop_seats(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.create_drop_seats(p_drop_id uuid) RETURNS void
    LANGUAGE sql
    AS $$
  INSERT INTO seats (drop_id, seat_no, status)
  SELECT d.id, g, 'free' FROM drops d, generate_series(1, d.capacity) g WHERE d.id = p_drop_id;
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

CREATE SEQUENCE public.abuse_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: abuse_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.abuse_events_id_seq OWNED BY public.abuse_events.id;


--
-- Name: allocations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.allocations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    drop_id uuid NOT NULL,
    entry_id uuid NOT NULL,
    seat_id bigint NOT NULL,
    idempotency_key uuid NOT NULL,
    run_no integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
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
    drop_id uuid NOT NULL,
    run_no integer NOT NULL,
    mode text NOT NULL,
    started_at timestamp with time zone,
    ended_at timestamp with time zone,
    summary jsonb,
    scorecard jsonb
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
    window_s integer NOT NULL,
    run_no integer DEFAULT 1 NOT NULL,
    reg_opens_at timestamp with time zone,
    reg_closes_at timestamp with time zone,
    claim_window_s integer DEFAULT 120 NOT NULL,
    seed_commit text NOT NULL,
    seed text,
    entry_set_hash text,
    drawn_at timestamp with time zone,
    closed_at timestamp with time zone,
    done_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT drops_capacity_check CHECK ((capacity > 0)),
    CONSTRAINT drops_mode_check CHECK ((mode = ANY (ARRAY['fair'::text, 'fifo'::text]))),
    CONSTRAINT drops_phase_check CHECK ((phase = ANY (ARRAY['SCHEDULED'::text, 'OPEN'::text, 'CLOSED'::text, 'DRAWN'::text, 'CLAIMING'::text, 'DONE'::text]))),
    CONSTRAINT drops_window_s_check CHECK ((window_s > 0))
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
    run_no integer DEFAULT 1 NOT NULL,
    CONSTRAINT entries_status_check CHECK ((status = ANY (ARRAY['REGISTERED'::text, 'OFFERED'::text, 'STEP_UP_REQUIRED'::text, 'WAITLISTED'::text, 'NOT_SELECTED'::text, 'OFFER_EXPIRED'::text, 'ALLOCATED'::text, 'DISQUALIFIED'::text])))
);


--
-- Name: idempotency_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.idempotency_records (
    user_id uuid NOT NULL,
    key uuid NOT NULL,
    drop_id uuid,
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
    seat_no integer NOT NULL,
    status text NOT NULL,
    entry_id uuid,
    sold_at timestamp with time zone,
    CONSTRAINT seats_check CHECK (((status = 'free'::text) = (entry_id IS NULL))),
    CONSTRAINT seats_status_check CHECK ((status = ANY (ARRAY['free'::text, 'sold'::text])))
);


--
-- Name: seats_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.seats_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: seats_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.seats_id_seq OWNED BY public.seats.id;


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
 SELECT d.id AS drop_id,
    d.capacity,
    COALESCE(s.total, (0)::bigint) AS seats_total,
    COALESCE(s.sold, (0)::bigint) AS sold,
    (COALESCE(s.total, (0)::bigint) - COALESCE(s.sold, (0)::bigint)) AS free,
    GREATEST((0)::bigint, (COALESCE(s.sold, (0)::bigint) - d.capacity)) AS oversold,
    COALESCE(dup.n, (0)::bigint) AS duplicate_entries_with_seats,
    COALESCE(a.n, (0)::bigint) AS allocations_count,
    COALESCE(sw.n, (0)::bigint) AS sold_without_allocation,
    COALESCE(aw.n, (0)::bigint) AS allocation_without_sold_seat,
    COALESCE(e.n, (0)::bigint) AS entries_allocated_count,
    abs((COALESCE(e.n, (0)::bigint) - COALESCE(a.n, (0)::bigint))) AS entries_allocated_mismatch,
    ((COALESCE(s.total, (0)::bigint) = d.capacity) AND (COALESCE(s.sold, (0)::bigint) <= d.capacity) AND (COALESCE(dup.n, (0)::bigint) = 0) AND (COALESCE(sw.n, (0)::bigint) = 0) AND (COALESCE(aw.n, (0)::bigint) = 0) AND (COALESCE(e.n, (0)::bigint) = COALESCE(a.n, (0)::bigint)) AND (COALESCE(a.n, (0)::bigint) = COALESCE(s.sold, (0)::bigint))) AS invariant_ok
   FROM ((((((public.drops d
     LEFT JOIN LATERAL ( SELECT count(*) AS total,
            count(*) FILTER (WHERE (seats.status = 'sold'::text)) AS sold
           FROM public.seats
          WHERE (seats.drop_id = d.id)) s ON (true))
     LEFT JOIN LATERAL ( SELECT count(*) AS n
           FROM ( SELECT seats.entry_id
                   FROM public.seats
                  WHERE ((seats.drop_id = d.id) AND (seats.entry_id IS NOT NULL))
                  GROUP BY seats.entry_id
                 HAVING (count(*) > 1)) x) dup ON (true))
     LEFT JOIN LATERAL ( SELECT count(*) AS n
           FROM public.allocations
          WHERE (allocations.drop_id = d.id)) a ON (true))
     LEFT JOIN LATERAL ( SELECT count(*) AS n
           FROM public.seats st
          WHERE ((st.drop_id = d.id) AND (st.status = 'sold'::text) AND (NOT (EXISTS ( SELECT 1
                   FROM public.allocations al
                  WHERE (al.seat_id = st.id)))))) sw ON (true))
     LEFT JOIN LATERAL ( SELECT count(*) AS n
           FROM (public.allocations al
             JOIN public.seats st ON ((st.id = al.seat_id)))
          WHERE ((al.drop_id = d.id) AND (st.status <> 'sold'::text))) aw ON (true))
     LEFT JOIN LATERAL ( SELECT count(*) AS n
           FROM public.entries
          WHERE ((entries.drop_id = d.id) AND (entries.status = 'ALLOCATED'::text))) e ON (true));


--
-- Name: abuse_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.abuse_events ALTER COLUMN id SET DEFAULT nextval('public.abuse_events_id_seq'::regclass);


--
-- Name: seats id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.seats ALTER COLUMN id SET DEFAULT nextval('public.seats_id_seq'::regclass);


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
-- Name: drop_runs drop_runs_drop_id_run_no_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drop_runs
    ADD CONSTRAINT drop_runs_drop_id_run_no_key UNIQUE (drop_id, run_no);


--
-- Name: drops drops_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drops
    ADD CONSTRAINT drops_pkey PRIMARY KEY (id);


--
-- Name: entries entries_drop_id_draw_rank_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entries
    ADD CONSTRAINT entries_drop_id_draw_rank_key UNIQUE (drop_id, draw_rank);


--
-- Name: entries entries_drop_id_user_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entries
    ADD CONSTRAINT entries_drop_id_user_id_key UNIQUE (drop_id, user_id);


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
-- Name: seats seats_drop_id_entry_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.seats
    ADD CONSTRAINT seats_drop_id_entry_id_key UNIQUE (drop_id, entry_id);


--
-- Name: seats seats_drop_id_seat_no_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.seats
    ADD CONSTRAINT seats_drop_id_seat_no_key UNIQUE (drop_id, seat_no);


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
-- Name: seats_free_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX seats_free_idx ON public.seats USING btree (drop_id) WHERE (status = 'free'::text);


--
-- Name: sessions_user_device_live_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX sessions_user_device_live_idx ON public.sessions USING btree (user_id, device_id) WHERE (revoked_at IS NULL);


--
-- Name: sessions_user_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX sessions_user_idx ON public.sessions USING btree (user_id);


--
-- Name: allocations allocations_drop_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.allocations
    ADD CONSTRAINT allocations_drop_id_fkey FOREIGN KEY (drop_id) REFERENCES public.drops(id);


--
-- Name: allocations allocations_entry_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.allocations
    ADD CONSTRAINT allocations_entry_id_fkey FOREIGN KEY (entry_id) REFERENCES public.entries(id);


--
-- Name: allocations allocations_seat_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.allocations
    ADD CONSTRAINT allocations_seat_id_fkey FOREIGN KEY (seat_id) REFERENCES public.seats(id);


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
-- Name: seats seats_drop_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.seats
    ADD CONSTRAINT seats_drop_id_fkey FOREIGN KEY (drop_id) REFERENCES public.drops(id);


--
-- Name: seats seats_entry_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.seats
    ADD CONSTRAINT seats_entry_id_fkey FOREIGN KEY (entry_id) REFERENCES public.entries(id);


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
    ('20261004000001');
