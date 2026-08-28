"""One differential test that pins the whole copy-instead-of-rebuild fast path."""

import dataclasses
import re
from typing import Annotated, Any

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.routing import _EffectiveRouteContext, _iter_routes_with_context
from fastapi.testclient import TestClient
from pydantic import BaseModel


class Item(BaseModel):
    name: str


def dep(q: str = "x") -> str:
    return q


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/plain")
    def plain(limit: int = 0) -> dict[str, int]:
        """Docstring description."""
        return {"limit": limit}

    @router.post("/items/{item_id}", status_code=201, tags=["route-tag"])
    def create(item_id: int, body: Item, c: Annotated[str, Depends(dep)]) -> Item:
        return body

    @router.get("/text", response_class=PlainTextResponse, deprecated=True)
    def text() -> str:
        return "hi"

    @router.get("/resp", responses={404: {"model": Item}})
    def resp() -> Item:
        return Item(name="a")

    return router


INCLUDE_KWARGS: list[dict[str, Any]] = [
    {},
    {"tags": ["include-tag"]},
    {"deprecated": True},
    {"include_in_schema": False},
]

SKIP_FIELDS = {"original_route"}
ROUTER = build_router()


def normalize(value: Any) -> str:
    return re.sub(r"0x[0-9a-fA-F]{6,}", "0xADDR", repr(value))


def contexts_for(**include_kwargs: Any) -> list[_EffectiveRouteContext]:
    app = FastAPI()
    app.include_router(ROUTER, **include_kwargs)
    return [c for _, c in _iter_routes_with_context(app.routes) if c is not None]


@pytest.mark.parametrize("include_kwargs", INCLUDE_KWARGS)
def test_copied_state_matches_rebuilt_state(
    monkeypatch: pytest.MonkeyPatch, include_kwargs: dict[str, Any]
) -> None:
    fast = contexts_for(**include_kwargs)

    import fastapi.routing as routing

    monkeypatch.setattr(routing, "_can_copy_route_state", lambda *a, **k: False)
    slow = contexts_for(**include_kwargs)

    assert len(fast) == len(slow)
    for fast_ctx, slow_ctx in zip(fast, slow, strict=True):
        for field in dataclasses.fields(_EffectiveRouteContext):
            if field.name in SKIP_FIELDS:
                continue
            a = getattr(fast_ctx, field.name)
            b = getattr(slow_ctx, field.name)
            assert normalize(a) == normalize(b), (
                f"{field.name} differs for {fast_ctx.path}: {a!r} != {b!r}"
            )


def test_copied_route_state_covers_every_context_field() -> None:
    all_fields = {f.name for f in dataclasses.fields(_EffectiveRouteContext)}
    copied = set(_EffectiveRouteContext._COPIED_ROUTE_STATE_FIELDS)
    combined = {
        "tags",
        "dependencies",
        "responses",
        "callbacks",
        "deprecated",
        "include_in_schema",
        "dependency_overrides_provider",
        "strict_content_type",
    }
    structural = {"original_route", "starlette_route", "frontend_prefix"}
    assert copied | combined | structural == all_fields


def test_fast_path_endpoints_serve_requests() -> None:
    app = FastAPI()
    app.include_router(ROUTER)
    client = TestClient(app)
    assert client.get("/plain", params={"limit": "3"}).json() == {"limit": 3}
    assert client.post("/items/1", json={"name": "n"}).json() == {"name": "n"}
    assert client.get("/text").text == "hi"
    assert client.get("/resp").json() == {"name": "a"}
