# backend/app/all_models.py
# Duplication check after 2.1.c/d/e, item D: the single shared place that
# imports every domain's models.py for the side effect of registering its
# tables on Base.metadata. Replaces two independent per-entrypoint import
# blocks (alembic/env.py, app/worker.py) that each had to separately
# remember every domain package -- alembic/env.py forgetting app.ingest.
# models caused a real, silent autogenerate gap at Task 2.1.b; app/worker.py
# forgetting app.tenancy.models/app.plans.models caused a real
# NoReferencedTableError at Task 2.1.e, the first time a genuine standalone
# worker process was exercised. Both needed this for different underlying
# mechanisms (Alembic's target_metadata diff vs. SQLAlchemy's flush-time FK
# dependency sort -- see PROJECT_SPEC.md's Step 2.1.c/d/e duplication-check
# decision entry), but the fix is the same either way: one shared module
# that imports every package once, so a third future entrypoint gets the
# property for free by importing this module, with nothing new to remember
# to register.
from app.ingest import models as ingest_models  # noqa: F401
from app.plans import models as plans_models  # noqa: F401
from app.tenancy import models as tenancy_models  # noqa: F401
