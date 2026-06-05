#!/usr/bin/env python3
"""
CTDOTEAM - Discord Quest Auto-Completer (Async Module)
"""

import aiohttp
import asyncio
import time
import json
import random
import sys
import os
import re
import base64
import traceback
from datetime import datetime, timezone
from typing import Optional

# ── Config ─────────────────────────────────────────────────────────────────────
API_BASE = "https://discord.com/api/v9"
POLL_INTERVAL = 60          # seconds between quest scans
HEARTBEAT_INTERVAL = 20     # seconds between heartbeat calls
AUTO_ACCEPT = True          # auto-enroll in all available quests
LOG_PROGRESS = True
DEBUG = True                # verbose debug logging

SUPPORTED_TASKS = [
    "WATCH_VIDEO",
    "PLAY_ON_DESKTOP",
    "STREAM_ON_DESKTOP",
    "PLAY_ACTIVITY",
    "WATCH_VIDEO_ON_MOBILE",
]


# ── Logging ────────────────────────────────────────────────────────────────────
class Colors:
    RESET  = "\033[0m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    RED    = "\033[91m"
    CYAN   = "\033[96m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"


# Log and report callbacks for discord bot integration
log_callback = None
report_callback = None

def register_log_callback(callback):
    global log_callback
    log_callback = callback

def register_report_callback(callback):
    global report_callback
    report_callback = callback


def log(msg: str, level: str = "info"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {
        "info":     f"{Colors.CYAN}[INFO]{Colors.RESET}",
        "ok":       f"{Colors.GREEN}[  OK]{Colors.RESET}",
        "warn":     f"{Colors.YELLOW}[WARN]{Colors.RESET}",
        "error":    f"{Colors.RED}[ ERR]{Colors.RESET}",
        "progress": f"{Colors.DIM}[PROG]{Colors.RESET}",
        "debug":    f"{Colors.DIM}[DBG ]{Colors.RESET}",
    }.get(level, f"[{level.upper()}]")

    if level == "debug" and not DEBUG:
        return
    
    formatted_msg = f"{Colors.DIM}{ts}{Colors.RESET} {prefix} {msg}"
    if LOG_PROGRESS or level != "progress":
        print(formatted_msg)

    # Invoke registered callback to send to Discord channel
    if log_callback:
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                # Strip ANSI color codes for Discord text messages
                clean_msg = re.sub(r'\x1b\[[0-9;]*m', '', msg)
                loop.create_task(log_callback(clean_msg, level))
        except RuntimeError:
            pass


# ── Build number fetcher ───────────────────────────────────────────────────────
async def fetch_latest_build_number() -> int:
    """Scrape Discord web app to get the latest client_build_number."""
    FALLBACK = 504649
    try:
        log("Đang lấy build number mới nhất từ Discord...", "info")
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        async with aiohttp.ClientSession() as session:
            async with session.get("https://discord.com/app", headers={"User-Agent": ua}, timeout=15) as r:
                if r.status != 200:
                    log(f"Không lấy được trang Discord ({r.status}), dùng fallback", "warn")
                    return FALLBACK
                text = await r.text()

            scripts = re.findall(r'/assets/([a-f0-9]+)\.js', text)
            if not scripts:
                scripts_alt = re.findall(r'src="(/assets/[^"]+\.js)"', text)
                scripts = [s.split('/')[-1].replace('.js', '') for s in scripts_alt]

            if not scripts:
                log("Không tìm thấy JS assets, dùng fallback", "warn")
                return FALLBACK

            for asset_hash in scripts[-5:]:
                try:
                    async with session.get(
                        f"https://discord.com/assets/{asset_hash}.js",
                        headers={"User-Agent": ua}, timeout=15
                    ) as ar:
                        ar_text = await ar.text()
                        m = re.search(r'buildNumber["\s:]+["\s]*(\d{5,7})', ar_text)
                        if m:
                            bn = int(m.group(1))
                            log(f"Build number: {Colors.BOLD}{bn}{Colors.RESET}", "ok")
                            return bn
                except Exception:
                    continue

            log(f"Không tìm thấy build number, dùng fallback {FALLBACK}", "warn")
            return FALLBACK
    except Exception as e:
        log(f"Lỗi lấy build number: {e}, dùng fallback {FALLBACK}", "warn")
        return FALLBACK


def make_super_properties(build_number: int) -> str:
    """Create base64-encoded X-Super-Properties header."""
    obj = {
        "os": "Windows",
        "browser": "Discord Client",
        "release_channel": "stable",
        "client_version": "1.0.9175",
        "os_version": "10.0.26100",
        "os_arch": "x64",
        "app_arch": "x64",
        "system_locale": "en-US",
        "browser_user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "discord/1.0.9175 Chrome/128.0.6613.186 "
            "Electron/32.2.7 Safari/537.36"
        ),
        "browser_version": "32.2.7",
        "client_build_number": build_number,
        "native_build_number": 59498,
        "client_event_source": None,
    }
    return base64.b64encode(json.dumps(obj).encode()).decode()


# ── HTTP helpers ───────────────────────────────────────────────────────────────
class DiscordAPI:
    def __init__(self, token: str, build_number: int):
        self.token = token
        self.build_number = build_number
        self.session = None
        self.ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "discord/1.0.9175 Chrome/128.0.6613.186 "
            "Electron/32.2.7 Safari/537.36"
        )
        self.sp = make_super_properties(build_number)

    async def get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(headers={
                "Authorization": self.token,
                "Content-Type": "application/json",
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "User-Agent": self.ua,
                "X-Super-Properties": self.sp,
                "X-Discord-Locale": "en-US",
                "X-Discord-Timezone": "Asia/Ho_Chi_Minh",
                "Origin": "https://discord.com",
                "Referer": "https://discord.com/channels/@me",
            })
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def get(self, path: str, **kwargs) -> aiohttp.ClientResponse:
        url = f"{API_BASE}{path}"
        log(f"GET {path}", "debug")
        session = await self.get_session()
        r = await session.get(url, **kwargs)
        log(f"  -> {r.status} (async)", "debug")
        return r

    async def post(self, path: str, payload: Optional[dict] = None, **kwargs) -> aiohttp.ClientResponse:
        url = f"{API_BASE}{path}"
        log(f"POST {path}", "debug")
        session = await self.get_session()
        r = await session.post(url, json=payload, **kwargs)
        log(f"  -> {r.status} (async)", "debug")
        return r

    async def validate_token(self) -> bool:
        try:
            r = await self.get("/users/@me")
            if r.status == 200:
                user = await r.json()
                name = user.get("username", "?")
                log(f"Đăng nhập: {Colors.BOLD}{name}{Colors.RESET} (ID: {user['id']})", "ok")
                return True
            else:
                log(f"Token không hợp lệ (status {r.status})", "error")
                return False
        except Exception as e:
            log(f"Không thể kết nối tới Discord: {e}", "error")
            return False


# ── Quest helpers (handles both camelCase & snake_case) ────────────────────────
def _get(d: Optional[dict], *keys):
    """Get value from dict trying multiple key names."""
    if d is None:
        return None
    for k in keys:
        if k in d:
            return d[k]
    return None


def get_task_config(quest: dict) -> Optional[dict]:
    cfg = quest.get("config", {})
    return _get(cfg, "taskConfig", "task_config", "taskConfigV2", "task_config_v2")


def get_quest_name(quest: dict) -> str:
    cfg = quest.get("config", {})
    msgs = cfg.get("messages", {})
    name = _get(msgs, "questName", "quest_name")
    if name:
        return name.strip()
    game = _get(msgs, "gameTitle", "game_title")
    if game:
        return game.strip()
    app_name = cfg.get("application", {}).get("name")
    if app_name:
        return app_name
    return f"Quest#{quest.get('id', '?')}"


def get_expires_at(quest: dict) -> Optional[str]:
    cfg = quest.get("config", {})
    return _get(cfg, "expiresAt", "expires_at")


def get_user_status(quest: dict) -> dict:
    us = _get(quest, "userStatus", "user_status")
    return us if isinstance(us, dict) else {}


def is_completable(quest: dict) -> bool:
    expires = get_expires_at(quest)
    if expires:
        try:
            exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
            if exp_dt <= datetime.now(timezone.utc):
                return False
        except Exception:
            pass

    tc = get_task_config(quest)
    if not tc or "tasks" not in tc:
        return False

    tasks = tc["tasks"]
    return any(tasks.get(t) is not None for t in SUPPORTED_TASKS)


def is_enrolled(quest: dict) -> bool:
    us = get_user_status(quest)
    return bool(_get(us, "enrolledAt", "enrolled_at"))


def is_completed(quest: dict) -> bool:
    us = get_user_status(quest)
    return bool(_get(us, "completedAt", "completed_at"))


def get_task_type(quest: dict) -> Optional[str]:
    tc = get_task_config(quest)
    if not tc or "tasks" not in tc:
        return None
    for t in SUPPORTED_TASKS:
        if tc["tasks"].get(t) is not None:
            return t
    return None


def get_seconds_needed(quest: dict) -> int:
    tc = get_task_config(quest)
    task_type = get_task_type(quest)
    if not tc or not task_type:
        return 0
    return tc["tasks"][task_type].get("target", 0)


def get_seconds_done(quest: dict) -> float:
    task_type = get_task_type(quest)
    if not task_type:
        return 0
    us = get_user_status(quest)
    progress = us.get("progress", {})
    if not progress:
        progress = {}
    return progress.get(task_type, {}).get("value", 0)


def get_enrolled_at(quest: dict) -> Optional[str]:
    us = get_user_status(quest)
    return _get(us, "enrolledAt", "enrolled_at")


# ── Core logic ─────────────────────────────────────────────────────────────────
class QuestAutocompleter:
    def __init__(self, api: DiscordAPI, user_id: str, username: str, avatar_url: str):
        self.api = api
        self.user_id = user_id
        self.username = username
        self.avatar_url = avatar_url
        self.completed_ids: set = set()
        self.running = False

    def log(self, msg: str, level: str = "info"):
        """Wrapper to add username context to logs."""
        log(f"[@{self.username}] {msg}", level)

    async def trigger_report(self, results: list):
        """Invoke report callback to send summary report to Discord channel."""
        if report_callback:
            try:
                loop = asyncio.get_running_loop()
                if loop.is_running():
                    loop.create_task(report_callback(self.user_id, self.username, self.avatar_url, results))
            except RuntimeError:
                pass

    # ── Fetch quests ───────────────────────────────────────────────────────────
    async def fetch_quests(self) -> list:
        try:
            r = await self.api.get("/quests/@me")

            if r.status == 200:
                data = await r.json()
                if isinstance(data, dict):
                    quests = data.get("quests", [])
                    excluded = data.get("excluded_quests", [])
                    blocked = _get(data, "quest_enrollment_blocked_until")
                    if blocked:
                        self.log(f"Enrollment blocked until: {blocked}", "warn")
                    if excluded:
                        self.log(f"{len(excluded)} quest(s) excluded", "debug")
                    return quests
                elif isinstance(data, list):
                    return data
                return []

            elif r.status == 429:
                body = await r.json()
                retry_after = body.get("retry_after", 10)
                self.log(f"Rate limited – chờ {retry_after}s", "warn")
                await asyncio.sleep(retry_after)
                return await self.fetch_quests()
            else:
                text = await r.text()
                self.log(f"Quest fetch lỗi ({r.status}): {text[:200]}", "warn")
                return []

        except Exception as e:
            self.log(f"Error fetching quests: {e}", "error")
            if DEBUG:
                traceback.print_exc()
            return []

    # ── Auto-accept ────────────────────────────────────────────────────────────
    async def enroll_quest(self, quest: dict) -> bool:
        name = get_quest_name(quest)
        qid = quest["id"]

        for attempt in range(1, 4):
            try:
                r = await self.api.post(f"/quests/{qid}/enroll", {
                    "location": 11,
                    "is_targeted": False,
                    "metadata_raw": None,
                    "metadata_sealed": None,
                    "traffic_metadata_raw": quest.get("traffic_metadata_raw"),
                    "traffic_metadata_sealed": quest.get("traffic_metadata_sealed"),
                })

                if r.status == 429:
                    body = await r.json()
                    retry_after = body.get("retry_after", 5)
                    wait = retry_after + 1
                    self.log(f"Rate limited nhận \"{name}\" (lần {attempt}/3) – chờ {wait}s", "warn")
                    await asyncio.sleep(wait)
                    continue

                if r.status in (200, 201, 204):
                    self.log(f"Đã nhận: {Colors.BOLD}{name}{Colors.RESET}", "ok")
                    return True

                text = await r.text()
                self.log(f"Enroll \"{name}\" thất bại ({r.status}): {text[:200]}", "warn")
                return False

            except Exception as e:
                self.log(f"Lỗi enroll \"{name}\": {e}", "error")
                return False

        self.log(f"Bỏ qua \"{name}\" sau 3 lần rate limited", "warn")
        return False

    async def auto_accept(self, quests: list) -> list:
        if not AUTO_ACCEPT:
            return quests

        unaccepted = [
            q for q in quests
            if not is_enrolled(q) and not is_completed(q) and is_completable(q)
        ]

        if not unaccepted:
            return quests

        self.log(f"Tìm thấy {len(unaccepted)} quest chưa nhận – đang auto-accept...", "info")

        for q in unaccepted:
            if not self.running:
                break
            await self.enroll_quest(q)
            await asyncio.sleep(3)

        await asyncio.sleep(2)
        return await self.fetch_quests()

    # ── Complete: WATCH_VIDEO ──────────────────────────────────────────────────
    async def complete_video(self, quest: dict):
        name = get_quest_name(quest)
        qid = quest["id"]
        seconds_needed = get_seconds_needed(quest)
        seconds_done = get_seconds_done(quest)
        enrolled_at_str = get_enrolled_at(quest)

        if enrolled_at_str:
            enrolled_ts = datetime.fromisoformat(enrolled_at_str.replace("Z", "+00:00")).timestamp()
        else:
            enrolled_ts = time.time()

        self.log(f"🎬 Video: {Colors.BOLD}{name}{Colors.RESET} ({seconds_done:.0f}/{seconds_needed}s)", "info")

        max_future = 10
        speed = 7
        interval = 1

        while seconds_done < seconds_needed and self.running:
            max_allowed = (time.time() - enrolled_ts) + max_future
            diff = max_allowed - seconds_done
            timestamp = seconds_done + speed

            if diff >= speed:
                try:
                    r = await self.api.post(f"/quests/{qid}/video-progress", {
                        "timestamp": min(seconds_needed, timestamp + random.random())
                    })
                    if r.status == 200:
                        body = await r.json()
                        if body.get("completed_at"):
                            self.log(f"✅ Hoàn thành: {Colors.BOLD}{name}{Colors.RESET}", "ok")
                            return
                        seconds_done = min(seconds_needed, timestamp)
                        self.log(f"  [{name}] {seconds_done:.0f}/{seconds_needed}s", "progress")
                    elif r.status == 429:
                        body = await r.json()
                        retry_after = body.get("retry_after", 5)
                        self.log(f"  Rate limited – chờ {retry_after + 1}s", "warn")
                        await asyncio.sleep(retry_after + 1)
                        continue
                    else:
                        text = await r.text()
                        self.log(f"  Video progress lỗi ({r.status}): {text[:200]}", "warn")
                except Exception as e:
                    self.log(f"  Lỗi: {e}", "error")

            if timestamp >= seconds_needed:
                break
            await asyncio.sleep(interval)

        try:
            await self.api.post(f"/quests/{qid}/video-progress", {"timestamp": seconds_needed})
        except Exception:
            pass
        self.log(f"✅ Hoàn thành: {Colors.BOLD}{name}{Colors.RESET}", "ok")

    # ── Complete: PLAY_ON_DESKTOP / STREAM_ON_DESKTOP ──────────────────────────
    async def complete_heartbeat(self, quest: dict):
        name = get_quest_name(quest)
        qid = quest["id"]
        task_type = get_task_type(quest)
        seconds_needed = get_seconds_needed(quest)
        seconds_done = get_seconds_done(quest)

        remaining = max(0, seconds_needed - seconds_done)
        self.log(
            f"🎮 {task_type}: {Colors.BOLD}{name}{Colors.RESET} "
            f"(~{remaining // 60} phút còn lại)",
            "info"
        )

        pid = random.randint(1000, 30000)

        while seconds_done < seconds_needed and self.running:
            try:
                r = await self.api.post(f"/quests/{qid}/heartbeat", {
                    "stream_key": f"call:0:{pid}",
                    "terminal": False,
                })

                if r.status == 200:
                    body = await r.json()
                    progress_data = body.get("progress", {})
                    if progress_data and task_type in progress_data:
                        seconds_done = progress_data[task_type].get("value", seconds_done)
                    self.log(f"  [{name}] {seconds_done:.0f}/{seconds_needed}s", "progress")

                    if body.get("completed_at") or seconds_done >= seconds_needed:
                        self.log(f"✅ Hoàn thành: {Colors.BOLD}{name}{Colors.RESET}", "ok")
                        return

                elif r.status == 429:
                    body = await r.json()
                    retry_after = body.get("retry_after", 10)
                    self.log(f"  Rate limited – chờ {retry_after + 1}s", "warn")
                    await asyncio.sleep(retry_after + 1)
                    continue
                else:
                    text = await r.text()
                    self.log(f"  Heartbeat lỗi ({r.status}): {text[:200]}", "warn")

            except Exception as e:
                self.log(f"  Lỗi heartbeat: {e}", "error")

            await asyncio.sleep(HEARTBEAT_INTERVAL)

        try:
            await self.api.post(f"/quests/{qid}/heartbeat", {
                "stream_key": f"call:0:{pid}",
                "terminal": True,
            })
        except Exception:
            pass
        self.log(f"✅ Hoàn thành: {Colors.BOLD}{name}{Colors.RESET}", "ok")

    # ── Complete: PLAY_ACTIVITY ────────────────────────────────────────────────
    async def complete_activity(self, quest: dict):
        name = get_quest_name(quest)
        qid = quest["id"]
        seconds_needed = get_seconds_needed(quest)
        seconds_done = get_seconds_done(quest)

        remaining = max(0, seconds_needed - seconds_done)
        self.log(
            f"🕹️  Activity: {Colors.BOLD}{name}{Colors.RESET} "
            f"(~{remaining // 60} phút còn lại)",
            "info"
        )

        stream_key = "call:0:1"

        while seconds_done < seconds_needed and self.running:
            try:
                r = await self.api.post(f"/quests/{qid}/heartbeat", {
                    "stream_key": stream_key,
                    "terminal": False,
                })

                if r.status == 200:
                    body = await r.json()
                    progress_data = body.get("progress", {})
                    if progress_data and "PLAY_ACTIVITY" in progress_data:
                        seconds_done = progress_data["PLAY_ACTIVITY"].get("value", seconds_done)
                    self.log(f"  [{name}] {seconds_done:.0f}/{seconds_needed}s", "progress")

                    if body.get("completed_at") or seconds_done >= seconds_needed:
                        break
                elif r.status == 429:
                    body = await r.json()
                    retry_after = body.get("retry_after", 10)
                    self.log(f"  Rate limited – chờ {retry_after + 1}s", "warn")
                    await asyncio.sleep(retry_after + 1)
                    continue
                else:
                    text = await r.text()
                    self.log(f"  Heartbeat lỗi ({r.status}): {text[:200]}", "warn")
            except Exception as e:
                self.log(f"  Lỗi: {e}", "error")

            await asyncio.sleep(HEARTBEAT_INTERVAL)

        try:
            await self.api.post(f"/quests/{qid}/heartbeat", {
                "stream_key": stream_key,
                "terminal": True,
            })
        except Exception:
            pass
        self.log(f"✅ Hoàn thành: {Colors.BOLD}{name}{Colors.RESET}", "ok")

    # ── Process a single quest ─────────────────────────────────────────────────
    async def process_quest(self, quest: dict):
        qid = quest.get("id")
        name = get_quest_name(quest)
        task_type = get_task_type(quest)

        if not task_type:
            self.log(f"\"{name}\" – task không hỗ trợ, bỏ qua", "warn")
            return

        if qid in self.completed_ids:
            return

        self.log(f"━━━ Bắt đầu: {Colors.BOLD}{name}{Colors.RESET} (task: {task_type}) ━━━", "info")

        if task_type in ("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE"):
            await self.complete_video(quest)
        elif task_type in ("PLAY_ON_DESKTOP", "STREAM_ON_DESKTOP"):
            await self.complete_heartbeat(quest)
        elif task_type == "PLAY_ACTIVITY":
            await self.complete_activity(quest)

        self.completed_ids.add(qid)

    # ── Main loop ──────────────────────────────────────────────────────────────
    async def run(self):
        self.log("=" * 60, "info")
        self.log(f"{Colors.BOLD}Discord Quest Auto-Completer v3.0 (Async Bot Mode){Colors.RESET}", "info")
        self.log(f"Auto-accept: {'BẬT' if AUTO_ACCEPT else 'TẮT'}  |  Poll: {POLL_INTERVAL}s", "info")
        self.log("=" * 60, "info")

        self.running = True
        cycle = 0
        while self.running:
            cycle += 1
            self.log(f"── Quét lần #{cycle} ──", "info")

            quests = await self.fetch_quests()
            
            if not quests:
                self.log("Không có quest nào", "info")
            else:
                # Auto-accept
                quests = await self.auto_accept(quests)

                # Filter actionable
                actionable = [
                    q for q in quests
                    if is_enrolled(q) and not is_completed(q) and is_completable(q)
                    and q.get("id") not in self.completed_ids
                ]

                if actionable:
                    self.log(f"\n{len(actionable)} quest(s) cần hoàn thành:", "info")
                    
                    results = []
                    for q in actionable:
                        if not self.running:
                            break
                        
                        name = get_quest_name(q)
                        try:
                            # Process the quest
                            task_type = get_task_type(q)
                            if task_type in ("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE"):
                                await self.complete_video(q)
                            elif task_type in ("PLAY_ON_DESKTOP", "STREAM_ON_DESKTOP"):
                                await self.complete_heartbeat(q)
                            elif task_type == "PLAY_ACTIVITY":
                                await self.complete_activity(q)
                                
                            results.append({"name": name, "success": True})
                            self.completed_ids.add(q.get("id"))
                        except Exception as e:
                            self.log(f"Lỗi khi xử lý quest \"{name}\": {e}", "error")
                            results.append({"name": name, "success": False})
                    
                    # Trigger summary report at the end of the processing
                    if results:
                        await self.trigger_report(results)
                else:
                    self.log("Không có quest nào cần hoàn thành lúc này", "info")

            self.log(f"\nChờ {POLL_INTERVAL}s... (Sử dụng /stop để dừng)\n", "info")
            
            # Sleep in 1s increments to respond quickly to stops
            for _ in range(POLL_INTERVAL):
                if not self.running:
                    break
                await asyncio.sleep(1)
