#!/bin/sh
# Runs once, on first initialisation of the PostgreSQL data directory.
# Creates the database the integration suite expects alongside the main one.
set -eu

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-'SQL'
	CREATE DATABASE m4d_test;
SQL
