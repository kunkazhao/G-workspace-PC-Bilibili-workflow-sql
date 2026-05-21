"""删除 数码-鼠标.md 中所有 <!-- ... --> 注释行"""
import re
from pathlib import Path

path = Path(r"G:\WriteSpace\B站-文案脚本\10_b站文案\3.商品文案\数码-鼠标.md")
content = path.read_text(encoding="utf-8")
cleaned = re.sub(r'<!-- .*? -->\s*\n?', '', content)
path.write_text(cleaned, encoding="utf-8")
print(f"完成：{len(content)} 字符 → {len(cleaned)} 字符")
input("按回车退出...")
