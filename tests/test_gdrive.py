"""Google Drive URL parsing + interstitial form parsing (offline fixtures)."""
import pytest

from transcriptor.acquisition.base import VerificationFailed
from transcriptor.acquisition.gdrive import GoogleDriveAdapter, extract_file_id, parse_confirm_form

# Fixture: trimmed replica of the REAL interstitial observed on the test file
# (docs/RESEARCH.md §1) — same structure, same field names.
INTERSTITIAL = """<!DOCTYPE html><html><head><title>Google Drive - Virus scan warning</title></head>
<body><div class="uc-main"><p>Google Drive can't scan this file for viruses.</p>
<span class="uc-name-size"><a href="https://drive.usercontent.google.com/download">BABYMONSTER CHOOM TOUR KYOCERA DAY2.mp4</a> (2.0G)</span>
<form action="https://drive.usercontent.google.com/download" method="get">
<input type="hidden" name="id" value="1VKilIC4400a5aAde0Z7Aag5TVF-uP3YC">
<input type="hidden" name="export" value="download">
<input type="hidden" name="confirm" value="t">
<input type="hidden" name="uuid" value="f9b8510f-7e45-446a-85d2-f56af4a3aa1f">
<input type="submit" id="download-form" value="Download anyway">
</form></div></body></html>"""


@pytest.mark.parametrize("url", [
    "https://drive.google.com/file/d/1VKilIC4400a5aAde0Z7Aag5TVF-uP3YC/view?usp=drivesdk",
    "https://drive.google.com/open?id=1VKilIC4400a5aAde0Z7Aag5TVF-uP3YC",
    "https://drive.google.com/uc?id=1VKilIC4400a5aAde0Z7Aag5TVF-uP3YC&export=download",
    "https://drive.usercontent.google.com/download?id=1VKilIC4400a5aAde0Z7Aag5TVF-uP3YC&export=download",
    "https://docs.google.com/file/d/1VKilIC4400a5aAde0Z7Aag5TVF-uP3YC/edit",
])
def test_extract_file_id_all_url_shapes(url):
    assert extract_file_id(url) == "1VKilIC4400a5aAde0Z7Aag5TVF-uP3YC"


def test_extract_file_id_rejects_non_drive():
    with pytest.raises(VerificationFailed):
        extract_file_id("https://example.com/some/video.mp4")


def test_parse_confirm_form_generic():
    action, params = parse_confirm_form(INTERSTITIAL)
    assert action == "https://drive.usercontent.google.com/download"
    assert params["id"] == "1VKilIC4400a5aAde0Z7Aag5TVF-uP3YC"
    assert params["confirm"] == "t"
    assert params["uuid"] == "f9b8510f-7e45-446a-85d2-f56af4a3aa1f"
    assert params["export"] == "download"


def test_parse_form_raises_on_page_without_form():
    with pytest.raises(VerificationFailed):
        parse_confirm_form("<html><body>Google Drive - download quota exceeded</body></html>")


def test_detect_quota_error():
    with pytest.raises(Exception) as exc:
        GoogleDriveAdapter._detect_html_errors(
            "Sorry, this file is too many users have viewed or downloaded this file recently.", "X")
    assert "quota" in str(exc.value).lower()


def test_detect_access_denied():
    with pytest.raises(Exception) as exc:
        GoogleDriveAdapter._detect_html_errors("You need access to this file. Request access.", "X")
    assert "access" in str(exc.value).lower()


def test_can_handle_domains():
    adapter = GoogleDriveAdapter()
    assert adapter.can_handle("https://drive.google.com/file/d/ABC/view")
    assert adapter.can_handle("https://docs.google.com/uc?id=ABC")
    assert not adapter.can_handle("https://example.com/video.mp4")
