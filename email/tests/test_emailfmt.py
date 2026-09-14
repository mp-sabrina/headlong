from headlong_email import emailfmt


def test_clean_inbound_strips_on_wrote_quote():
    text = "Sounds good.\n\nOn Tue, Sep 9, 2026 at 1:00 PM Jane <jane@x.com> wrote:\n> earlier text"
    assert emailfmt.clean_inbound(text) == "Sounds good."


def test_clean_inbound_strips_original_message_marker():
    text = "New reply here.\n-----Original Message-----\nFrom: bob@x.com\nold stuff"
    assert emailfmt.clean_inbound(text) == "New reply here."


def test_clean_inbound_strips_trailing_quoted_lines():
    text = "Reply text\n> quoted line one\n> quoted line two"
    assert emailfmt.clean_inbound(text) == "Reply text"


def test_clean_inbound_passthrough_when_no_quote():
    assert emailfmt.clean_inbound("just a plain reply") == "just a plain reply"


def test_strip_leaked_command():
    assert emailfmt.strip_leaked_command("chat reply email-jane-abc hello") == "hello"
    assert emailfmt.strip_leaked_command("hello there") == "hello there"


def test_reply_subject_adds_re_prefix():
    assert emailfmt.reply_subject("Project update") == "Re: Project update"


def test_reply_subject_keeps_existing_re():
    assert emailfmt.reply_subject("Re: Project update") == "Re: Project update"


def test_reply_subject_empty():
    assert emailfmt.reply_subject("") == "Re: (no subject)"
