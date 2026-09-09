#!/usr/bin/env python3
"""アップロードされた zip / html を公開フォルダに整形し、一覧ページを再生成する。

メンバーはリポジトリ直下にファイルをドラッグ&ドロップするだけでよい。
このスクリプトが以下を自動で行う:
  1. 直下の *.zip を展開し、*.html を <スラッグ>/index.html に配置
  2. フォルダ名(URL)をファイル名から自動生成（日本語ファイル名にも対応）
  3. トップの一覧ページ index.html を再生成
"""

from __future__ import annotations

import datetime
import html as html_mod
import re
import shutil
import subprocess
import sys
import unicodedata
import zipfile
from pathlib import Path

ROOT = Path(".")
BASE_URL = "https://goodvibesagency.tokyo/reports"
# 整形対象から除外するトップレベルの名前
RESERVED = {"index.html", ".github", ".git", ".nojekyll", "README.md", "CNAME"}


def run_git(*args):
    """git コマンドを実行して標準出力を返す"""
    result = subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False
    )
    return result.stdout.strip()


def slugify(name: str) -> str:
    """ファイル名から URL に使えるスラッグを作る"""
    stem = Path(name).stem
    stem = unicodedata.normalize("NFKC", stem).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    # 先頭が数字以外の記号だけになったケースを除去
    slug = re.sub(r"^-+", "", slug)
    return slug


def unique_slug(slug: str) -> str:
    """スラッグが空、または英数字を含まない場合に日付ベースの名前を割り当てる"""
    if slug and re.search(r"[a-z0-9]", slug):
        return slug
    today = datetime.date.today().strftime("%Y%m%d")
    index = 1
    while (ROOT / f"{today}-report-{index}").exists():
        index += 1
    return f"{today}-report-{index}"


def extract_zip(zip_path: Path, dest: Path) -> bool:
    """zip を dest に展開する。失敗したら False"""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            for member in archive.namelist():
                # zip slip 対策: 展開先の外に出るパスは無視する
                target = (dest / member).resolve()
                if not str(target).startswith(str(dest.resolve())):
                    print(f"  警告: 不正なパスを無視しました: {member}")
                    continue
                archive.extract(member, dest)
    except zipfile.BadZipFile:
        print(f"  エラー: zip として読めませんでした: {zip_path.name}")
        return False
    # macOS の zip に含まれるゴミを削除
    for junk in list(dest.rglob("__MACOSX")) + list(dest.rglob(".DS_Store")):
        shutil.rmtree(junk, ignore_errors=True) if junk.is_dir() else junk.unlink(
            missing_ok=True
        )
    return True


def flatten_single_dir(path: Path) -> Path:
    """中身が1フォルダだけなら1階層上げる"""
    entries = [p for p in path.iterdir() if p.name != "__MACOSX"]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return path


def ensure_index_html(folder: Path) -> bool:
    """フォルダ内に index.html を用意する。html が1つも無ければ False"""
    if (folder / "index.html").exists():
        return True
    html_files = sorted(
        p for p in folder.rglob("*") if p.suffix.lower() in (".html", ".htm")
    )
    if not html_files:
        return False
    # 最上位に近い html を index.html として採用する
    html_files.sort(key=lambda p: (len(p.relative_to(folder).parts), p.name))
    html_files[0].rename(folder / "index.html")
    return True


def publish_zip(zip_path: Path) -> str | None:
    """zip を <スラッグ>/index.html の形に展開して配置する"""
    slug = unique_slug(slugify(zip_path.name))
    work = ROOT / f".tmp-{slug}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)

    if not extract_zip(zip_path, work):
        shutil.rmtree(work, ignore_errors=True)
        return None

    source = flatten_single_dir(work)
    if not ensure_index_html(source):
        print(f"  エラー: html が見つかりません: {zip_path.name}")
        shutil.rmtree(work, ignore_errors=True)
        return None

    target = ROOT / slug
    shutil.rmtree(target, ignore_errors=True)
    shutil.move(str(source), str(target))
    shutil.rmtree(work, ignore_errors=True)
    zip_path.unlink()
    print(f"  公開: {slug}/ ← {zip_path.name}")
    return slug


def publish_html(html_path: Path) -> str:
    """直下の html を <スラッグ>/index.html に移す"""
    slug = unique_slug(slugify(html_path.name))
    target = ROOT / slug
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    shutil.move(str(html_path), str(target / "index.html"))
    print(f"  公開: {slug}/ ← {html_path.name}")
    return slug


def read_title(index_path: Path) -> str:
    """html の <title> を取り出す。取れなければフォルダ名を使う"""
    try:
        text = index_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return index_path.parent.name
    match = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
    if not match:
        return index_path.parent.name
    title = html_mod.unescape(match.group(1)).strip()
    return title or index_path.parent.name


def last_updated(folder: Path) -> str:
    """フォルダの最終更新日を git から取得する"""
    date = run_git("log", "-1", "--format=%cs", "--", str(folder))
    return date or datetime.date.today().isoformat()


def build_index() -> None:
    """公開中のレポート一覧ページを生成する"""
    reports = []
    for folder in sorted(ROOT.iterdir(), reverse=True):
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        if folder.name in RESERVED:
            continue
        index_path = folder / "index.html"
        if not index_path.exists():
            continue
        reports.append(
            {
                "slug": folder.name,
                "title": read_title(index_path),
                "date": last_updated(folder),
            }
        )

    if reports:
        rows = "\n".join(
            f"""      <li>
        <a href="./{html_mod.escape(r['slug'])}/">
          <span class="title">{html_mod.escape(r['title'])}</span>
          <span class="meta">{html_mod.escape(r['date'])} ・ /{html_mod.escape(r['slug'])}/</span>
        </a>
      </li>"""
            for r in reports
        )
        body = f'    <ul class="list">\n{rows}\n    </ul>'
    else:
        body = '    <p class="empty">まだレポートがありません。</p>'

    generated = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    page = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>レポート一覧 | GOOD VIBES AGENCY</title>
<style>
:root{{--text:#1f2937;--muted:#6b7280;--line:#e5e7eb;--accent:#111827;--bg:#fafafa;}}
*{{box-sizing:border-box}}
body{{font-family:"Hiragino Kaku Gothic ProN","Yu Gothic","Meiryo",sans-serif;margin:0;background:var(--bg);color:var(--text);line-height:1.7;}}
header{{background:#111827;color:#fff;padding:40px 24px;}}
header h1{{margin:0 0 6px;font-size:24px;letter-spacing:.02em;}}
header p{{margin:0;font-size:13px;opacity:.75;}}
main{{max-width:820px;margin:0 auto;padding:28px 20px 60px;}}
.list{{list-style:none;margin:0;padding:0;}}
.list li{{margin-bottom:12px;}}
.list a{{display:block;background:#fff;border:1px solid var(--line);border-radius:10px;padding:18px 20px;text-decoration:none;color:inherit;transition:.15s;}}
.list a:hover{{border-color:var(--accent);transform:translateY(-1px);box-shadow:0 4px 12px rgba(0,0,0,.06);}}
.title{{display:block;font-size:16px;font-weight:600;margin-bottom:4px;}}
.meta{{display:block;font-size:12px;color:var(--muted);}}
.empty{{color:var(--muted);text-align:center;padding:48px 0;}}
footer{{text-align:center;font-size:11px;color:var(--muted);padding-bottom:32px;}}
@media(prefers-color-scheme:dark){{
:root{{--text:#e5e7eb;--muted:#9ca3af;--line:#374151;--bg:#0f172a;}}
.list a{{background:#1e293b;}}
}}
</style>
</head>
<body>
<header>
  <h1>レポート一覧</h1>
  <p>GOOD VIBES AGENCY</p>
</header>
<main>
{body}
</main>
<footer>最終更新: {generated}（自動生成）</footer>
</body>
</html>
"""
    (ROOT / "index.html").write_text(page, encoding="utf-8")
    print(f"一覧ページを更新しました（{len(reports)}件）")


def main() -> int:
    published = []

    for zip_path in sorted(ROOT.glob("*.zip")):
        slug = publish_zip(zip_path)
        if slug:
            published.append(slug)

    for html_path in sorted(
        p for p in ROOT.glob("*.htm*") if p.name not in RESERVED and p.is_file()
    ):
        published.append(publish_html(html_path))

    build_index()

    if published:
        print("\n公開URL:")
        for slug in published:
            print(f"  {BASE_URL}/{slug}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
