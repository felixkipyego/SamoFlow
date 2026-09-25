# backend/app/retrieval/service.py
# Step 1.1b: whole-application skeleton stub for retrieval's future service.
def retrieve():
    # TODO(3.1): hybrid retrieval (dense + sparse BM25, server-side RRF)
    # against Qdrant; the client_id filter must be applied inside every
    # sub-query. Inputs: the tenant id and the query. Outputs: ranked,
    # tenant-scoped chunks.
    raise NotImplementedError
