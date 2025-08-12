#!/usr/bin/env python
import os
import sys

import env

sys.dont_write_bytecode = True


def create_database_if_not_exists():

    import psycopg2

    # Establish connection to the default 'postgres'
    # database to check for existence and create new databases.
    conn = psycopg2.connect(
        dbname="postgres",
        user=os.environ.get('POSTGRES_DB_USER'),
        password=os.environ.get('POSTGRES_DB_PASSWORD'),
        host=os.environ.get('POSTGRES_HOST'),
        port=os.environ.get('POSTGRES_PORT')
    )

    conn.autocommit = True
    cursor = conn.cursor()

    db_name = os.environ.get('POSTGRES_DB_NAME')
    # Check if the database already exists
    cursor.execute(f"SELECT 1 FROM pg_catalog.pg_database WHERE datname = '{db_name}'")
    exists = cursor.fetchone()

    if not exists:
        print(f"Creating database '{db_name}'...")
        cursor.execute(f"CREATE DATABASE {db_name}")

    cursor.close()
    conn.close()


if __name__ == "__main__":
    # TODO: Uncomment when we switch to Postgres
    # create_database_if_not_exists()

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "db.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)
