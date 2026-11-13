import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 测试用独立数据库，必须在导入 zffp 之前设置
_tmp = Path(tempfile.mkdtemp(prefix="zffp_test_")) / "test.db"
os.environ["ZFFP_DB"] = str(_tmp)
