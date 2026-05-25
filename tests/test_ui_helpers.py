from bworkflow_sql.settings import DEFAULT_SPOKEN_MD_ROOT
from bworkflow_sql.ui import (
    ProjectEditorState,
    ProjectPageDialog,
    asset_folder_paths,
    build_project_issue_summary,
    is_valid_windows_filename,
    manifest_account_label,
    manifest_missing_assets,
    manifest_product_video_gaps,
    parse_uid_list,
    voice_generation_targets_from_rows,
    voice_state,
)
from bworkflow_sql.utils import text_hash


class FakeVar:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeCombo:
    def __init__(self):
        self.values = None

    def configure(self, **kwargs):
        if "values" in kwargs:
            self.values = kwargs["values"]


class FakeDialog:
    def winfo_exists(self):
        return True


def _editor_state() -> ProjectEditorState:
    fields = {
        key: FakeVar()
        for key in [
            "name",
            "workspace_id",
            "workspace_name",
            "category_parent_id",
            "category_parent_name",
            "category_id",
            "category_name",
            "scheme_id",
            "scheme_name",
        ]
    }
    return ProjectEditorState(
        dialog=FakeDialog(),
        mode="new",
        project_id=0,
        fields=fields,
        workspace_var=FakeVar(),
        parent_category_var=FakeVar(),
        child_category_var=FakeVar(),
        scheme_var=FakeVar(),
        parent_combo=FakeCombo(),
        child_combo=FakeCombo(),
        scheme_combo=FakeCombo(),
    )


def test_windows_filename_validation_accepts_default_srt_names():
    assert is_valid_windows_filename("字幕-5月-小燃.srt")
    assert is_valid_windows_filename("字幕-5月-小燃")
    assert not is_valid_windows_filename(r"G:\2026项目-b站\字幕-5月-小燃.srt")
    assert not is_valid_windows_filename("字幕:5月-小燃.srt")


def test_project_dialog_master_combos_receive_candidate_values(monkeypatch):
    page = ProjectPageDialog.__new__(ProjectPageDialog)
    page.category_tree = [
        {"id": "p1", "name": "Digital", "children": [{"id": "c1", "name": "Mouse"}, {"id": "c2", "name": "Keyboard"}]},
        {"id": "p2", "name": "Home", "children": []},
    ]
    page.log = lambda _text: None
    state = _editor_state()
    monkeypatch.setattr(page, "_editor_on_child_selected", lambda *_args, **_kwargs: None)

    page._apply_category_tree_to_editor(state, page.category_tree, source="test", keep_existing=False)

    assert state.parent_combo.values == ["Digital", "Home"]
    assert state.child_combo.values == ["Mouse", "Keyboard"]
    assert state.scheme_combo.values == []


def test_project_dialog_scheme_combo_receives_loaded_values():
    class FakeApp:
        def run_background(self, _title, work, *, on_success=None, **_kwargs):
            if on_success:
                on_success(work())

    class FakeMasterData:
        def fetch_schemes(self, *, workspace_id, category_id):
            assert workspace_id == "w1"
            assert category_id == "c1"
            return ([{"id": "s1", "name": "Main"}, {"id": "s2", "name": "Backup"}], "test")

    page = ProjectPageDialog.__new__(ProjectPageDialog)
    page.app = FakeApp()
    page.master_data = FakeMasterData()
    page.workspaces = [{"id": "w1", "name": "Zhaoer"}]
    page.category_tree = [{"id": "p1", "name": "Digital", "children": [{"id": "c1", "name": "Mouse"}]}]
    page.schemes = []
    page.log = lambda _text: None
    state = _editor_state()
    state.workspace_var.set("Zhaoer")
    state.parent_category_var.set("Digital")
    state.child_category_var.set("Mouse")

    page._editor_on_child_selected(state)

    assert state.scheme_combo.values == ["Main", "Backup"]
    assert state.scheme_var.get() == "Main"
    assert state.fields["scheme_id"].get() == "s1"


def test_parse_uid_list_accepts_chinese_and_english_commas_only():
    assert parse_uid_list("JP096, JP097，JP098") == [
        "JP096",
        "JP097",
        "JP098",
    ]


def test_spoken_markdown_default_root_is_spoken_copy_folder():
    assert str(DEFAULT_SPOKEN_MD_ROOT) == r"G:\WriteSpace\B站-文案脚本\10_b站文案\1.口播文案"


def test_manifest_account_label_prefers_manifest_then_entries():
    assert manifest_account_label({"account_label": "小燃", "entries": [{"account_label": "荣荣"}]}) == "小燃"
    assert manifest_account_label({"entries": [{"account_label": ""}, {"account_label": "小博"}]}) == "小博"
    assert manifest_account_label({"entries": []}) == ""


def test_manifest_product_video_gaps_reports_products_without_video():
    payload = {
        "entries": [
            {"type": "product", "product_uid": "A1", "product_name": "Alpha", "image_path": "a.png"},
            {"type": "product", "product_uid": "B2", "product_name": "Beta", "display_video_path": "b.mp4"},
            {"type": "transition", "product_uid": "PRICE_TRANSITION"},
        ]
    }

    assert manifest_product_video_gaps(payload) == ["A1 Alpha"]


def test_manifest_missing_assets_reports_selected_copy_without_audio():
    payload = {
        "entries": [
            {
                "type": "transition",
                "order_index": 2,
                "product_uid": "PRICE_TRANSITION",
                "product_name": "价格过渡 200元以下",
                "source_label": "价格过渡 200元以下",
                "audio_path": "",
            }
        ]
    }

    missing = manifest_missing_assets(payload)

    assert missing["audio"] == ["#2 transition PRICE_TRANSITION 价格过渡 200元以下 价格过渡 200元以下：路径为空"]


def test_asset_folder_paths_prefer_current_category_user_and_global_video(tmp_path):
    image_root = tmp_path / "images"
    video_root = tmp_path / "videos"
    voice_root = tmp_path / "voices"
    current = image_root / "数码-键盘" / "小燃"
    old = image_root / "键盘" / "小燃"
    voice_dir = voice_root / "数码-键盘" / "小燃"
    current.mkdir(parents=True)
    old.mkdir(parents=True)
    voice_dir.mkdir(parents=True)
    project = {
        "name": "数码-键盘",
        "category_name": "键盘",
        "image_root": str(image_root),
        "video_root": str(video_root),
        "voice_root": str(voice_root),
    }
    assets = [
        {"asset_type": "image", "status": "ready", "account_label": "小燃", "path": str(current / "模板1" / "JP001.png")},
        {"asset_type": "image", "status": "ready", "account_label": "小燃", "path": str(old / "模板1" / "JP002.png")},
        {"asset_type": "video", "status": "ready", "account_label": "", "path": str(video_root / "数码-键盘" / "小燃" / "JP001.mp4")},
    ]

    paths = asset_folder_paths(project, assets, "小燃")

    assert paths["image"] == str(current)
    assert paths["video"] == str(video_root / "数码-键盘")
    assert paths["voice"] == str(voice_dir)


def test_voice_state_marks_stale_hash_as_expired_even_with_unhashed_scan():
    assets = [
        {"uid": "JP071", "asset_type": "voice", "status": "ready", "account_label": "小燃", "text_hash": "old"},
        {"uid": "JP071", "asset_type": "voice", "status": "ready", "account_label": "小燃", "text_hash": ""},
    ]

    assert voice_state(assets, uid="JP071", account_label="小燃", hashes={"new"}) == "expired"


def test_voice_state_treats_deleted_stale_voice_as_missing(tmp_path):
    deleted_path = tmp_path / "deleted.wav"
    assets = [
        {
            "uid": "JP071",
            "asset_type": "voice",
            "status": "ready",
            "account_label": "小燃",
            "text_hash": "old",
            "path": str(deleted_path),
        },
    ]

    assert voice_state(assets, uid="JP071", account_label="小燃", hashes={"new"}) == "missing"


def test_issue_summary_reports_voice_gaps_per_script_block():
    products = [{"uid": "JP071", "title": "Keyboard"}]
    blocks = [
        {
            "script_type": "product",
            "owner_uid": "JP071",
            "block_label": "正文",
            "price_range_label": "",
            "text_hash": text_hash("body one"),
        },
        {
            "script_type": "product",
            "owner_uid": "JP071",
            "block_label": "正文2",
            "price_range_label": "",
            "text_hash": text_hash("body two"),
        },
    ]
    assets = [
        {"uid": "JP071", "asset_type": "image", "status": "ready", "account_label": ""},
        {"uid": "JP071", "asset_type": "video", "status": "ready", "account_label": ""},
        {
            "uid": "JP071",
            "asset_type": "voice",
            "status": "ready",
            "account_label": "小燃",
            "block_label": "正文",
            "text_hash": text_hash("body one"),
        },
    ]

    issues = build_project_issue_summary(
        {},
        products,
        blocks,
        assets,
        [{"label": "小燃"}],
        selected_user="小燃",
    )

    assert issues["missing_voice"] == ["小燃 / JP071 Keyboard 正文2"]
    assert issues["missing_copy"] == []
    assert issues["missing_image"] == []
    assert issues["missing_video"] == []


def test_voice_generation_targets_prefers_unique_product_uids_then_script_ids():
    rows = [
        {"script_type": "product", "uid": "JP071", "script_id": "product:JP071:V001"},
        {"script_type": "product", "uid": "JP071", "script_id": "product:JP071:V002"},
        {"script_type": "intro", "uid": "INTRO", "script_id": "intro:V001"},
        {"script_type": "price_transition", "uid": "PRICE_TRANSITION", "script_id": "price:100-200:V001"},
    ]

    assert voice_generation_targets_from_rows(rows) == ["JP071", "intro:V001", "price:100-200:V001"]
