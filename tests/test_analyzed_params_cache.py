from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.dependencies.utils import get_dependant
from fastapi.testclient import TestClient


def no_path_params(q: str = "default") -> str:
    return q


def consumes_id(id: str) -> str:
    return id


def test_analysis_shared_across_paths_when_dependency_ignores_path_params() -> None:
    users = get_dependant(path="/users/{user_id}", call=no_path_params)
    orgs = get_dependant(path="/orgs/{org_id}", call=no_path_params)
    assert users.query_params[0] is orgs.query_params[0]


def test_analysis_not_shared_when_dependency_consumes_path_param() -> None:
    with_param = get_dependant(path="/users/{id}", call=consumes_id)
    without_param = get_dependant(path="/users", call=consumes_id)
    assert with_param.path_params
    assert not without_param.path_params
    assert without_param.query_params


def test_shared_dependency_behaves_per_path() -> None:
    app = FastAPI()

    @app.get("/users/{id}")
    def read_user(id: str, dep: str = Depends(no_path_params)) -> dict[str, str]:
        return {"id": id, "dep": dep}

    @app.get("/orgs/{id}")
    def read_org(id: str, dep: str = Depends(consumes_id)) -> dict[str, str]:
        return {"id": id, "dep": dep}

    client = TestClient(app)
    assert client.get("/users/u1", params={"q": "x"}).json() == {
        "id": "u1",
        "dep": "x",
    }
    assert client.get("/orgs/o1").json() == {"id": "o1", "dep": "o1"}


def sub_dep(q2: str = "y") -> str:
    return q2


def parent_dep(s: str = Depends(sub_dep)) -> str:
    return s


def test_sub_dependant_subtrees_shared_across_routes() -> None:
    first = get_dependant(path="/a", call=parent_dep)
    second = get_dependant(path="/b", call=parent_dep)
    assert first is not second
    assert first.dependencies[0] is second.dependencies[0]


def test_root_dependants_stay_per_route() -> None:
    app = FastAPI()

    @app.get("/a")
    def endpoint_a(p: str = Depends(parent_dep)) -> str:
        return p

    @app.get("/b")
    def endpoint_b(p: str = Depends(parent_dep)) -> str:
        return p

    routes = [r for r in app.routes if getattr(r, "path", "") in {"/a", "/b"}]
    assert routes[0].dependant is not routes[1].dependant  # type: ignore[attr-defined]
    client = TestClient(app)
    assert client.get("/a", params={"q2": "z"}).text == '"z"'
    assert client.get("/b").text == '"y"'


def test_typed_signature_computed_once_per_callable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fastapi.dependencies.utils as utils

    def fresh_dep(v: str = "w") -> str:
        return v  # pragma: no cover

    calls: list[object] = []
    original = utils.get_typed_signature

    def counting(call):  # type: ignore[no-untyped-def]
        calls.append(call)
        return original(call)

    monkeypatch.setattr(utils, "get_typed_signature", counting)
    get_dependant(path="/x", call=fresh_dep)
    get_dependant(path="/y", call=fresh_dep)
    assert calls.count(fresh_dep) == 1


def maybe_path(id: str = "fallback") -> str:
    return id


def test_path_param_binding_not_shared_across_differing_paths() -> None:
    app = FastAPI()

    @app.get("/a/{id}")
    def a(v: Annotated[str, Depends(maybe_path)]) -> str:
        return v

    @app.get("/b")
    def b(v: Annotated[str, Depends(maybe_path)]) -> str:
        return v

    client = TestClient(app)
    assert client.get("/a/xyz").json() == "xyz"
    assert client.get("/b", params={"id": "q"}).json() == "q"
    assert client.get("/b").json() == "fallback"
    schema = app.openapi()
    assert schema["paths"]["/a/{id}"]["get"]["parameters"][0]["in"] == "path"
    assert schema["paths"]["/b"]["get"]["parameters"][0]["in"] == "query"
