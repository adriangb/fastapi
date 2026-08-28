"""Synthetic benchmark for pydantic/platform#36383 memory work.

Run against two checkouts to compare:

    uv run python scripts/bench_dependants.py

Not a CI benchmark - numbers are shape-dependent. The app reuses a small set
of dependency callables across many routes, mimicking Platform's ratio of
~1,000 distinct dependency targets behind ~50k Dependant nodes.
"""

import gc
import time
import tracemalloc

from fastapi import APIRouter, Depends, FastAPI
from fastapi._compat import ModelField
from fastapi.dependencies.models import Dependant
from fastapi.testclient import TestClient
from pydantic.fields import FieldInfo


def dep_a(q: str = "x") -> str:
    return q


def dep_b(a: str = Depends(dep_a), h: int = 1) -> str:
    return a


def dep_c(b: str = Depends(dep_b)) -> str:
    return b


def build_app() -> FastAPI:
    app = FastAPI()
    for i in range(50):
        router = APIRouter(dependencies=[Depends(dep_c)])
        for m in range(20):

            @router.get(f"/i{i}r{m}", dependencies=[Depends(dep_a)])
            def endpoint(limit: int = 10, c: str = Depends(dep_c)) -> dict:  # type: ignore[type-arg]
                return {"c": c, "limit": limit}

        app.include_router(router)
    return app


def main() -> None:
    tracemalloc.start()
    start = time.perf_counter()
    app = build_app()
    client = TestClient(app)
    assert client.get("/i3r7").status_code == 200
    assert len(client.get("/openapi.json").json()["paths"]) == 1000
    elapsed = time.perf_counter() - start
    current, _ = tracemalloc.get_traced_memory()
    gc.collect()
    objects = gc.get_objects()
    counts = {
        "tracked": len(objects),
        "Dependant": sum(1 for o in objects if isinstance(o, Dependant)),
        "FieldInfo": sum(1 for o in objects if isinstance(o, FieldInfo)),
        "ModelField": sum(1 for o in objects if isinstance(o, ModelField)),
    }
    print(f"{counts} traced={current / 1e6:.1f}MB build+openapi={elapsed:.2f}s")


if __name__ == "__main__":
    main()
