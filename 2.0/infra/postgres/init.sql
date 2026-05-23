-- AGORA control-plane Postgres init script.
-- Runs once on first container startup (mounted at /docker-entrypoint-initdb.d/).
--
-- Creates the auxiliary databases that Temporal auto-setup and Letta need.
-- The primary `agora` database is created by Postgres itself from POSTGRES_DB.
-- Letta requires the pgvector extension (we use the pgvector/pgvector image
-- which ships it preinstalled).

CREATE DATABASE temporal;
CREATE DATABASE temporal_visibility;
CREATE DATABASE letta;

\c letta
CREATE EXTENSION IF NOT EXISTS vector;

\c agora
CREATE EXTENSION IF NOT EXISTS vector;
