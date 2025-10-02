from utils.ats import bamboohr


def test_map_fields_on_site_with_compensation():
    job_opening = {
        "jobOpeningName": "CNC Lathe Operator ",
        "employmentStatusLabel": "Full-Time",
        "location": {
            "city": "Fairview",
            "state": "Pennsylvania",
            "addressCountry": "United States",
        },
        "atsLocation": {
            "country": "United States",
        },
        "locationType": "0",
        "description": "<p>Sample description</p>",
        "compensation": {
            "range": {
                "min": "50,000",
                "max": "80000",
            }
        },
        "jobOpeningShareUrl": "https://example.bamboohr.com/careers/1",
    }
    company_info = {"name": "Example Corp"}

    fields = bamboohr._map_fields(job_opening, company_info)  # type: ignore[attr-defined]

    assert fields["title"] == "CNC Lathe Operator"
    assert fields["company_name"] == "Example Corp"
    assert fields["location"] == "Fairview, Pennsylvania, United States"
    assert fields["remote_ok"] is False
    assert fields["job_type"] == "Full-Time"
    assert fields["description_html"] == "<p>Sample description</p>"
    assert fields["min_salary"] == 50000.0
    assert fields["max_salary"] == 80000.0
    assert fields["application_link"] == "https://example.bamboohr.com/careers/1"


def test_map_fields_remote_location_fallback():
    job_opening = {
        "jobOpeningName": "Remote Engineer",
        "employmentStatusLabel": "Full-Time",
        "location": {},
        "atsLocation": {
            "city": "Anywhere",
            "state": "Remote",
        },
        "locationType": "1",
        "description": "<p>Remote role</p>",
        "compensation": {},
        "jobOpeningShareUrl": "https://example.bamboohr.com/careers/2",
    }
    company_info = {"name": "Example Corp"}

    fields = bamboohr._map_fields(job_opening, company_info)  # type: ignore[attr-defined]

    assert fields["location"] == "Anywhere, Remote"
    assert fields["remote_ok"] is True
