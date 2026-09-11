import argparse
import json
import re
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


START_URL = "https://www.zhipin.com/"
PROFILE_DIR = Path(".boss_firefox_profile")
STATE_FILE = Path("boss_state.json")
DEFAULT_MESSAGE = "您好，我对这个岗位很感兴趣，方便的话希望进一步了解。"
STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || {runtime: {}};
console.table = () => undefined;
console.table.toString = () => 'function table() { [native code] }';
performance.now = () => Date.now() - performance.timeOrigin;
"""


def parse_args():
    parser = argparse.ArgumentParser(description="Boss 直聘职位搜索与简历投递助手")
    parser.add_argument("--keyword", required=True, help="搜索关键词，例如：AI 数据开发")
    parser.add_argument("--city-code", default="101280300", help="Boss 城市代码，默认惠州")
    parser.add_argument("--max-jobs", type=int, default=10, help="目标成功投递数，默认 10")
    parser.add_argument("--max-pages", type=int, default=5, help="最多搜索页数，默认 5")
    parser.add_argument("--delay", type=float, default=2.0, help="职位之间的等待秒数，默认 2")
    parser.add_argument("--message", default=DEFAULT_MESSAGE, help="沟通时发送的消息")
    parser.add_argument("--send", action="store_true", help="实际点击投递/沟通按钮并发送消息")
    parser.add_argument("--dry-run", action="store_true", help="只识别职位，不执行任何投递操作")
    parser.add_argument("--reset-state", action="store_true", help="清空已处理职位记录")
    return parser.parse_args()


def load_state(reset=False):
    if reset or not STATE_FILE.exists():
        return {"processed": {}}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print("[提示] 状态文件无法读取，将重新开始记录", flush=True)
        return {"processed": {}}


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def open_target_page(context, url: str):
    page = context.pages[0] if context.pages else context.new_page()
    for stray_page in context.pages[1:]:
        if stray_page.url == "about:blank":
            stray_page.close()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except PlaywrightTimeoutError:
        print(f"[提示] 页面加载较慢，继续等待: {url}", flush=True)
    except PlaywrightError as error:
        # SPA 会在登录或安全检查时主动重定向，不能把这种中断当成程序崩溃。
        if page.url == "about:blank":
            raise
        print(f"[提示] 页面发生重定向，继续使用当前页面: {page.url}", flush=True)
    if page.url == "about:blank":
        page.goto(url, wait_until="load", timeout=30000)
    return page


def watch_page(page):
    page.on("framenavigated", lambda frame: print(
        f"[导航][{'主文档' if frame.parent_frame is None else '子框架'}] {frame.url}",
        flush=True,
    ))
    page.on("close", lambda: print("[页面关闭]", flush=True))
    page.on("crash", lambda: print("[页面崩溃]", flush=True))


def text_of(locator):
    try:
        return re.sub(r"\s+", " ", locator.inner_text(timeout=3000)).strip()
    except Exception:
        return ""


def collect_jobs(page, max_jobs):
    selectors = [
        "a.job-card-wrapper",
        ".job-card-wrapper",
        ".job-primary",
        "a[href*='/job_detail/']",
        ".job-list-box li",
        ".job-list li",
    ]
    cards = []
    for selector in selectors:
        cards = page.locator(selector).all()
        if cards:
            break

    jobs = []
    seen = set()
    for card in cards:
        try:
            href = card.get_attribute("href", timeout=2000)
        except PlaywrightTimeoutError:
            continue
        if not href:
            link = card.locator("a").first
            try:
                href = link.get_attribute("href", timeout=2000)
            except PlaywrightTimeoutError:
                continue
        if not href:
            continue
        if href.startswith("/"):
            href = f"https://www.zhipin.com{href}"
        if (
            href in seen
            or "zhipin.com" not in href
            or not re.search(r"/job_detail/[^/?#]+", href)
        ):
            continue
        seen.add(href)
        jobs.append({"url": href, "title": text_of(card)[:160] or href})
        if len(jobs) >= max_jobs:
            break
    return jobs


def search_jobs(page, keyword, city_code, page_number):
    search_url = (
        "https://www.zhipin.com/web/geek/job?"
        f"query={quote(keyword)}&city={quote(city_code)}&page={page_number}"
    )
    open_target_page(page.context, search_url)
    page.wait_for_timeout(5000)


def first_visible_button(page, names):
    for name in names:
        locator = page.get_by_role("button", name=re.compile(name))
        if locator.count() and locator.first.is_visible():
            return locator.first
        locator = page.get_by_text(re.compile(name))
        if locator.count() and locator.first.is_visible():
            return locator.first
    return None


def submit_job(page, message):
    action = first_visible_button(page, ["立即沟通", "投递简历", "立即投递", "立即申请"])
    if action is None:
        return "未找到投递按钮", False
    action.click(timeout=8000)
    page.wait_for_timeout(1200)

    message_box = None
    for selector in [
        "textarea[placeholder*='打招呼']",
        "textarea[placeholder*='消息']",
        "textarea",
        "input[placeholder*='消息']",
    ]:
        candidate = page.locator(selector).first
        if candidate.count() and candidate.is_visible():
            message_box = candidate
            break
    if message_box is None:
        return "已点击投递入口，投递成功（未发送招呼语）", True

    message_box.fill(message)
    send_button = first_visible_button(page, ["发送", "立即发送"])
    if send_button is None:
        return "已点击投递入口，投递成功（招呼语未发送）", True
    send_button.click(timeout=8000)
    page.wait_for_timeout(800)
    return "已投递并发送消息", True


def process_jobs(page, jobs, state, args, remaining):
    attempted = 0
    sent = 0
    for index, job in enumerate(jobs, start=1):
        progress = sent if args.send and not args.dry_run else attempted
        if progress >= remaining:
            break
        url = job["url"]
        record = state["processed"].get(url, {})
        already_sent = (
            record.get("success") is True
            or record.get("result") == "已投递并发送消息"
        )
        if already_sent:
            print(f"[{index}/{len(jobs)}] 跳过已处理: {job['title']}", flush=True)
            continue
        print(f"[{index}/{len(jobs)}] 打开: {job['title']}", flush=True)
        page = open_target_page(page.context, url)
        page.wait_for_timeout(1200)
        attempted += 1
        if args.send and not args.dry_run:
            result, success = submit_job(page, args.message)
            if success:
                sent += 1
                state["processed"][url] = {
                    "title": job["title"],
                    "result": result,
                    "success": True,
                }
                save_state(state)
        else:
            result = "预演，未执行投递"
            success = False
        print(f"    {result}", flush=True)
        page.wait_for_timeout(max(0, int(args.delay * 1000)))
    return attempted, sent


def main():
    args = parse_args()
    if args.max_jobs <= 0:
        raise SystemExit("--max-jobs 必须大于 0")
    if args.send and not args.dry_run:
        print("[警告] 已启用真实投递，将会点击网页按钮并发送消息", flush=True)
    else:
        print("[模式] 预演，不会执行投递或发送消息", flush=True)
    state = load_state(args.reset_state)

    with sync_playwright() as playwright:
        context = playwright.firefox.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        context.add_init_script(STEALTH_SCRIPT)
        for existing_page in context.pages:
            watch_page(existing_page)
        context.on("page", lambda new_page: watch_page(new_page))

        page = open_target_page(context, START_URL)
        print(f"[启动完成] 当前 URL: {page.url}", flush=True)
        page.wait_for_timeout(5000)
        if page.url == "about:blank":
            raise SystemExit("Boss 页面被安全策略清空，请检查 Chrome 登录态或稍后重试")

        input("请在浏览器中完成登录，确认职位页面可正常访问后按回车继续...")
        seen_urls = set()
        attempted_total = 0
        sent_total = 0
        collection_limit = max(args.max_jobs * 2, args.max_jobs + 10)

        for page_number in range(1, args.max_pages + 1):
            if (args.send and not args.dry_run and sent_total >= args.max_jobs) or (
                (not args.send or args.dry_run) and attempted_total >= args.max_jobs
            ):
                break

            search_jobs(page, args.keyword, args.city_code, page_number)
            if (
                "/security.html" in page.url
                or "code=37" in page.url
                or "/web/user/" in page.url
            ):
                raise SystemExit(
                    "Boss 安全验证拦截了当前自动化环境（code=37）。请先在 Firefox 中完成验证，"
                    "确认职位列表可以正常打开后再重试。"
                )

            jobs = collect_jobs(page, collection_limit)
            if not jobs:
                page.wait_for_timeout(5000)
                jobs = collect_jobs(page, collection_limit)
            fresh_jobs = [job for job in jobs if job["url"] not in seen_urls]
            for job in fresh_jobs:
                seen_urls.add(job["url"])
            if not fresh_jobs:
                print(f"[第 {page_number} 页] 没有更多新职位，停止翻页", flush=True)
                break

            print(
                f"[第 {page_number}/{args.max_pages} 页] 找到 {len(fresh_jobs)} 个新职位候选",
                flush=True,
            )
            attempted, sent = process_jobs(
                page,
                fresh_jobs,
                state,
                args,
                args.max_jobs - (sent_total if args.send and not args.dry_run else attempted_total),
            )
            attempted_total += attempted
            sent_total += sent

        if args.send and not args.dry_run:
            print(
                f"[完成] 实际成功投递 {sent_total}/{args.max_jobs} 个，打开尝试 {attempted_total} 个",
                flush=True,
            )
        else:
            print(f"[完成] 预演打开 {attempted_total} 个，未执行投递", flush=True)


if __name__ == "__main__":
    main()
