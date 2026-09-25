# backend/app/plans/service.py
# Step 1.1b: whole-application skeleton stub for plans' future service.
def get_plan_limits():
    # TODO(4.3): look up a tenant's plan limits and current usage for quota
    # enforcement and the 80%/100% alert emails. Inputs: the tenant id.
    # Outputs: the plan's limits and current usage counts. UNCERTAIN: this
    # package's owning step is not a single clear one (its __init__.py NOTE
    # says "Step 4.3 and later"); plan-tier CRUD/assignment may instead
    # belong with 6.2 Tenant settings. 4.3 was picked as the first step that
    # explicitly needs plan-limit data.
    raise NotImplementedError
