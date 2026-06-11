"""环境变量解析工具函数"""

from typing import Optional


def truthy(value: Optional[str]) -> bool:
    """检查环境变量是否为真值"""
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def positive_int(value: Optional[str], default: int) -> int:
    """解析正整数环境变量，失败返回默认值"""
    try:
        parsed = int(str(value or "").strip())
    except ValueError:
        return default
    return parsed if parsed > 0 else default
