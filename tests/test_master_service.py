import subprocess
import urllib.error

from bworkflow_sql.master_service import MasterServiceManager, is_master_connection_error


def test_master_connection_error_detects_wrapped_winerror_message():
    exc = RuntimeError("无法连接 master 方案接口: <urlopen error [WinError 10061] 由于目标计算机积极拒绝，无法连接。>")

    assert is_master_connection_error(exc)


def test_master_service_start_uses_backend_main(tmp_path, monkeypatch):
    root = tmp_path / "master"
    main = root / "backend" / "main.py"
    main.parent.mkdir(parents=True)
    main.write_text("print('ok')", encoding="utf-8")
    calls = []

    class FakePopen:
        pass

    def fake_popen(*args, **kwargs):
        calls.append((args, kwargs))
        return FakePopen()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    manager = MasterServiceManager(service_root=root)
    process = manager.start()

    assert isinstance(process, FakePopen)
    args, kwargs = calls[0]
    assert str(main) in args[0]
    assert kwargs["cwd"] == str(root)


def test_master_service_is_running_handles_url_errors(monkeypatch):
    def fake_urlopen(*_args, **_kwargs):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr("bworkflow_sql.master_service.urllib.request.urlopen", fake_urlopen)

    assert not MasterServiceManager().is_running()
