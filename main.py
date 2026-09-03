from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


START_URL = "https://www.zhipin.com/"
PROFILE_DIR = Path(".boss_playwright_profile")
STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || {runtime: {}};

// Boss 的安全脚本会用 console.table 的执行耗时判断调试环境。
// 保持页面正常运行，同时避免该检测把 Playwright 当成打开了 DevTools。
console.table = () => undefined;
"""


def open_target_page(context, url: str):
    # 复用已有标签页，避免新建空白页后停留在 about:blank。
    page = context.pages[0] if context.pages else context.new_page()

    # 清理可能残留的空白页，减少“打开后跳到 about:blank”的体感问题。
    for stray_page in context.pages[1:]:
        if stray_page.url == "about:blank":
            stray_page.close()

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except PlaywrightTimeoutError:
        # 有些网络环境首次打开会慢一点，继续让页面自己加载。
        pass

    if page.url == "about:blank":
        page.goto(url, wait_until="load", timeout=30000)

    return page


def watch_page(page):
    """记录页面生命周期，便于定位站点或浏览器触发的异常跳转。"""
    page.on("framenavigated", lambda frame: print(
        f"[导航][{'主文档' if frame.parent_frame is None else '子框架'}] {frame.url}",
        flush=True,
    ))
    page.on("close", lambda: print("[页面关闭]", flush=True))
    page.on("crash", lambda: print("[页面崩溃]", flush=True))


with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        channel="chrome",
        args=["--disable-blink-features=AutomationControlled"],
        ignore_default_args=["--enable-automation"],
    )
    context.add_init_script(STEALTH_SCRIPT)
    for existing_page in context.pages:
        watch_page(existing_page)

    def handle_new_page(new_page):
        watch_page(new_page)
        print(f"[新标签页] {new_page.url}", flush=True)

    context.on("page", handle_new_page)

    page = open_target_page(context, START_URL)
    print(f"[启动完成] 当前 URL: {page.url}", flush=True)
    page.wait_for_timeout(5000)
    print(
        f"[等待后] 主页面 URL: {page.url}; "
        f"标签页数量: {len(context.pages)}; "
        f"标签页 URL: {[item.url for item in context.pages]}",
        flush=True,
    )

    if page.is_closed():
        print("[异常] 主页面已关闭，重新打开目标地址", flush=True)
        page = open_target_page(context, START_URL)

    input("登录完成后按回车继续...")
 
    # 后面开始自动操作
