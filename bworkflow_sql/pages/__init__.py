import importlib

_PAGE_REGISTRY: dict[str, tuple[str, str]] = {
    "品类项目": (".project_page", "ProjectPageDialog"),
    "文案中心": (".copy_page", "CopyPage"),
    "资产中心": (".asset_page", "AssetPage"),
    "同步中心": (".sync_page", "SyncPage"),
    "用户管理": (".account_page", "AccountPage"),
    "生成配音": (".voice_page", "VoicePage"),
    "组合口播稿": (".assemble_page", "AssemblePage"),
    "生成剪映草稿": (".jianying_page", "JianyingPage"),
    "单独配音": (".standalone_voice_page", "StandaloneVoicePage"),
    "roll-b改名": (".rollb_rename_page", "RollBRenamePage"),
    "导出字幕 SRT": (".subtitle_srt_page", "SubtitleSrtPage"),
    "CutMe 引言": (".cutme_page", "CutMePage"),
}

_page_class_cache: dict[str, type] = {}


class _LazyPageMap:
    """延迟加载页面类——首次访问某页面时才 import 对应模块。"""

    def __getitem__(self, key: str) -> type:
        cls = _page_class_cache.get(key)
        if cls is None:
            module_path, class_name = _PAGE_REGISTRY[key]
            module = importlib.import_module(module_path, package=__package__)
            cls = getattr(module, class_name)
            _page_class_cache[key] = cls
        return cls

    def __contains__(self, key: object) -> bool:
        return key in _PAGE_REGISTRY

    def keys(self):
        return _PAGE_REGISTRY.keys()


PAGE_MAP = _LazyPageMap()

__all__ = ["PAGE_MAP"]
