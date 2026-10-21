import os

import pytest
from yt_dlp import YoutubeDL

from app import downloader
from app.downloader import (
    DownloadFailed,
    DownloadRequest,
    _progress_hook,
    build_options,
    download_audio,
    friendly_error,
    remove_partial_files,
)


def make_request(tmp_path, fmt="mp3"):
    return DownloadRequest(job_id="job1", url="https://youtu.be/x", audio_format=fmt, bitrate=192, out_dir=tmp_path)


def test_progress_hook_reports_percent_and_conversion():
    events = []
    hook = _progress_hook(lambda p, stage: events.append((round(p), stage)))
    hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 200})
    hook({"status": "downloading", "downloaded_bytes": 10})  # không biết tổng -> bỏ qua
    hook({"status": "finished"})
    assert events == [(25, "downloading"), (99, "converting")]


def test_options_extract_audio_single_video(tmp_path):
    opts = build_options(make_request(tmp_path, "m4a"), lambda *_: None)
    assert opts["noplaylist"] is True and opts["playlist_items"] == "1"
    assert opts["postprocessors"][0] == {"key": "FFmpegExtractAudio", "preferredcodec": "m4a", "preferredquality": "192"}
    assert opts["outtmpl"].endswith("job1.%(ext)s")
    assert opts["socket_timeout"] > 0 and opts["max_filesize"] > 0
    assert "cookiefile" not in opts


def test_only_single_video_extractors_are_loaded(tmp_path):
    ydl = YoutubeDL(build_options(make_request(tmp_path), lambda *_: None))
    assert sorted(ydl._ies) == ["Facebook", "FacebookPluginsVideo", "FacebookReel", "Youtube", "YoutubeYtBe"]


def test_match_filter_rejects_live_and_too_long(tmp_path):
    match = build_options(make_request(tmp_path), lambda *_: None)["match_filter"]
    assert match({"is_live": False, "duration": 600}, incomplete=False) is None
    assert match({"is_live": True, "duration": 600}, incomplete=False) is not None
    assert match({"is_live": False, "duration": 10**6}, incomplete=False) is not None


def test_each_job_uses_private_copy_of_cookie_file(tmp_path, monkeypatch):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")
    monkeypatch.setattr(downloader, "COOKIES_FILE", str(cookies))
    seen = {}

    def fake_ydl(opts):
        seen["path"] = opts["cookiefile"]
        assert open(opts["cookiefile"]).read().startswith("# Netscape")
        return FakeYDL(opts, info={"title": "t"})

    monkeypatch.setattr(downloader, "YoutubeDL", fake_ydl)
    download_audio(make_request(tmp_path), lambda *_: None)
    assert seen["path"] != str(cookies)
    assert not os.path.exists(seen["path"])  # bản sao đã được xoá


def test_remove_partial_files_only_touches_job_files(tmp_path):
    (tmp_path / "job1.webm.part").write_text("x")
    (tmp_path / "job1.mp3").write_text("x")
    (tmp_path / "other.mp3").write_text("x")
    remove_partial_files(make_request(tmp_path))
    assert [p.name for p in tmp_path.iterdir()] == ["other.mp3"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("\x1b[0;31mERROR:\x1b[0m Private video", "Video riêng tư"),
        ("ERROR: Unsupported URL: https://facebook.com/", "Link không được hỗ trợ"),
        ("ERROR: No suitable extractor found for URL https://youtube.com/@x", "không nhận playlist/kênh"),
        ("Live stream does not pass filter (!is_live)", "livestream"),
        ("File is larger than max-filesize", "quá lớn"),
        ("ERROR: something else", "something else"),
    ],
)
def test_friendly_error(raw, expected):
    message = friendly_error(Exception(raw))
    assert expected in message and "\x1b" not in message


class FakeYDL:
    def __init__(self, opts, create_file=True, info=None):
        self.opts, self.create_file, self.info = opts, create_file, info

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download):
        if self.create_file:
            path = self.opts["outtmpl"].replace("%(ext)s", "mp3")
            open(path, "wb").close()
        return self.info


def test_download_returns_result_from_playlist_entry(tmp_path, monkeypatch):
    info = {"entries": [None, {"title": "Bài hát", "duration": 61}]}
    monkeypatch.setattr(downloader, "YoutubeDL", lambda opts: FakeYDL(opts, info=info))
    result = download_audio(make_request(tmp_path), lambda *_: None)
    assert result.title == "Bài hát" and result.duration == 61
    assert result.file_path == tmp_path / "job1.mp3"


def test_download_raises_when_no_file_created(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader, "YoutubeDL", lambda opts: FakeYDL(opts, create_file=False, info={}))
    with pytest.raises(DownloadFailed):
        download_audio(make_request(tmp_path), lambda *_: None)
