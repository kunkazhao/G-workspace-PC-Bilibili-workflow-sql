from bworkflow_sql.pages import sync_page
from bworkflow_sql.pages.assemble_page import AssemblePage
from bworkflow_sql.pages.workflow_page import WorkflowPage


class _Var:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class _Combo:
    def __init__(self) -> None:
        self.values: list[str] = []

    def configure(self, **kwargs) -> None:
        self.values = list(kwargs.get("values", []))


def test_workflow_page_type_checks_do_not_require_child_class_globals():
    page = WorkflowPage.__new__(WorkflowPage)
    page.page_title = "生成配音"

    assert page._running_dialog_title() == "正在生成配音"


def test_sync_asset_result_dialog_has_section_builder_available():
    assert callable(sync_page._build_dialog_section)


def test_assemble_page_template_refresh_imports_root_template_config():
    page = AssemblePage.__new__(AssemblePage)
    page.account_var = _Var("小博")
    page.asm_user_var = _Var("")
    page.template_var = _Var("")
    page.asm_template_combo = _Combo()

    page._on_asm_user_changed(update_path=False)

    assert page.asm_user_var.get() == "小博"
    assert "小博-模板1" in page.asm_template_combo.values
