#!/usr/bin/env python3
"""把已下载档位上传到 Hugging Face，并（可选）设为下载源。

    python examples/hf_upload.py --tier L1 --repo yourname/paimind-models --create
    python examples/hf_upload.py --tier L1 L2 --repo yourname/paimind-models --set-source
    python examples/hf_upload.py --tier L1 --repo yourname/paimind-models --preview

前置 / Prerequisites:
    1. HF Write Token 已存密钥库：
       python -c "from core.keys import set_secret; set_secret('hf_token', 'hf_xxx')"
    2. 目标档位已下载（models/<tier>/ 下有 .onnx）
    3. 依赖：pip install -r requirements.txt

Token 只从系统密钥库读取，绝不写在命令行或日志里。
The token is read from the system keyring only — never from argv or logs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import get_config  # noqa: E402
from core.hf import (  # noqa: E402
    UploadManager,
    collect_upload_files,
    set_as_download_source,
    status,
)


def parse_tiers(values: list[str]) -> list[str]:
    tiers: list[str] = []
    for value in values:
        for part in value.replace(",", " ").split():
            tier = part.strip().upper()
            if tier not in ("L0", "L1", "L2", "L3"):
                raise SystemExit(f"未知档位 / unknown tier: {part}")
            if tier not in tiers:
                tiers.append(tier)
    return tiers


def human(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024
    return f"{size:.1f} GB"


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload a tier to Hugging Face")
    parser.add_argument("--tier", nargs="+", required=True, help="L0..L3，可多个 / one or more tiers")
    parser.add_argument("--repo", required=True, help="namespace/name")
    parser.add_argument("--create", action="store_true", help="仓库不存在时创建 / create if missing")
    parser.add_argument("--private", action="store_true", help="建成私有仓库 / create private")
    parser.add_argument("--preview", action="store_true", help="只预览，不上传 / preview only")
    parser.add_argument("--set-source", action="store_true", help="上传后设为下载源 / set as download source")
    parser.add_argument("--include-custom", action="store_true", help="包含 models/custom / include custom models")
    args = parser.parse_args()

    tiers = parse_tiers(args.tier)
    cfg = get_config()
    auth = status(cfg)
    if not auth.logged_in:
        print("未登录 / not logged in. 先运行 / first run:")
        print("  python -c \"from core.keys import set_secret; set_secret('hf_token', 'hf_xxx')\"")
        return 2

    print(f"已登录 / logged in as: {auth.username}")
    if auth.token_write_scope is False:
        print("⚠  token 可能缺少 write 权限，上传会返回 403 / token may lack write scope")

    if args.create and "/" not in args.repo:
        print("仓库名需为 namespace/name / repo must be namespace/name")
        return 2

    manager = UploadManager(cfg)

    if args.preview:
        preview = manager.preview(args.repo, tiers, args.include_custom)
        print(f"\n目标仓库 / target repo: {preview['repo_id']}")
        print(f"文件数 / files: {len(preview['files'])}  "
              f"总大小 / total: {human(preview['total_bytes'])}  "
              f"可跳过 / skippable: {preview['skippable_count']}")
        for entry in preview["files"]:
            flag = "跳过 / skip" if entry["skip"] else "上传 / upload"
            print(f"  {flag:14s} {entry['path']:40s} {human(entry['size']):>10s}  {entry['sha256'][:8]}…")
        return 0

    files = collect_upload_files(cfg, tiers, args.include_custom)
    if not files:
        print("没有可上传的文件（档位尚未下载？）/ nothing to upload — download the tier first")
        return 2

    print(f"\n准备上传 / uploading {len(files)} 个文件到 / files to {args.repo}")
    task = manager.start(args.repo, tiers, args.include_custom, progress=_progress)
    print()

    if task.state == "done":
        print(f"✅ 完成 / done: {task.repo_url}")
        print(f"   上传 / uploaded: {human(task.uploaded_bytes)}  跳过 / skipped: {task.skipped_count}")
        if args.set_source:
            result = set_as_download_source(args.repo, cfg)
            print(f"✅ 已设为 HF 下载源 / set as HF download source: {result['repo']}")
        return 0

    print(f"❌ 失败 / failed: {task.error}")
    for f in task.files:
        if not f.ok:
            print(f"   {f.path}: {f.error}")
    return 1


def _progress(ratio: float, uploaded: int, total: int) -> None:
    bar = "█" * int(ratio * 24) + "░" * (24 - int(ratio * 24))
    print(f"\r  [{bar}] {ratio * 100:5.1f}%  {human(uploaded)} / {human(total)}", end="", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
