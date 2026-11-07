from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from icalendar import Calendar

import update_calendar as uc


FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 11, 7)
HORIZON_END = date(2027, 1, 31)


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def find_event(content: str):
    calendar = Calendar.from_ical(content)
    events = [item for item in calendar.walk() if item.name == "VEVENT"]
    assert len(events) == 1
    return events[0]


def dates_for(pickups, category: str) -> list[date]:
    return [
        pickup.day
        for pickup in pickups
        if pickup.category == category
    ]


def test_normalize_category_aliases():
    assert uc.normalize_category("Restavfall") == "restavfall"
    assert uc.normalize_category("Matavfall") == "matavfall"
    assert uc.normalize_category("Papir") == "papir"
    assert uc.normalize_category("Papir og plastemballasje") == "papir"
    assert uc.normalize_category("Plastemballasje") == "plast"
    assert uc.normalize_category("Glass og metall") == "glass"
    assert uc.normalize_category("Glass og metallemballasje") == "glass"
    assert uc.normalize_category("Ukjend") is None


def test_parse_frequency():
    assert uc.parse_frequency("Tømmes mandager") == (1, 0)
    assert uc.parse_frequency("Tømmes hver 2. onsdag") == (2, 2)
    assert uc.parse_frequency("Tømmes hver 4. torsdag") == (4, 3)
    assert uc.parse_frequency("Tømmes hver 8. fredag") == (8, 4)
    assert uc.parse_frequency("Ingen frekvens") is None


def test_parse_day_month_rolls_to_next_year():
    assert uc.parse_day_month("9. nov", TODAY) == date(2026, 11, 9)
    assert uc.parse_day_month("9. jan", date(2026, 12, 20)) == date(2027, 1, 9)
    assert uc.parse_day_month("28. des", date(2026, 1, 5)) == date(2026, 12, 28)


def test_parse_address_page():
    schedules = uc.parse_address_page(
        read_fixture("address_page.html"), TODAY
    )
    by_category = {
        schedule.category: schedule for schedule in schedules
    }

    assert by_category["restavfall"].next_date == date(2026, 11, 9)
    assert by_category["restavfall"].interval_weeks == 1
    assert by_category["restavfall"].weekday == 0

    assert by_category["matavfall"].next_date == date(2026, 11, 11)
    assert by_category["matavfall"].interval_weeks == 2
    assert by_category["matavfall"].weekday == 2

    assert by_category["papir"].next_date == date(2026, 11, 12)
    assert by_category["papir"].interval_weeks == 4
    assert by_category["papir"].weekday == 3

    assert by_category["glass"].next_date == date(2026, 11, 20)
    assert by_category["glass"].interval_weeks == 8
    assert by_category["glass"].weekday == 4


def test_parse_toemmekalender():
    pickups = uc.parse_toemmekalender(
        read_fixture("toemmekalender.html"), TODAY
    )

    assert len(pickups) == 9
    assert dates_for(pickups, "restavfall") == [
        date(2026, 11, 2),
        date(2026, 11, 9),
        date(2026, 11, 16),
        date(2026, 11, 23),
        date(2026, 11, 30),
    ]
    assert dates_for(pickups, "matavfall") == [
        date(2026, 11, 11),
        date(2026, 11, 25),
    ]
    assert dates_for(pickups, "papir") == [date(2026, 11, 12)]
    assert dates_for(pickups, "glass") == [date(2026, 11, 20)]


def test_parse_api_pickups():
    pickups = uc.parse_api_pickups(
        [
            {
                "dato": "2026-11-09T00:00:00",
                "fraksjon": "Restavfall",
                "frekvensIntervall": 1,
            },
            {
                "dato": "2026-11-12T00:00:00",
                "fraksjon": "Papir",
                "frekvensIntervall": 4,
            },
            {
                "dato": "2026-11-20T00:00:00",
                "fraksjon": "Glass og metallemballasje",
                "frekvensIntervall": 8,
            },
            {
                "dato": "2026-11-21T00:00:00",
                "fraksjon": "Ukjend",
            },
        ]
    )

    assert pickups == [
        uc.Pickup(date(2026, 11, 9), "restavfall", "api"),
        uc.Pickup(date(2026, 11, 12), "papir", "api"),
        uc.Pickup(date(2026, 11, 20), "glass", "api"),
    ]


def test_project_pickups_three_months():
    api_pickups = uc.parse_api_pickups(
        [
            {"dato": "2026-11-02T00:00:00", "fraksjon": "Restavfall"},
            {"dato": "2026-11-09T00:00:00", "fraksjon": "Restavfall"},
            {"dato": "2026-11-16T00:00:00", "fraksjon": "Restavfall"},
            {"dato": "2026-11-23T00:00:00", "fraksjon": "Restavfall"},
            {"dato": "2026-11-30T00:00:00", "fraksjon": "Restavfall"},
            {"dato": "2026-11-11T00:00:00", "fraksjon": "Matavfall"},
            {"dato": "2026-11-25T00:00:00", "fraksjon": "Matavfall"},
            {"dato": "2026-11-12T00:00:00", "fraksjon": "Papir"},
            {
                "dato": "2026-11-20T00:00:00",
                "fraksjon": "Glass og metallemballasje",
            },
        ]
    )
    schedules = uc.parse_address_page(
        read_fixture("address_page.html"), TODAY
    )

    pickups = uc.project_pickups(
        api_pickups, schedules, [], TODAY, HORIZON_END
    )

    assert len(pickups) == 23
    assert dates_for(pickups, "restavfall") == [
        date(2026, 11, 9),
        date(2026, 11, 16),
        date(2026, 11, 23),
        date(2026, 11, 30),
        date(2026, 12, 7),
        date(2026, 12, 14),
        date(2026, 12, 21),
        date(2026, 12, 28),
        date(2027, 1, 4),
        date(2027, 1, 11),
        date(2027, 1, 18),
        date(2027, 1, 25),
    ]
    assert dates_for(pickups, "matavfall") == [
        date(2026, 11, 11),
        date(2026, 11, 25),
        date(2026, 12, 9),
        date(2026, 12, 23),
        date(2027, 1, 6),
        date(2027, 1, 20),
    ]
    assert dates_for(pickups, "papir") == [
        date(2026, 11, 12),
        date(2026, 12, 10),
        date(2027, 1, 7),
    ]
    assert dates_for(pickups, "glass") == [
        date(2026, 11, 20),
        date(2027, 1, 15),
    ]


def test_project_pickups_uses_schedule_without_api():
    schedules = uc.parse_address_page(
        read_fixture("address_page.html"), TODAY
    )
    extra_pickups = uc.parse_toemmekalender(
        read_fixture("toemmekalender.html"), TODAY
    )

    pickups = uc.project_pickups(
        [], schedules, extra_pickups, TODAY, HORIZON_END
    )

    assert len(pickups) == 23
    assert date(2026, 11, 2) not in dates_for(pickups, "restavfall")
    assert date(2027, 1, 15) in dates_for(pickups, "glass")


def test_build_calendar_is_generic_and_all_day():
    pickup = uc.Pickup(date(2026, 11, 9), "restavfall", "api")
    content = uc.build_calendar(
        [pickup],
        "secret-calendar-id",
        now=datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc),
    )
    event = find_event(content)

    assert event.decoded("dtstart") == date(2026, 11, 9)
    assert event.decoded("dtend") == date(2026, 11, 10)
    assert "DTSTART;VALUE=DATE:20261109" in content
    assert str(event["SUMMARY"]) == "BIR: Restavfall"
    assert str(event["DESCRIPTION"]) == "Tømming av restavfall."
    assert "LOCATION" not in content
    assert "URL:" not in content
    assert "secret-calendar-id" not in content
    assert "Testvegen" not in content
    assert "test-r-id" not in content

    uc.validate_private_calendar(
        content,
        [
            "Testvegen 1",
            "test-r-id",
            "secret-calendar-id",
        ],
    )


def test_safe_error_redacts_urls_and_private_values():
    error = RuntimeError(
        "Fann ikkje Testvegen 1 på "
        "https://example.test/sok?rId=test-r-id&name=Testvegen%201"
    )

    message = uc.safe_error(error, ["Testvegen 1", "test-r-id"])

    assert message.startswith("RuntimeError:")
    assert "https://" not in message
    assert "Testvegen" not in message
    assert "test-r-id" not in message


def test_validate_private_calendar_rejects_private_value():
    pickup = uc.Pickup(date(2026, 11, 9), "restavfall", "api")
    content = uc.build_calendar([pickup], "secret-calendar-id")
    leaked = content.replace("BIR tømmedager", "Testvegen 1")

    with pytest.raises(ValueError):
        uc.validate_private_calendar(leaked, ["Testvegen 1"])


def test_uid_is_stable_and_hides_calendar_id():
    pickup = uc.Pickup(date(2026, 11, 9), "restavfall", "api")

    first = uc.uid_for("secret-calendar-id", pickup)
    second = uc.uid_for("secret-calendar-id", pickup)

    assert first == second
    assert first.startswith("bir-")
    assert first.endswith("@bir-kalender.invalid")
    assert "secret-calendar-id" not in first
    assert "restavfall" not in first


def test_unchanged_event_keeps_dtstamp_and_output_is_identical():
    pickup = uc.Pickup(date(2026, 11, 9), "restavfall", "api")
    first_time = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    second_time = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)

    first = uc.build_calendar(
        [pickup], "secret-calendar-id", now=first_time
    )
    second = uc.build_calendar(
        [pickup],
        "secret-calendar-id",
        now=second_time,
        previous_content=first,
    )

    event = find_event(second)
    assert event.decoded("dtstamp") == first_time
    assert int(event["SEQUENCE"]) == 0
    assert second == first


def test_changed_event_gets_new_dtstamp_and_incremented_sequence():
    pickup = uc.Pickup(date(2026, 11, 9), "restavfall", "api")
    first_time = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    second_time = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)

    first = uc.build_calendar(
        [pickup], "secret-calendar-id", now=first_time
    )
    changed_previous = first.replace(
        "SUMMARY:BIR: Restavfall", "SUMMARY:BIR: Endra"
    )
    second = uc.build_calendar(
        [pickup],
        "secret-calendar-id",
        now=second_time,
        previous_content=changed_previous,
    )

    event = find_event(second)
    assert event.decoded("dtstamp") == second_time
    assert int(event["SEQUENCE"]) == 1


def test_write_atomic_keeps_existing_file_on_invalid_content(tmp_path):
    target = tmp_path / "calendar.ics"
    target.write_text("gammalt innhald", encoding="utf-8")
    invalid = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n"

    with pytest.raises(ValueError):
        uc.write_atomic(target, invalid)

    assert target.read_text(encoding="utf-8") == "gammalt innhald"


def test_write_atomic_replaces_file_with_valid_content(tmp_path):
    target = tmp_path / "calendar.ics"
    pickup = uc.Pickup(date(2026, 11, 9), "restavfall", "api")
    content = uc.build_calendar([pickup], "secret-calendar-id")

    uc.write_atomic(target, content)

    assert target.read_bytes() == content.encode("utf-8")


def test_load_config_from_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("CALENDAR_MONTHS", raising=False)
    monkeypatch.setenv("BIR_NAME", "Testkalender")
    monkeypatch.setenv("BIR_ADDRESS", "Testvegen 1")
    monkeypatch.setenv("BIR_RID", "test-r-id")
    monkeypatch.setenv("BIR_CALENDAR_ID", "test-calendar-id")

    months, calendars = uc.load_config(tmp_path / "missing.toml")

    assert months == 3
    assert calendars == [
        uc.CalendarConfig(
            name="Testkalender",
            address="Testvegen 1",
            calendar_id="test-calendar-id",
            r_id="test-r-id",
        )
    ]


def test_horizon_end_covers_three_calendar_months():
    assert uc.horizon_end_for(date(2026, 11, 7), 3) == date(2027, 1, 31)
    assert uc.horizon_end_for(date(2026, 1, 31), 3) == date(2026, 3, 31)
