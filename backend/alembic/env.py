import sys
from logging.config import fileConfig

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from alembic import context
from app.config import SettingsError, get_settings

# This is the Alembic Config object, which provides access to the values
# within the .ini file in use.
config = context.config

# Interpret the config file for Python logging. This line sets up loggers
# basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# TODO(1.2): set target_metadata to the models' Base.metadata once they
# exist, so "alembic revision --autogenerate" has something to diff against.
target_metadata = None


def run_migrations_online() -> None:
    # The URL comes from get_settings() only, never from config.set_main_option
    # (that call breaks on "%" characters in the URL and would also copy the
    # secret into the Config object, e.g. for a later config.get_main_option
    # call or exception message).
    try:
        url = get_settings().database_url_str()
    except SettingsError as exc:
        # Same behaviour as app/worker.py's main(): a clean one-line message,
        # no traceback, so a misconfigured environment never prints one.
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    connectable = create_engine(url, poolclass=NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
