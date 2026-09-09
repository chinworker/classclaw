import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import init_db

if __name__ == "__main__":
    init_db(strict_usage=True)
    print("业务库和用量库初始化完成")
