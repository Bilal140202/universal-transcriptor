"""Magic-byte / HTML / completeness verification tests (pure logic)."""
import struct

from transcriptor.acquisition.verify import (
    looks_like_html,
    quarantine,
    sniff_magic,
    verify_download,
)


def _mp4_head() -> bytes:
    # minimal ftyp box: size(4) + 'ftyp' + brand
    box = struct.pack(">I", 24) + b"ftyp" + b"isom" + b"\x00\x00\x02\x00" + b"isomiso2mp41"
    return box + b"\x00" * 128


def _ebml_head() -> bytes:
    return b"\x1a\x45\xdf\xa3" + b"\x00" * 128


def test_sniff_mp4(tmp_path):
    p = tmp_path / "video.mp4"
    p.write_bytes(_mp4_head() + b"\x00" * 2048)
    assert any("ftyp" in name for name in sniff_magic(p))


def test_sniff_mkv(tmp_path):
    p = tmp_path / "video.mkv"
    p.write_bytes(_ebml_head())
    assert sniff_magic(p) == ["mkv/webm (EBML)"]


def test_html_detected_by_body(tmp_path):
    p = tmp_path / "page.html"
    p.write_bytes(b"<!DOCTYPE html><html><body>Google Drive - Virus scan warning</body></html>")
    assert looks_like_html(p.read_bytes()[:512])
    result = verify_download(p)
    assert not result.ok
    assert any("HTML" in r for r in result.reasons)


def test_html_detected_by_content_type(tmp_path):
    p = tmp_path / "fake.mp4"
    p.write_bytes(b"<html>some error page but content-type says video</html>")
    assert looks_like_html(p.read_bytes()[:512], content_type="text/html; charset=utf-8")


def test_verify_passes_on_valid_mp4(tmp_path):
    p = tmp_path / "video.mp4"
    p.write_bytes(_mp4_head() + b"\x00" * (4 << 20))
    result = verify_download(p, content_type="video/mp4", content_length=p.stat().st_size,
                             expected_ext="mp4")
    assert result.ok, result.reasons
    assert result.checks["completeness"].startswith("pass")
    assert result.checks["magic"].startswith("pass")


def test_verify_fails_incomplete_download(tmp_path):
    p = tmp_path / "video.mp4"
    p.write_bytes(_mp4_head() + b"\x00" * 4096)
    result = verify_download(p, content_length=10 << 20)
    assert not result.ok
    assert any("Incomplete" in r for r in result.reasons)


def test_verify_fails_on_unknown_binary(tmp_path):
    p = tmp_path / "mystery.bin"
    p.write_bytes(b"\x00\x01\x02\x03" * 4096)
    result = verify_download(p)
    assert not result.ok
    assert any("container signature" in r for r in result.reasons)


def test_quarantine_moves_file(tmp_path):
    p = tmp_path / "bad.mp4"
    p.write_bytes(b"junk")
    q = quarantine(p, "test reason")
    assert q.exists() and not p.exists()
    assert "test reason" in (tmp_path / "_quarantine" / "bad.mp4.reason.txt").read_text()
