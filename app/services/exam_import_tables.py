from __future__ import annotations

import csv
import io
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePath

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from app.config import settings
from app.core.errors import AppError

MAX_FILES = 4
MAX_TABLES = 24
MAX_ROWS = 6000
MAX_COLUMNS = 100
MAX_CELLS = 150_000
NAME_HEADERS = {"姓名", "学生姓名", "考生姓名", "名字", "学生", "name", "studentname"}
NUMBER_HEADERS = {"学号", "班内学号", "学生学号", "学生编号", "studentno", "studentnumber"}
CLASS_HEADERS = {"班级", "行政班", "行政班级", "学生班级", "所在班级", "班号", "班级名称", "班", "class"}
_SUBHEADERS = {"分", "分数", "成绩", "得分", "排名", "名次", "班排", "班名", "班序", "班排名", "班名次",
               "班级排名", "班级名次", "级排", "级名", "级序", "级排名", "级名次", "年级排名", "年级名次"}


def clean(value) -> str:
    return unicodedata.normalize("NFKC", str(value)).strip() if value is not None else ""


def header_key(value) -> str:
    return "".join(clean(value).lower().split())


def invalid(message: str) -> AppError:
    return AppError("EXAM_FILE_INVALID", message, 422)


def _merge_subheader(headers: list[str], row: tuple) -> list[str] | None:
    lower = [header_key(v) for v in row]
    identities = NAME_HEADERS | NUMBER_HEADERS | CLASS_HEADERS
    if (not any(lower) or any(v and v not in _SUBHEADERS for v in lower)
            or any(v and i < len(headers) and header_key(headers[i]) in identities for i, v in enumerate(lower))):
        return None
    combined = []
    section = ""
    for index in range(max(len(headers), len(row))):
        upper = headers[index] if index < len(headers) else ""
        sub = clean(row[index]) if index < len(row) else ""
        if header_key(upper) in identities:
            section = ""
        elif upper:
            section = upper
        combined.append(f"{section} {sub}".strip()[:120] if sub else upper)
    return combined


@dataclass(slots=True)
class Table:
    source: str
    header_row: int
    headers: list[str]
    titles: list[str]
    rows: list[tuple[int, tuple]] = field(default_factory=list)


def read_uploads(files: list) -> list[Table]:
    tables: list[Table] = []
    cells = 0
    size_limit = min(settings.max_attachment_bytes, 5 * 1024 * 1024)
    for upload in files:
        filename = PurePath((upload.filename or "").replace("\\", "/")).name
        suffix = PurePath(filename).suffix.lower()
        if suffix not in {".xlsx", ".csv"}:
            raise invalid("请把成绩文件另存为 .xlsx 或 .csv 后重新上传。")
        raw = upload.file.read(size_limit + 1)
        if not raw or len(raw) > size_limit:
            raise invalid(f"文件为空或超过 {size_limit / 1024**2:g} MiB，请拆分后上传。")
        workbook = None
        try:
            if suffix == ".xlsx":
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    members = archive.infolist()
                    if len(members) > 1000 or sum(item.file_size for item in members) > 32 * 1024**2:
                        raise invalid("Excel 展开后过大，请仅保留成绩工作表，或按考试拆分文件。")
                workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=True, keep_links=False)
                sheets = ((s.title, s.iter_rows(values_only=True)) for s in workbook.worksheets if s.sheet_state == "visible")
            else:
                try:
                    decoded = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    decoded = raw.decode("gb18030")
                sheets = [("", csv.reader(io.StringIO(decoded), strict=True))]
            for sheet, rows in sheets:
                source = f"{filename} / {sheet}" if sheet else filename
                table = None
                merged_subheader = False
                titles: list[str] = [sheet[:100]] if sheet else []
                for row_no, values in enumerate(rows, 1):
                    cells += len(values)
                    if row_no > MAX_ROWS or len(values) > MAX_COLUMNS or cells > MAX_CELLS:
                        raise invalid("表格行列或单元格过多，请删除空白区域，或按考试拆分文件。")
                    if not any(clean(v) for v in values):
                        continue
                    keys = {header_key(v) for v in values}
                    if keys & (NAME_HEADERS | NUMBER_HEADERS) and len(keys - {""}) >= 2:
                        table = Table(source[:260], row_no, [clean(v)[:120] for v in values], titles[:4])
                        tables.append(table)
                        if len(tables) > MAX_TABLES:
                            raise invalid("识别到的表头超过 24 组，请分批上传。")
                        titles = [sheet[:100]] if sheet else []
                        merged_subheader = False
                    elif sum(bool(clean(v)) for v in values) == 1 and table is None:
                        if len(titles) < 4:
                            titles.append(next(clean(v)[:120] for v in values if clean(v)))
                    elif table is not None:
                        if not table.rows and not merged_subheader:
                            combined = _merge_subheader(table.headers, tuple(values))
                            if combined:
                                table.headers = combined
                                merged_subheader = True
                                continue
                        table.rows.append((row_no, tuple(values)))
        except (ValueError, OSError, KeyError, OverflowError, SyntaxError, csv.Error, zipfile.BadZipFile, InvalidFileException) as exc:
            raise invalid("无法读取成绩文件，请在表格软件中重新另存为 .xlsx 或 UTF-8 CSV，取消文件保护后重试。") from exc
        finally:
            if workbook is not None:
                workbook.close()
    if not tables:
        raise invalid("找不到成绩表头。请把“班级、姓名、学号、科目成绩”写在同一行；年级成绩请保留班级列。")
    return tables
