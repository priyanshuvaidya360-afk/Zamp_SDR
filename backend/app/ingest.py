"""CSV intake: header mapping, cleaning, file-level and row-level checks, in-file dedupe.

Spec: docs/GUIDE.md "Input: the prospect CSV" and T04. Pure functions, no database and no
API credits: everything here runs before the user clicks "Run N prospects".

Identity rule: every row needs first name, last name, and a company domain or LinkedIn
URL, because a company name alone is not a unique identity.
"""

import csv
import io
import re
from typing import Any, Literal
from urllib.parse import unquote, urlparse

import pandas as pd
from pydantic import BaseModel

MAX_ROWS = 50  # demo-scale cap (ASSUMPTIONS.md)
MAX_BYTES = 1_000_000
PREVIEW_ROWS = 20

RowStatus = Literal["valid", "invalid", "skipped"]

# Canonical field -> accepted headers, compared after normalising (lowercase, non-alphanumerics
# to "_"). Matching is exact, so e.g. Apollo's "Company Linkedin Url" never maps to the person.
ALIASES: dict[str, list[str]] = {
    "first_name": ["first_name", "first", "firstname", "given_name"],
    "last_name": ["last_name", "last", "lastname", "surname", "family_name"],
    "full_name": ["full_name", "name", "prospect", "contact", "contact_name", "prospect_name"],
    "company_domain": ["company_domain", "website", "domain", "company_url", "company_website"],
    "linkedin_url": ["linkedin_url", "linkedin", "profile_url", "person_linkedin_url", "linkedin_profile"],
    "company": ["company", "company_name", "account", "account_name", "organization", "organisation"],
    "title": ["title", "job_title", "role", "position"],
    "email": ["email", "work_email", "email_address"],
    "notes": ["notes", "rep_notes", "context"],
    "public_bio": ["public_bio", "bio", "about"],  # untrusted, like scraped web text
}
FIELDS = list(ALIASES)

DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class CsvRejected(Exception):
    """The whole file is unusable. The message is shown to the user as-is."""


class RowResult(BaseModel):
    row_number: int  # line in the file, as a spreadsheet shows it (header is line 1)
    status: RowStatus
    reason: str | None = None
    warnings: list[str] = []
    duplicate_of: int | None = None
    name_split: bool = False  # first/last came from splitting a full-name column
    data: dict[str, str | None]  # cleaned canonical fields
    raw: dict[str, str]  # the original row, every column, for the export


class ParseResult(BaseModel):
    headers: list[str]
    source_rows: list[dict[str, str]]
    mapping: dict[str, str | None]
    rows: list[RowResult]

    @property
    def counts(self) -> dict[str, int]:
        return {
            "total": len(self.rows),
            "valid": sum(r.status == "valid" for r in self.rows),
            "invalid": sum(r.status == "invalid" for r in self.rows),
            "duplicate": sum(r.status == "skipped" for r in self.rows),
        }

    def preview(self) -> dict[str, Any]:
        mapped = {h for h in self.mapping.values() if h}
        return {
            "mapping": self.mapping,
            "headers": self.headers,
            "unmapped_headers": [h for h in self.headers if h not in mapped],
            "counts": self.counts,
            "rows": [r.model_dump() for r in self.rows[:PREVIEW_ROWS]],
        }


# --- File level ---------------------------------------------------------------


def _normalise_header(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", header.strip().lower()).strip("_")


def detect_mapping(headers: list[str]) -> dict[str, str | None]:
    """Canonical field -> the first header that matches one of its aliases."""
    by_norm: dict[str, str] = {}
    for h in headers:
        by_norm.setdefault(_normalise_header(h), h)
    mapping: dict[str, str | None] = {}
    for field, aliases in ALIASES.items():
        mapping[field] = next((by_norm[a] for a in aliases if a in by_norm), None)
    return mapping


def read_csv(content: bytes, filename: str = "") -> tuple[list[str], list[dict[str, str]]]:
    """Decode and parse the upload. Raises CsvRejected with a user-facing message."""
    if len(content) > MAX_BYTES:
        raise CsvRejected("File is larger than 1 MB. Upload at most 50 prospects per file.")
    if content.startswith(b"PK\x03\x04") or filename.lower().endswith((".xlsx", ".xls")):
        raise CsvRejected("This looks like an Excel file. Export it as CSV (UTF-8) and upload that.")
    if content.startswith(b"%PDF"):
        raise CsvRejected("This is a PDF, not a CSV. Upload a .csv file.")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CsvRejected(
            "File is not UTF-8 text. In Excel or Google Sheets, save it as 'CSV UTF-8' and try again."
        ) from None
    if "\x00" in text:
        raise CsvRejected("File is not a text CSV. Upload a .csv file.")
    if not text.strip():
        raise CsvRejected("The file is empty.")

    try:
        delimiter = csv.Sniffer().sniff(text[:4096], delimiters=",;\t").delimiter
    except csv.Error:
        delimiter = ","
    try:
        frame = pd.read_csv(
            io.StringIO(text), sep=delimiter, dtype=str, keep_default_na=False,
            skip_blank_lines=True,
        )
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as error:
        raise CsvRejected(f"Couldn't read this file as a CSV ({error}).") from None

    headers = [str(h).strip() for h in frame.columns]
    frame.columns = headers
    rows = [
        {h: str(v) for h, v in record.items()}
        for record in frame.to_dict(orient="records")
        if any(str(v).strip() for v in record.values())
    ]
    if not rows:
        raise CsvRejected("The file has a header but no prospect rows.")
    if len(rows) > MAX_ROWS:
        raise CsvRejected(f"The file has {len(rows)} prospects; the limit is {MAX_ROWS} per upload.")
    return headers, rows


def check_mapping(mapping: dict[str, str | None], headers: list[str]) -> None:
    """File-level identity checks on a (detected or user-corrected) mapping."""
    unknown_fields = set(mapping) - set(FIELDS)
    if unknown_fields:
        raise CsvRejected(f"Unknown field(s) in mapping: {', '.join(sorted(unknown_fields))}.")
    missing_headers = [h for h in mapping.values() if h and h not in headers]
    if missing_headers:
        raise CsvRejected(f"Mapping points to column(s) not in the file: {', '.join(missing_headers)}.")
    has_names = (mapping.get("first_name") and mapping.get("last_name")) or mapping.get("full_name")
    if not has_names:
        raise CsvRejected(
            "Couldn't find name columns. Expected first_name and last_name "
            "(or a single full_name column)."
        )
    if not (mapping.get("company_domain") or mapping.get("linkedin_url")):
        raise CsvRejected(
            "Couldn't find a company_domain or linkedin_url column. Each prospect needs one of "
            "them, because a company name alone is not a unique identity."
        )


# --- Cleaning -----------------------------------------------------------------


def _clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def clean_name(value: str) -> str:
    """Fix ALL CAPS / all lowercase names; leave mixed case (McDonald, van der Berg) alone."""
    value = _clean(value)
    if value and (value.isupper() or value.islower()):
        return " ".join(part[:1].upper() + part[1:].lower() for part in value.split(" "))
    return value


def split_full_name(full: str) -> tuple[str, str]:
    """Split on the last space: 'Mary Ann Lee' -> ('Mary Ann', 'Lee')."""
    full = _clean(full)
    if " " not in full:
        return full, ""
    first, last = full.rsplit(" ", 1)
    return first, last


def normalise_domain(value: str) -> tuple[str | None, str | None]:
    """(domain, error). Strips scheme, www., path, port; lowercases."""
    raw = _clean(value).lower()
    if not raw:
        return None, None
    if " " in raw:
        return None, f"company domain '{value.strip()}' contains spaces"
    if "@" in raw:
        return None, f"company domain '{value.strip()}' looks like an email address"
    host = urlparse(raw if "://" in raw else f"//{raw}").hostname or ""
    host = host.rstrip(".").removeprefix("www.")
    if not DOMAIN_RE.match(host):
        return None, f"company domain '{value.strip()}' is not a valid domain (e.g. acme.com)"
    return host, None


def normalise_linkedin(value: str) -> tuple[str | None, str | None]:
    """(linkedin.com/in/<slug>, error). Accepts any scheme / subdomain / trailing junk."""
    raw = _clean(value)
    if not raw:
        return None, None
    parsed = urlparse(raw if "://" in raw else f"//{raw}")
    host = (parsed.hostname or "").lower()
    parts = [p for p in unquote(parsed.path).split("/") if p]
    if not (host == "linkedin.com" or host.endswith(".linkedin.com")):
        return None, f"LinkedIn URL '{raw}' is not a linkedin.com address"
    if len(parts) < 2 or parts[0].lower() != "in":
        return None, f"LinkedIn URL '{raw}' is not a personal profile (expected linkedin.com/in/...)"
    return f"linkedin.com/in/{parts[1].lower()}", None


# --- Row level ----------------------------------------------------------------


def validate_rows(
    source_rows: list[dict[str, str]], mapping: dict[str, str | None]
) -> list[RowResult]:
    def get(raw: dict[str, str], field: str) -> str:
        header = mapping.get(field)
        return _clean(raw.get(header, "")) if header else ""

    results: list[RowResult] = []
    seen: dict[tuple, int] = {}  # dedupe key -> row_number of the first valid copy
    use_full_name = not (mapping.get("first_name") and mapping.get("last_name"))

    for index, raw in enumerate(source_rows):
        row_number = index + 2  # header is line 1
        warnings: list[str] = []
        problems: list[str] = []

        if use_full_name:
            first, last = split_full_name(get(raw, "full_name"))
        else:
            first, last = get(raw, "first_name"), get(raw, "last_name")
        first, last = clean_name(first), clean_name(last)

        domain, domain_error = normalise_domain(get(raw, "company_domain"))
        linkedin, linkedin_error = normalise_linkedin(get(raw, "linkedin_url"))
        email = get(raw, "email").lower() or None
        if email and not EMAIL_RE.match(email):
            warnings.append(f"ignored malformed email '{email}'")
            email = None
        company = get(raw, "company") or None

        if not first and not last:
            problems.append("missing first and last name")
        elif not first:
            problems.append("missing first name")
        elif not last:
            problems.append("missing last name")
        if domain_error:
            problems.append(domain_error)
        if linkedin_error:
            problems.append(linkedin_error)
        if not (domain or linkedin) and not (domain_error or linkedin_error):
            problems.append("needs a company domain or LinkedIn URL to identify this person")
        if not company and not problems:
            warnings.append("no company name; it will be derived from the domain or LinkedIn")

        data = {
            "first_name": first or None,
            "last_name": last or None,
            "company": company,
            "title": get(raw, "title") or None,
            "company_domain": domain,
            "linkedin_url": linkedin,
            "email": email,
            "notes": get(raw, "notes") or None,
            "public_bio": get(raw, "public_bio") or None,
        }
        result = RowResult(
            row_number=row_number,
            status="invalid" if problems else "valid",
            reason="; ".join(problems) or None,
            warnings=warnings,
            name_split=use_full_name and bool(first or last),
            data=data,
            raw=raw,
        )

        if result.status == "valid":
            keys = []
            if linkedin:
                keys.append(("linkedin", linkedin))
            if email:
                keys.append(("email", email))
            if domain:
                keys.append(("person", first.lower(), last.lower(), domain))
            first_copy = next((seen[k] for k in keys if k in seen), None)
            if first_copy is not None:
                result.status = "skipped"
                result.duplicate_of = first_copy
                result.reason = f"duplicate of row {first_copy}"
            else:
                for key in keys:
                    seen[key] = row_number
        results.append(result)
    return results


# --- Entry points -------------------------------------------------------------


def parse_upload(content: bytes, filename: str = "") -> ParseResult:
    headers, source_rows = read_csv(content, filename)
    mapping = detect_mapping(headers)
    check_mapping(mapping, headers)
    return ParseResult(
        headers=headers, source_rows=source_rows, mapping=mapping,
        rows=validate_rows(source_rows, mapping),
    )


def revalidate(
    headers: list[str], source_rows: list[dict[str, str]], mapping: dict[str, str | None]
) -> ParseResult:
    """Re-run the row checks with a user-corrected mapping."""
    mapping = {field: mapping.get(field) or None for field in FIELDS}
    check_mapping(mapping, headers)
    return ParseResult(
        headers=headers, source_rows=source_rows, mapping=mapping,
        rows=validate_rows(source_rows, mapping),
    )
