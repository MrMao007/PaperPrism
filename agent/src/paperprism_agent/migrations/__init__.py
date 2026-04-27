"""SQL migrations applied at startup.

Files named `NNNN_*.sql` are run in lexicographic order. Each file
runs inside a single transaction; the highest applied version is
tracked in `schema_version`.
"""
