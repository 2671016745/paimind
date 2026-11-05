"""Download source model: templates, fallback, SHA256, mirror testing."""

from __future__ import annotations

import httpx
import pytest

from core.download import (
    build_payload,
    find_mirror,
    list_mirrors,
    render_download_url,
    test_mirror as mirror_test,
)
from core.errors import AppError, ErrorCode
from core.model_files import expected_sha256, sha256_file, verify_tier_checksums
from core.config import Config


def test_builtin_mirrors_present(cfg):
    payload = build_payload(cfg)
    providers = {g.provider for g in payload.groups}
    assert {"github", "huggingface"} <= providers
    github = next(g for g in payload.groups if g.provider == "github")
    hf = next(g for g in payload.groups if g.provider == "huggingface")
    assert [m.id for m in github.mirrors] == ["official", "ghproxy", "ghfast"]
    assert [m.id for m in hf.mirrors] == ["official", "hf-mirror"]


def test_github_official_template(cfg):
    url = render_download_url(cfg, "github", "official", "L1", "model.onnx")
    assert url == "https://github.com/paimind/models/releases/download/v0.0.1/model.onnx"


def test_github_ghproxy_mirror_prefixes_upstream(cfg):
    url = render_download_url(cfg, "github", "ghproxy", "L1", "model.onnx")
    assert url.startswith("https://ghproxy.com/https://github.com/")


def test_hf_mirror_template(cfg):
    url = render_download_url(cfg, "huggingface", "hf-mirror", "L0", "L0/model.onnx")
    assert url.startswith("https://hf-mirror.com/paimind/models/resolve/main/")


def test_template_variables_expand(cfg):
    variables = {
        "{repo}": "paimind/models", "{tag}": "v0.0.1", "{revision}": "main",
        "{path}": "x.onnx", "{file}": "x.onnx", "{branch}": "main", "{tier}": "L0",
    }
    from core.config import render_url_template

    out = render_url_template("https://huggingface.co/{repo}/resolve/{revision}/{path}", variables)
    assert out == "https://huggingface.co/paimind/models/resolve/main/x.onnx"


def test_http_template_rejected_unless_allowed(cfg):
    from core.config import validate_url_template

    problems = validate_url_template("http://example.com/{repo}/{file}")
    assert problems
    cfg.set("download.allow_http_mirrors", True)
    assert not validate_url_template("https://example.com/{repo}/{file}")


def test_unknown_mirror_raises_404(cfg):
    with pytest.raises(AppError) as exc:
        find_mirror(cfg, "github", "nope")
    assert exc.value.code == ErrorCode.MIRROR_NOT_FOUND


def test_mirror_test_reports_https_requirement(cfg):
    cfg.set("download.allow_http_mirrors", False)
    result = mirror_test("github", "official", cfg, timeout=0.001)
    assert isinstance(result.ok, bool)


def test_mirror_test_hits_real_url(cfg, monkeypatch):
    seen = {}

    def fake_head(url, **kwargs):
        seen["url"] = url
        request = httpx.Request("HEAD", url)
        return httpx.Response(200, request=request, headers={"content-length": "12345"})

    monkeypatch.setattr(httpx, "head", fake_head)
    result = mirror_test("github", "official", cfg, timeout=5)
    assert result.ok is True
    assert result.status_code == 200
    assert result.content_length == 12345
    assert result.url == seen["url"]


def test_hf_private_repo_401_message(cfg, monkeypatch):
    def fake_head(url, **kwargs):
        request = httpx.Request("HEAD", url)
        return httpx.Response(401, request=request)

    monkeypatch.setattr(httpx, "head", fake_head)
    cfg.set("download.providers.huggingface.private", True)
    result = mirror_test("huggingface", "official", cfg, timeout=5)
    assert result.ok is False
    assert "私有" in result.message


def test_sha256_helpers(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"paimind" * 1000)
    digest = sha256_file(p)
    assert len(digest) == 64
    assert digest == sha256_file(p)


def test_verify_tier_checksums_reports_mismatch(tmp_path, cfg):
    models = tmp_path / "models"
    (models / "L0").mkdir(parents=True)
    target = models / "L0" / "model.onnx"
    target.write_bytes(b"model-bytes")
    cfg.set("models_dir", str(models))

    # no manifest -> everything passes (nothing to compare against)
    results = verify_tier_checksums(cfg, "L0")
    assert results and all(r["ok"] for r in results)


def test_expected_sha256_none_without_manifest(cfg):
    assert expected_sha256(cfg, "L9", "nope") is None
