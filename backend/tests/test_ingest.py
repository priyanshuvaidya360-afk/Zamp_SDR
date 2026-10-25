"""T04: CSV ingest + preview. Spec: docs/PROJECT_SPEC.md "Input: the prospect CSV"."""

import pytest
from sqlmodel import Session

from app import ingest
from app.config import settings
from app.ingest import CsvRejected
from app.models import Batch

# The guide's sample CSV, row for row.
GUIDE_SAMPLE = """first_name,last_name,company,title,company_domain,linkedin_url,notes
Priya,Raman,Acme Fintech,VP Finance,acmefintech.com,,
Rahul,Sharma,Infosys,,infosys.com,,
Ananya,Iyer,Stealthco Labs,Founder,stealthco.io,,met at SaaSBoomi
David,Lee,BigCorp Retail,CFO,,https://www.linkedin.com/in/davidlee-cfo,
Meera,Shah,NewCo Payments,Head of Finance Ops,,,
,,Acme Fintech,Controller,acmefintech.com,,
Priya,Raman,Acme Fintech,VP Finance,acmefintech.com,,
"""


def parse(text: str, filename: str = "prospects.csv") -> ingest.ParseResult:
    return ingest.parse_upload(text.encode(), filename)


def statuses(result: ingest.ParseResult) -> list[str]:
    return [r.status for r in result.rows]


def test_clean_file_matches_the_guide_sample():
    result = parse(GUIDE_SAMPLE)
    assert statuses(result) == ["valid", "valid", "valid", "valid", "invalid", "invalid", "skipped"]
    assert result.counts == {"total": 7, "valid": 4, "invalid": 2, "duplicate": 1}
    meera, controller, dup = result.rows[4], result.rows[5], result.rows[6]
    assert meera.reason == "needs a company domain or LinkedIn URL to identify this person"
    assert controller.reason == "missing first and last name"
    assert (dup.reason, dup.duplicate_of) == ("duplicate of row 2", 2)
    assert result.rows[0].row_number == 2  # spreadsheet line numbers: header is line 1
    assert result.rows[0].data["notes"] is None and result.rows[2].data["notes"] == "met at SaaSBoomi"


def test_linkedin_only_row_is_valid_and_normalised():
    david = parse(GUIDE_SAMPLE).rows[3]
    assert david.status == "valid"
    assert david.data["linkedin_url"] == "linkedin.com/in/davidlee-cfo"
    assert david.data["company_domain"] is None


def test_aliased_headers_from_a_crm_export():
    text = (
        "First Name,Last Name,Job Title,Company Name,Website,Person Linkedin Url,"
        "Company Linkedin Url,Work Email,Rep Notes,Bio\n"
        "priya,RAMAN,VP Finance,Acme Fintech,https://www.AcmeFintech.com/about,,"
        "https://linkedin.com/company/acme,Priya.Raman@AcmeFintech.com,warm intro,Loves AP automation\n"
    )
    result = parse(text)
    assert result.mapping["first_name"] == "First Name"
    assert result.mapping["linkedin_url"] == "Person Linkedin Url"  # not the company page
    assert result.mapping["public_bio"] == "Bio"
    row = result.rows[0]
    assert row.status == "valid"
    assert row.data == {
        "first_name": "Priya", "last_name": "Raman", "full_name": "Priya Raman", "company": "Acme Fintech",
        "title": "VP Finance", "company_domain": "acmefintech.com", "linkedin_url": None,
        "email": "priya.raman@acmefintech.com", "notes": "warm intro",
        "public_bio": "Loves AP automation",
    }
    assert row.raw["Company Linkedin Url"] == "https://linkedin.com/company/acme"  # kept for export


def test_bom_and_semicolon_delimiter():
    text = "﻿first_name;last_name;domain\nPriya;Raman;acmefintech.com\n"
    result = parse(text)
    assert result.headers[0] == "first_name"  # BOM stripped, not part of the header
    assert statuses(result) == ["valid"]


def test_full_name_column_is_split_on_the_last_space():
    text = "name,website\nMary Ann Lee,acme.com\nCher,acme.com\n"
    result = parse(text)
    mary, cher = result.rows
    assert (mary.data["first_name"], mary.data["last_name"], mary.name_split) == ("Mary Ann", "Lee", True)
    assert (cher.status, cher.reason) == ("invalid", "missing last name")


def test_missing_last_name_is_invalid_with_reason():
    result = parse("first_name,last_name,company_domain\nPriya,,acmefintech.com\n")
    assert (result.rows[0].status, result.rows[0].reason) == ("invalid", "missing last name")


def test_missing_both_domain_and_linkedin_is_invalid():
    result = parse("first_name,last_name,company,company_domain,linkedin_url\nMeera,Shah,NewCo,,\n")
    row = result.rows[0]
    assert row.status == "invalid"
    assert row.reason == "needs a company domain or LinkedIn URL to identify this person"


def test_linkedin_is_required_when_the_rule_is_on(monkeypatch):
    # Two Priya Sharmas at HDFC Bank: the domain alone can't tell them apart.
    monkeypatch.setattr(settings, "require_linkedin", True)
    result = parse(
        "first_name,last_name,company,company_domain,linkedin_url\n"
        "Priya,Sharma,HDFC Bank,hdfcbank.com,\n"
        "Priya,Sharma,HDFC Bank,,https://in.linkedin.com/in/priya-sharma-hdfc/\n"
        "Rahul,Mehta,HDFC Bank,hdfcbank.com,https://www.linkedin.com/company/hdfc-bank\n"
    )
    first, second, third = result.rows
    assert (first.status, first.reason) == ("invalid", ingest.NEEDS_LINKEDIN)
    assert second.status == "valid" and second.data["linkedin_url"] == "linkedin.com/in/priya-sharma-hdfc"
    assert third.status == "invalid" and "not a personal profile" in third.reason


def test_file_without_linkedin_column_is_rejected_when_the_rule_is_on(monkeypatch):
    monkeypatch.setattr(settings, "require_linkedin", True)
    with pytest.raises(CsvRejected, match="linkedin_url column"):
        parse("first_name,last_name,company_domain\nPriya,Sharma,hdfcbank.com\n")


@pytest.mark.parametrize(
    ("url", "reason_part"),
    [
        ("https://www.linkedin.com/company/acme", "not a personal profile"),
        ("https://twitter.com/priya", "not a linkedin.com address"),
        ("linkedin.com/in/", "not a personal profile"),
    ],
)
def test_malformed_linkedin_url_is_invalid(url, reason_part):
    result = parse(f"first_name,last_name,linkedin_url\nPriya,Raman,{url}\n")
    assert result.rows[0].status == "invalid" and reason_part in result.rows[0].reason


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://in.linkedin.com/in/Priya-Raman-123/?trk=x", "linkedin.com/in/priya-raman-123"),
        ("www.linkedin.com/in/priya%2Draman", "linkedin.com/in/priya-raman"),
        ("linkedin.com/in/priya", "linkedin.com/in/priya"),
    ],
)
def test_linkedin_urls_are_normalised(raw, expected):
    assert ingest.normalise_linkedin(raw) == (expected, None)


@pytest.mark.parametrize(
    ("raw", "expected", "error_part"),
    [
        ("https://www.Acme.com/careers?x=1", "acme.com", None),
        ("acme.co.uk.", "acme.co.uk", None),
        ("acme", None, "not a valid domain"),
        ("acme .com", None, "contains spaces"),
        ("priya@acme.com", None, "looks like an email"),
    ],
)
def test_domains_are_normalised_or_rejected(raw, expected, error_part):
    domain, error = ingest.normalise_domain(raw)
    assert domain == expected
    assert (error is None) if error_part is None else (error_part in error)


def test_in_file_duplicates_by_linkedin_email_or_name_and_domain():
    text = (
        "first_name,last_name,company_domain,linkedin_url,email\n"
        "Priya,Raman,acmefintech.com,,\n"
        "PRIYA,raman,www.acmefintech.com,,\n"  # same person after cleaning
        "Dev,Rao,,linkedin.com/in/devrao,\n"
        "Devendra,Rao,,https://www.linkedin.com/in/DevRao/,\n"  # same profile
        "Sam,Patel,contosologistics.com,,sam@contoso.com\n"
        "Samuel,Patel,contoso.com,,SAM@contoso.com\n"  # same email
        "Priya,Raman,othercompany.com,,\n"  # same name, different company: not a duplicate
    )
    result = parse(text)
    assert statuses(result) == ["valid", "skipped", "valid", "skipped", "valid", "skipped", "valid"]
    assert [r.duplicate_of for r in result.rows] == [None, 2, None, 4, None, 6, None]


def test_invalid_rows_never_count_as_the_first_copy():
    text = "first_name,last_name,company_domain\nPriya,,acme.com\nPriya,Raman,acme.com\nPriya,Raman,acme.com\n"
    assert statuses(parse(text)) == ["invalid", "valid", "skipped"]


def test_malformed_email_is_a_warning_not_an_error():
    row = parse("first_name,last_name,company_domain,email\nPriya,Raman,acme.com,not-an-email\n").rows[0]
    assert row.status == "valid" and row.data["email"] is None
    assert "ignored malformed email" in row.warnings[0]


def test_missing_company_name_is_only_a_warning():
    row = parse("first_name,last_name,company_domain\nPriya,Raman,acme.com\n").rows[0]
    assert row.status == "valid" and "derived" in row.warnings[0]


# --- Real-export quirks (found on a 100-row Saleshandy export) ------------------

SALESHANDY_HEADER = (
    "Name,First Name,Last Name,Valid Emails,Risky Emails,Job Title,Company Name,"
    "LinkedIn URL,Company Website,Company Domain,Company LinkedIn URL\n"
)


def test_saleshandy_export_maps_valid_emails_and_ignores_placeholders():
    text = SALESHANDY_HEADER + (
        '"Philipp Wenger Lebron","Philipp","Lebron","philipp.wenger@get-sides.de"," - ",'
        '"Head of Growth","SIDES","https://www.linkedin.com/in/philippwenger",'
        '"https://www.get-sides.de","get-sides.de","https://www.linkedin.com/company/get-sides"\n'
        '"Sofia Novak","Sofia","Novak"," - "," - ","Head of Growth","sonarly",'
        '"https://www.linkedin.com/in/sofia-novak"," - "," - "," - "\n'
    )
    result = parse(text)
    assert result.mapping["email"] == "Valid Emails"
    assert result.mapping["company_domain"] == "Company Domain"
    philipp, sofia = result.rows
    assert philipp.data["email"] == "philipp.wenger@get-sides.de"
    # " - " means empty: no bogus "invalid domain", no bogus email warning.
    assert sofia.status == "valid" and sofia.warnings == []
    assert sofia.data["email"] is None and sofia.data["company_domain"] is None


def test_full_name_keeps_middle_names_that_first_and_last_drop():
    text = SALESHANDY_HEADER + (
        '"Philipp Wenger Lebron","Philipp","Lebron"," - "," - ","Head of Growth","SIDES",'
        '"linkedin.com/in/philippwenger"," - ","get-sides.de"," - "\n'
        '"Malini Gujral, MBA","Malini","Gujral"," - "," - ","CMO","Celebrus",'
        '"linkedin.com/in/malinigujral"," - ","celebrus.com"," - "\n'
    )
    philipp, malini = parse(text).rows
    assert (philipp.data["last_name"], philipp.data["full_name"]) == ("Lebron", "Philipp Wenger Lebron")
    assert malini.data["full_name"] == "Malini Gujral"


@pytest.mark.parametrize(
    ("full", "expected"),
    [("Malini Gujral, MBA", ("Malini", "Gujral")), ("Jane Doe, PhD, CPA", ("Jane", "Doe")),
     ("Raman, Priya", ("Raman,", "Priya"))],  # not a credential: left for the user to fix
)
def test_credentials_are_stripped_before_splitting(full, expected):
    assert ingest.split_full_name(full) == expected


def test_link_shortener_domain_is_ignored_and_linkedin_carries_identity():
    text = (
        "first_name,last_name,company,company_domain,linkedin_url\n"
        "Akil,Natchimuthu,iCare Software,linktr.ee,linkedin.com/in/akil-natchimuthu\n"
        "Hanna,Andrzejewska,GetResponse,bit.ly,\n"
    )
    akil, hanna = parse(text).rows
    assert akil.status == "valid" and akil.data["company_domain"] is None
    assert "not the company's site" in akil.warnings[0]
    assert hanna.status == "invalid"
    assert hanna.reason == "needs a company domain or LinkedIn URL to identify this person"


def test_initial_only_last_name_is_flagged():
    row = parse("first_name,last_name,company,linkedin_url\nAlice,W,Canny,linkedin.com/in/alice-wong\n").rows[0]
    assert row.status == "valid"
    assert row.warnings == ["last name is only an initial; identity will rely on the LinkedIn URL"]


# --- File-level rejections -----------------------------------------------------


def test_51_rows_is_rejected():
    text = "first_name,last_name,company_domain\n" + "".join(f"P{i},Q,acme.com\n" for i in range(51))
    with pytest.raises(CsvRejected, match="51 prospects; the limit is 50"):
        parse(text)


def test_50_rows_is_accepted():
    text = "first_name,last_name,company_domain\n" + "".join(f"P{i},Q{i},acme.com\n" for i in range(50))
    assert parse(text).counts["valid"] == 50


@pytest.mark.parametrize(
    ("content", "filename", "message"),
    [
        (b"PK\x03\x04\x14\x00binary", "prospects.xlsx", "Excel file"),
        (b"%PDF-1.7 ...", "prospects.pdf", "PDF"),
        ("first_name,last_name\nJos\xe9,P\xe9rez\n".encode("latin-1"), "p.csv", "not UTF-8"),
        (b"", "p.csv", "empty"),
        (b"first_name,last_name,company_domain\n\n\n", "p.csv", "no prospect rows"),
    ],
)
def test_non_csv_and_empty_files_are_rejected(content, filename, message):
    with pytest.raises(CsvRejected, match=message):
        ingest.parse_upload(content, filename)


def test_file_without_name_columns_is_rejected():
    with pytest.raises(CsvRejected, match="Couldn't find name columns"):
        parse("company,company_domain\nAcme,acme.com\n")


def test_file_without_domain_or_linkedin_column_is_rejected():
    with pytest.raises(CsvRejected, match="company_domain or linkedin_url column"):
        parse("first_name,last_name,company\nPriya,Raman,Acme\n")


# --- API ------------------------------------------------------------------------


def upload(client, text: str, filename: str = "prospects.csv"):
    return client.post("/batches", files={"file": (filename, text.encode(), "text/csv")})


def test_post_batches_stores_batch_and_returns_preview(client, session: Session):
    response = upload(client, GUIDE_SAMPLE)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "validating"
    assert body["preview"]["counts"] == {"total": 7, "valid": 4, "invalid": 2, "duplicate": 1}
    assert len(body["preview"]["rows"]) == 7
    assert body["preview"]["unmapped_headers"] == []

    batch = session.get(Batch, body["batch_id"])
    assert (batch.row_count, batch.valid_count, batch.invalid_count, batch.duplicate_count) == (7, 4, 2, 1)
    assert batch.source_rows[0]["first_name"] == "Priya"  # raw rows kept for the export


def test_preview_shows_at_most_20_rows(client):
    text = "first_name,last_name,company_domain\n" + "".join(f"P{i},Q{i},acme.com\n" for i in range(30))
    body = upload(client, text).json()
    assert body["preview"]["counts"]["total"] == 30 and len(body["preview"]["rows"]) == 20


def test_post_batches_rejects_bad_file_with_message(client):
    response = upload(client, "company\nAcme\n")
    assert response.status_code == 422
    assert "Couldn't find name columns" in response.json()["detail"]


def test_patch_mapping_revalidates_rows(client):
    # "Site" is not a known alias, so the domain column is missed at first...
    text = "first_name,last_name,linkedin_url,Site\nPriya,Raman,,acmefintech.com\nDev,Rao,linkedin.com/in/devrao,\n"
    body = upload(client, text).json()
    assert body["preview"]["counts"]["valid"] == 1
    assert body["preview"]["unmapped_headers"] == ["Site"]

    # ...and the user maps it by hand.
    mapping = body["preview"]["mapping"] | {"company_domain": "Site"}
    response = client.patch(f"/batches/{body['batch_id']}/mapping", json={"mapping": mapping})
    assert response.status_code == 200
    assert response.json()["preview"]["counts"]["valid"] == 2
    assert client.get(f"/batches/{body['batch_id']}/preview").json()["preview"]["counts"]["valid"] == 2


def test_patch_mapping_rejects_unknown_column_and_missing_identity(client):
    body = upload(client, GUIDE_SAMPLE).json()
    url = f"/batches/{body['batch_id']}/mapping"
    bad_column = body["preview"]["mapping"] | {"company_domain": "Nope"}
    assert client.patch(url, json={"mapping": bad_column}).status_code == 422
    no_identity = body["preview"]["mapping"] | {"company_domain": None, "linkedin_url": None}
    response = client.patch(url, json={"mapping": no_identity})
    assert response.status_code == 422 and "company_domain or linkedin_url" in response.json()["detail"]


def test_patch_mapping_after_start_is_refused(client, session: Session):
    body = upload(client, GUIDE_SAMPLE).json()
    batch = session.get(Batch, body["batch_id"])
    batch.status = "running"
    session.add(batch)
    session.commit()
    response = client.patch(f"/batches/{batch.id}/mapping", json={"mapping": body["preview"]["mapping"]})
    assert response.status_code == 409


def test_unknown_batch_is_404(client):
    assert client.get("/batches/999/preview").status_code == 404
