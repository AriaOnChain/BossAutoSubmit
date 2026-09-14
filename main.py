import argparse
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


START_URL = "https://www.zhipin.com/"
PROFILE_ROOT = Path(".boss_profiles")
DEFAULT_PROFILE = "account_a"
STATE_FILE = Path("boss_state.json")
STATE_BACKUP_DIR = Path("boss_state_backups")
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

_STATE_BACKED_UP = False


def parse_args():
    parser = argparse.ArgumentParser(description="Boss 直聘职位搜索与简历投递助手")
    parser.add_argument("--keyword", required=True, help="搜索关键词，例如：AI 数据开发")
    parser.add_argument("--city-code", default="101280100", help="Boss 城市代码，默认惠州")
    parser.add_argument("--max-jobs", type=int, default=10, help="目标成功投递数，默认 10")
    parser.add_argument("--max-pages", type=int, default=0, help="最大结果加载批次，0 表示不限；按网站下一页或滚动加载")
    parser.add_argument("--salary-min", type=float, help="最低薪资，单位 K")
    parser.add_argument("--salary-max", type=float, help="最高薪资，单位 K")
    parser.add_argument("--delay", type=float, default=2.0, help="职位之间的等待秒数，默认 2")
    parser.add_argument("--message", default=DEFAULT_MESSAGE, help="沟通时发送的消息")
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help="登录态名称，默认 account_a；新增账号可用 account_b，投递记录仍共用")
    parser.add_argument("--send", action="store_true", help="实际点击投递/沟通按钮并发送消息")
    parser.add_argument("--dry-run", action="store_true", help="只识别职位，不执行任何投递操作")
    parser.add_argument("--reset-state", action="store_true", help="清空已处理职位记录")
    return parser.parse_args()


def backup_state(reason):
    global _STATE_BACKED_UP
    if _STATE_BACKED_UP or not STATE_FILE.exists():
        return
    STATE_BACKUP_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = STATE_BACKUP_DIR / f"boss_state_{reason}_{timestamp}.json"
    shutil.copy2(STATE_FILE, backup_file)
    _STATE_BACKED_UP = True
    print(f"[备份] 已保存状态备份: {backup_file}", flush=True)


def resolve_profile_dir(profile_name):
    profile_name = profile_name or DEFAULT_PROFILE
    if not re.fullmatch(r"[\w.-]+", profile_name):
        raise SystemExit("--profile 只能包含字母、数字、下划线、中划线和点")
    return PROFILE_ROOT / profile_name


def load_state(reset=False):
    if reset:
        backup_state("reset")
        return {"processed": {}}
    if not STATE_FILE.exists():
        return {"processed": {}}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        backup_state("broken")
        print("[提示] 状态文件无法读取，将重新开始记录", flush=True)
        return {"processed": {}}


def save_state(state):
    backup_state("before_write")
    temp_file = STATE_FILE.with_suffix(".json.tmp")
    temp_file.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temp_file.replace(STATE_FILE)


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


def normalize_salary_text(text):
    boss_digit_map = str.maketrans({
        "\ue031": "0",
        "\ue032": "1",
        "\ue033": "2",
        "\ue034": "3",
        "\ue035": "4",
        "\ue036": "5",
        "\ue037": "6",
        "\ue038": "7",
        "\ue039": "8",
        "\ue03a": "9",
    })
    return text.translate(boss_digit_map)


def parse_salary(text):
    text = normalize_salary_text(text)
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:-|~|到)\s*(\d+(?:\.\d+)?)\s*[万千kK]",
        text,
    )
    if match:
        low, high = float(match.group(1)), float(match.group(2))
        if "万" in match.group(0):
            low, high = low * 10, high * 10
        return low, high

    single = re.search(r"(\d+(?:\.\d+)?)\s*[万千kK]", text)
    if single:
        value = float(single.group(1))
        if "万" in single.group(0):
            value *= 10
        return value, value
    return None


def salary_matches(salary, salary_min, salary_max):
    if salary is None:
        return salary_min is None and salary_max is None
    low, high = salary
    if salary_min is not None and high < salary_min:
        return False
    if salary_max is not None and low > salary_max:
        return False
    return True


def collect_jobs(page, max_jobs, salary_min=None, salary_max=None):
    selectors = [
        "li.job-card-box",
        ".job-card-wrap",
        ".card-area",
        "a.job-card-wrapper",
        ".job-card-wrapper",
        ".job-primary",
        ".job-list-box li",
        ".job-list li",
        "a.job-name[href*='/job_detail/']",
    ]
    cards = page.evaluate(
        """(selectors) => {
            let nodes = [];
            for (const selector of selectors) {
                nodes = [...document.querySelectorAll(selector)]
                    .filter((el) => el.getClientRects().length);
                if (nodes.length) break;
            }
            return nodes.map((node) => {
                const link = node.matches('a[href*="/job_detail/"]')
                    ? node
                    : node.querySelector('a.job-name[href*="/job_detail/"], a[href*="/job_detail/"]');
                const card = link
                    ? (link.closest('li.job-card-box, .job-card-wrap, .card-area') || node)
                    : node;
                return {
                    href: link ? link.getAttribute('href') : '',
                    linkClass: link ? link.className : '',
                    linkText: link ? (link.innerText || link.textContent || '') : '',
                    text: card ? (card.innerText || card.textContent || '') : '',
                };
            });
        }""",
        selectors,
    )

    jobs = []
    seen = set()
    for card in cards:
        href = card.get("href") or ""
        link_class = card.get("linkClass") or ""
        link_text = re.sub(r"\s+", " ", card.get("linkText") or "").strip()
        if not href:
            continue
        if "more-job-btn" in link_class or "查看更多信息" in link_text:
            continue
        if href.startswith("/"):
            href = f"https://www.zhipin.com{href}"
        key = job_key(href)
        if (
            key in seen
            or "zhipin.com" not in href
            or not re.search(r"/job_detail/[^/?#]+", href)
        ):
            continue
        card_text = re.sub(r"\s+", " ", card.get("text") or "").strip()
        card_text = normalize_salary_text(card_text)
        salary = parse_salary(card_text)
        if not salary_matches(salary, salary_min, salary_max):
            continue
        seen.add(key)
        jobs.append({
            "url": href,
            "title": card_text[:160] or href,
            "salary": salary,
        })
        if len(jobs) >= max_jobs:
            break
    return jobs


def search_jobs(page, keyword, city_code):
    search_url = (
        "https://www.zhipin.com/web/geek/jobs?"
        f"query={quote(keyword)}&city={quote(city_code)}"
    )
    open_target_page(page.context, search_url)
    page.wait_for_timeout(5000)


def job_key(url):
    return urlsplit(url).path


def check_search_page(page):
    if page.is_closed():
        raise RuntimeError("搜索页已关闭")
    if any(marker in page.url for marker in ("about:blank", "/web/user/", "/security.html")):
        raise RuntimeError("搜索页进入登录、验证或空白页面，请在浏览器中处理后重试")


def advance_results(page):
    """优先使用可见分页控件，否则滚动职位所在的容器。"""
    for locator in (
        page.get_by_role("button", name=re.compile(r"^下一页$")),
        page.get_by_role("link", name=re.compile(r"^下一页$")),
        page.locator('[aria-label="下一页"], a[rel="next"]'),
    ):
        for button in locator.all():
            if not button.is_visible():
                continue
            disabled = button.get_attribute("aria-disabled") == "true"
            disabled = disabled or "disabled" in (button.get_attribute("class") or "").split()
            if disabled or not button.is_enabled():
                return "end"
            button.click(timeout=8000)
            return "next"
    page.evaluate("""() => {
        const a = [...document.querySelectorAll('a[href*="/job_detail/"]')]
            .find(el => el.getClientRects().length);
        let root = a && a.parentElement;
        while (root && root !== document.body) {
            const style = getComputedStyle(root);
            if (/(auto|scroll)/.test(style.overflowY) &&
                root.scrollHeight > root.clientHeight) break;
            root = root.parentElement;
        }
        root = root && root !== document.body ? root : document.scrollingElement;
        root.scrollBy(0, Math.max(100, root.clientHeight * 0.8));
    }""")
    return "scroll"


def iter_result_batches(page, max_batches=0, idle_limit=15):
    seen = set()
    batches = idle = 0
    awaiting_next = False
    while True:
        check_search_page(page)
        # 在每次滚动前保存职位；虚拟列表的 DOM 数量可能始终不变。
        jobs = collect_jobs(page, float("inf"))
        fresh = [job for job in jobs if job_key(job["url"]) not in seen]
        if fresh:
            seen.update(job_key(job["url"]) for job in fresh)
            batches += 1
            idle = 0
            awaiting_next = False
            print(f"[结果批次 {batches}] 新职位 {len(fresh)}，累计 {len(seen)}", flush=True)
            yield fresh
            if max_batches and batches >= max_batches:
                print("[停止] 已达到结果批次上限", flush=True)
                return
        else:
            idle += 1
            if idle >= idle_limit:
                print("[停止] 连续等待未发现新职位；可能已到末尾或加载未成功", flush=True)
                return
        if not awaiting_next:
            action = advance_results(page)
            if action == "end":
                print("[停止] 网站下一页按钮已禁用", flush=True)
                return
            awaiting_next = action == "next"
        page.wait_for_timeout(1000)



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
    try:
        # Boss 的沟通入口通常通过异步请求打开聊天窗口，可能不会完成一次
        # 标准的导航生命周期。禁止 click 等待导航，避免“已经点击成功”却
        # 被 Playwright 误报为超时。
        action.click(timeout=8000, no_wait_after=True)
    except PlaywrightTimeoutError:
        # 点击动作可能已经发出，只是页面没有在超时前完成后续状态变化。
        # 仅在页面仍可用且聊天输入框已出现时视为成功；否则交给调用方记录
        # 失败并继续处理下一个职位。
        page.wait_for_timeout(800)
        if page.is_closed():
            return "点击投递入口超时，详情页已关闭", False
        if not any(
            page.locator(selector).count()
            and page.locator(selector).first.is_visible()
            for selector in (
                "textarea[placeholder*='打招呼']",
                "textarea[placeholder*='消息']",
                "textarea",
                "input[placeholder*='消息']",
            )
        ):
            return "点击投递入口超时，未确认投递结果", False
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
    skipped = 0
    for index, job in enumerate(jobs, start=1):
        progress = sent if args.send and not args.dry_run else attempted
        if progress >= remaining:
            break
        url = job["url"]
        record = next(
            (value for key, value in state["processed"].items()
             if job_key(key) == job_key(url) and (
                 value.get("success") is True
                 or value.get("result") == "已投递并发送消息")),
            {},
        )
        already_sent = (
            record.get("success") is True
            or record.get("result") == "已投递并发送消息"
        )
        if already_sent:
            skipped += 1
            print(f"[{index}/{len(jobs)}] 跳过已处理: {job['title']}", flush=True)
            continue
        print(f"[{index}/{len(jobs)}] 打开: {job['title']}", flush=True)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except PlaywrightTimeoutError:
            print("    页面加载超时，跳过此职位", flush=True)
            continue
        except PlaywrightError as error:
            print(f"    页面打开失败，跳过此职位: {error}", flush=True)
            continue
        page.wait_for_timeout(1200)
        attempted += 1
        if args.send and not args.dry_run:
            try:
                result, success = submit_job(page, args.message)
            except PlaywrightError as error:
                result, success = f"投递操作失败，已跳过: {error}", False
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
    return attempted, sent, skipped


def main():
    args = parse_args()
    profile_dir = resolve_profile_dir(args.profile)
    if args.max_jobs <= 0:
        raise SystemExit("--max-jobs 必须大于 0")
    if args.max_pages < 0:
        raise SystemExit("--max-pages 不能小于 0")
    if args.salary_min is not None and args.salary_min < 0:
        raise SystemExit("--salary-min 不能小于 0")
    if args.salary_max is not None and args.salary_max < 0:
        raise SystemExit("--salary-max 不能小于 0")
    if (
        args.salary_min is not None
        and args.salary_max is not None
        and args.salary_min > args.salary_max
    ):
        raise SystemExit("--salary-min 不能大于 --salary-max")
    if args.send and not args.dry_run:
        print("[警告] 已启用真实投递，将会点击网页按钮并发送消息", flush=True)
    else:
        print("[模式] 预演，不会执行投递或发送消息", flush=True)
    print(f"[登录态] 使用浏览器 Profile: {profile_dir}", flush=True)
    print(f"[投递记录] 多账号共用: {STATE_FILE}", flush=True)
    state = load_state(args.reset_state)

    with sync_playwright() as playwright:
        context = playwright.firefox.launch_persistent_context(
            user_data_dir=str(profile_dir),
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
        search_jobs(page, args.keyword, args.city_code)
        attempted_total = sent_total = skipped_total = 0
        detail_page = context.new_page()
        real_send = args.send and not args.dry_run
        try:
            for batch in iter_result_batches(page, args.max_pages):
                jobs = [job for job in batch if salary_matches(
                    job["salary"], args.salary_min, args.salary_max)]
                print(f"[薪资筛选] 保留 {len(jobs)}/{len(batch)} 个", flush=True)
                remaining = args.max_jobs - (sent_total if real_send else attempted_total)
                attempted, sent, skipped = process_jobs(
                    detail_page, jobs, state, args, remaining)
                attempted_total += attempted
                sent_total += sent
                skipped_total += skipped
                if (sent_total if real_send else attempted_total) >= args.max_jobs:
                    break
        finally:
            if not detail_page.is_closed():
                detail_page.close()

        if args.send and not args.dry_run:
            print(
                f"[完成] 实际成功投递 {sent_total}/{args.max_jobs} 个，"
                f"打开尝试 {attempted_total} 个，跳过历史成功 {skipped_total} 个",
                flush=True,
            )
        else:
            print(
                f"[完成] 预演打开 {attempted_total} 个，跳过历史成功 {skipped_total} 个，未执行投递",
                flush=True,
            )


if __name__ == "__main__":
    main()
