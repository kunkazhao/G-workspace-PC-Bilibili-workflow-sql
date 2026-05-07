import subprocess
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Stage all changes
subprocess.run(["git", "add", "-A"], check=True)

# Commit
result = subprocess.run(
    [
        "git", "commit", "-m",
        "fix(ui): 多项界面与功能改进\n"
        "\n"
        "- 新增 template_config.py 硬编码模板坐标与用户模板映射\n"
        "- 剪映草稿页：添加口播用户/展示模板下拉选择器\n"
        "- 剪映草稿名默认填充\"完整-5月-小X\"格式\n"
        "- 删除设置模块导航入口与 SettingsPage\n"
        "- 资产中心：价格过渡文案不计入缺文案/缺配音\n"
        "- 资产中心：用户筛选改为 Listbox 多选模式\n"
        "- 资产中心：有问题行高亮（浅红色背景）\n"
        "- 文案中心：删除Hash列，新增品类筛选/产品名称列\n"
        "- 文案中心：类型字段改用中文（引言/价格过渡/商品文案）\n"
        "- 文案中心：单击正文弹窗显示完整内容并居中\n"
        "- 文案中心：品类默认选中第一个，排序引→价格过渡→商品\n"
        "- 窗口启动时默认最大化",
    ],
    capture_output=True, text=True,
)
print(result.stdout)
if result.stderr:
    print(result.stderr)

# Push
push = subprocess.run(["git", "push", "origin", "main"], capture_output=True, text=True)
print(push.stdout)
if push.stderr:
    print(push.stderr)
