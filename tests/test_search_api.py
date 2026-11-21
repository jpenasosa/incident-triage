from factories import incident_dict, ndjson

NDJSON = {"Content-Type": "application/x-ndjson"}


def seed(client):
    rows = [
        incident_dict(id=1, title="SSO login timeout", tags=["sso", "login"]),
        incident_dict(id=2, title="Report timeout", service="reporting", tags=["report"]),
    ]
    client.post("/ingest", content=ndjson(*rows), headers=NDJSON)


def test_search_returns_page_shape(client):
    seed(client)

    resp = client.get("/search", params={"q": "timeout", "service": "reporting"})

    body = resp.json()
    assert resp.status_code == 200
    assert (body["total"], body["limit"], body["offset"]) == (1, 10, 0)
    assert set(body["results"][0]) == {
        "id",
        "title",
        "snippet",
        "score",
        "service",
        "environment",
        "severity",
        "created_at",
    }


def test_repeated_tags_param(client):
    seed(client)

    resp = client.get("/search", params=[("tags", "sso"), ("tags", "LOGIN")])

    assert [r["id"] for r in resp.json()["results"]] == [1]


def test_no_query_and_no_filters_is_400(client):
    resp = client.get("/search")

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "BAD_REQUEST"


def test_invalid_params_are_422(client):
    for params in [
        {"q": "x", "limit": 51},
        {"q": "x", "offset": -1},
        {"environment": "uat"},
        {"q": "x" * 501},
        {"q": "x", "bogus": "1"},
    ]:
        assert client.get("/search", params=params).status_code == 422, params
