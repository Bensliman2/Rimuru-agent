"""The pages, in a real browser: they load without script errors, and the main
things a person does on them work. Against a real server in demo mode, so offline.

Needs Playwright and Chrome or Edge, and is skipped without them - except on
GitHub (RIMURU_BROWSER_TESTS=required), where a missing browser is a failure.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).parent.parent
REQUIRED = os.environ.get("RIMURU_BROWSER_TESTS") == "required"
playwright_api = pytest.importorskip("playwright.sync_api") if not REQUIRED else __import__(
    "playwright.sync_api", fromlist=["sync_playwright"])
sync_playwright = playwright_api.sync_playwright
expect = playwright_api.expect
PlaywrightError = playwright_api.Error

expect.set_options(timeout=15_000)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def serve(folder: Path, *flags: str):
    """The real app, on its own port and its own data, as `python run.py` runs it."""
    port = free_port()
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "RIMURU_HANDS_HOST": "127.0.0.1",
           "RIMURU_HANDS_PORT": str(free_port()), "RIMURU_VIEW_PORT": str(free_port()),
           "RIMURU_MODELS": str(folder / "models.toml"),
           "RIMURU_ENV_FILE": str(folder / ".env"), "RIMURU_SKILLS": str(folder / "skills")}
    process = subprocess.Popen(
        [sys.executable, "run.py", *flags, "--speed", "6", "--data", str(folder / "data"), "--port", str(port)],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(150):
        try:
            if httpx.get(f"{base}/api/overview", timeout=1).status_code == 200:
                return base, process
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    process.kill()
    raise RuntimeError("the app did not start")


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as play:
        launched = None
        for channel in ("chrome", "msedge", None):
            try:
                launched = play.chromium.launch(channel=channel) if channel else play.chromium.launch()
                break
            except PlaywrightError:
                continue
        if launched is None:
            if REQUIRED:
                raise RuntimeError("no browser to test with")
            pytest.skip("no browser to test with")
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    base, process = serve(tmp_path_factory.mktemp("demo"), "--demo")
    yield base
    process.kill()
    process.wait()


@pytest.fixture
def page(browser):
    """A page that fails the test on any script error."""
    context = browser.new_context(viewport={"width": 1320, "height": 860})
    tab = context.new_page()
    errors: list[str] = []
    tab.on("pageerror", lambda exc: errors.append(f"script error: {exc}"))
    tab.on("console", lambda msg: errors.append(f"console: {msg.text}") if msg.type == "error" else None)
    yield tab
    context.close()
    assert not errors, errors


def say(tab, text: str) -> None:
    box = tab.locator("#input")
    box.fill(text)
    box.press("Enter")


def test_a_job_from_the_chat_page(page, demo):
    page.goto(demo)
    expect(page.locator("#welcome")).to_be_visible()
    say(page, "Build a small Python calculator with tests")
    expect(page.locator(".msg.user .bubble").first).to_contain_text("calculator")
    card = page.locator(".clones .card").first
    expect(card).to_be_visible()
    expect(page.locator(".msg.rimuru .bubble").first).to_be_visible()

    card.click()
    expect(page.locator("#panel")).to_be_visible()
    expect(page.locator("#panel-title")).not_to_be_empty()
    page.keyboard.press("Escape")
    expect(page.locator("#panel")).to_be_hidden()


def test_slash_shows_the_commands(page, demo):
    page.goto(demo)
    page.locator("#input").fill("/")
    page.locator("#input").press("End")
    page.locator("#input").type("s")
    expect(page.locator("#slash-menu")).to_contain_text("status")


@pytest.mark.parametrize("button, panel", [
    ("#models-btn", "#settings"), ("#skills-btn", "#skills"), ("#connections-btn", "#connections"),
    ("#memory-btn", "#memory"), ("#fb-change", "#folders"),
])
def test_every_panel_opens_and_closes(page, demo, button, panel):
    page.goto(demo)
    page.locator(button).click()
    expect(page.locator(panel)).to_be_visible()
    expect(page.locator("#scrim")).to_be_visible()
    page.locator("#scrim").click(position={"x": 5, "y": 300})
    expect(page.locator(panel)).to_be_hidden()
    expect(page.locator("#scrim")).to_be_hidden()


def test_memory_can_be_added_changed_and_forgotten(page, demo):
    page.goto(demo)
    page.on("dialog", lambda dialog: dialog.accept())
    page.locator("#memory-btn").click()
    page.locator("#mem-add-text").fill("I like dark designs")
    page.locator("#mem-add-text").press("Enter")
    expect(page.locator(".mem-text")).to_have_text("I like dark designs")

    page.locator("[data-mem-edit]").click()
    page.locator(".mem-edit").fill("I like light designs")
    page.locator("[data-mem-save]").click()
    expect(page.locator(".mem-text")).to_have_text("I like light designs")

    page.locator("[data-mem-del]").click()
    expect(page.locator(".mem-text")).to_have_count(0)


def test_a_skill_can_be_written(page, demo):
    page.goto(demo)
    page.on("dialog", lambda dialog: dialog.accept())
    page.locator("#skills-btn").click()
    page.locator("#skill-name").fill("House style")
    page.locator("#skill-text").fill("- Short functions.")
    page.locator("#skill-save").click()
    expect(page.locator(".skill-name")).to_contain_text("House style")


def test_arabic_reads_right_to_left(page, demo):
    page.goto(demo)
    say(page, "مرحبا كيف حالك")
    bubble = page.locator(".msg.user .bubble").last
    expect(bubble).to_contain_text("مرحبا")
    assert bubble.evaluate("e => getComputedStyle(e).unicodeBidi") == "plaintext"


@pytest.mark.parametrize("view", ["overview", "rimuru", "clones", "watchdog", "messages", "memory"])
def test_under_the_hood(page, demo, view):
    page.goto(f"{demo}/inside")
    page.locator(f'[role=tab][data-view="{view}"]').click()
    expect(page.locator(f"#view-{view}")).to_be_visible()
    page.wait_for_timeout(300)


def test_a_session_can_be_deleted(page, demo):
    page.goto(demo)
    say(page, "Make a simple to-do list page in a single HTML file")
    expect(page.locator(".msg.user .bubble").first).to_be_visible()
    row = page.locator(".session-row").first
    expect(row).to_be_visible()
    before = page.locator(".session-row").count()
    row.hover()
    row.locator(".session-del").click()
    expect(page.locator("#del-dialog")).to_be_visible()
    page.locator("#del-confirm").click()
    expect(page.locator("#del-dialog")).to_be_hidden()
    expect(page.locator(".session-row")).to_have_count(before - 1)


def test_on_a_phone_the_buttons_are_in_a_menu_and_stop_is_not(browser, demo):
    context = browser.new_context(viewport={"width": 375, "height": 812})
    tab = context.new_page()
    tab.goto(demo)
    expect(tab.locator("#stop")).to_be_visible()
    expect(tab.locator("#models-btn")).to_be_hidden()
    tab.locator("#more-btn").click()
    expect(tab.locator("#models-btn")).to_contain_text("Models")
    expect(tab.locator("#models-btn")).to_be_visible()
    assert tab.evaluate("document.documentElement.scrollWidth - innerWidth") <= 0, "nothing runs off the screen"
    tab.locator("#memory-btn").click()
    expect(tab.locator("#memory")).to_be_visible()
    context.close()


@pytest.mark.parametrize("width", [1440, 1320, 1240, 1201, 1200, 1150, 1090, 1081, 1000, 800, 761, 760, 600, 375])
def test_the_top_bar_fits_at_every_width(browser, demo, width):
    context = browser.new_context(viewport={"width": width, "height": 800})
    tab = context.new_page()
    tab.goto(demo)
    expect(tab.locator("#stop")).to_be_visible()
    fits = tab.evaluate("""() => {
      const bar = document.querySelector(".bar");
      const stop = document.getElementById("stop").getBoundingClientRect();
      return { over: bar.scrollWidth - bar.clientWidth, stopRight: stop.right, room: innerWidth };
    }""")
    context.close()
    assert fits["over"] <= 0 and fits["stopRight"] <= fits["room"], fits


def test_a_new_install_opens_on_the_setup(browser, tmp_path):
    base, process = serve(tmp_path)
    try:
        context = browser.new_context(viewport={"width": 1100, "height": 800})
        tab = context.new_page()
        tab.goto(base)
        expect(tab.locator("#setup")).to_be_visible()
        expect(tab.locator("#setup-body h1")).to_contain_text("Rimuru")
        expect(tab.locator(".composer")).to_be_hidden()
        tab.locator("#setup-provider").select_option("groq")
        expect(tab.locator("#setup-hint")).to_contain_text("console.groq.com")

        tab.locator("#setup-skip").click()
        expect(tab.locator("#setup")).to_be_hidden()
        expect(tab.locator(".composer")).to_be_visible()
        expect(tab.locator("#demo-banner")).to_be_visible()
        tab.reload()
        expect(tab.locator("#setup")).to_be_hidden()
        context.close()
    finally:
        process.kill()
        process.wait()


def test_every_file_a_page_names_is_served(demo):
    import re

    for path in ("/", "/inside"):
        html = httpx.get(demo + path).text
        assert "<!-- symbols -->" not in html and 'id="mark-rimuru"' in html, "the shared icons are put in"
        for asset in re.findall(r'(?:src|href)="(/static/[^"]+)"', html):
            answer = httpx.get(demo + asset)
            assert answer.status_code == 200, asset
            if asset.endswith(".js"):
                assert "javascript" in answer.headers["content-type"], "or a browser refuses to run it"


def test_when_saving_fails_the_chat_says_so(page, demo):
    def broken(route):
        answer = route.fetch()
        route.fulfill(response=answer, json={**answer.json(), "save_problem": "Rimuru cannot save right now: the disk is full."})

    page.route("**/api/work*", broken)
    page.goto(demo)
    expect(page.locator("#save-banner")).to_contain_text("the disk is full")


def test_a_failed_model_can_be_tried_again(browser, tmp_path):
    base, process = serve(tmp_path)
    try:
        context = browser.new_context(viewport={"width": 1100, "height": 800})
        tab = context.new_page()
        tries: list[str] = []

        def connect(route):
            tries.append(route.request.post_data_json.get("preset", ""))
            if len(tries) == 1:
                route.fulfill(json={"ok": False, "offered": [], "model": "", "checks": [
                    {"ok": False, "model": "free-model", "who": "Rimuru", "seconds": 0, "problem": "It is busy."}]})
            else:
                route.fulfill(json={"ok": True, "checks": [
                    {"ok": True, "model": "free-model", "who": "Rimuru", "seconds": 1.5, "problem": ""}]})

        tab.route("**/api/setup/connect", connect)
        tab.goto(base)
        tab.locator("[data-setup-preset]").click()
        expect(tab.locator("#setup-body")).to_contain_text("It is busy.")
        tab.locator("#setup-retry").click()
        expect(tab.locator("#setup-body h1")).to_have_text("It works.")
        assert tries == ["free", "free"]
        context.close()
    finally:
        process.kill()
        process.wait()


def test_what_a_clone_built_opens_from_the_chat(page, demo):
    page.goto(demo)
    say(page, "Make a simple to-do list page in a single HTML file")
    expect(page.locator(".msg.user .bubble").first).to_be_visible()
    expect(page.locator(".session.on[data-chat]")).to_have_count(1)
    key = page.locator(".session.on[data-chat]").get_attribute("data-chat")
    for _ in range(100):
        folder = httpx.get(f"{demo}/api/work", params={"chat": key}).json()["folder"]
        if folder.get("path"):
            break
        time.sleep(0.2)
    Path(folder["path"]).mkdir(parents=True, exist_ok=True)
    (Path(folder["path"]) / "index.html").write_text("<h1>My to-do list</h1>", encoding="utf-8")
    (Path(folder["path"]) / ".env").write_text("TOKEN=never-shown-123456", encoding="utf-8")
    page.reload()
    view = page.locator("#fb-view")
    expect(view).to_be_visible()
    href = view.get_attribute("href")
    assert href.startswith("http://127.0.0.1:") and demo.split(":")[-1] not in href.split("/")[2], "its own port"
    opened = page.context.new_page()
    opened.goto(href)
    expect(opened.locator("h1")).to_have_text("My to-do list")
    assert httpx.get(href.rstrip("/") + "/.env").status_code == 404
    opened.close()
    expect(page.locator("#fb-download")).to_be_visible()


def test_the_interface_speaks_arabic_right_to_left(browser, demo):
    context = browser.new_context(viewport={"width": 1320, "height": 860}, locale="ar")
    tab = context.new_page()
    tab.goto(demo)
    expect(tab.locator("html")).to_have_attribute("dir", "rtl")
    expect(tab.locator("#memory-btn")).to_contain_text("الذاكرة")
    expect(tab.locator("#input")).to_have_attribute("placeholder", "أخبر ريمورو بما تحتاجه…")
    assert tab.evaluate("document.documentElement.scrollWidth - innerWidth") <= 0
    tab.locator("#lang").select_option("fr")
    expect(tab.locator("html")).to_have_attribute("dir", "ltr")
    expect(tab.locator("#memory-btn")).to_contain_text("Mémoire")
    context.close()


def test_practice_runs_are_for_developers_only(page, demo):
    page.goto(f"{demo}/inside")
    expect(page.locator('[role=tab][data-view="practice"]')).to_be_hidden()
