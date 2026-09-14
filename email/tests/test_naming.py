from headlong_email import naming


def test_encode_is_deterministic():
    assert naming.encode("jane@example.com") == naming.encode("Jane@Example.com ")


def test_encode_matches_chat_from_re():
    import re

    CHAT_FROM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    for addr in ("jane@example.com", "a+tag@x.co", "weird!! name@x.com", "x@y.com"):
        assert CHAT_FROM_RE.match(naming.encode(addr))


def test_different_addresses_dont_collide():
    a = naming.encode("jane@example.com")
    b = naming.encode("jane@other.com")
    assert a != b


def test_is_email_name():
    assert naming.is_email_name(naming.encode("jane@example.com"))
    assert not naming.is_email_name("telegram-1-1")
    assert not naming.is_email_name(None)
    assert not naming.is_email_name("email-")
