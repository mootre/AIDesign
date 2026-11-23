"""Create a restorable Training Studio snapshot for Google Drive."""

import argparse
from contextlib import closing
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from datetime import datetime, timezone
from zipfile import ZIP_DEFLATED, ZipFile


def create_backup(root, destination):
    root = root.resolve(strict=True)
    database = root / "training.sqlite3"
    sources = root / "sources"
    if not database.is_file() or not sources.is_dir():
        raise ValueError("ไม่พบคลัง Training Studio ที่ตำแหน่งที่เลือก")
    if database.resolve().parent != root or sources.resolve().parent != root:
        raise ValueError("ฐานข้อมูลและโฟลเดอร์ PDF ต้องอยู่ในคลังเดียวกัน")
    if not destination.is_dir():
        raise ValueError("ไม่พบโฟลเดอร์ปลายทางที่เลือก")

    with tempfile.TemporaryDirectory(prefix="training-backup-") as temp:
        staging = Path(temp)
        snapshot = staging / database.name
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as live:
            with closing(sqlite3.connect(snapshot)) as saved:
                live.backup(saved)
                if saved.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise ValueError("ตรวจความสมบูรณ์ของฐานข้อมูลไม่ผ่าน")
                files = saved.execute("SELECT source_rel, sha256 FROM drawings ORDER BY id").fetchall()

        archive = staging / "snapshot.zip"
        with ZipFile(archive, "x", ZIP_DEFLATED) as output:
            output.write(snapshot, snapshot.name)
            for name, sha256 in files:
                if not re.fullmatch(r"[a-f0-9]{32}\.pdf", name):
                    raise ValueError("พบชื่อไฟล์ต้นฉบับที่ไม่ตรงกับทะเบียน")
                path = sources / name
                if path.resolve(strict=True).parent != sources or path.is_symlink():
                    raise ValueError("ไฟล์ต้นฉบับอยู่นอกโฟลเดอร์คลัง")
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != sha256:
                    raise ValueError("ไฟล์ PDF ไม่ตรงกับรุ่นในทะเบียน")
                output.writestr("sources/" + name, raw)
        with ZipFile(archive) as verified:
            if verified.testzip() is not None:
                raise ValueError("ตรวจความสมบูรณ์ของ ZIP ไม่ผ่าน")

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%fZ")
        final = destination / f"training-studio-backup-{stamp}.zip"
        partial = final.with_suffix(".partial")
        try:
            with archive.open("rb") as source, partial.open("xb") as target:
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
            partial.rename(final)
        except OSError:
            partial.unlink(missing_ok=True)
            raise
    return final, len(files)


def main():
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(os.environ.get(
        "THAI_TRAINING_DATA", local / "ThaiFurnitureTrainingStudio")))
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    destination = args.destination
    if destination is None:
        from tkinter import Tk, filedialog
        window = Tk()
        window.withdraw()
        try:
            selected = filedialog.askdirectory(title="เลือกโฟลเดอร์สำรองข้อมูล เช่น My Drive")
        finally:
            window.destroy()
        if not selected:
            return
        destination = Path(selected)
    try:
        path, count = create_backup(args.source, destination)
    except (OSError, sqlite3.Error, ValueError) as error:
        raise SystemExit(f"สำรองข้อมูลไม่สำเร็จ: {error}") from None
    print(f"สร้าง ZIP แล้ว ({count} PDF): {path}")


if __name__ == "__main__":
    main()
