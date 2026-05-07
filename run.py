# -*- coding: utf-8 -*-
"""B-Workflow SQL launcher — double-click or run: python run.py"""

import os
import sys
import traceback


def main():
    # Try to output UTF-8 to console
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    # Ensure project root is in path
    project_root = os.path.dirname(os.path.abspath(__file__))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    # Check customtkinter
    try:
        import customtkinter  # noqa: F401
    except ImportError:
        print("=" * 50)
        print("Missing customtkinter. Installing...")
        print("=" * 50)
        import subprocess
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "customtkinter"])
            print("Installation done. Please run again.")
        except Exception:
            print("Installation failed. Try: pip install customtkinter")
        input("Press Enter to exit...")
        return

    # Launch
    try:
        from bworkflow_sql.settings import DB_PATH
        from bworkflow_sql.ui import App, UI_VERSION

        print(f"UI version: {UI_VERSION}")
        print(f"Database: {DB_PATH}")

        app = App()
        app.mainloop()
    except Exception as exc:
        print("=" * 50)
        print(f"LAUNCH FAILED: {exc}")
        print()
        traceback.print_exc()
        print("=" * 50)
        input("Press Enter to exit...")


if __name__ == "__main__":
    main()
