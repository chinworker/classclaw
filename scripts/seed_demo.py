from datetime import date
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.database import init_db, writer_session
from app.models.entities import ClassPeriod, ClassRoom, Student


def seed() -> None:
    init_db()
    with writer_session() as db:
        if db.scalar(select(ClassRoom.id).limit(1)):
            print("数据库已有班级，跳过演示数据")
            return
        cls = ClassRoom(name="高三(2)班", grade="高三", room="教学楼302", head_teacher="李老师", semester_name="2026秋季学期", semester_start=date(2026, 9, 1), semester_end=date(2027, 1, 31))
        db.add(cls)
        db.flush()
        names = ["张明", "李华", "王芳", "陈强", "刘洋", "赵静", "周晨", "吴桐"]
        for index, name in enumerate(names, 1):
            db.add(Student(class_id=cls.id, student_no=f"202602{index:02d}", name=name, gender="男" if index % 2 else "女", boarding_status="住校" if index % 3 else "走读", group_no=str((index - 1) % 4 + 1), tags=[]))
        for index in range(1, 9):
            db.add(ClassPeriod(class_id=cls.id, period_no=index, name=f"第{index}节", sort_order=index))
        db.commit()
        print(f"演示数据已创建，班级ID: {cls.id}")


if __name__ == "__main__":
    seed()
