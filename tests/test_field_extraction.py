from utils.parsing import normalize_job_type, detect_remote_from_text


def test_normalize_job_type():
    assert normalize_job_type("Full-Time") == "Full Time"
    assert normalize_job_type("part time") == "Part Time"
    assert normalize_job_type("Software Engineering Internship") == "Internship"
    assert normalize_job_type("contract") is None


def test_detect_remote_from_text():
    assert detect_remote_from_text("This role is remote") is True
    assert detect_remote_from_text("hybrid working policy") is True
    assert detect_remote_from_text("on-site only") is False

