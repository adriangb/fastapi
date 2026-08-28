from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from starlette.routing import Mount, Router


def test_empty_prefix_validation_traverses_all_route_types(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("app")
    inner = APIRouter()
    inner.frontend("/ui", directory=dist, check_dir=True)
    inner.routes.append(Mount("/sub", app=Router()))

    @inner.get("/route")
    def route() -> dict[str, bool]:
        return {"ok": True}

    outer = APIRouter()
    outer.include_router(inner, prefix="/inner")
    app = FastAPI()
    app.include_router(outer)
    assert TestClient(app).get("/inner/route").json() == {"ok": True}
