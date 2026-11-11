"""What a clone built opens in the browser (docs/production-plan.md, phase 1):
on its own port, only its session's folder, never a file that holds keys."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from rimuru.config import Config
from rimuru.system import RimuruSystem
from rimuru.web.shared import Web
from rimuru.web.viewer import create_viewer, servable, view_path, zip_folder
from rimuru.worker.shell import DockerShell


def make_system(tmp_path: Path) -> RimuruSystem:
    config = Config()
    config.provider = "mock"
    config.db_path = tmp_path / "data" / "t.db"
    config.workspace = tmp_path / "ws"
    config.sessions_dir = tmp_path / "data" / "sessions"
    return RimuruSystem(config, speed=50.0)


def a_session_with_files(tmp_path: Path):
    system = make_system(tmp_path)
    folder = tmp_path / "site"
    (folder / "sub").mkdir(parents=True)
    (folder / "index.html").write_text("<h1>Hello page</h1>", encoding="utf-8")
    (folder / "style.css").write_text("h1{color:blue}", encoding="utf-8")
    (folder / "sub" / "about.html").write_text("<p>About</p>", encoding="utf-8")
    (folder / ".env").write_text("TOKEN=secret-value-123456", encoding="utf-8")
    (folder / "secrets.json").write_text('{"key": "x"}', encoding="utf-8")
    (folder / ".git").mkdir()
    (folder / ".git" / "config").write_text("[core]", encoding="utf-8")
    (folder / "node_modules").mkdir()
    (folder / "node_modules" / "lib.js").write_text("x", encoding="utf-8")
    thread = system.store.get_or_create_thread("console", "s-view0001", "A page")
    thread.folder = str(folder)
    system.store.put_thread(thread)
    return system, folder


def viewer(system):
    return TestClient(create_viewer(Web(system)), base_url="http://127.0.0.1:8820")


def test_a_built_page_opens(tmp_path):
    system, _folder = a_session_with_files(tmp_path)
    client = viewer(system)
    assert "Hello page" in client.get("/s-view0001/").text
    assert client.get("/s-view0001/style.css").headers["content-type"].startswith("text/css")
    assert "About" in client.get("/s-view0001/sub/about.html").text
    answer = client.get("/s-view0001/index.html")
    assert answer.headers["cache-control"] == "no-store" and answer.headers["x-content-type-options"] == "nosniff"
    system.store.close()


def test_keys_hidden_files_and_dependencies_are_never_served(tmp_path):
    system, _folder = a_session_with_files(tmp_path)
    client = viewer(system)
    for path in (".env", "secrets.json", ".git/config", "node_modules/lib.js", "..%2F..%2Fdata%2Ft.db",
                 "%2e%2e/%2e%2e/data/t.db"):
        answer = client.get(f"/s-view0001/{path}")
        assert answer.status_code == 404, path
        assert "secret-value" not in answer.text
    assert client.get("/s-nobody01/").status_code == 404, "an unknown session shows nothing"
    system.store.close()


def test_only_this_computer_may_ask(tmp_path):
    system, _folder = a_session_with_files(tmp_path)
    client = TestClient(create_viewer(Web(system)), base_url="http://evil.example:8820")
    assert client.get("/s-view0001/").status_code == 403
    system.store.close()


def test_a_folder_without_a_page_lists_its_files(tmp_path):
    system, folder = a_session_with_files(tmp_path)
    (folder / "index.html").unlink()
    text = viewer(system).get("/s-view0001/").text
    assert "style.css" in text and ".env" not in text and "secrets.json" not in text and "node_modules" not in text
    system.store.close()


def test_the_page_to_open_is_chosen_well():
    assert view_path(["app.py", "templates/base.html", "index.html"]) == "index.html"
    assert view_path(["web/index.html", "web/about.html", "docs/a/b.html"]) == "web/index.html"
    assert view_path(["main.py", "README.md"]) == ""
    assert view_path([".hidden/index.html"]) == ""
    assert view_path(["site\\index.html"]) == "site/index.html"
    assert servable("a/b.js") and not servable("a/.env") and not servable("id_rsa")


def test_the_download_leaves_keys_out(tmp_path):
    _system, folder = a_session_with_files(tmp_path)
    names = set(zipfile.ZipFile(io.BytesIO(zip_folder(folder))).namelist())
    assert names == {"index.html", "style.css", "sub/about.html"}
    _system.store.close()


def test_the_download_route(tmp_path):
    from rimuru.web import app as web_app

    system, folder = a_session_with_files(tmp_path)
    system.store.close()
    config = web_app.CONFIG
    saved = (config.db_path, config.workspace, config.sessions_dir, config.provider)
    config.db_path, config.workspace = tmp_path / "data" / "t.db", tmp_path / "ws"
    config.sessions_dir, config.provider = tmp_path / "data" / "sessions", "mock"
    try:
        with TestClient(web_app.create_app(speed=50)) as client:
            answer = client.get("/api/session/zip", params={"chat": "s-view0001"})
            assert answer.status_code == 200 and answer.headers["content-type"] == "application/zip"
            assert "site.zip" in answer.headers["content-disposition"]
            assert ".env" not in zipfile.ZipFile(io.BytesIO(answer.content)).namelist()
            info = client.get("/api/work", params={"chat": "s-view0001"}).json()
            assert info["folder"]["view"] == "index.html" and "view_port" in info
    finally:
        config.db_path, config.workspace, config.sessions_dir, config.provider = saved


def test_a_preview_is_sealed_like_a_command_but_reachable(tmp_path):
    (tmp_path / ".env").write_text("TOKEN=x", encoding="utf-8")
    shell = DockerShell(tmp_path)
    args = shell._preview_args("python app.py", 5000, "rimuru-preview-x", tmp_path, None, 3600)
    joined = " ".join(args)
    assert args[:3] == ["docker", "run", "-d"] and "--rm" not in args
    assert "-p 127.0.0.1::5000" in joined, "this computer only"
    assert args[args.index("--network") + 1] == "bridge"
    assert "--cap-drop ALL" in joined and "--read-only" in joined
    assert "/workspace/.env:ro" in joined, "keys are hidden in a preview too"
    assert args[-1].startswith("exec timeout 3600 sh -c") and "python app.py" in args[-1]
    assert "PORT=5000" in joined
