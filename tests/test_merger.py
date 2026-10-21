import subprocess

import pytest

from app.merger import MergeFailed, build_merge_command, merge_audio


def test_command_without_gap_concats_all_inputs(tmp_path):
    inputs = [tmp_path / "a.mp3", tmp_path / "b.mp3", tmp_path / "c.mp3"]
    cmd = build_merge_command("ffmpeg", inputs, tmp_path / "out.mp3", "mp3", 192)
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "[a0][a1][a2]concat=n=3:v=0:a=1[out]" in graph
    assert "anullsrc" not in " ".join(cmd)
    assert cmd[-3:] == ["-b:a", "192k", str(tmp_path / "out.mp3")]


def test_command_with_gap_inserts_silence_between_items(tmp_path):
    inputs = [tmp_path / "a.mp3", tmp_path / "b.mp3", tmp_path / "c.mp3"]
    cmd = build_merge_command("ffmpeg", inputs, tmp_path / "out.wav", "wav", 192, gap_seconds=1.5)
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "[a0][g0][a1][g1][a2]concat=n=5" in graph
    assert cmd.count("anullsrc=r=44100:cl=stereo") == 2
    assert "-b:a" not in cmd  # wav không dùng bitrate


def test_command_requires_two_inputs(tmp_path):
    with pytest.raises(ValueError):
        build_merge_command("ffmpeg", [tmp_path / "a.mp3"], tmp_path / "o.mp3", "mp3", 128)


def test_merges_real_files_with_different_formats(tmp_path, tone, duration_of):
    a = tone(tmp_path / "a.mp3", 1.0)
    b = tone(tmp_path / "b.m4a", 2.0, frequency=660, sample_rate=22050, channels=1)
    out = merge_audio([a, b], tmp_path / "out" / "merged.mp3", "mp3", 128, gap_seconds=0.5)
    assert out.exists()
    assert duration_of(out) == pytest.approx(3.5, abs=0.15)


def test_merge_fails_when_source_missing(tmp_path, tone):
    a = tone(tmp_path / "a.mp3", 0.5)
    with pytest.raises(MergeFailed, match="Thiếu 1 file") as info:
        merge_audio([a, tmp_path / "missing.mp3"], tmp_path / "o.mp3", "mp3", 128)
    assert str(tmp_path) not in str(info.value)  # không lộ đường dẫn server


def test_merge_failure_hides_stderr_and_removes_partial_output(tmp_path):
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"not audio")
    out = tmp_path / "o.mp3"
    with pytest.raises(MergeFailed, match="ffmpeg không gộp được") as info:
        merge_audio([bad, bad], out, "mp3", 128)
    assert str(tmp_path) not in str(info.value)
    assert not out.exists()


def test_merge_timeout_is_reported(tmp_path, tone, monkeypatch):
    a = tone(tmp_path / "a.mp3", 0.5)

    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", slow)
    with pytest.raises(MergeFailed, match="quá thời gian"):
        merge_audio([a, a], tmp_path / "o.mp3", "mp3", 128)
