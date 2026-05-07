from __future__ import annotations

import sys
import traceback


def main() -> None:
    """启动 B-Workflow SQL 应用。"""
    # 先检查 customtkinter
    try:
        import customtkinter  # noqa: F401
    except ImportError:
        print("=" * 60)
        print("错误：缺少 customtkinter 库")
        print("请运行：pip install customtkinter")
        print("或双击 start_app.bat 自动安装")
        print("=" * 60)
        input("按回车键退出...")
        sys.exit(1)

    try:
        from .ui import App

        app = App()
        app.mainloop()
    except Exception as exc:
        print("=" * 60)
        print(f"应用启动失败：{exc}")
        print()
        traceback.print_exc()
        print("=" * 60)
        input("按回车键退出...")
        sys.exit(1)


if __name__ == "__main__":
    main()
