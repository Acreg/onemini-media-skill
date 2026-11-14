#!/usr/bin/env python3
"""独立安装 Onemini 多媒体技能；Python 3.10+，仅标准库。"""
import argparse
import hashlib
from http.client import HTTPException
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

VERSION = "v0.1.0"
DOWNLOAD_URL = "https://github.com/Acreg/onemini-media-skill/releases/download/v0.1.0/onemini-media-v0.1.0.zip"
EXPECTED_SHA256 = "987f1f797cc4d559ca28e522e6b853f77349519578cf6df412ce5f42693b8bc8"
SKILL_NAME = "onemini-media"
ENV_NAME = "【务必安全使用不要打印出来】.env"


class InstallError(Exception):
    pass


def download_archive(path):
    checksum = hashlib.sha256()
    try:
        with urllib.request.urlopen(DOWNLOAD_URL, timeout=60) as response, Path(path).open("wb") as output:
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                checksum.update(chunk)
                output.write(chunk)
    except (urllib.error.URLError, OSError, HTTPException) as exc:
        raise InstallError("下载安装包失败，请检查网络和目录权限。" + str(exc)) from None
    if checksum.hexdigest() != EXPECTED_SHA256:
        raise InstallError("安装包 SHA256 校验不匹配，已停止；未覆盖已安装文件。")


def unpack_archive(archive, stage):
    """先完整验证并解压到临时目录，禁止 ZIP 越界及链接文件。"""
    files, seen = [], set()
    try:
        with zipfile.ZipFile(archive) as package:
            for entry in package.infolist():
                name = entry.orig_filename
                if "\\" in name or ":" in name or "\x00" in name or not name.startswith(SKILL_NAME + "/"):
                    raise InstallError("安装包包含非法路径或不正确的根目录。")
                parts = name.rstrip("/").split("/")
                if any(part in {"", ".", ".."} or part.rstrip(" .") != part for part in parts):
                    raise InstallError("安装包包含越界或不安全的文件路径。")
                file_type = stat.S_IFMT(entry.external_attr >> 16)
                if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    raise InstallError("安装包包含链接或特殊文件，已停止。")
                if entry.is_dir():
                    continue
                relative = PurePosixPath(*parts[1:])
                if not relative.parts or relative.as_posix() in {ENV_NAME, ".env"}:
                    raise InstallError("安装包不能包含真实密钥配置或空文件路径。")
                identity = relative.as_posix().casefold()
                if identity in seen:
                    raise InstallError("安装包包含重复或大小写冲突的文件路径。")
                seen.add(identity)
                target = Path(stage).joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(entry) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=65536)
                files.append(relative)
    except (zipfile.BadZipFile, RuntimeError, EOFError):
        raise InstallError("安装包不是有效 ZIP 或文件已损坏，未安装。") from None
    required = {PurePosixPath("SKILL.md"), PurePosixPath(ENV_NAME + ".example")}
    if not required.issubset(files):
        raise InstallError("安装包缺少 SKILL.md 或密钥配置示例，未安装。")
    return files


def destination(root, relative):
    target = root.joinpath(*relative.parts)
    # 阻止旧安装目录中的符号链接/目录联接把写入引向其他位置。
    if target.is_symlink() or target.resolve() != target:
        raise InstallError("安装目标包含链接或目录联接，不能安全写入。")
    if target.exists() and not target.is_file():
        raise InstallError("安装目标存在同名目录，不能替换为文件：" + str(target))
    for parent in target.parents:
        if parent == root:
            break
        if parent.exists() and not parent.is_dir():
            raise InstallError("安装所需的目录已被同名文件占用：" + str(parent))
    return target


def install(parent):
    parent = Path(parent).expanduser().resolve()
    parent.mkdir(parents=True, exist_ok=True)
    root = parent / SKILL_NAME
    if root.is_symlink() or root.resolve() != root or (root.exists() and not root.is_dir()):
        raise InstallError("目标 onemini-media 必须是普通目录，不能是文件或链接。")
    with tempfile.TemporaryDirectory(prefix=".onemini-install-", dir=parent) as temporary:
        temporary = Path(temporary)
        archive, stage = temporary / "package.zip", temporary / "staged"
        download_archive(archive)
        files = unpack_archive(archive, stage)
        # 只读发行包里的示例，绝不读取用户已有的 .env。
        template = (stage / (ENV_NAME + ".example")).read_text(encoding="utf-8-sig")
        config_text = "\n".join("ONEMINI_API_KEY=" if line.partition("=")[0].strip() == "ONEMINI_API_KEY" else line for line in template.splitlines()) + "\n"
        targets = [(relative, destination(root, relative)) for relative in files]
        root.mkdir(exist_ok=True)
        for relative, target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(stage.joinpath(*relative.parts), target)
        config = root / ENV_NAME
        try:
            handle = config.open("x", encoding="utf-8", newline="\n")
        except FileExistsError:
            pass  # 保留已有配置，不读取、不覆盖、不输出内容。
        else:
            with handle:
                handle.write(config_text)
            config.chmod(0o600)
    return root


class ChineseParser(argparse.ArgumentParser):
    def error(self, message):
        raise InstallError("命令参数错误：请使用 --目录 指定 skills 父目录；可用 --help 查看用法。")

    def format_help(self):
        return super().format_help().replace("usage:", "用法：")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = ChineseParser(description="下载并安装固定版本 Onemini 多媒体技能，无需 Git、Node 或 pip。", add_help=False)
    parser._positionals.title = "位置参数"
    parser._optionals.title = "可选参数"
    parser.add_argument("-h", "--help", action="help", help="显示中文帮助并退出")
    parser.add_argument("--目录", required=True, metavar="SKILLS父目录", help="在此父目录下安装 onemini-media，不自动猜测智能体目录")
    try:
        args = parser.parse_args(argv)
        root = install(args.目录)
        print("安装完成，版本：" + VERSION)
        print("技能目录：" + str(root))
        print("密钥配置文件：" + str(root / ENV_NAME))
        print("请在本地配置文件中填写自己的创作密钥；已有配置保持不变。")
        print("请重启智能体，或重新加载智能体技能后使用。")
        return 0
    except (InstallError, OSError, ValueError) as exc:
        print("安装失败：" + str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("安装已中断；未删除已有密钥配置、作品或任务文件。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
