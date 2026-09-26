import sys
from logging.config import fileConfig

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from alembic import context
from app.config import SettingsError, get_settings
from app.db import Base

# Imported for the side effect of registering their tables on Base.metadata
# (Task 1.2.b) -- target_metadata below must see every domain's models, not
# just whichever one happened to be imported first.
from app.plans import models as plans_models  # noqa: F401
from app.tenancy import models as tenancy_models  # noqa: F401

# This is the Alembic Config object, which provides access to the values
# within the .ini file in use.
config = context.config

# Interpret the config file for Python logging. This line sets up loggers
# basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


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
