# ops/backup/Dockerfile -- Step 7.3 prep (ops track). The one small,
# purpose-built image both backup.sh and restore-drill.sh run in,
# joining the same Compose network as postgres/qdrant so it can reach
# them by service name with zero published ports anywhere (deploy/
# docker-compose.prod.yml's own `!reset []` guarantee).
#
# debian:trixie-slim, not the backend's own Python base image: this
# image shares NOTHING with backend/Dockerfile on purpose -- it has no
# Python, imports no backend code, and exists only to run shell + psql +
# curl + age + the AWS CLI. A backup/restore path that depended on the
# application image would couple data-durability tooling to an
# unrelated image's own build/dependency churn for no reason.
#
# postgresql-client-18, not a floating "postgresql-client" meta-package:
# must match deploy/docker-compose.yml's own pinned `postgres:18.2-trixie`
# server major version exactly, or pg_dump/psql can refuse to talk to a
# newer server. Debian trixie's own base repo only ships
# postgresql-client-17 (confirmed live: `apt-cache policy postgresql-
# client` offers no -18 candidate) -- the official PGDG apt repo below is
# the standard, well-known source for a client matching a server version
# this new (rule 11: the real upstream repo, not a hand-built package).
FROM debian:trixie-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        gnupg \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
        https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt trixie-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        postgresql-client-18 \
        age \
        awscli \
        jq \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /ops
COPY backup.sh restore-drill.sh lib.sh ./
RUN chmod +x backup.sh restore-drill.sh

ENTRYPOINT ["/bin/bash"]
