import json
from types import SimpleNamespace

from bworkflow_sql.db import Database
from bworkflow_sql.md_parser import parse_markdown_text
from bworkflow_sql.repositories import Repository
from bworkflow_sql.settings import DEFAULT_SPOKEN_MD_ROOT
from bworkflow_sql.sync_service import SyncService
from bworkflow_sql.ui_helpers import (
    ProjectEditorState,
    asset_folder_paths,
    build_project_issue_summary,
    collect_voice_status,
    entry_asset_issue_lines,
    is_valid_windows_filename,
    manifest_account_label,
    manifest_display_template,
    manifest_missing_assets,
    manifest_product_video_gaps,
    parse_uid_list,
    project_id_from_selector_value,
    project_name_exists,
    project_selector_value,
    split_missing_voice_rows_by_removed_assets,
    voice_generation_targets_from_rows,
    voice_inventory_stats,
    voice_state,
)
from bworkflow_sql.pages.project_page import ProjectPageDialog
from bworkflow_sql.pages.workflow_page import WorkflowPage
from bworkflow_sql.utils import text_hash
from bworkflow_sql.workflow_service import WorkflowService


class FakeVar:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def test_assembly_precheck_initializes_display_template_before_asset_checks(tmp_path):
    db = Database(tmp_path / "test.db")
    repo = Repository(db)
    project_id = db.upsert_project({"name": "数码-键盘"})
    repo.upsert_products_from_master(
        project_id,
        [{"uid": "JP031", "title": "Keyboard", "price_label": "299元"}],
    )
    SyncService(db).sync_markdown_payload(
        project_id,
        parse_markdown_text(
            """
## 引言文案

### 引言1
开场

## 商品文案

### Keyboard-JP031-299元
#### 正文
商品正文

## 价格过渡文案

### 200-300元
#### 正文
价格过渡
""".strip()
        ),
    )
    output_path = tmp_path / "spoken.md"
    page = SimpleNamespace(
        repo=repo,
        workflow=WorkflowService(db),
        uid_var=FakeVar("JP031"),
        account_var=FakeVar("小博"),
        mode_var=FakeVar("Top 模式"),
        intro_var=FakeVar("1"),
        spoken_md_var=FakeVar(str(output_path)),
        _display_template_for_account=lambda: "小博-模板2",
        _remember_spoken_md=lambda _project_id: str(output_path),
    )

    sections, can_continue = WorkflowPage._assembly_precheck(page, repo.project(project_id))

    assert can_continue
    assert sections[0].rows[2] == ("模式", "Top 模式")


def test_jianying_precheck_initializes_display_template_from_manifest(tmp_path):
    manifest_path = tmp_path / "spoken.manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "account_label": "小博",
                "display_template": "小博-模板2",
                "entries": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    page = SimpleNamespace(
        spoken_md_var=FakeVar(str(tmp_path / "spoken.md")),
        intro_video_var=FakeVar(""),
        account_var=FakeVar("完整-键盘-小博"),
        workflow=SimpleNamespace(spoken_manifest_path=lambda _project_id, _path: manifest_path),
        repo=SimpleNamespace(
            products=lambda _project_id, include_removed=False: [],
            script_blocks=lambda _project_id: [],
            asset_bindings=lambda _project_id: [],
            accounts=lambda: [],
        ),
    )

    sections, _can_continue = WorkflowPage._jianying_precheck(page, {"id": 1, "name": "数码-键盘"})

    assert sections[-1].rows[0] == ("缺图片", "0")


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


def test_project_selector_helpers_round_trip_project_ids():
    value = project_selector_value({"id": 42, "name": "键盘项目"})

    assert value == "键盘项目"
    assert project_id_from_selector_value("42 - 键盘项目") == 42
    assert project_id_from_selector_value("") is None
    assert project_id_from_selector_value("未选择") is None


def test_project_name_exists_matches_names_case_insensitively():
    projects = [{"id": 1, "name": "Keyboard"}, {"id": 2, "name": "数码-充电宝"}]

    assert project_name_exists(projects, "keyboard")
    assert project_name_exists(projects, "数码-充电宝")
    assert not project_name_exists(projects, "数码-充电宝", exclude_project_id=2)
    assert not project_name_exists(projects, "数码-耳机")


def test_spoken_markdown_default_root_is_spoken_copy_folder():
    assert str(DEFAULT_SPOKEN_MD_ROOT) == r"G:\WriteSpace\B站-文案脚本\10_b站文案\1.口播文案"


def test_manifest_account_label_prefers_manifest_then_entries():
    assert manifest_account_label({"account_label": "小燃", "entries": [{"account_label": "荣荣"}]}) == "小燃"
    assert manifest_account_label({"entries": [{"account_label": ""}, {"account_label": "小博"}]}) == "小博"
    assert manifest_account_label({"entries": []}) == ""


def test_manifest_display_template_supports_explicit_and_legacy_manifests():
    assert manifest_display_template({"display_template": "小博-模板2"}) == "小博-模板2"
    assert manifest_display_template(
        {
            "account_label": "小博",
            "entries": [{"image_path": r"G:\素材\数码-键盘\小博\模板2\JP001.png"}],
        }
    ) == "小博-模板2"


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


def test_entry_asset_issue_lines_hides_ready_asset_details(tmp_path):
    audio = tmp_path / "voice.wav"
    image = tmp_path / "image.png"
    video = tmp_path / "video.mp4"
    for path in (audio, image, video):
        path.write_bytes(b"ok")

    lines = entry_asset_issue_lines(
        [
            {
                "type": "product",
                "section": "product",
                "order_index": 1,
                "product_uid": "A1",
                "product_name": "Alpha",
                "audio_path": str(audio),
                "image_path": str(image),
                "display_video_path": str(video),
            }
        ]
    )

    assert lines == []


def test_entry_asset_issue_lines_reports_only_problem_assets(tmp_path):
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"ok")
    missing_video = tmp_path / "missing.mp4"

    lines = entry_asset_issue_lines(
        [
            {
                "type": "product",
                "section": "product",
                "order_index": 2,
                "product_uid": "B2",
                "product_name": "Beta",
                "audio_path": str(audio),
                "image_path": "",
                "display_video_path": str(missing_video),
            }
        ]
    )

    assert lines == [
        "#2 商品文案 B2 Beta｜图片：未匹配",
        f"#2 商品文案 B2 Beta｜视频路径不存在：{missing_video}",
    ]


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


def test_asset_folder_paths_use_selected_image_template(tmp_path):
    image_root = tmp_path / "images"
    project = {
        "name": "数码-键盘",
        "category_name": "键盘",
        "image_root": str(image_root),
        "video_root": "",
        "voice_root": "",
    }

    paths = asset_folder_paths(project, [], "小燃", "小燃-模板2")

    assert paths["image"] == str(image_root / "数码-键盘" / "小燃" / "模板2")


def test_issue_summary_treats_other_template_image_as_missing(tmp_path):
    products = [{"uid": "JP071", "title": "Keyboard"}]
    blocks = [{"script_type": "product", "owner_uid": "JP071", "block_label": "正文", "text_hash": text_hash("body")}]
    template1_image = tmp_path / "数码-键盘" / "小燃" / "模板1" / "JP071.png"
    template2_image = tmp_path / "数码-键盘" / "小燃" / "模板2" / "JP071.png"
    assets = [
        {"uid": "JP071", "asset_type": "image", "status": "ready", "account_label": "小燃", "path": str(template1_image)},
        {"uid": "JP071", "asset_type": "video", "status": "ready", "account_label": "", "path": ""},
        {
            "uid": "JP071",
            "asset_type": "voice",
            "status": "ready",
            "account_label": "小燃",
            "block_label": "正文",
            "text_hash": text_hash("body"),
            "path": "",
        },
    ]

    missing = build_project_issue_summary({}, products, blocks, assets, [{"label": "小燃"}], selected_user="小燃", image_template="小燃-模板2")
    template2_image.parent.mkdir(parents=True)
    template2_image.write_bytes(b"image")
    assets[0]["path"] = str(template2_image)
    ready = build_project_issue_summary({}, products, blocks, assets, [{"label": "小燃"}], selected_user="小燃", image_template="小燃-模板2")

    assert missing["missing_image"] == ["JP071 Keyboard"]
    assert ready["missing_image"] == []


def test_voice_state_marks_stale_hash_as_expired_even_with_unhashed_scan():
    assets = [
        {"uid": "JP071", "asset_type": "voice", "status": "ready", "account_label": "小燃", "text_hash": "old"},
        {"uid": "JP071", "asset_type": "voice", "status": "ready", "account_label": "小燃", "text_hash": ""},
    ]

    assert voice_state(assets, uid="JP071", account_label="小燃", hashes={"new"}) == "expired"


def test_voice_state_treats_deleted_ready_voice_as_missing_file(tmp_path):
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

    assert voice_state(assets, uid="JP071", account_label="小燃", hashes={"new"}) == "missing_file"


def test_collect_voice_status_counts_missing_file_separately(tmp_path):
    deleted_path = tmp_path / "deleted.wav"
    result = collect_voice_status(
        [
            {
                "id": 42,
                "script_type": "product",
                "owner_uid": "JP071",
                "block_label": "正文",
                "price_range_label": "",
                "script_id": "product:JP071:V001",
                "text_hash": text_hash("body"),
            }
        ],
        [
            {
                "uid": "JP071",
                "asset_type": "voice",
                "status": "ready",
                "account_label": "小燃",
                "block_label": "正文",
                "text_hash": text_hash("body"),
                "path": str(deleted_path),
            }
        ],
        [{"label": "小燃"}],
        {"JP071": {"title": "Keyboard"}},
        selected_user="小燃",
    )

    assert result["ready"] == 0
    assert result["missing"] == []
    assert result["missing_file"][0]["display"] == "JP071 Keyboard 正文"


def test_voice_inventory_stats_separates_coverage_duplicates_and_old_files(tmp_path):
    voice_dir = tmp_path / "voice"
    voice_dir.mkdir()
    current = voice_dir / "current.mp3"
    duplicate = voice_dir / "current-1.mp3"
    old = voice_dir / "old.mp3"
    for path in (current, duplicate, old):
        path.write_bytes(b"voice")
    blocks = [{"id": 42, "text_hash": "current-hash"}]
    assets = [
        {
            "script_block_id": 42,
            "asset_type": "voice",
            "status": "ready",
            "account_label": "小博",
            "text_hash": "current-hash",
            "path": str(current),
        },
        {
            "script_block_id": 42,
            "asset_type": "voice",
            "status": "ready",
            "account_label": "小博",
            "text_hash": "current-hash",
            "path": str(duplicate),
        },
        {
            "script_block_id": 42,
            "asset_type": "voice",
            "status": "stale",
            "account_label": "小博",
            "text_hash": "old-hash",
            "path": str(old),
        },
    ]

    stats = voice_inventory_stats(blocks, assets, account_label="小博", directory=voice_dir)

    assert stats == {
        "valid_files": 2,
        "duplicate_files": 1,
        "directory_files": 3,
        "untracked_files": 1,
    }


def test_split_missing_voice_rows_by_removed_assets_marks_file_loss():
    missing_rows = [
        {
            "account_label": "小燃",
            "uid": "JP071",
            "block_label": "正文",
            "script_block_id": "42",
            "state": "missing",
        },
        {
            "account_label": "小燃",
            "uid": "JP072",
            "block_label": "正文",
            "script_block_id": "43",
            "state": "missing",
        },
    ]
    removed_items = [
        {
            "asset_type": "voice",
            "account_label": "小燃",
            "uid": "JP071",
            "block_label": "正文",
            "script_block_id": 42,
        }
    ]

    missing, missing_file = split_missing_voice_rows_by_removed_assets(missing_rows, removed_items)

    assert [row["uid"] for row in missing] == ["JP072"]
    assert [(row["uid"], row["state"]) for row in missing_file] == [("JP071", "missing_file")]


def test_issue_summary_reports_voice_gaps_per_script_block(tmp_path):
    products = [{"uid": "JP071", "title": "Keyboard"}]
    image_path = tmp_path / "JP071.png"
    video_path = tmp_path / "JP071.mp4"
    image_path.write_bytes(b"image")
    video_path.write_bytes(b"video")
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
        {"uid": "JP071", "asset_type": "image", "status": "ready", "account_label": "", "path": str(image_path)},
        {"uid": "JP071", "asset_type": "video", "status": "ready", "account_label": "", "path": str(video_path)},
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


def test_voice_status_rows_include_script_block_id_for_manual_mapping():
    rows = collect_voice_status(
        [
            {
                "id": 42,
                "script_type": "product",
                "owner_uid": "JP071",
                "block_label": "正文",
                "price_range_label": "",
                "script_id": "product:JP071:V001",
                "text_hash": text_hash("body"),
            }
        ],
        [],
        [{"label": "小燃"}],
        {"JP071": {"title": "Keyboard"}},
        selected_user="小燃",
    )

    assert rows["missing"][0]["script_block_id"] == "42"


def test_voice_generation_targets_prefers_unique_product_uids_then_script_ids():
    rows = [
        {"script_type": "product", "uid": "JP071", "script_id": "product:JP071:V001"},
        {"script_type": "product", "uid": "JP071", "script_id": "product:JP071:V002"},
        {"script_type": "intro", "uid": "INTRO", "script_id": "intro:V001"},
        {"script_type": "price_transition", "uid": "PRICE_TRANSITION", "script_id": "price:100-200:V001"},
    ]

    assert voice_generation_targets_from_rows(rows) == ["JP071", "intro:V001", "price:100-200:V001"]
