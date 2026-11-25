#!/usr/bin/env python3
"""
quote_check.py - check what a cloud reader returned against the page code saved.

Every value must come with a quote, and the quote must be found in the saved
page text. A value passes only when its quote is in the page, and the value
appears in that quote (number, date, enum word or text). Nothing here calls
the network; it uses the standard library only.

Not a command: cloud_read.py imports it.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

FieldType = Literal["number", "integer", "date", "enum", "text"]
MAX_QUOTE_CHARS = 200
MAX_OUTPUT_BYTES = 64 * 1024
MAX_FIELDS = 40
MAX_PIECES = 3
MAX_GAP_CHARS = 150
RUN_STATUSES = ("done", "not_found", "blocked_login", "blocked_challenge")
FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
ELLIPSIS_RE = re.compile(r"…|\.\.\.")
FIELD_TYPES = ("number", "integer", "date", "enum", "text")
ALLOWED_FIELD_KEYS = ("name", "type", "unit", "min", "max", "enum", "pattern", "max_length")


@dataclass(frozen=True)
class FieldSpec:
    name: str
    type: FieldType
    unit: str                              # "" when the field has none
    minimum: float | None                  # number, integer
    maximum: float | None
    enum_words: dict[str, tuple[str, ...]]  # enum: value -> the words that mean it
    pattern: str                           # text; "" when none
    max_length: int                        # text; 200 by default


class SchemaError(Exception):
    """A schema file that is unreadable or not in the allowed shape."""


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _field_from(entry: object) -> FieldSpec:
    if not isinstance(entry, dict):
        raise SchemaError("a field is not an object")
    for key in entry:
        if key not in ALLOWED_FIELD_KEYS:
            raise SchemaError(f"unknown field key {key}")
    name = entry.get("name")
    if not isinstance(name, str) or not FIELD_NAME_RE.match(name):
        raise SchemaError(f"bad field name {name}")
    field_type = entry.get("type")
    if field_type not in FIELD_TYPES:
        raise SchemaError(f"{name}: unknown type {field_type}")
    unit = entry.get("unit", "")
    if not isinstance(unit, str):
        raise SchemaError(f"{name}: unit must be text")
    minimum = entry.get("min")
    maximum = entry.get("max")
    for bound in (minimum, maximum):
        if bound is not None and not _is_number(bound):
            raise SchemaError(f"{name}: min and max must be numbers")
    enum_words: dict[str, tuple[str, ...]] = {}
    if field_type == "enum":
        raw_enum = entry.get("enum")
        if not isinstance(raw_enum, dict) or not raw_enum:
            raise SchemaError(f"{name}: an enum field needs its enum")
        for value, words in raw_enum.items():
            if not isinstance(words, list) or not words or not all(isinstance(word, str) for word in words):
                raise SchemaError(f"{name}: enum {value} needs a list of words")
            enum_words[value] = tuple(words)
    pattern = entry.get("pattern", "")
    if not isinstance(pattern, str):
        raise SchemaError(f"{name}: pattern must be text")
    try:
        re.compile(pattern)
    except re.error:
        raise SchemaError(f"{name}: pattern does not compile") from None
    max_length = entry.get("max_length", MAX_QUOTE_CHARS)
    if not isinstance(max_length, int) or isinstance(max_length, bool) or max_length < 1:
        raise SchemaError(f"{name}: max_length must be a whole number")
    return FieldSpec(name=name, type=field_type, unit=unit, minimum=minimum, maximum=maximum,
                     enum_words=enum_words, pattern=pattern, max_length=max_length)


def load_schema(path: Path) -> list[FieldSpec]:
    """The fields in a schema file. Raises SchemaError for anything outside the shape."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SchemaError(f"{path.name} is not readable JSON") from None
    if not isinstance(document, dict):
        raise SchemaError("the schema is not an object")
    for key in document:
        if key not in ("job", "fields"):
            raise SchemaError(f"unknown schema key {key}")
    entries = document.get("fields")
    if not isinstance(entries, list) or not entries:
        raise SchemaError("the schema has no fields")
    if len(entries) > MAX_FIELDS:
        raise SchemaError(f"more than {MAX_FIELDS} fields")
    fields = [_field_from(entry) for entry in entries]
    names = [field.name for field in fields]
    if len(set(names)) != len(names):
        raise SchemaError("a field name is repeated")
    return fields


def output_schema(fields: list[FieldSpec]) -> dict[str, object]:
    """The JSON Schema sent as the run's outputSchema."""
    field_properties: dict[str, object] = {}
    for field in fields:
        if field.type == "number":
            value_schema: dict[str, object] = {"type": "number"}
        elif field.type == "integer":
            value_schema = {"type": "integer"}
        elif field.type == "enum":
            value_schema = {"type": "string", "enum": list(field.enum_words)}
        else:
            value_schema = {"type": "string"}
        unit_schema: dict[str, object] = {"type": "string"}
        if field.unit != "":
            unit_schema = {"type": "string", "enum": [field.unit]}
        found = {"type": "object",
                 "properties": {"value": value_schema, "unit": unit_schema,
                                "quote": {"type": "string", "maxLength": MAX_QUOTE_CHARS}},
                 "required": ["value", "quote"], "additionalProperties": False}
        not_found = {"type": "object", "properties": {"not_found": {"const": True}},
                     "required": ["not_found"], "additionalProperties": False}
        field_properties[field.name] = {"anyOf": [found, not_found]}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["status", "page_url", "injection_seen", "fields"],
        "properties": {
            "status": {"type": "string", "enum": list(RUN_STATUSES)},
            "page_url": {"type": "string"},
            "injection_seen": {"type": "boolean"},
            "fields": {"type": "object", "additionalProperties": False,
                       "required": [field.name for field in fields], "properties": field_properties},
        },
    }


QUOTE_MARKS = {"‘": "'", "’": "'", "‚": "'", "′": "'",
               "“": '"', "”": '"', "„": '"', "″": '"'}


def normalize(text: str) -> str:
    """Compare-friendly text: NFKC, straight quotes, single plain spaces, casefolded."""
    text = unicodedata.normalize("NFKC", text)
    characters = []
    for character in text:
        if character in QUOTE_MARKS:
            characters.append(QUOTE_MARKS[character])
        elif character.isspace() or unicodedata.category(character) == "Zs" or character == "\u200b":
            characters.append(" ")
        else:
            characters.append(character)
    collapsed = re.sub(r" +", " ", "".join(characters)).strip()
    return collapsed.casefold()


@dataclass
class FieldResult:
    name: str
    outcome: Literal["accepted", "not_found", "rejected"]
    value: object
    unit: str
    quote: str
    rule: str    # "" unless rejected


@dataclass
class RecordResult:
    valid: bool
    rule: str                  # "", "shape" or "too-large"
    run_status: str
    page_url: str
    injection_seen: bool
    fields: list[FieldResult]
    unrequested: int


# -- where the quote sits in the page ------------------------------------------------


def _quote_pieces(quote: str) -> list[str]:
    pieces = [normalize(piece) for piece in ELLIPSIS_RE.split(quote)]
    return [piece for piece in pieces if piece != ""]


def _occurrences(page: str, piece: str, first_start: int, last_start: int) -> list[int]:
    """Every start of piece in page between first_start and last_start (both inclusive)."""
    starts = []
    position = page.find(piece, first_start)
    while position != -1 and position <= last_start:
        starts.append(position)
        position = page.find(piece, position + 1)
    return starts


def _pieces_in_page(pieces: list[str], page: str, first_start: int, last_start: int) -> bool:
    if not pieces:
        return True
    piece = pieces[0]
    for start in _occurrences(page, piece, first_start, last_start):
        end = start + len(piece)
        if _pieces_in_page(pieces[1:], page, end, end + MAX_GAP_CHARS):
            return True
    return False


def quote_in_page(quote: str, normalized_page: str) -> bool:
    pieces = _quote_pieces(quote)
    if not pieces or len(pieces) > MAX_PIECES:
        return False
    return _pieces_in_page(pieces, normalized_page, 0, len(normalized_page))


# -- the value appears in the quote ---------------------------------------------------

NUMBER_TOKEN_RE = re.compile(r"(?<![\d.,])(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?!\d|,\d|\.\d)")
MONTHS = {"jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
          "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9,
          "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12}
SLASH_DATE_RE = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})(?![\d/])")
ISO_DATE_RE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
WORD_DATE_RE = re.compile(r"(?<![a-z])([a-z]{3,9})\.? (\d{1,2}),? (\d{4})(?!\d)")


def _to_decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _number_in_quote(value: object, normalized_quote: str) -> bool:
    wanted = _to_decimal(value)
    if wanted is None:
        return False
    for token in NUMBER_TOKEN_RE.findall(normalized_quote):
        if Decimal(token.replace(",", "")) == wanted:
            return True
    return False


def _real_day(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _dates_in_quote(normalized_quote: str) -> list[date]:
    found: list[date] = []
    for month_text, day_text, year_text in SLASH_DATE_RE.findall(normalized_quote):
        year = int(year_text)
        if len(year_text) == 2:
            year = 2000 + year
        found.append(_real_day(year, int(month_text), int(day_text)))
    for year_text, month_text, day_text in ISO_DATE_RE.findall(normalized_quote):
        found.append(_real_day(int(year_text), int(month_text), int(day_text)))
    for month_name, day_text, year_text in WORD_DATE_RE.findall(normalized_quote):
        if month_name in MONTHS:
            found.append(_real_day(int(year_text), MONTHS[month_name], int(day_text)))
    return [day for day in found if day is not None]


def _whole_word_in(word: str, text: str) -> bool:
    pattern = r"(?<!\w)" + re.escape(normalize(word)) + r"(?!\w)"
    return re.search(pattern, text) is not None


def _value_in_quote(field: FieldSpec, value: object, quote: str) -> bool:
    normalized_quote = normalize(quote)
    if field.type in ("number", "integer"):
        return _number_in_quote(value, normalized_quote)
    if field.type == "date":
        wanted = date.fromisoformat(str(value))
        return wanted in _dates_in_quote(normalized_quote)
    if field.type == "enum":
        words = field.enum_words.get(str(value), ())
        return any(_whole_word_in(word, normalized_quote) for word in words)
    return normalize(str(value)) in normalized_quote


# -- one field ------------------------------------------------------------------------


def _type_problem(field: FieldSpec, value: object) -> bool:
    """True when value is not a legal value for field's type."""
    if field.type == "integer":
        return not isinstance(value, int) or isinstance(value, bool)
    if field.type == "number":
        return not _is_number(value) or not math.isfinite(value)
    if field.type == "date":
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return True
        return _real_day(int(value[0:4]), int(value[5:7]), int(value[8:10])) is None
    if field.type == "enum":
        return not isinstance(value, str) or value not in field.enum_words
    if not isinstance(value, str) or len(value) > field.max_length:
        return True
    return field.pattern != "" and re.search(field.pattern, value) is None


def _out_of_range(field: FieldSpec, value: object) -> bool:
    if field.type not in ("number", "integer"):
        return False
    if field.minimum is not None and value < field.minimum:
        return True
    return field.maximum is not None and value > field.maximum


def _first_broken_rule(field: FieldSpec, entry: dict[str, object], normalized_page: str) -> str:
    value = entry.get("value")
    if _type_problem(field, value):
        return "type"
    unit = entry.get("unit", "")
    if not isinstance(unit, str) or unit not in ("", field.unit):
        return "unit"
    if _out_of_range(field, value):
        return "range"
    quote = entry.get("quote")
    if not isinstance(quote, str) or quote.strip() == "":
        return "quote-missing"
    if len(quote) > MAX_QUOTE_CHARS:
        return "quote-long"
    if not quote_in_page(quote, normalized_page):
        return "quote-not-in-page"
    if not _value_in_quote(field, value, quote):
        return "quote-value"
    return ""


def _check_field(field: FieldSpec, entry: object, normalized_page: str) -> FieldResult:
    if entry is None or entry == {"not_found": True}:
        return FieldResult(field.name, "not_found", None, "", "", "")
    if not isinstance(entry, dict):
        return FieldResult(field.name, "rejected", None, "", "", "type")
    rule = _first_broken_rule(field, entry, normalized_page)
    if rule != "":
        return FieldResult(field.name, "rejected", entry.get("value"), "", "", rule)
    return FieldResult(field.name, "accepted", entry.get("value"), field.unit, str(entry.get("quote")), "")


# -- the record -----------------------------------------------------------------------


def _bad_record(rule: str, status: object = "", page_url: object = "") -> RecordResult:
    return RecordResult(False, rule, status if isinstance(status, str) else "",
                        page_url if isinstance(page_url, str) else "", False, [], 0)


def check_record(output: object, fields: list[FieldSpec], page_text: str) -> RecordResult:
    """Check the run's output against the fields asked for and the saved page text."""
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except ValueError:
            return _bad_record("shape")
    try:
        encoded = json.dumps(output).encode("utf-8")
    except (TypeError, ValueError):
        return _bad_record("shape")
    if len(encoded) > MAX_OUTPUT_BYTES:
        return _bad_record("too-large")
    if not isinstance(output, dict) or set(output) != {"status", "page_url", "injection_seen", "fields"}:
        return _bad_record("shape")
    status = output["status"]
    page_url = output["page_url"]
    injection_seen = output["injection_seen"]
    returned_fields = output["fields"]
    if (not isinstance(status, str) or status not in RUN_STATUSES or not isinstance(page_url, str)
            or not isinstance(injection_seen, bool) or not isinstance(returned_fields, dict)):
        return _bad_record("shape", status, page_url)
    normalized_page = normalize(page_text)
    results = [_check_field(field, returned_fields.get(field.name), normalized_page) for field in fields]
    asked_names = {field.name for field in fields}
    unrequested = len([name for name in returned_fields if name not in asked_names])
    return RecordResult(True, "", status, page_url, injection_seen, results, unrequested)
