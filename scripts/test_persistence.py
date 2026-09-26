from app.core.persistence import persistence_health
from app.core.redis_runtime import redis_health

print("PostgreSQL:", persistence_health())
print("Redis:", redis_health())

if not persistence_health()["ok"]:
    raise SystemExit("PostgreSQL persistence is not ready.")
if not redis_health()["ok"]:
    raise SystemExit("Redis is not ready.")

print("M11 persistence services are ready.")
