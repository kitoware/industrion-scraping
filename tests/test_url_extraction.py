from utils.parsing import absolutize_and_dedupe_urls


def test_absolutize_and_dedupe_urls():
    base = "https://example.com/careers/"
    urls = [
        "/jobs/123",
        "https://example.com/jobs/123",
        "/jobs/456",
        "mailto:careers@example.com",
        "tel:+15551234567",
        "javascript:void(0)",
        "#section",
    ]
    out = absolutize_and_dedupe_urls(urls, base)
    assert out == [
        "https://example.com/jobs/123",
        "https://example.com/jobs/456",
    ]

