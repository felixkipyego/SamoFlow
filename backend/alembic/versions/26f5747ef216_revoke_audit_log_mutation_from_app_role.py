"""revoke audit_log mutation from the app role

Revision ID: 26f5747ef216
Revises: 795d1045e0df
Create Date: 2026-10-18 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "26f5747ef216"
down_revision: str | Sequence[str] | None = "795d1045e0df"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Must match app/db_provisioning.py's own APP_ROLE constant -- deliberately
# a literal, not an import: a migration is a frozen historical record, and
# must never change behavior because application code it imports later
# changed (the same reasoning 610897dd7071's own REVOKE already follows,
# hardcoding "widgetplatform" rather than importing anything). backend/
# tests/test_alembic.py's own test_audit_log_update_delete_and_truncate_
# are_denied_for_the_app_role is the live proof the two names stay in
# sync -- it queries the real grants using db_provisioning.APP_ROLE, so a
# drift here would make that test fail loudly, not pass silently.
APP_ROLE = "widgetplatform_app"


def upgrade() -> None:
    """Upgrade schema."""
    # [SECURITY] L2 (Phase 7 role split, 2026-10-18): this is the REAL
    # enforcement 610897dd7071's own REVOKE always intended, finally
    # possible because APP_ROLE is a genuine non-superuser (unlike
    # "widgetplatform", the old single shared role that migration's own
    # comment documents as a full superuser -- D-2.2-superuser,
    # docs/DECISIONS_REGISTER.md).
    #
    # app/db_provisioning.py's own grant_app_role_privileges() grants APP
    # full SELECT/INSERT/UPDATE/DELETE on every table by default (via
    # ALTER DEFAULT PRIVILEGES, so future migrations never need a manual
    # re-grant step) -- audit_log is the one deliberate exception: SELECT
    # and INSERT stay (the dashboard reads the trail; every write path
    # inserts into it), UPDATE/DELETE/TRUNCATE are revoked here,
    # specifically, mirroring exactly where the OLD, inert REVOKE already
    # lived rather than special-casing audit_log inside the provisioning
    # script itself.
    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM {APP_ROLE}")


def downgrade() -> None:
    """Downgrade schema."""
    # Restores the default-privilege grant audit_log would otherwise have
    # gotten from ALTER DEFAULT PRIVILEGES alone, so a downgrade leaves
    # audit_log's own grants matching what every other table already has
    # -- not a "more locked down than before this migration ever existed"
    # state.
    op.execute(f"GRANT UPDATE, DELETE, TRUNCATE ON audit_log TO {APP_ROLE}")
