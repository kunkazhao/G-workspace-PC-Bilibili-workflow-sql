from .project_page import ProjectPageDialog
from .copy_page import CopyPage
from .asset_page import AssetPage
from .sync_page import SyncPage
from .account_page import AccountPage
from .standalone_voice_page import StandaloneVoicePage
from .workflow_page import WorkflowPage
from .voice_page import VoicePage
from .assemble_page import AssemblePage
from .jianying_page import JianyingPage
from .rollb_rename_page import RollBRenamePage
from .subtitle_srt_page import SubtitleSrtPage
from .cutme_page import CutMePage

PAGE_MAP: dict[str, type] = {
    "品类项目": ProjectPageDialog,
    "文案中心": CopyPage,
    "资产中心": AssetPage,
    "同步中心": SyncPage,
    "用户管理": AccountPage,
    "生成配音": VoicePage,
    "组合口播稿": AssemblePage,
    "生成剪映草稿": JianyingPage,
    "单独配音": StandaloneVoicePage,
    "roll-b改名": RollBRenamePage,
    "导出字幕 SRT": SubtitleSrtPage,
    "CutMe 引言": CutMePage,
}

__all__ = [
    "PAGE_MAP",
    "ProjectPageDialog",
    "CopyPage",
    "AssetPage",
    "SyncPage",
    "AccountPage",
    "StandaloneVoicePage",
    "WorkflowPage",
    "VoicePage",
    "AssemblePage",
    "JianyingPage",
    "RollBRenamePage",
    "SubtitleSrtPage",
    "CutMePage",
]
