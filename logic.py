# logic.py  — 更強韌版（Windows 解鎖 + 原子寫入 + 重試 + WOW64 路徑處理）

import os
import sys
import platform
import shutil
import tempfile
import time
import ctypes
import urllib.request
import urllib.error
from datetime import datetime
from typing import Tuple

# 暫存檔（下載結果）
TEMP_DOMAINS_FILE = os.path.join(tempfile.gettempdir(), "hosts_updater_domains.tmp")

# 備份命名與輪替
BACKUP_PREFIX = ".backup_"
MAX_BACKUPS = 10

# 重試參數（處理被 AV/系統短暫鎖住）
RETRY_TIMES = 6
RETRY_BASE_SLEEP = 0.25  # 秒（會做指數退避）


# -----------------------
# 平台與路徑工具
# -----------------------
def is_windows() -> bool:
    return platform.system() == "Windows"


def get_default_hosts_path() -> str:
    if is_windows():
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        return os.path.join(system_root, "System32", "drivers", "etc", "hosts")
    return "/etc/hosts"


def _is_wow64_32bit_process_on_64bit_windows() -> bool:
    """32 位元 Python 在 64 位元 Windows 上執行時為 True。"""
    if not is_windows():
        return False
    try:
        return bool(os.environ.get("PROCESSOR_ARCHITEW6432")) or \
               (ctypes.sizeof(ctypes.c_void_p) == 4 and "PROGRAMFILES(X86)" in os.environ)
    except Exception:
        return False


def resolve_windows_hosts_realpath(path: str) -> str:
    """
    避免 WOW64 路徑轉向：32-bit 行程要存取真正的 System32 需走 Sysnative。
    在多數情況下兩處的 hosts 是同一份，但個別環境可能有差異；保險起見做解析。
    """
    if not is_windows():
        return path
    try:
        norm = os.path.normpath(path)
        if _is_wow64_32bit_process_on_64bit_windows() and norm.lower().startswith(
            (r"c:\windows\system32", os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32").lower())
        ):
            sysnative = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Sysnative")
            mapped = norm.replace(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"), sysnative)
            # 只有在 Sysnative 存在且檔案存在時才替換
            if os.path.exists(mapped):
                return mapped
        return norm
    except Exception:
        return path


# -----------------------
# Windows 檔案屬性處理
# -----------------------
def _win_clear_attrs(path: str):
    """移除唯讀/系統/隱藏，避免無法覆寫。"""
    FILE_ATTRIBUTE_READONLY = 0x0001
    FILE_ATTRIBUTE_HIDDEN   = 0x0002
    FILE_ATTRIBUTE_SYSTEM   = 0x0004

    try:
        GetFileAttributesW = ctypes.windll.kernel32.GetFileAttributesW
        SetFileAttributesW = ctypes.windll.kernel32.SetFileAttributesW
        GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
        GetFileAttributesW.restype = ctypes.c_uint32
        SetFileAttributesW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
        SetFileAttributesW.restype = ctypes.c_int

        attrs = GetFileAttributesW(path)
        if attrs == 0xFFFFFFFF:
            return  # 取不到就算了

        new_attrs = attrs & ~(FILE_ATTRIBUTE_READONLY | FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM)
        if new_attrs != attrs:
            SetFileAttributesW(path, new_attrs)
    except Exception:
        pass  # 失敗就忽略，不影響主流程


def _ensure_writable(path: str) -> Tuple[bool, str]:
    """確認目錄存在且可寫。"""
    try:
        directory = os.path.dirname(path) or "."
        if not os.path.isdir(directory):
            return False, f"錯誤：目錄不存在：{directory}"
        testfile = tempfile.NamedTemporaryFile(delete=True, dir=directory)
        testfile.close()
        return True, ""
    except PermissionError:
        return False, "權限錯誤：目標目錄不可寫入。請以『系統管理員 / root』執行。"
    except Exception as e:
        return False, f"寫入檢查失敗：{e}"


# -----------------------
# 重試輔助
# -----------------------
def _with_retries(func, *args, **kwargs):
    """
    在 PermissionError / OSError(資源被佔用) 等情況重試多次，指數退避。
    """
    last_exc = None
    for i in range(RETRY_TIMES):
        try:
            return func(*args, **kwargs)
        except (PermissionError, OSError) as e:
            last_exc = e
            # 指數退避
            time.sleep(RETRY_BASE_SLEEP * (2 ** i))
        except Exception as e:
            raise
    # 重試仍失敗
    if last_exc:
        raise last_exc


# -----------------------
# 下載
# -----------------------
def _http_get_text(url: str, timeout: float = 25.0) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "HostsUpdater/1.1 (+https://local.app)"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="replace")


def download_domains(url: str):
    if not url or not url.startswith(("http://", "https://")):
        return "錯誤：請提供有效的 URL（需以 http:// 或 https:// 開頭）。", "error"
    try:
        text = _http_get_text(url, timeout=30.0).replace("\r", "")
        line_count = len(text.split("\n"))
        with open(TEMP_DOMAINS_FILE, "w", encoding="utf-8") as f:
            f.write(text)
        return f"成功從 {url} 下載 {line_count} 行內容，已暫存。", "success"
    except urllib.error.HTTPError as e:
        return f"HTTP 錯誤（{e.code}）：無法下載 {url}。", "error"
    except urllib.error.URLError as e:
        return f"網路錯誤：無法連線 {url}。\n詳細：{getattr(e, 'reason', e)}", "error"
    except Exception as e:
        return f"下載過程發生未預期錯誤：\n{e}", "error"


# -----------------------
# 備份 / 寫入 / 還原
# -----------------------
def _make_backup(hosts_path: str) -> str:
    directory = os.path.dirname(hosts_path)
    base = os.path.basename(hosts_path)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"{base}{BACKUP_PREFIX}{ts}"
    backup_path = os.path.join(directory, backup_name)

    def _copy():
        with open(hosts_path, "rb") as src, open(backup_path, "wb") as dst:
            shutil.copyfileobj(src, dst)

    _with_retries(_copy)

    # 輪替
    backups = sorted(
        [f for f in os.listdir(directory) if f.startswith(base + BACKUP_PREFIX)],
        key=lambda n: os.path.getmtime(os.path.join(directory, n)),
        reverse=True
    )
    for old in backups[MAX_BACKUPS:]:
        try:
            os.remove(os.path.join(directory, old))
        except Exception:
            pass

    return backup_path


def write_hosts(hosts_path: str):
    if not os.path.exists(TEMP_DOMAINS_FILE):
        return "錯誤：找不到已下載的內容。請先按『下載』。", "error"
    if not hosts_path:
        return "錯誤：未提供 Hosts 檔案路徑。", "error"

    target = resolve_windows_hosts_realpath(hosts_path)
    if not os.path.exists(target):
        return f"錯誤：找不到 Hosts 檔案：{target}", "error"

    ok, msg = _ensure_writable(target)
    if not ok:
        return msg, "error"

    # Windows：先清掉唯讀/系統屬性
    if is_windows():
        _win_clear_attrs(target)

    try:
        with open(TEMP_DOMAINS_FILE, "r", encoding="utf-8") as f:
            new_content = f.read()

        backup_path = _make_backup(target)

        directory = os.path.dirname(target) or "."
        def _atomic_replace():
            with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=directory) as tf:
                temp_target = tf.name
                tf.write(new_content)
            os.replace(temp_target, target)

        _with_retries(_atomic_replace)

        return (
            f"成功寫入：{target}\n"
            f"已建立備份：{backup_path}",
            "success"
        )
    except PermissionError:
        return (
            f"權限錯誤：無法寫入 {target}。\n"
            f"可能原因：檔案被其他程式短暫鎖住（防毒/索引/同步）、或未以系統管理員執行。\n"
            f"建議：關閉即時防護或將本程式加入允許清單後重試。", "error"
        )
    except Exception as e:
        return f"寫入過程發生未預期錯誤：\n{e}", "error"


def undo_hosts(hosts_path: str):
    if not hosts_path:
        return "錯誤：未提供 Hosts 檔案路徑。", "error"

    target = resolve_windows_hosts_realpath(hosts_path)
    directory = os.path.dirname(target) or "."
    base = os.path.basename(target)

    try:
        backups = [
            f for f in os.listdir(directory)
            if f.startswith(base + BACKUP_PREFIX)
        ]
        if not backups:
            return "錯誤：找不到任何備份檔（尚未寫入過或備份被清除）。", "error"

        latest = max(backups, key=lambda n: os.path.getmtime(os.path.join(directory, n)))
        latest_path = os.path.join(directory, latest)

        ok, msg = _ensure_writable(target)
        if not ok:
            return msg, "error"

        if is_windows():
            _win_clear_attrs(target)

        def _atomic_restore():
            with tempfile.NamedTemporaryFile("wb", delete=False, dir=directory) as tf:
                temp_target = tf.name
                with open(latest_path, "rb") as src:
                    shutil.copyfileobj(src, tf)
            os.replace(temp_target, target)

        _with_retries(_atomic_restore)

        return f"已自 {latest} 還原至 {target}。", "success"
    except PermissionError:
        return (
            f"權限錯誤：無法寫入 {target}（還原時）。\n"
            f"建議：關閉防毒即時掃描或將本程式加入允許清單後重試。", "error"
        )
    except FileNotFoundError:
        return f"錯誤：路徑不存在或無法存取：{target}", "error"
    except Exception as e:
        return f"還原過程發生未預期錯誤：\n{e}", "error"
