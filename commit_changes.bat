@echo off
setlocal
cd /d "%~dp0"

echo === 正在提交并推送修改到 GitHub ===
echo.

git add -A
if %errorlevel% neq 0 (
    echo git add 失败
    pause
    exit /b 1
)

git commit -m "fix(ui): 多项界面与功能改进

- 新增 template_config.py 硬编码模板坐标与用户模板映射
- 剪映草稿页：添加口播用户/展示模板下拉选择器
- 剪映草稿名默认填充"完整-5月-小X"格式
- 删除设置模块导航入口与 SettingsPage
- 资产中心：价格过渡文案不计入缺文案/缺配音
- 资产中心：用户筛选改为 Listbox 多选模式
- 资产中心：有问题行高亮（浅红色背景）
- 文案中心：删除Hash列，新增品类筛选/产品名称列
- 文案中心：类型字段改用中文（引言/价格过渡/商品文案）
- 文案中心：单击正文弹窗显示完整内容并居中
- 文案中心：品类默认选中第一个，排序引→价格过渡→商品
- 窗口启动时默认最大化"

if %errorlevel% neq 0 (
    echo git commit 失败（可能没有需要提交的修改）
    pause
    exit /b 1
)

git push origin main
if %errorlevel% neq 0 (
    echo git push 失败
    pause
    exit /b 1
)

echo.
echo === 已完成提交并推送到 GitHub ===
pause
