import os
import sys
import platform
import ctypes
import tkinter
from tkinter import filedialog, messagebox

import eel

from logic import (
    get_default_hosts_path,
    download_domains,
    write_hosts,
    undo_hosts,
)

# 避免 Windows 事件圈相容性問題：改成完全同步版本，不使用 asyncio
# 你先前版本使用 asyncio/aiohttp，常見打包與事件圈衝突，這裡已移除。  # 參考：原始做法 

def is_admin():
    """檢查是否具備系統管理員/root 權限。"""
    try:
        if platform.system() == "Windows":
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        else:
            return os.geteuid() == 0
    except Exception:
        return False

def create_response(message, tag):
    """統一回傳格式供前端使用。"""
    return {"message": str(message), "tag": str(tag)}

def _pick_web_dir():
    """
    自動偵測前端所在資料夾：
    1) 嘗試目前資料夾（與 main.py 同層）
    2) 嘗試 ./web 子資料夾（避免你原本 main.py 只找 web/ 的限制）  # 參考：原始做法 
    """
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [here, os.path.join(here, "web")]
    for c in cands:
        if os.path.exists(os.path.join(c, "index.html")):
            return c
    raise FileNotFoundError("找不到 index.html。請確認前端檔案位置。")

# === Eel 暴露給前端的 API ===

@eel.expose
def is_admin_py():
    return is_admin()

@eel.expose
def get_hosts_path_py():
    return get_default_hosts_path()

@eel.expose
def browse_for_hosts_file_py():
    try:
        root = tkinter.Tk()
        root.withdraw()
        root.wm_attributes("-topmost", 1)
        initial_dir = os.path.dirname(get_default_hosts_path())
        filepath = filedialog.askopenfilename(
            title="選擇 Hosts 檔案",
            initialdir=initial_dir if os.path.isdir(initial_dir) else ".",
            filetypes=(("所有檔案", "*.*"), ("文字檔案", "*.txt")),
        )
        return filepath or ""
    except Exception as e:
        print(f"[錯誤] browse_for_hosts_file_py: {e}")
        return ""

@eel.expose
def download_domains_py(url):
    try:
        message, tag = download_domains(url)
        return create_response(message, tag)
    except Exception as e:
        print(f"[錯誤] download_domains_py: {e}")
        return create_response(f"後端未預期錯誤：{e}", "error")

@eel.expose
def write_hosts_py(hosts_path):
    try:
        message, tag = write_hosts(hosts_path)
        return create_response(message, tag)
    except Exception as e:
        print(f"[錯誤] write_hosts_py: {e}")
        return create_response(f"後端未預期錯誤：{e}", "error")

@eel.expose
def undo_hosts_py(hosts_path):
    try:
        message, tag = undo_hosts(hosts_path)
        return create_response(message, tag)
    except Exception as e:
        print(f"[錯誤] undo_hosts_py: {e}")
        return create_response(f"後端未預期錯誤：{e}", "error")

if __name__ == "__main__":
    try:
        web_dir = _pick_web_dir()
        eel.init(web_dir)

        # 若不是管理員，仍允許啟動（寫入/還原會回覆權限錯誤，前端也會顯示警示條）
        eel.start(
            "index.html",
            size=(900, 640),
            port=0,
            shutdown_delay=0
        )
    except (SystemExit, KeyboardInterrupt):
        print("應用程式結束。")
    except Exception as e:
        print(f"啟動錯誤：{e}")
        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror("啟動錯誤", f"無法啟動應用程式：\n{e}")
