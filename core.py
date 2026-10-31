"""AutoFusion Community Core v0.1: independent launcher and job orchestration.

Zero third-party dependencies. Installation is handled by independent install_manager.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional

APP_TITLE = "AutoFusion Community"
SUPPORTED = ("ww", "sra")
MODULE_INFO = {
    "ww": {"label": "鸣潮 · OK-WW", "repo": "https://github.com/ok-oldking/ok-wuthering-waves", "exe_hint": "ok-ww.exe"},
    "sra": {"label": "崩坏：星穹铁道 · SRA", "repo": "https://github.com/Shasnow/StarRailAssistant", "exe_hint": "SRA-cli.exe"},
}


def data_dir() -> Path:
    if sys.platform == "win32":
        root = os.getenv("LOCALAPPDATA") or str(Path.home())
        return Path(root) / "AutoFusionCommunity"
    return Path.home() / ".autofusion_community"


@dataclass
class Settings:
    install_root: str = ""
    ww_exe: str = ""
    sra_exe: str = ""
    ww_task: int = 1
    sra_config: str = "Default"
    enable_ww: bool = True
    enable_sra: bool = True
    dry_run: bool = True
    job_timeout_minutes: int = 90
    keep_install_packages: bool = False


def load_settings(path: Optional[Path] = None) -> Settings:
    path = path or (data_dir() / "settings.json")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("设置文件不是 JSON 对象")
        known = set(Settings.__dataclass_fields__.keys())
        return Settings(**{k: v for k, v in raw.items() if k in known})
    except FileNotFoundError:
        return Settings()
    except (OSError, ValueError, TypeError):
        # Preserve invalid files for manual recovery; never delete user data.
        return Settings()


def save_settings(settings: Settings, path: Optional[Path] = None) -> Path:
    path = path or (data_dir() / "settings.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return path


def validate_module_parameters(module: str, settings: Settings) -> None:
    """Validate task options independently of executable paths (for simulation)."""
    if module not in SUPPORTED:
        raise ValueError(f"不支持的模块：{module}")
    if module == "ww":
        if not isinstance(settings.ww_task, int) or not 1 <= settings.ww_task <= 999:
            raise ValueError("鸣潮任务序号应为 1～999 的整数")
    elif not isinstance(settings.sra_config, str) or not re.fullmatch(r"[\w.-]{1,80}", settings.sra_config, re.UNICODE):
        raise ValueError("SRA 配置名称仅能包含字母、数字、中文、下划线、点或减号")


def build_command(module: str, settings: Settings, *, check_exe: bool = True) -> tuple[list[str], Path]:
    validate_module_parameters(module, settings)
    filename = settings.ww_exe.strip().strip('"') if module == "ww" else settings.sra_exe.strip().strip('"')
    if not filename:
        raise ValueError(f"请先指定 {MODULE_INFO[module]['label']} 的程序路径")
    path = Path(filename).expanduser()
    if check_exe:
        if not path.is_file():
            raise ValueError(f"找不到程序：{path}")
        if path.suffix.lower() != ".exe" and sys.platform == "win32":
            raise ValueError("Windows 版请选择有效的 .exe 文件")
    if module == "ww":
        return [str(path), "-t", str(settings.ww_task), "-e"], path.parent
    # Verified against https://starrailassistant.top/cli/argument-and-startup/
    # `run` is blocking while `task run` is asynchronous; use `run ... + exit`.
    return [str(path), "-e", "run", settings.sra_config, "+", "exit"], path.parent


def selected_modules(settings: Settings) -> list[str]:
    return [m for m, enabled in (("ww", settings.enable_ww), ("sra", settings.enable_sra)) if enabled]


class Runner:
    """Runs modules sequentially, reporting progress through a thread-safe callback.

    In dry-run mode no external program is invoked. During live runs we only
    manage processes directly started by this application; game executables
    and unrelated processes are never targeted.
    """

    def __init__(self, report: Callable[[str, str], None]):
        self.report = report
        self._cancel = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, settings: Settings) -> None:
        if self.running:
            raise RuntimeError("已有任务正在运行")
        if not selected_modules(settings):
            raise ValueError("请至少选择一个游戏")
        # Snapshot settings so edits in UI cannot change a running job.
        snapshot = Settings(**asdict(settings))
        for m in selected_modules(snapshot):
            validate_module_parameters(m, snapshot)
            # Simulation is intentionally independent of installed third-party programs.
            if not snapshot.dry_run:
                build_command(m, snapshot, check_exe=True)
        if not 1 <= snapshot.job_timeout_minutes <= 1440:
            raise ValueError("单个模块超时范围：1～1440 分钟")
        self._cancel.clear()
        self._thread = threading.Thread(target=self._work, args=(snapshot,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._cancel.set()
        with self._lock:
            proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                self.report("warning", "已向启动的模块主进程发送停止请求；部分子进程可能仍需在原工具内手动停止。")
            except OSError as exc:
                self.report("error", f"结束进程失败：{exc}")

    def _work(self, settings: Settings) -> None:
        modules = selected_modules(settings)
        self.report("start", f"开始任务：{' → '.join(MODULE_INFO[m]['label'] for m in modules)}")
        for m in modules:
            if self._cancel.is_set():
                self.report("stop", "队列已取消，后续模块不再执行。")
                return
            self.report("info", f"准备运行 {MODULE_INFO[m]['label']}")
            if settings.dry_run:
                description = (f"任务序号 {settings.ww_task}" if m == "ww"
                               else f"配置 {settings.sra_config}")
                self.report("info", f"【模拟模式】{description}；无需填写 EXE 路径，不启动程序、不操作游戏。")
                for _ in range(10):
                    if self._cancel.wait(.14):
                        self.report("stop", "模拟任务已停止。")
                        return
                self.report("success", f"【模拟完成】{MODULE_INFO[m]['label']}")
                continue
            cmd, cwd = build_command(m, settings, check_exe=True)
            self.report("command", subprocess.list2cmdline(cmd))
            try:
                proc = subprocess.Popen(
                    cmd, cwd=str(cwd), stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                    creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                )
            except (OSError, ValueError) as exc:
                self.report("error", f"无法启动 {MODULE_INFO[m]['label']}：{exc}")
                return
            with self._lock:
                self._proc = proc
            self.report("info", f"进程已启动，PID={proc.pid}。退出码仅代表进程状态，不保证游戏任务成功。")
            reader = threading.Thread(target=self._read_output, args=(proc, m), daemon=True)
            reader.start()
            deadline = time.monotonic() + settings.job_timeout_minutes * 60
            timed_out = False
            while proc.poll() is None:
                if self._cancel.is_set():
                    try:
                        proc.terminate()
                    except OSError:
                        pass
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    self.report("warning", f"{MODULE_INFO[m]['label']} 已达到超时时间，尝试结束启动的主进程。")
                    try:
                        proc.terminate()
                    except OSError:
                        pass
                    break
                self._cancel.wait(.2)
            try:
                proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                self.report("warning", "主进程尚未退出，请在原软件中检查后手动结束；不会自动强制关闭游戏。")
                return
            finally:
                with self._lock:
                    if self._proc is proc:
                        self._proc = None
            if self._cancel.is_set():
                self.report("stop", "任务已停止；不会启动后续模块。")
                return
            if timed_out:
                self.report("error", "任务超时，队列停止。")
                return
            if proc.returncode != 0:
                self.report("error", f"{MODULE_INFO[m]['label']} 返回退出码 {proc.returncode}，队列停止。")
                return
            self.report("success", f"{MODULE_INFO[m]['label']} 主进程正常退出（仍需核对原项目日志确认任务结果）。")
        self.report("complete", "队列执行结束。")

    def _read_output(self, proc: subprocess.Popen, module: str) -> None:
        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    clean = line.strip()
                    if clean:
                        self.report("output", f"[{module}] {clean[:1200]}")
        except (OSError, ValueError):
            pass
