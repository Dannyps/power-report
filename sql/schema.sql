-- power-report database schema (PostgreSQL; no data). Regenerate with
--   runuser -u postgres -- pg_dump -d power --schema-only --no-owner > sql/schema.sql   (then restore this header)
--
-- Set up once (roles are not part of the dump):
--   CREATE ROLE power LOGIN PASSWORD '...';           -- the app; password in /etc/power-report/pgpass
--   CREATE ROLE grafana_ro LOGIN PASSWORD '...';      -- optional, read-only for Grafana
--   CREATE DATABASE power OWNER power;
--   then run this file as the `power` role: psql -U power -d power -f sql/schema.sql
-- The `daily` view groups by local day in Europe/Lisbon: change the time zone there to match `timezone` in the config.

--
-- PostgreSQL database dump
--

\restrict fXSB8ipIGL1XD0nageQQPolE1h0cq53noQ3E3QcFvqMIO54zee1tMYrb38JwDh8

-- Dumped from database version 17.12 (Debian 17.12-0+deb13u1)
-- Dumped by pg_dump version 17.12 (Debian 17.12-0+deb13u1)

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

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: climate_hourly; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.climate_hourly (
    site text NOT NULL,
    entity text NOT NULL,
    ts timestamp with time zone NOT NULL,
    metric text NOT NULL,
    label text NOT NULL,
    value double precision NOT NULL
);


--
-- Name: TABLE climate_hourly; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.climate_hourly IS 'Optional [sites.<site>.climate] entities from HA hourly statistics. Counters (metric heating_hours, burner_hours in h; gas in m3) hold the change over the hour; measurements (temperature in C, humidity in %) hold the hourly mean.';


--
-- Name: hourly; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hourly (
    entity text NOT NULL,
    ts timestamp with time zone NOT NULL,
    kwh double precision NOT NULL,
    avg_w double precision,
    site text NOT NULL,
    estimated boolean DEFAULT false NOT NULL
);


--
-- Name: COLUMN hourly.estimated; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.hourly.estimated IS 'kwh spread evenly over a gap (HA statistics hole, or meter feed outage with a catch-up hour); avg_w is null then';


--
-- Name: daily; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.daily AS
 SELECT site,
    entity,
    ((ts AT TIME ZONE 'Europe/Lisbon'::text))::date AS day,
    sum(kwh) AS kwh,
    count(*) AS hours,
    min(avg_w) FILTER (WHERE ((EXTRACT(hour FROM (ts AT TIME ZONE 'Europe/Lisbon'::text)) >= (2)::numeric) AND (EXTRACT(hour FROM (ts AT TIME ZONE 'Europe/Lisbon'::text)) <= (5)::numeric))) AS night_min_w
   FROM public.hourly
  GROUP BY site, entity, (((ts AT TIME ZONE 'Europe/Lisbon'::text))::date);


--
-- Name: entities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.entities (
    entity text NOT NULL,
    label text NOT NULL,
    kind text NOT NULL,
    parent text,
    site text NOT NULL,
    price double precision
);


--
-- Name: COLUMN entities.site; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.entities.site IS 'which Home Assistant instance (a [sites.<name>] table in the config)';


--
-- Name: COLUMN entities.price; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.entities.price IS 'EUR/kWh: flat house tariff, or the tariff of a kind=tariff register';


--
-- Name: forecast; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.forecast (
    entity text NOT NULL,
    ts timestamp with time zone NOT NULL,
    kwh double precision NOT NULL,
    made_at timestamp with time zone NOT NULL,
    site text NOT NULL
);


--
-- Name: climate_hourly climate_hourly_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.climate_hourly
    ADD CONSTRAINT climate_hourly_pkey PRIMARY KEY (site, entity, ts);


--
-- Name: entities entities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entities
    ADD CONSTRAINT entities_pkey PRIMARY KEY (site, entity);


--
-- Name: forecast forecast_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.forecast
    ADD CONSTRAINT forecast_pkey PRIMARY KEY (site, entity, ts, made_at);


--
-- Name: hourly hourly_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hourly
    ADD CONSTRAINT hourly_pkey PRIMARY KEY (site, entity, ts);


--
-- Name: SCHEMA public; Type: ACL; Schema: -; Owner: -
--

GRANT USAGE ON SCHEMA public TO grafana_ro;


--
-- Name: TABLE climate_hourly; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT ON TABLE public.climate_hourly TO grafana_ro;


--
-- Name: TABLE hourly; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT ON TABLE public.hourly TO grafana_ro;


--
-- Name: TABLE daily; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT ON TABLE public.daily TO grafana_ro;


--
-- Name: TABLE entities; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT ON TABLE public.entities TO grafana_ro;


--
-- Name: TABLE forecast; Type: ACL; Schema: public; Owner: -
--

GRANT SELECT ON TABLE public.forecast TO grafana_ro;


--
-- Name: DEFAULT PRIVILEGES FOR TABLES; Type: DEFAULT ACL; Schema: public; Owner: -
--

ALTER DEFAULT PRIVILEGES FOR ROLE power IN SCHEMA public GRANT SELECT ON TABLES TO grafana_ro;


--
-- PostgreSQL database dump complete
--

\unrestrict fXSB8ipIGL1XD0nageQQPolE1h0cq53noQ3E3QcFvqMIO54zee1tMYrb38JwDh8

