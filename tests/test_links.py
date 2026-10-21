from app.links import detect_source, parse_links


def test_parses_multiple_links_separated_by_newlines_spaces_and_commas():
    text = """
    https://www.youtube.com/watch?v=abc
    https://youtu.be/xyz https://www.facebook.com/watch/?v=123,
    https://fb.watch/qwe;https://m.facebook.com/story.php?id=1
    """
    parsed = parse_links(text)
    assert [s for _, s in parsed.valid] == ["youtube", "youtube", "facebook", "facebook", "facebook"]
    assert parsed.invalid == ()


def test_removes_duplicates_and_keeps_order():
    parsed = parse_links("https://youtu.be/a\nhttps://youtu.be/b\nhttps://youtu.be/a")
    assert [u for u, _ in parsed.valid] == ["https://youtu.be/a", "https://youtu.be/b"]


def test_adds_https_when_scheme_missing():
    parsed = parse_links("youtube.com/watch?v=1")
    assert parsed.valid == (("https://youtube.com/watch?v=1", "youtube"),)


def test_rejects_other_hosts_and_lookalike_domains():
    parsed = parse_links(
        "https://evil.com/youtube.com https://youtube.com.evil.com/x https://notfacebook.com/v hello"
    )
    assert parsed.valid == ()
    assert len(parsed.invalid) == 4


def test_rejects_non_http_schemes():
    assert detect_source("file:///etc/passwd") is None
    assert detect_source("ftp://youtube.com/x") is None
    assert detect_source("javascript:alert(1)") is None


def test_empty_text_returns_nothing():
    parsed = parse_links("   \n ")
    assert parsed.valid == () and parsed.invalid == ()
