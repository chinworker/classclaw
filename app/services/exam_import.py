from __future__ import annotations

import asyncio
import json
import math
import re
import time
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime
from threading import Lock

from openpyxl.utils import get_column_letter
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.database import suspend_writer
from app.models.entities import Exam, ExamImportedTotal, Student
from app.schemas.domain import ExamCreate, ScoreBatch
from app.schemas.exam_import import ExamImportApply, ExamLayout, ExamLayouts, RankColumns
from app.services import academic, openclaw_bridge
from app.services.class_student import get_class
from app.services.exam_import_tables import CLASS_HEADERS, NAME_HEADERS, NUMBER_HEADERS, Table, clean, header_key, read_uploads
from app.services.student_refs import normalize_student_ref
from app.utils.student_sort import student_no_key

_TTL = 900
_MAX_PREVIEWS = 24
_MAX_SCORES = 12_000
_previews: dict[str, tuple[float, str, str, list[dict]]] = {}
_lock = Lock()
_MISSING = {"", "-", "--", "—", "/", "缺考", "未考", "缺", "未录入", "免考"}
_LETTER_GRADE = re.compile(r"[A-F][+-]?", re.IGNORECASE | re.ASCII)


def _error(message: str, code: str = "EXAM_IMPORT_INVALID", status: int = 422) -> AppError:
    return AppError(code, message, status)


def _prune() -> None:
    for key, (expires, *_) in list(_previews.items()):
        if expires <= time.monotonic():
            del _previews[key]


async def analyze(db: Session, class_id: str, owner: str, files: list, *, cancelled) -> dict:
    async with suspend_writer(db):
        tables = await asyncio.to_thread(read_uploads, files)
    metadata = [{"table": i, "titles": t.titles, "headers": t.headers}
                for i, t in enumerate(tables, 1) if not t.simple_scores]
    layouts = [ExamLayout(table=i, name_column=1, subjects=[{"score": 2}])
               for i, t in enumerate(tables, 1) if t.simple_scores]
    if metadata:
        layouts.extend(await _extract_layouts(db, metadata, cancelled=cancelled))
    return await _build_preview(db, class_id, owner, tables, layouts, cancelled=cancelled)


async def _extract_layouts(db: Session, metadata: list[dict], *, cancelled) -> list[ExamLayout]:
    encoded = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode()) > 60_000:
        raise _error("表头信息过多，请删除无关列或按考试拆分文件后重新上传。")
    prompt = """
只根据标题及表头映射考试列；表和列从 1 开始编号。不要生成成绩。
格式：{"datasets":[{"table":1,"name":null,"exam_date":null,"name_column":2,"student_no_column":3,"class_column":1,
"exam_column":null,"date_column":null,"subjects":[{"subject":"数学","score":4,"full_score":null,"class_rank":5,"grade_rank":6}],
"total":{"score":7,"class_rank":8,"grade_rank":9}}]}。
同一表可以有多个考试/成绩版本的列组，每组分别输出；如有明确的考试名称列或日期列，用 exam_column/date_column 让程序逐行分组。
表头中的简称、别名转换成常见科目名称；班名次、班序对应 class_rank，级名次、年级序对应 grade_rank。
无法区分范围的名次不猜测。不要把排名、总评等级、折算率、考号当作成绩或班内学号。
除非标题或列名明确写出考试名称、日期及满分，否则用 null。成绩数据由程序读取，不要转录或生成成绩。
总分和总排名放到 total；只有总分没有分科成绩时，将总分作为名为“总分”的科目，total 留空。
每个列号必须实际存在且不能重复使用。成绩列明确但科目缺失或只写“成绩”“分数”时，保留列号，subject 填 null，交给用户选择。无法识别任何成绩列时输出空数组。
""".strip()
    output = await openclaw_bridge._await_unless_cancelled(
        openclaw_bridge._responses_json(
            f"{prompt}\n表头：{encoded}",
            user=f"classclaw-exam-import-{uuid.uuid4()}",
            db=db,
            require_extractor=True,
            max_output_tokens=7000,
        ),
        cancelled,
    )
    try:
        return ExamLayouts.model_validate(output).datasets
    except ValidationError as exc:
        raise _error("未能可靠识别表格列。请保留一行清晰表头，写明科目及排名范围，或按考试拆分文件后重试。") from exc


async def _build_preview(db: Session, class_id: str, owner: str, tables: list[Table], layouts: list[ExamLayout], *, cancelled) -> dict:
    if await cancelled():
        raise _error("已取消成绩文件解析", "REQUEST_CANCELLED", 409)
    cls = get_class(db, class_id)
    if cls.owner_user_id != owner:
        raise _error("当前账号已不再管理这个班级", "CLASS_ACCESS_DENIED", 403)
    roster = list(db.scalars(select(Student).where(Student.class_id == class_id, Student.deleted_at.is_(None))))
    candidates = []
    warnings = []
    for layout in layouts:
        if layout.table > len(tables):
            warnings.append("有一组识别结果指向不存在的表格，未生成预览。请核对表头后重试。")
            continue
        table = tables[layout.table - 1]
        try:
            candidates.extend(_candidates(table, layout, cls.name, roster))
        except AppError as exc:
            warnings.append(f"{table.source}（第 {table.header_row} 行表头）：{exc.message}")
        if len(candidates) > 24 or sum(len(c["scores"]) for c in candidates) > _MAX_SCORES:
            raise _error("可导入的数据组或本班成绩过多，请按考试拆分文件后再上传。")
    covered = {layout.table for layout in layouts}
    for index, table in enumerate(tables, 1):
        if index not in covered:
            warnings.append(f"{table.source}（第 {table.header_row} 行表头）未识别出成绩列。请写明“科目＋分数”，排名注明班级或年级。")
    for index, item in enumerate(candidates, 1):
        item["id"] = index
    preview_id = None
    if any(not item["errors"] for item in candidates):
        preview_id = uuid.uuid4().hex
        with _lock:
            _prune()
            if len(_previews) >= _MAX_PREVIEWS:
                del _previews[next(iter(_previews))]
            _previews[preview_id] = (time.monotonic() + _TTL, owner, class_id, candidates)
    return {"preview_id": preview_id, "datasets": candidates, "warnings": warnings}


def _class_labels(name: str) -> set[str]:
    name = header_key(name)
    match = re.fullmatch(r"(.*?)([0-9]+|[一二三四五六七八九]?十[一二三四五六七八九]?|[一二三四五六七八九])班", name)
    if not match:
        return {name}
    prefix, label = match.groups()
    digits = "零一二三四五六七八九"
    if label.isascii():
        number = int(label)
        if not 1 <= number <= 99:
            return {name}
        chinese = digits[number] if number < 10 else (digits[number // 10] if number >= 20 else "") + "十" + (
            digits[number % 10] if number % 10 else "")
    else:
        chinese = label
        if "十" in label:
            tens, ones = label.split("十", 1)
            if len(tens) > 1 or len(ones) > 1 or (tens and tens not in digits) or (ones and ones not in digits):
                return {name}
            number = (digits.index(tens) if tens else 1) * 10 + (digits.index(ones) if ones else 0)
        elif len(label) == 1:
            number = digits.index(label)
        else:
            return {name}
    return {name, str(number), f"{number:02}", f"{number}.0", chinese, f"{number}班", f"{chinese}班",
            f"第{number}班", f"{prefix}{number}班", f"{prefix}{chinese}班"}


def _no(value) -> str:
    text = normalize_student_ref(value) if value is not None else ""
    if re.fullmatch(r"[0-9]+\.0+", text):
        text = text.split(".", 1)[0]
    return str(int(text)) if text.isascii() and text.isdecimal() and len(text) <= 50 else text


def _cell(row: tuple, index: int | None):
    return row[index - 1] if index and index <= len(row) else None


def _identity_column(headers: list[str], chosen: int | None, aliases: set[str]) -> int | None:
    recognized = [index for index, name in enumerate(headers, 1) if header_key(name) in aliases]
    if len(recognized) == 1:
        return recognized[0]
    if recognized and chosen not in recognized:
        raise _error("表中有多列学生身份或班级字段，请为各表分开保留一列再上传。")
    return chosen


def _number(value, *, rank: bool = False) -> float | int | None:
    text = clean(value)
    if text in _MISSING:
        return None
    if rank:
        text = text.removeprefix("第").removesuffix("名").strip()
    number = float(text)
    if not math.isfinite(number) or number < 0 or (rank and (not number.is_integer() or not 1 <= number <= 2**31 - 1)):
        raise ValueError
    return int(number) if rank else number


def _letter_grade_column(table: Table, index: int) -> bool:
    found_grade = False
    for _, row in table.rows:
        value = clean(_cell(row, index))
        if value in _MISSING:
            continue
        if not _LETTER_GRADE.fullmatch(value):
            return False
        found_grade = True
    return found_grade


def _date(value) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = clean(value).replace("年", "-").replace("月", "-").removesuffix("日").replace("/", "-")
    match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if not match:
        return None
    try:
        return date(*map(int, match.groups())).isoformat()
    except ValueError:
        return None


def _candidates(table: Table, layout: ExamLayout, class_name: str, roster: list[Student]) -> list[dict]:
    name_col = _identity_column(table.headers, layout.name_column, NAME_HEADERS)
    no_col = _identity_column(table.headers, layout.student_no_column, NUMBER_HEADERS)
    class_col = _identity_column(table.headers, layout.class_column, CLASS_HEADERS)
    name_only = bool(name_col and not no_col and not class_col)
    if not (name_col or no_col) or (not class_col and not name_col):
        raise _error("无法可靠区分班级。请添加班级列，或同时提供姓名和本班学号。")
    identity_indexes = [name_col, no_col, class_col, layout.exam_column, layout.date_column]
    metric_indexes = []
    for mapping in [*layout.subjects, layout.total]:
        if mapping:
            metric_indexes.extend(i for i in (mapping.score, mapping.class_rank, mapping.grade_rank) if i is not None)
    used = [i for i in [*identity_indexes, *metric_indexes] if i is not None]
    subjects = [clean(s.subject) or f"第 {s.score} 列成绩" for s in layout.subjects]
    if (len(used) != len(set(used)) or max(used) > len(table.headers)
            or len(set(subjects)) != len(subjects) or not all(subjects)):
        raise _error("识别出的列位置或科目有冲突。请把不同考试的列分到不同工作表，并给各科列写明名称。")
    unmapped = [f"第 {i} 列 {name}" for i, name in enumerate(table.headers, 1) if name and i not in used]
    numeric_subjects = []
    letter_grade_headers = []
    for subject, mapping in zip(subjects, layout.subjects, strict=True):
        if _letter_grade_column(table, mapping.score):
            letter_grade_headers.append(table.headers[mapping.score - 1] or subject)
        else:
            numeric_subjects.append((subject, mapping))
    if not numeric_subjects:
        raise _error("这组科目使用字母等级。考试成绩需要数字分数，请提供对应的分数列。")
    groups: dict = {}
    labels = _class_labels(class_name)
    by_name = defaultdict(set)
    by_no = defaultdict(set)
    students = {s.id: s for s in roster}
    for s in roster:
        by_name[clean(s.name)].add(s.id)
        by_no[_no(s.student_no)].add(s.id)

    for row_no, row in table.rows:
        name = clean(_cell(row, name_col))
        no = _no(_cell(row, no_col))
        class_value = header_key(_cell(row, class_col))
        has_identity = bool(name or no or class_value)
        if (name in {"平均分", "合计", "汇总"} and not no) or (
            not has_identity and not any(clean(_cell(row, i)) not in _MISSING for i in metric_indexes)
        ):
            continue
        raw_name = clean(_cell(row, layout.exam_column))
        raw_date = _cell(row, layout.date_column)
        group_key = (raw_name, clean(raw_date))
        if group_key not in groups:
            if len(groups) >= 24:
                raise _error("考试名称或日期列产生了太多分组。请按考试拆表，或整理表头后重新解析。")
            groups[group_key] = {
                "name": raw_name[:100] or layout.name or "",
                "exam_date": _date(raw_date) or (layout.exam_date.isoformat() if layout.exam_date and not clean(raw_date) else None),
                "source": table.source, "header_row": table.header_row,
                "simple_scores": table.simple_scores,
                "subjects": [{"subject": name, "full_score": item.full_score, "score_header": table.headers[item.score - 1],
                              "requires_subject": not item.subject,
                              "class_rank_header": _cell(tuple(table.headers), item.class_rank),
                              "grade_rank_header": _cell(tuple(table.headers), item.grade_rank)}
                             for name, item in numeric_subjects],
                "scores": [], "totals": [], "students": [], "warnings": [], "errors": [], "unmapped_columns": unmapped,
                "excluded_rows": 0, "missing_cells": 0, "matched_without_class": 0, "seen": set(),
                "name_only": name_only,
            }
        group = groups[group_key]
        if not has_identity:
            _issue(group, f"第 {row_no} 行有成绩或排名但缺少学生身份。请补齐班级、姓名和学号，或删除汇总行。")
            continue
        if class_col and class_value and class_value not in labels:
            group["excluded_rows"] += 1
            continue
        named = by_name.get(name, set())
        numbered = by_no.get(no, set())
        if named and not numbered and (prefixed := re.fullmatch(r"[男女]\s*([0-9]+)", no)):
            numbered = by_no.get(_no(prefixed[1]), set())
        matches = (named & numbered) if name and no else ((named or numbered) if class_value or name_only else set())
        if len(matches) != 1:
            if class_value or named or name_only:
                _issue(group, f"第 {row_no} 行学生无法唯一匹配，请核对姓名、班内学号及班级。")
            else:
                group["excluded_rows"] += 1
            continue
        student_id = next(iter(matches))
        if student_id in group["seen"]:
            _issue(group, f"第 {row_no} 行学生重复。若是两次考试或两版成绩，请分开工作表或补齐考试名称／日期列。")
            continue
        group["seen"].add(student_id)
        if not class_value:
            group["matched_without_class"] += 1
        try:
            entries = []
            for subject, mapping in numeric_subjects:
                values = _read_metrics(row, mapping, row_no)
                if values["score"] is None:
                    if values["class_rank"] is not None or values["grade_rank"] is not None:
                        address = f"{get_column_letter(mapping.score)}{row_no}"
                        raise ValueError(f"{address}（{subject}）缺少成绩，但对应的排名有值。请核对原表。")
                    group["missing_cells"] += 1
                    continue
                entries.append({"student_id": student_id, "subject": subject, **values})
            if layout.total:
                values = _read_metrics(row, layout.total, row_no)
                if any(v is not None for v in values.values()):
                    group["totals"].append({"student_id": student_id, **values})
        except ValueError as exc:
            _issue(group, f"第 {row_no} 行，{exc}")
            continue
        group["scores"].extend(entries)

    if not groups:
        raise _error("表头下没有可读取的学生行。请检查是否为空表或公式没有保存结果。")
    relevant = [group for group in groups.values() if group["seen"] or group["errors"]]
    if not relevant:
        raise _error("没有匹配到本班学生。请检查班级列是否与本班对应，以及姓名和学号是否与名单一致。")
    for group in relevant:
        matched_count = len(group.pop("seen"))
        ids = {s["student_id"] for s in group["scores"] + group["totals"]}
        group["students"] = [{"id": s.id, "name": s.name, "student_no": s.student_no}
                             for s in sorted((students[i] for i in ids), key=lambda s: student_no_key(s.student_no))]
        if not group["scores"]:
            _issue(group, "没有可导入的本班成绩。请核对名单和成绩列。")
        if letter_grade_headers:
            names = "、".join(letter_grade_headers)
            group["warnings"].append(f"{names}使用字母等级，这些列未导入。")
        if group["missing_cells"]:
            group["warnings"].append(f'{group["missing_cells"]} 个成绩单元格为空或缺考，将留空，不记为零分。')
        if name_only:
            group["warnings"].append("原表没有班级和学号，已按姓名与本班名单匹配；重名或无法匹配的记录不能导入。")
        elif group["matched_without_class"]:
            group["warnings"].append(
                f'{group["matched_without_class"]} 行没有班级标记，按姓名和班内学号同时匹配。请确认这些记录确属本班。')
        if matched_count < len(roster):
            group["warnings"].append(f"本班有 {len(roster) - matched_count} 名学生没有匹配到此组数据，请核对名单或确认确有缺考。")
        if group["unmapped_columns"]:
            group["warnings"].append("有未导入的列，请核对是否含需保留的成绩或排名。")
        highest: dict[str, float] = defaultdict(float)
        for row in group["scores"]:
            highest[row["subject"]] = max(highest[row["subject"]], row["score"])
        for subject in group["subjects"]:
            maximum = highest[subject["subject"]]
            subject["highest_score"] = maximum
            if subject["full_score"] is not None and maximum > subject["full_score"]:
                group["warnings"].append(f'{subject["subject"]}有成绩超过识别出的满分，请核对满分或原表。')
    return relevant


def _issue(group: dict, issue: str) -> None:
    if len(group["errors"]) < 12:
        group["errors"].append(issue)


def _read_metrics(row: tuple, mapping: RankColumns, row_no: int) -> dict:
    values = {}
    for field, index, label in (("score", mapping.score, "成绩"), ("class_rank", mapping.class_rank, "班级排名"),
                                ("grade_rank", mapping.grade_rank, "年级排名")):
        rank = field != "score"
        try:
            values[field] = _number(_cell(row, index), rank=rank) if index is not None else None
        except (ValueError, OverflowError) as exc:
            address = f"{get_column_letter(index)}{row_no}"
            guidance = "请填写正整数排名或留空。" if rank else "请填写非负数字，缺考留空。"
            issue = f"{label}不是正整数" if rank else "成绩不是有效非负数"
            raise ValueError(f"{address} 的{issue}。{guidance}") from exc
    return values


def apply(db: Session, class_id: str, owner: str, body: ExamImportApply) -> dict:
    with _lock:
        _prune()
        preview = _previews.get(body.preview_id)
    if not preview or preview[1:3] != (owner, class_id):
        raise _error("导入预览已过期、已使用或不可访问，请重新解析文件。", "EXAM_IMPORT_PREVIEW_GONE", 404)
    if get_class(db, class_id).owner_user_id != owner:
        raise _error("当前账号已不再管理这个班级", "CLASS_ACCESS_DENIED", 403)
    current = {s.id: (s.name, s.student_no) for s in db.scalars(
        select(Student).where(Student.class_id == class_id, Student.deleted_at.is_(None)))}
    name_counts = Counter(clean(name) for name, _ in current.values())
    existing = {(header_key(name), exam_date) for name, exam_date in db.execute(
        select(Exam.name, Exam.exam_date).where(Exam.class_id == class_id))}
    datasets = {item["id"]: item for item in preview[3]}
    seen = set()
    plans = []
    for selection in body.datasets:
        item = datasets.get(selection.dataset_id)
        if not item or item["errors"] or selection.dataset_id in seen:
            raise _error("请选择本次预览中可导入的数据组，每组只选一次。")
        seen.add(selection.dataset_id)
        if any(current.get(s["id"]) != (s["name"], s["student_no"])
               or (item["name_only"] and name_counts[clean(s["name"])] != 1)
               for s in item["students"]):
            raise _error("班级名单已变更，请重新解析文件后核对。", "EXAM_IMPORT_STALE", 409)
        key = (header_key(selection.name), selection.exam_date)
        if key in existing:
            raise _error(f"「{selection.name}」同日已有考试或同时选中了多个版本。请选择一版，或为不同考试改名。", "EXAM_IMPORT_DUPLICATE", 409)
        existing.add(key)
        full = selection.full_scores
        if set(full) != {s["subject"] for s in item["subjects"]}:
            raise _error(f"请填写「{selection.name}」每个科目的满分。")
        unresolved = {s["subject"] for s in item["subjects"] if s["requires_subject"]}
        if set(selection.subject_names) != unresolved:
            raise _error(f"请选择「{selection.name}」中尚未识别的科目。")
        subject_map = {key: clean(selection.subject_names.get(key, key)) for key in full}
        if len({header_key(name) for name in subject_map.values()}) != len(full) or not all(subject_map.values()):
            raise _error(f"「{selection.name}」的科目不能重复或留空。")
        for score in item["scores"]:
            if score["score"] > full[score["subject"]]:
                student = current[score["student_id"]][0]
                raise _error(f'{student}的{score["subject"]}成绩 {score["score"]:g} 超过满分 {full[score["subject"]]:g}。请核对满分或原表。')
        plans.append((selection, item, subject_map))
    imported = []
    try:
        for selection, item, subject_map in plans:
            exam = academic.create_exam(db, ExamCreate(
                class_id=class_id, name=selection.name, exam_date=selection.exam_date,
                subjects=[{"subject": subject_map[name], "full_score": full} for name, full in selection.full_scores.items()],
            ), commit=False)
            mapped_scores = [{**s, "subject": subject_map[s["subject"]]} for s in item["scores"]]
            rows = academic.save_scores(db, exam.id, ScoreBatch(scores=mapped_scores), commit=False)
            source = {(s["student_id"], s["subject"]): s for s in mapped_scores}
            for row in rows:
                score = source[row.student_id, row.subject]
                row.imported_class_rank = score["class_rank"]
                row.imported_grade_rank = score["grade_rank"]
            db.add_all(ExamImportedTotal(exam_id=exam.id, **row) for row in item["totals"])
            imported.append({"id": exam.id, "name": exam.name, "score_count": len(rows)})
        db.commit()
    except Exception:
        db.rollback()
        raise
    with _lock:
        _previews.pop(body.preview_id, None)
    return {"exams": imported}
