from headlong_email.api import Client


class FakeResponse:
    status_code = 200

    def json(self):
        return {"ok": True}

    text = ""


def test_send_uses_the_api_key(monkeypatch):
    seen = {}

    def fake_post(url, headers, json, timeout):
        seen["headers"] = headers
        return FakeResponse()

    monkeypatch.setattr("headlong_email.api.httpx.post", fake_post)
    client = Client("re_x")

    client.send(from_addr="a@x.com", to=["b@x.com"], subject="hi", text="hi")

    assert seen["headers"]["Authorization"] == "Bearer re_x"


def test_get_received_email_uses_the_api_key(monkeypatch):
    seen = {}

    def fake_get(url, headers, timeout):
        seen["headers"] = headers
        return FakeResponse()

    monkeypatch.setattr("headlong_email.api.httpx.get", fake_get)
    client = Client("re_x")

    client.get_received_email("e1")

    assert seen["headers"]["Authorization"] == "Bearer re_x"
