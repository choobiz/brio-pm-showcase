
## Running the suite

**Never run this suite against a database you cannot afford to lose.** The
`worker_db` fixture deletes every row from the domain tables and commits.

A throwaway database exists on the app VM for exactly this:

```bash
PW=$(docker exec deploy-db-1 sh -c 'echo $POSTGRES_PASSWORD')
docker exec -w /app \
  -e TEST_DATABASE_URL="postgresql+psycopg://brio:$PW@db:5432/brio_pm_test" \
  deploy-api-1 python -m pytest tests/ -q
```

Without `TEST_DATABASE_URL` the DB-backed tests SKIP rather than fall back to
`DATABASE_URL` — see `conftest.py` for why, and what happened on 2026-08-31 when
that fallback existed.
