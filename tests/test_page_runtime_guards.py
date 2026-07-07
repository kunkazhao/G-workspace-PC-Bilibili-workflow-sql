from bworkflow_sql.pages import sync_page
from bworkflow_sql.pages import workflow_page
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


def test_assembly_precheck_uses_existing_price_block_matcher():
    page = WorkflowPage.__new__(WorkflowPage)
    page.repo = type(
        "Repo",
        (),
        {
            "script_blocks": lambda self, project_id: [
                {
                    "id": 1,
                    "script_type": "product",
                    "owner_uid": "P001",
                    "block_label": "正文",
                    "text_hash": "hash-product",
                },
                {
                    "id": 2,
                    "script_type": "price_transition",
                    "owner_uid": "",
                    "block_label": "过渡",
                    "text_hash": "hash-price",
                    "price_range_label": "100元以内",
                },
            ],
            "asset_bindings": lambda self, project_id: [],
        },
    )()

    class Workflow:
        def _ordered_products(self, project_id, *, mode, top_uids, product_uids):
            return [{"uid": "P001", "title": "测试商品"}]

        def _matching_price_block(self, product, price_blocks):
            return price_blocks[0]

        def _choose_voice_ready_block(self, versions, assets, *, uid, account_label):
            return versions[0]

        def _voice_scope_fragment(self, project, account_label):
            return ""

        def _manifest_entry(self, **kwargs):
            return {
                "type": kwargs["entry_type"],
                "section": kwargs["section"],
                "audio_path": "",
                "image_path": "",
                "video_path": "",
            }

    page.workflow = Workflow()
    page.account_var = _Var("小博")
    page.mode_var = _Var("标准模式")
    page.uid_var = _Var("")
    page.intro_var = _Var("1")
    page.spoken_md_var = _Var(r"G:\WriteSpace\口播稿.md")
    page._display_template_for_account = lambda: ""
    page._remember_spoken_md = lambda project_id: page.spoken_md_var.get()

    sections, can_continue = page._assembly_precheck({"id": 1, "name": "测试项目"})

    assert sections
    assert can_continue


def test_run_command_reports_precheck_exception(monkeypatch):
    page = WorkflowPage.__new__(WorkflowPage)
    page.page_title = "组合口播稿"
    logged: list[str] = []
    shown: list[tuple[str, str]] = []

    page._confirm_precheck = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    page.log = logged.append

    monkeypatch.setattr(
        workflow_page.messagebox,
        "showerror",
        lambda title, message, parent=None: shown.append((title, message)),
    )

    page._run_command()

    assert logged
    assert "RuntimeError: boom" in logged[0]
    assert shown == [("预检查失败", "boom")]
