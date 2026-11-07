from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from icalendar import Calendar, Event
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


API_BASE_URL = "https://webservice.bir.no/api"
API_LOGIN_URL = f"{API_BASE_URL}/login"
API_PICKUPS_URL = f"{API_BASE_URL}/v1/renovasjon/tomminger"
API_ADDRESS_SEARCH_URL = "https://bir.no/api/search/AddressSearch"
API_APP_ID = "94FA72AD-583D-4AA3-988F-491F694DFB7B"
API_PROVIDER_ID = "100;300;400"
BIR_BASE_URL = "https://bir.no"
ADDRESS_PAGE_URL = f"{BIR_BASE_URL}/adressesoek/"
TOEMMEKALENDER_URL = f"{BIR_BASE_URL}/adressesoek/toemmekalender/"
USER_AGENT = "BIRKalender/1.0 (+https://github.com/esnalabu/bir-calendar)"
NETWORK_TIMEOUT_SECONDS = 30
DEFAULT_MONTHS = 3
UID_DOMAIN = "bir-kalender.invalid"

WEEKDAYS = {
    "mandag": 0,
    "mandager": 0,
    "tirsdag": 1,
    "tirsdager": 1,
    "onsdag": 2,
    "onsdager": 2,
    "torsdag": 3,
    "torsdager": 3,
    "fredag": 4,
    "fredager": 4,
    "lørdag": 5,
    "lørdager": 5,
    "søndag": 6,
    "søndager": 6,
}

MONTHS = {
    "januar": 1,
    "jan": 1,
    "februar": 2,
    "feb": 2,
    "mars": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "mai": 5,
    "juni": 6,
    "jun": 6,
    "juli": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "oktober": 10,
    "okt": 10,
    "november": 11,
    "nov": 11,
    "desember": 12,
    "des": 12,
}

CATEGORY_LABELS = {
    "restavfall": "Restavfall",
    "matavfall": "Matavfall",
    "papir": "Papir og plastemballasje",
    "glass": "Glass og metallemballasje",
    "plast": "Plastemballasje",
}

ICON_CATEGORIES = {
    "restavfall.svg": "restavfall",
    "matavfall.svg": "matavfall",
    "papirogplast.svg": "papir",
    "glassogmetall.svg": "glass",
    "plastemballasje.svg": "plast",
}

FREQUENCY_PATTERN = re.compile(
    r"hver\s+(?:(\d+)\.?\s+)?([a-zæøå]+)", re.IGNORECASE
)
DATE_PATTERN = re.compile(r"(\d{1,2})\.\s*([a-zæøå]+)", re.IGNORECASE)
DAY_PATTERN = re.compile(r"(\d{1,2})\.")
MONTH_YEAR_PATTERN = re.compile(r"([a-zæøå]+)\s+(\d{4})", re.IGNORECASE)


@dataclass(frozen=True)
class Pickup:
    day: date
    category: str
    source: str


@dataclass(frozen=True)
class Schedule:
    category: str
    next_date: date
    interval_weeks: int
    weekday: int


@dataclass(frozen=True)
class CalendarConfig:
    name: str
    address: str
    calendar_id: str
    r_id: str | None = None
    output: str | None = None
    categories: tuple[str, ...] | None = None


@dataclass(frozen=True)
class PreviousEvent:
    dtstamp: datetime
    sequence: int
    signature: bytes


def warning(message: str) -> None:
    print(f"Åtvaring: {message}", file=sys.stderr)


def sanitize_text(text: str, private_values: Iterable[str] = ()) -> str:
    sanitized = re.sub(r"https?://\S+", "[URL fjerna]", text)
    for value in private_values:
        if value:
            sanitized = re.sub(
                re.escape(value),
                "[fjerna]",
                sanitized,
                flags=re.IGNORECASE,
            )
    return sanitized


def safe_error(exc: BaseException, private_values: Iterable[str] = ()) -> str:
    return f"{type(exc).__name__}: {sanitize_text(str(exc), private_values)}"


def normalize_category(value: str) -> str | None:
    text = re.sub(r"\s+", " ", value).strip().casefold()
    if not text:
        return None
    if "restavfall" in text:
        return "restavfall"
    if "matavfall" in text:
        return "matavfall"
    if text == "plast" or text == "plastemballasje":
        return "plast"
    if "papir" in text or "plast" in text:
        return "papir"
    if "glass" in text:
        return "glass"
    return None


def parse_categories(
    value: str | Iterable[str] | None,
) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, str):
        raw_values: Iterable[str] = value.split(",")
    elif isinstance(value, Iterable):
        raw_values = value
    else:
        raise ValueError(
            "categories må vere ei liste eller ein kommaseparert streng."
        )

    categories: list[str] = []
    for raw_value in raw_values:
        text = str(raw_value).strip()
        if not text:
            continue
        category = normalize_category(text)
        if category is None:
            valid = ", ".join(sorted(CATEGORY_LABELS))
            raise ValueError(
                f"Ukjend kategori {text!r}. Gyldige kategoriar: {valid}."
            )
        if category not in categories:
            categories.append(category)
    return tuple(categories) or None


def category_label(category: str) -> str:
    return CATEGORY_LABELS.get(category, category)


def category_from_icon(src: str) -> str | None:
    name = src.rsplit("/", 1)[-1].casefold()
    return ICON_CATEGORIES.get(name)


def parse_day_month(text: str, reference: date) -> date | None:
    match = DATE_PATTERN.search(text)
    if not match:
        return None
    day = int(match.group(1))
    month = MONTHS.get(match.group(2).casefold())
    if month is None:
        return None
    for year in (reference.year, reference.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate >= reference:
            return candidate
    return None


def parse_month_title(text: str) -> tuple[int, int] | None:
    match = MONTH_YEAR_PATTERN.search(text)
    if not match:
        return None
    month = MONTHS.get(match.group(1).casefold())
    if month is None:
        return None
    return int(match.group(2)), month


def parse_day_in_month(text: str, year: int, month: int) -> date | None:
    match = DAY_PATTERN.search(text)
    if not match:
        return None
    try:
        return date(year, month, int(match.group(1)))
    except ValueError:
        return None


def parse_frequency(text: str) -> tuple[int, int] | None:
    match = FREQUENCY_PATTERN.search(text)
    if match:
        interval = int(match.group(1) or 1)
        weekday = WEEKDAYS.get(match.group(2).casefold())
        if weekday is not None:
            return max(1, interval), weekday

    for word in reversed(re.findall(r"[a-zæøå]+", text.casefold())):
        weekday = WEEKDAYS.get(word)
        if weekday is not None:
            return 1, weekday
    return None


def infer_interval_weeks(days: Iterable[date]) -> int | None:
    unique = sorted(set(days))
    if len(unique) < 2:
        return None
    differences = [
        (later - earlier).days
        for earlier, later in zip(unique, unique[1:])
        if later > earlier
    ]
    if not differences:
        return None
    return max(1, round(min(differences) / 7))


def add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def last_day_of_month(value: date) -> date:
    return add_months(value, 1) - timedelta(days=1)


def horizon_end_for(today: date, months: int) -> date:
    return last_day_of_month(add_months(today, max(1, months) - 1))


def first_day_of_month(value: date) -> date:
    return value.replace(day=1)


def parse_api_pickups(data: object) -> list[Pickup]:
    if not isinstance(data, list):
        raise ValueError("BIR-API returnerte ikkje ei liste.")
    pickups: list[Pickup] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        raw_date = item.get("dato")
        raw_category = item.get("fraksjon")
        if not isinstance(raw_date, str) or not isinstance(raw_category, str):
            continue
        try:
            day = date.fromisoformat(raw_date.split("T", 1)[0])
        except ValueError:
            continue
        category = normalize_category(raw_category)
        if category is None:
            continue
        pickups.append(Pickup(day=day, category=category, source="api"))
    return pickups


def parse_address_page(html: str, reference: date) -> list[Schedule]:
    document = BeautifulSoup(html, "html.parser")
    items = document.select(
        "ul.address-page-box__list li.address-page-box__list__item"
    )
    if not items:
        raise ValueError("Fann ikkje tømmelista på adressesida.")

    schedules: dict[str, Schedule] = {}
    for item in items:
        inner = item.select_one(".text-content__inner")
        if inner is None:
            continue

        frequency_element = inner.select_one(".text-content__sub")
        frequency_text = (
            frequency_element.get_text(" ", strip=True)
            if frequency_element is not None
            else ""
        )
        category_text = " ".join(
            text.strip()
            for text in inner.find_all(string=True, recursive=False)
            if text.strip()
        )
        if not category_text:
            full_text = inner.get_text(" ", strip=True)
            category_text = full_text.replace(frequency_text, "", 1).strip()

        category = normalize_category(category_text)
        if category is None:
            continue

        date_element = item.select_one(".date__month")
        if date_element is None:
            continue
        next_date = parse_day_month(
            date_element.get_text(" ", strip=True), reference
        )
        if next_date is None:
            continue

        frequency = parse_frequency(frequency_text)
        if frequency is None:
            interval, weekday = 1, next_date.weekday()
        else:
            interval, weekday = frequency
        if weekday != next_date.weekday():
            weekday = next_date.weekday()

        existing = schedules.get(category)
        if existing is None or next_date < existing.next_date:
            schedules[category] = Schedule(
                category=category,
                next_date=next_date,
                interval_weeks=interval,
                weekday=weekday,
            )

    if not schedules:
        raise ValueError("Fann ingen tømmekategoriar på adressesida.")
    return list(schedules.values())


def parse_toemmekalender(html: str, reference: date) -> list[Pickup]:
    document = BeautifulSoup(html, "html.parser")
    pickups: list[Pickup] = []

    for container in document.select(".month-container"):
        title_element = container.select_one(".month-title")
        month_title = (
            parse_month_title(title_element.get_text(" ", strip=True))
            if title_element is not None
            else None
        )
        if month_title is None:
            year, month = reference.year, reference.month
        else:
            year, month = month_title

        for row in container.select(".category-row"):
            icon = row.select_one(".icon img")
            category = (
                category_from_icon(str(icon.get("src", "")))
                if icon is not None
                else None
            )
            if category is None:
                category = normalize_category(row.get_text(" ", strip=True))
            if category is None:
                continue

            for date_item in row.select(".date-item"):
                date_element = date_item.select_one(".date-item-date")
                if date_element is None:
                    continue
                day = parse_day_in_month(
                    date_element.get_text(" ", strip=True), year, month
                )
                if day is not None:
                    pickups.append(
                        Pickup(day=day, category=category, source="toemmekalender")
                    )

    return pickups


def project_pickups(
    api_pickups: Sequence[Pickup],
    schedules: Sequence[Schedule],
    extra_pickups: Sequence[Pickup],
    today: date,
    horizon_end: date,
) -> list[Pickup]:
    observed: dict[str, set[date]] = {}
    for pickup in [*api_pickups, *extra_pickups]:
        observed.setdefault(pickup.category, set()).add(pickup.day)

    schedule_by_category = {
        schedule.category: schedule for schedule in schedules
    }
    categories = set(observed) | set(schedule_by_category)
    result: dict[tuple[str, date], Pickup] = {}

    for category in categories:
        days = observed.get(category, set())
        schedule = schedule_by_category.get(category)
        if schedule is not None:
            days.add(schedule.next_date)
            interval = max(1, schedule.interval_weeks)
        else:
            interval = infer_interval_weeks(days)
            if interval is None:
                continue

        if not days:
            continue

        anchor = max(days)
        for day in days:
            if today <= day <= horizon_end:
                result[(category, day)] = Pickup(
                    day=day, category=category, source="observed"
                )

        day = anchor
        while True:
            day += timedelta(weeks=interval)
            if day > horizon_end:
                break
            if day >= today:
                result[(category, day)] = Pickup(
                    day=day, category=category, source="projected"
                )

    return sorted(
        result.values(),
        key=lambda pickup: (pickup.day, category_label(pickup.category)),
    )


def filter_pickups(
    pickups: Iterable[Pickup],
    categories: tuple[str, ...] | None,
) -> list[Pickup]:
    if categories is None:
        return list(pickups)
    allowed = set(categories)
    return [pickup for pickup in pickups if pickup.category in allowed]


def build_session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "nb-NO,nb;q=0.9,en;q=0.8",
            "Accept": "application/json,text/html,application/xhtml+xml",
        }
    )
    return session


class BirClient:
    def __init__(
        self,
        session: requests.Session | None = None,
        timeout: int = NETWORK_TIMEOUT_SECONDS,
    ) -> None:
        self.session = session or build_session()
        self.timeout = timeout
        self._token: str | None = None

    def login(self) -> None:
        response = self.session.post(
            API_LOGIN_URL,
            json={
                "applikasjonsId": API_APP_ID,
                "oppdragsgiverId": API_PROVIDER_ID,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        token = response.headers.get("Token")
        if not token:
            raise RuntimeError("BIR-innlogging gav ingen Token-header.")
        self._token = token

    def _authorized_get(
        self, url: str, params: dict[str, str]
    ) -> requests.Response:
        if self._token is None:
            self.login()

        for attempt in range(2):
            headers = {"Token": self._token or ""}
            response = self.session.get(
                url,
                params=params,
                headers=headers,
                timeout=self.timeout,
            )
            if response.status_code in (401, 403, 500) and attempt == 0:
                self._token = None
                self.login()
                continue
            response.raise_for_status()
            return response

        raise RuntimeError("BIR-API avviste tokenet etter ny innlogging.")

    def search_address(self, address: str) -> dict[str, object]:
        response = self.session.get(
            API_ADDRESS_SEARCH_URL,
            params={"q": address, "s": "false"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list) or not data:
            raise RuntimeError(f"Fann ikkje adressa {address!r}.")
        exact = next(
            (
                item
                for item in data
                if isinstance(item, dict)
                and str(item.get("Title", "")).casefold()
                == address.casefold()
            ),
            None,
        )
        result = exact or next(
            (item for item in data if isinstance(item, dict)), None
        )
        if result is None:
            raise RuntimeError(f"Fann ikkje adressa {address!r}.")
        return result

    def resolve_property(
        self, address: str, r_id: str | None = None
    ) -> tuple[str, str]:
        if r_id:
            return r_id, address
        result = self.search_address(address)
        property_id = str(result.get("Id") or "").strip()
        if not property_id:
            raise RuntimeError(f"Adressesøket gav ingen Id for {address!r}.")
        official_address = str(result.get("Title") or address).strip()
        return property_id, official_address

    def get_pickups(
        self, property_id: str, from_date: date, to_date: date
    ) -> list[Pickup]:
        response = self._authorized_get(
            API_PICKUPS_URL,
            params={
                "eiendomId": property_id,
                "datoFra": from_date.isoformat(),
                "datoTil": to_date.isoformat(),
            },
        )
        return parse_api_pickups(response.json())

    def fetch_text(
        self, url: str, params: dict[str, str] | None = None
    ) -> str:
        response = self.session.get(
            url, params=params, timeout=self.timeout
        )
        response.raise_for_status()
        return response.content.decode("utf-8", errors="replace")


def uid_for(calendar_id: str, pickup: Pickup) -> str:
    identity = f"{calendar_id}|{pickup.category}|{pickup.day.isoformat()}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return f"bir-{digest}@{UID_DOMAIN}"


def create_event(
    pickup: Pickup,
    calendar_id: str,
    dtstamp: datetime,
    sequence: int,
) -> Event:
    label = category_label(pickup.category)
    event = Event()
    event.add("uid", uid_for(calendar_id, pickup))
    event.add("dtstamp", dtstamp)
    event.add("dtstart", pickup.day)
    event.add("dtend", pickup.day + timedelta(days=1))
    event.add("summary", f"BIR: {label}")
    event.add("description", f"Tømming av {label.lower()}.")
    event.add("categories", ["BIR", label])
    event.add("status", "CONFIRMED")
    event.add("sequence", sequence)
    event.add("transp", "TRANSPARENT")
    return event


def event_signature(event: Event) -> bytes:
    return b"\r\n".join(
        line
        for line in event.to_ical().split(b"\r\n")
        if not line.startswith((b"DTSTAMP", b"SEQUENCE"))
    )


def previous_events(content: str | None) -> dict[str, PreviousEvent]:
    if not content:
        return {}

    try:
        calendar = Calendar.from_ical(content)
    except Exception:
        return {}

    events: dict[str, PreviousEvent] = {}
    for component in calendar.walk():
        if component.name != "VEVENT":
            continue

        uid = str(component.get("UID", "")).strip()
        if not uid:
            continue

        try:
            dtstamp = component.decoded("DTSTAMP")
        except (KeyError, TypeError, ValueError):
            continue
        if not isinstance(dtstamp, datetime):
            continue
        if dtstamp.tzinfo is None:
            dtstamp = dtstamp.replace(tzinfo=timezone.utc)

        try:
            sequence = int(component.decoded("SEQUENCE"))
        except (KeyError, TypeError, ValueError):
            sequence = 0

        events[uid] = PreviousEvent(
            dtstamp=dtstamp.astimezone(timezone.utc),
            sequence=max(sequence, 0),
            signature=event_signature(component),
        )
    return events


def build_calendar(
    pickups: Iterable[Pickup],
    calendar_id: str,
    now: datetime | None = None,
    previous_content: str | None = None,
) -> str:
    timestamp = now or datetime.now(timezone.utc)
    previous = previous_events(previous_content)
    calendar = Calendar()
    calendar.add("prodid", "-//BIR Kalender//NO")
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    calendar.add("method", "PUBLISH")
    calendar.add("x-wr-calname", "BIR tømmedager")
    calendar.add("x-wr-caldesc", "Tømmedagar for avfall")
    calendar.add("x-wr-timezone", "Europe/Oslo")
    calendar.add("refresh-interval", "P1W", parameters={"VALUE": "DURATION"})
    calendar.add("x-published-ttl", "P1W")

    for pickup in sorted(
        pickups, key=lambda item: (item.day, category_label(item.category))
    ):
        uid = uid_for(calendar_id, pickup)
        preview = create_event(pickup, calendar_id, timestamp, 0)
        existing = previous.get(uid)
        if existing and existing.signature == event_signature(preview):
            dtstamp = existing.dtstamp
            sequence = existing.sequence
        else:
            dtstamp = timestamp
            sequence = existing.sequence + 1 if existing else 0
        calendar.add_component(
            create_event(pickup, calendar_id, dtstamp, sequence)
        )

    return calendar.to_ical().decode("utf-8")


def validate_calendar(content: str) -> int:
    calendar = Calendar.from_ical(content)
    events = [
        component
        for component in calendar.walk()
        if component.name == "VEVENT"
    ]
    if not events:
        raise ValueError("Kalenderen inneheld ingen VEVENT.")
    return len(events)


def validate_private_calendar(
    content: str, forbidden_values: Iterable[str]
) -> None:
    if re.search(r"(?im)^LOCATION(?:;|:)", content):
        raise ValueError("Kalenderen inneheld LOCATION.")
    if re.search(r"(?im)^URL(?:;|:)", content):
        raise ValueError("Kalenderen inneheld URL.")
    lowered = content.casefold()
    for value in forbidden_values:
        if value and value.casefold() in lowered:
            raise ValueError(f"Kalenderen inneheld privat verdi: {value!r}")


def write_atomic(path: Path, content: str) -> None:
    validate_calendar(content)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_bytes(content.encode("utf-8"))
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def load_config(path: Path) -> tuple[int, list[CalendarConfig]]:
    data: dict[str, object] = {}
    if path.exists():
        try:
            with path.open("rb") as file:
                data = tomllib.load(file)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ValueError("Klarte ikkje å lese configfila.") from exc

    months = int(os.environ.get("CALENDAR_MONTHS", data.get("months", DEFAULT_MONTHS)))
    entries = data.get("calendars", [])
    if not isinstance(entries, list):
        raise ValueError("calendars.toml: 'calendars' må vere ei liste.")

    calendars: list[CalendarConfig] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("calendars.toml: kvar kalender må vere ein tabell.")
        calendars.append(
            CalendarConfig(
                name=str(entry.get("name") or "BIR"),
                address=str(entry.get("address") or ""),
                calendar_id=str(entry.get("calendar_id") or ""),
                r_id=str(entry["r_id"]) if entry.get("r_id") else None,
                output=str(entry["output"]) if entry.get("output") else None,
                categories=parse_categories(entry.get("categories")),
            )
        )

    env_address = os.environ.get("BIR_ADDRESS", "").strip()
    env_calendar_id = os.environ.get("BIR_CALENDAR_ID", "").strip()
    env_rid = os.environ.get("BIR_RID", "").strip() or None
    env_categories_raw = os.environ.get("BIR_CATEGORIES", "").strip()
    env_categories = (
        parse_categories(env_categories_raw) if env_categories_raw else None
    )
    if not calendars and env_address and env_calendar_id:
        calendars.append(
            CalendarConfig(
                name=os.environ.get("BIR_NAME", "BIR").strip() or "BIR",
                address=env_address,
                calendar_id=env_calendar_id,
                r_id=env_rid,
                categories=env_categories,
            )
        )
    elif calendars and (
        env_address or env_calendar_id or env_rid or env_categories_raw
    ):
        first = calendars[0]
        calendars[0] = replace(
            first,
            name=os.environ.get("BIR_NAME", first.name).strip() or first.name,
            address=env_address or first.address,
            calendar_id=env_calendar_id or first.calendar_id,
            r_id=env_rid or first.r_id,
            categories=(
                env_categories if env_categories is not None else first.categories
            ),
        )

    if not calendars:
        raise ValueError(
            "Fann ingen kalendrar. Lag calendars.toml eller set BIR_ADDRESS "
            "og BIR_CALENDAR_ID."
        )

    for index, calendar in enumerate(calendars, start=1):
        if not calendar.address:
            raise ValueError(f"Kalender {index} manglar adresse.")
        if not calendar.calendar_id:
            raise ValueError(f"Kalender {index} manglar calendar_id.")

    return max(1, months), calendars


def output_path_for(
    calendar: CalendarConfig,
    index: int,
    output_dir: Path,
    output_override: Path | None,
    calendar_count: int,
) -> Path:
    if output_override is not None:
        if calendar_count != 1:
            raise ValueError("--output kan berre brukast med éin kalender.")
        return output_override
    return output_dir / (calendar.output or f"calendar-{index + 1}.ics")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Hentar BIR-tømmedagar og lagar ei ICS-fil."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("calendars.toml"),
        help="Stad for calendars.toml (standard: calendars.toml).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Stad for ICS-fila. Kan berre brukast med éin kalender.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("dist"),
        help="Mappe for ICS-filer når config har fleire kalendrar.",
    )
    parser.add_argument(
        "--months",
        type=int,
        help=f"Tal på månader som skal dekkjast (standard: {DEFAULT_MONTHS}).",
    )
    parser.add_argument(
        "--min-events",
        type=int,
        default=1,
        help="Krev minst dette talet på tømmedagar før fila vert skriven.",
    )
    args = parser.parse_args(argv)

    try:
        months, calendars = load_config(args.config)
        if args.months is not None:
            months = max(1, args.months)
        if args.output is not None and len(calendars) != 1:
            raise ValueError("--output kan berre brukast med éin kalender.")

        today = date.today()
        horizon_end = horizon_end_for(today, months)
        client = BirClient()
        failed = False

        for index, calendar in enumerate(calendars):
            private_values = [
                calendar.address,
                calendar.r_id or "",
                calendar.calendar_id,
            ]
            try:
                output_path = output_path_for(
                    calendar,
                    index,
                    args.output_dir,
                    args.output,
                    len(calendars),
                )
                private_values.extend([str(output_path), output_path.name])
                previous_content = (
                    output_path.read_text(encoding="utf-8")
                    if output_path.exists()
                    else None
                )

                property_id, official_address = client.resolve_property(
                    calendar.address, calendar.r_id
                )
                private_values.extend([property_id, official_address])

                api_pickups: list[Pickup] = []
                try:
                    api_pickups = client.get_pickups(
                        property_id,
                        first_day_of_month(today),
                        horizon_end,
                    )
                except Exception as exc:
                    warning(
                        "Klarte ikkje å hente BIR-API: "
                        f"{safe_error(exc, private_values)}"
                    )

                schedules: list[Schedule] = []
                try:
                    address_html = client.fetch_text(
                        ADDRESS_PAGE_URL,
                        params={
                            "rId": property_id,
                            "name": official_address,
                        },
                    )
                    schedules = parse_address_page(address_html, today)
                except Exception as exc:
                    warning(
                        "Klarte ikkje å lese adressesida: "
                        f"{safe_error(exc, private_values)}"
                    )

                extra_pickups: list[Pickup] = []
                if not api_pickups:
                    try:
                        calendar_html = client.fetch_text(
                            TOEMMEKALENDER_URL,
                            params={"rId": property_id},
                        )
                        extra_pickups = parse_toemmekalender(
                            calendar_html, today
                        )
                    except Exception as exc:
                        warning(
                            "Klarte ikkje å lese tømmekalenderen: "
                            f"{safe_error(exc, private_values)}"
                        )

                pickups = filter_pickups(
                    project_pickups(
                        api_pickups,
                        schedules,
                        extra_pickups,
                        today,
                        horizon_end,
                    ),
                    calendar.categories,
                )
                if len(pickups) < args.min_events:
                    raise RuntimeError(
                        f"Fann berre {len(pickups)} tømmedagar, "
                        f"kravde {args.min_events}."
                    )

                content = build_calendar(
                    pickups,
                    calendar.calendar_id,
                    previous_content=previous_content,
                )
                validate_private_calendar(
                    content,
                    [
                        *private_values,
                        urljoin(ADDRESS_PAGE_URL, ""),
                    ],
                )
                write_atomic(output_path, content)
                print(f"Skreiv {len(pickups)} tømmedagar.")
            except Exception as exc:
                print(
                    f"Feil for kalender {index + 1}: "
                    f"{safe_error(exc, private_values)}",
                    file=sys.stderr,
                )
                failed = True

        return 1 if failed else 0
    except Exception as exc:
        print(f"Feil: {safe_error(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
