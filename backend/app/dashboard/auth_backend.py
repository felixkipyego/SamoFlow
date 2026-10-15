# backend/app/dashboard/auth_backend.py
# Step 6.1 [SECURITY]: fastapi-users wiring (docs/SPEC.md §12's own direct
# instruction -- "use a maintained auth library rather than hand-rolling"
# -- and Felix's own 2026-10-15 correction naming fastapi-users
# specifically, and requiring every security requirement already named in
# this task's own prompt to stay layered on top of it, not replaced by
# it). See backend/pyproject.toml's own dependency comment for the full
# library-choice justification (rule 8) and its accepted footprint cost.
#
# What the library gives this project for free: a real user table adapter
# (SQLAlchemyUserDatabase), argon2id password hashing with automatic
# timing-safe handling of an unknown email (BaseUserManager.authenticate()
# -- confirmed by reading its source before relying on it: it runs the
# real hasher against the submitted password even when no such user
# exists, "to mitigate timing attack... inspired from Django", the exact
# property this project would otherwise have had to hand-roll), and a
# pluggable session-storage Strategy/Transport pair.
#
# What this project extends ON TOP of it, deliberately, because the
# library's own defaults do not meet this task's own named requirements
# (confirmed by reading DatabaseStrategy's real source before deciding to
# subclass it, not assumed from documentation):
#   - HashedDatabaseStrategy below stores a SHA-256 hash of the session
#     token, never the raw value (the library's own default stores it
#     raw) -- reuses app.auth.secrets' own generic hash function, the
#     same primitive visitors.secret_hash already uses, not a second
#     implementation.
#   - HashedDatabaseStrategy also enforces IDLE expiry (DashboardSession.
#     last_seen_at, touched on every valid read) on top of the library's
#     own absolute-only `lifetime_seconds` expiry.
#   - app/dashboard/csrf.py (unchanged by this adoption -- the library
#     has no CSRF mechanism of its own) and the per-IP/per-account
#     RateLimiter instances in app/dashboard/routes.py both still apply,
#     checked before this module's own authentication logic ever runs.
#
# Custom JSON routes (app/dashboard/routes.py), not the library's own
# generated get_auth_router()/get_register_router(): this project's own
# CSRF/rate-limit layering needs to run BEFORE user_manager.authenticate()
# in a specific, cheapest-first order (docs/SPEC.md §9's own ordering
# philosophy), and the generated login router expects an OAuth2 form body
# (username/password form fields), not this project's own JSON API shape
# -- the generated routers are never mounted; only the building blocks
# (user_manager, auth_backend, strategy) are reused directly.
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import Depends
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin, exceptions
from fastapi_users.authentication import AuthenticationBackend, CookieTransport
from fastapi_users.authentication.strategy import DatabaseStrategy
from fastapi_users.authentication.strategy.db import AccessTokenDatabase
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users_db_sqlalchemy.access_token import SQLAlchemyAccessTokenDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.secrets import generate_visitor_secret, hash_visitor_secret
from app.config import get_settings
from app.dashboard.models import DashboardSession, User
from app.dashboard.passwords import password_helper
from app.dashboard.repository import invalidate_all_sessions_for_user
from app.db import get_db_session

SESSION_COOKIE_NAME = "__Host-session"

# NIST SP 800-63B's own current guidance favours a length floor over
# composition rules (no forced mixed-case/digit/symbol requirements) --
# 12 characters is that guidance's own commonly-cited minimum, not a
# number invented here. Enforced through the library's own
# validate_password() hook (UserManager below), so it applies uniformly
# everywhere a password is ever set, not only in one call site.
MIN_PASSWORD_LENGTH = 12


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    async def validate_password(self, password: str, user) -> None:
        if len(password) < MIN_PASSWORD_LENGTH:
            raise exceptions.InvalidPasswordException(
                reason=f"password must be at least {MIN_PASSWORD_LENGTH} characters"
            )

    async def on_after_update(self, user: User, update_dict: dict, request=None) -> None:
        # Alignment task (2026-10-15), a named security follow-up: "a
        # password reset, an owner re-seed, or removing platform_admin
        # through the CLI must invalidate ALL of that user's existing
        # sessions immediately." None of those three CLI commands exist
        # yet (admin_cli.py's own command list, Felix's 2026-10-15
        # correction, names create-tenant/assign-plan/issue-or-revoke-
        # site-key/revoke-domain/seed-owner only -- never a password
        # reset or a platform_admin removal) -- building three new,
        # unrequested CLI surfaces to satisfy this would itself be new
        # feature work this same task explicitly rules out ("not new
        # features"). Hooked here instead, at BaseUserManager's own
        # `update()` -- the one real path this project's own admin_cli.py/
        # seed_cli.py precedent already builds every mutation on top of
        # (see those modules' own header comments) -- so whichever future
        # command resets a password or grants/revokes platform_admin
        # inherits this invalidation automatically the moment it calls
        # `UserManager.update()`, with no code of its own, rather than
        # three separate call sites each having to remember it
        # individually. `update_dict` carries the RAW requested fields
        # (fastapi_users.manager.BaseUserManager.update()'s own source,
        # confirmed by reading it directly: `user_update.
        # create_update_dict()`/`..._superuser()`, built BEFORE password
        # hashing) -- "password" here means a new password was requested,
        # not that `hashed_password` already changed.
        #
        # Does NOT close seed_cli.py's own named gap ("a user with email
        # ... already exists; this command does not reset an existing
        # password... use a direct database update for that") -- a
        # hand-edited database row never calls update() at all, so no
        # amount of code here can enforce this invariant for that
        # specific documented workaround; flagged plainly, not silently
        # assumed solved.
        if "password" in update_dict or "is_superuser" in update_dict:
            await invalidate_all_sessions_for_user(self.user_db.session, user.id)


async def get_user_db(session: AsyncSession = Depends(get_db_session)):  # noqa: B008
    yield SQLAlchemyUserDatabase(session, User)


async def get_user_manager(user_db=Depends(get_user_db)):  # noqa: B008
    yield UserManager(user_db, password_helper)


class HashedDatabaseStrategy(DatabaseStrategy[User, uuid.UUID, DashboardSession]):
    """Extends fastapi_users' own DatabaseStrategy with two project-
    specific requirements neither it nor its default AccessTokenDatabase
    provides (see this module's own header comment): the stored token is
    SHA-256-hashed, never raw, and a session also expires after a period
    of IDLE time, not only after its absolute lifetime.
    """

    def __init__(
        self,
        database: AccessTokenDatabase[DashboardSession],
        lifetime_seconds: int,
        idle_timeout_seconds: float,
    ) -> None:
        super().__init__(database, lifetime_seconds)
        self.idle_timeout_seconds = idle_timeout_seconds

    async def read_token(self, token: str | None, user_manager) -> User | None:
        if token is None:
            return None

        max_age = datetime.now(UTC) - timedelta(seconds=self.lifetime_seconds)
        access_token = await self.database.get_by_token(hash_visitor_secret(token), max_age)
        if access_token is None:
            return None

        idle_cutoff = datetime.now(UTC) - timedelta(seconds=self.idle_timeout_seconds)
        if access_token.last_seen_at < idle_cutoff:
            return None

        # Sliding idle window: every request that reaches this point
        # refreshes last_seen_at. Does not extend the absolute lifetime
        # (created_at, untouched) -- that ceiling never moves.
        await self.database.update(access_token, {"last_seen_at": datetime.now(UTC)})

        try:
            parsed_id = user_manager.parse_id(access_token.user_id)
            return await user_manager.get(parsed_id)
        except (exceptions.UserNotExists, exceptions.InvalidID):
            return None

    async def write_token(self, user: User) -> str:
        # Never a client-supplied token (session fixation defense, this
        # task's own adversarial-pass requirement): generated here,
        # server side, every time. The RAW token is returned (it becomes
        # the cookie value); only its hash is ever persisted.
        raw_token = generate_visitor_secret()
        await self.database.create(
            {
                "token": hash_visitor_secret(raw_token),
                "user_id": user.id,
                "last_seen_at": datetime.now(UTC),
            }
        )
        return raw_token

    async def destroy_token(self, token: str, user: User) -> None:
        access_token = await self.database.get_by_token(hash_visitor_secret(token))
        if access_token is not None:
            await self.database.delete(access_token)


async def get_access_token_db(session: AsyncSession = Depends(get_db_session)):  # noqa: B008
    yield SQLAlchemyAccessTokenDatabase(session, DashboardSession)


def get_database_strategy(
    access_token_db: AccessTokenDatabase[DashboardSession] = Depends(get_access_token_db),  # noqa: B008
) -> HashedDatabaseStrategy:
    settings = get_settings()
    return HashedDatabaseStrategy(
        access_token_db,
        lifetime_seconds=int(settings.dashboard_session_absolute_seconds),
        idle_timeout_seconds=settings.dashboard_session_idle_seconds,
    )


# cookie_max_age=None: a true session cookie (cleared when the browser
# closes), carrying no Max-Age/Expires attribute at all. Deliberate, for
# two reasons: the REAL absolute/idle enforcement happens server-side
# (HashedDatabaseStrategy above, read fresh from Settings on every
# request) regardless of what the cookie's own browser-side hint says,
# and building this transport with a Settings-derived Max-Age would mean
# reading Settings at MODULE IMPORT time -- this project's own established
# rule (app/config.py's own header comment: "nothing here reads the
# environment... at import time") would otherwise be violated the moment
# this module is imported, not merely at app startup.
#
# __Host-session: Secure, Path=/, no Domain, HttpOnly, SameSite=Strict --
# this task's own cookie/origin design. Never sent to a sibling subdomain
# (e.g. pm.softwareske.com, named explicitly in this task's own prompt as
# untrusted) and a sibling subdomain cannot overwrite it either (the
# __Host- prefix's whole point).
cookie_transport = CookieTransport(
    cookie_name=SESSION_COOKIE_NAME,
    cookie_max_age=None,
    cookie_path="/",
    cookie_domain=None,
    cookie_secure=True,
    cookie_httponly=True,
    cookie_samesite="strict",
)

auth_backend = AuthenticationBackend(
    name="dashboard-session",
    transport=cookie_transport,
    get_strategy=get_database_strategy,
)

fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])

# active=True: an inactive user (the library's own kill switch,
# User.is_active) can never authenticate, on every dashboard endpoint
# this dependency guards -- no v1 screen manages this flag yet, but the
# enforcement is real from day one.
current_active_user = fastapi_users.current_user(active=True)

# superuser=True: this project's own platform_admin role (Felix's own
# 2026-10-15 correction) -- reuses the library's own is_superuser
# semantics and current_user(superuser=True) enforcement directly,
# rather than inventing a second, parallel admin-identity mechanism.
current_platform_admin = fastapi_users.current_user(active=True, superuser=True)
