"""AutoFusion Community Windows UI. Standard-library only (Tkinter + Core)."""
from __future__ import annotations

import datetime
import base64
import math
import struct
import zlib
from functools import lru_cache
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
import urllib.parse
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from core import MODULE_INFO, Runner, Settings, build_command, data_dir, load_settings, save_settings, selected_modules, validate_module_parameters
from install_manager import default_root, discover_sra_executable, detect_ww_exe, download_official, module_executable, sra_desktop_executable, uninstall_managed_sra
from lifecycle import find_ww_registry_entries, inspect_ww, inspect_sra, plan_text_ww, plan_text_sra
from cache_manager import cache_plan, clean_module_cache, offline_package
from install_manager import extract_sra_safely
from catalog_adapter import load_catalog, detect_sevenzip, download_sevenzip, sevenzip_arch_supported, uninstall_path_from_record, sevenzip_update_message
from discovery import Repo, Asset, search_repositories, latest_release_assets, download_release_asset
from directory_diagnostics import APPS as DIAG_APPS, DOCS as DIAG_DOCS, scan_known_locations
from version import APP_VERSION
from offline_library import OfflineLibrary, default_library_root
from module_preferences import is_reminder_muted, set_reminder_muted
from discovery_cache import search_cached
from log_storage import append_session_log
from update_manager import get_feed_url, set_feed_url, check_update, prepare_release, start_update_worker

# V0.3 UI presentation definitions (same app.py for updater compatibility).
BG = '#f6f7f9'
CARD = '#ffffff'
TEXT = '#171a20'
SUB = '#707782'
BORDER = '#e5e8ed'
DARK = '#171a20'
ACTION = '#2563eb'  # Clear action color; page titles and selected navigation remain dark.
ACTION_HOVER = '#1d4ed8'
ACTION_PRESS = '#1e40af'
HOVER = '#edf0f4'
GREEN = '#248254'
ORANGE = '#a66e19'
FONT = 'Microsoft YaHei UI'

# Metadata describes cards; the Core owns actual install/launch/uninstall behavior.
CATALOG = load_catalog()



def _rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[i:i+2], 16) for i in (1, 3, 5))


def _rounded_coverage(x: float, y: float, w: int, h: int, radius: float, inset: float = 0.0) -> float:
    """Signed-distance coverage with a 1px antialiased edge; zero outside."""
    cx, cy = (w - 1) / 2, (h - 1) / 2
    bx, by = (w - 1) / 2 - inset, (h - 1) / 2 - inset
    radius = max(1.0, radius - inset)
    qx = abs(x-cx) - (bx-radius)
    qy = abs(y-cy) - (by-radius)
    distance = math.hypot(max(qx,0.0),max(qy,0.0)) + min(max(qx,qy),0.0) - radius
    return max(0.0,min(1.0,0.5-distance))


@lru_cache(maxsize=96)
def _pill_image_png(w: int, h: int, fill: str, outline: str, radius: int = 11) -> str:
    """Render a small antialiased button background as PNG using stdlib only.

    Tk's canvas arcs are rasterized without antialiasing on Windows. A cached PNG
    fixes the stair-step outlines without adding Pillow or a new UI toolkit.
    """
    w,h=max(4,w),max(4,h)
    rgb=_rgb(fill); border=_rgb(outline)
    rows=[]
    for y in range(h):
        row=bytearray(b'\x00')  # PNG filter type None
        for x in range(w):
            a=_rounded_coverage(x,y,w,h,radius)
            inside=_rounded_coverage(x,y,w,h,radius,1.0) if a else 0.0
            if a <= 0:
                row.extend((0,0,0,0))
            else:
                t=inside/a
                row.extend((*[min(255,round(border[c]*(1-t)+rgb[c]*t)) for c in range(3)],round(a*255)))
        rows.append(row)
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack('>I',len(body))+kind+body+struct.pack('>I',zlib.crc32(kind+body)&0xffffffff)
    header=b'\x89PNG\r\n\x1a\n'
    ihdr=struct.pack('>IIBBBBB',w,h,8,6,0,0,0)
    payload=header+chunk(b'IHDR',ihdr)+chunk(b'IDAT',zlib.compress(b''.join(rows),6))+chunk(b'IEND',b'')
    return base64.b64encode(payload).decode('ascii')


class Pill(tk.Canvas):
    """Rounded, keyboard-accessible, high DPI-friendly Tk button."""
    def __init__(self, parent, text, command, *, primary=False, width=124, height=40,
                 active=False, font_size=10):
        super().__init__(parent, width=width, height=height, bg=parent.cget('bg'),
                         highlightthickness=0, bd=0, cursor='hand2', takefocus=1)
        self.label = text
        self.command = command
        self.primary = primary
        self.is_active = active
        self.hover = False
        self.down = False
        self.font_size = font_size
        self._state = 'normal'
        self.bind('<Configure>', lambda _: self._paint())
        self.bind('<Enter>', self._enter)
        self.bind('<Leave>', self._leave)
        self.bind('<ButtonPress-1>', self._press)
        self.bind('<ButtonRelease-1>', self._release)
        self.bind('<Return>', lambda _: self._activate())
        self.bind('<space>', lambda _: self._activate())
        self.bind('<FocusIn>', lambda _: self._paint())
        self.bind('<FocusOut>', lambda _: self._paint())
        self._paint()

    def configure(self, cnf=None, **kw):
        if 'state' in kw:
            self._state = kw['state']
        if 'text' in kw:
            self.label = kw.pop('text')
        result = super().configure(cnf, **kw)
        if hasattr(self, 'label'):
            self._paint()
        return result
    config = configure

    def set_active(self, active):
        if self.is_active != active:
            self.is_active = active
            self._paint()

    def _enter(self, _):
        self.hover = True
        self._paint()

    def _leave(self, _):
        self.hover = False
        self.down = False
        self._paint()

    def _press(self, _):
        self.down = True
        self._paint()

    def _release(self, event):
        inside = 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()
        pressed = self.down
        self.down = False
        self._paint()
        if pressed and inside:
            self._activate()

    def _activate(self):
        if self._state != 'disabled' and self.command:
            self.command()

    def _paint(self):
        if not self.winfo_exists():
            return
        w = max(4, self.winfo_width() - 2)
        h = max(4, self.winfo_height() - 2)
        self.delete('all')
        if self._state == 'disabled':
            fill, fg, outline = '#dedfe2', '#989da3', '#dedfe2'
        elif self.down:
            fill = ACTION_PRESS if self.primary else '#343943'
            fg, outline = 'white', fill
        elif self.primary:
            fill = ACTION_HOVER if self.hover else ACTION
            fg, outline = 'white', fill
        elif self.is_active:
            fill = '#30343c' if self.hover else DARK
            fg, outline = 'white', fill
        else:
            fill, fg, outline = (HOVER if self.hover else CARD), TEXT, BORDER
        # Cached alpha-blended PNG: smooth rounded edge at every Windows DPI.
        # Shared by all buttons at the same size/state to avoid repeated renders.
        root = self.winfo_toplevel()
        if not hasattr(root, '_pill_photo_cache'):
            root._pill_photo_cache = {}
        key=(w,h,fill,outline)
        sprite=root._pill_photo_cache.get(key)
        if sprite is None:
            sprite=tk.PhotoImage(master=self, data=_pill_image_png(w,h,fill,outline), format='png')
            root._pill_photo_cache[key]=sprite
        self.create_image(1,1,image=sprite,anchor='nw')
        self.create_text((w+1)/2,(h+1)/2,text=self.label,fill=fg,
                         font=(FONT,self.font_size,'bold' if (self.primary or self.is_active) else 'normal'))
        if self.focus_get() == self:
            self.create_rectangle(4,4,w-3,h-3,outline='#93a9c8',width=1)


class SoftCard(tk.Frame):
    """Rounded light card drawn with standard Tk primitives (no external UI library)."""
    def __init__(self, parent, padx=17, pady=15):
        super().__init__(parent, bg=CARD, padx=padx, pady=pady, highlightthickness=0)
        radius = 11
        corners = [
            ('nw', 0, 0, 0, 0, 2*radius, 2*radius),
            ('ne', 1, 0, -radius, 0, radius, 2*radius),
            ('sw', 0, 1, 0, -radius, 2*radius, radius),
            ('se', 1, 1, -radius, -radius, radius, radius),
        ]
        for anchor, x, y, a, b, c, d in corners:
            # Mask only the outside of the rounded corner, not content or buttons.
            mask = tk.Canvas(self, width=radius, height=radius, bg=BG, highlightthickness=0, bd=0)
            mask.create_oval(a, b, c, d, fill=CARD, outline=CARD)
            mask.place(relx=x, rely=y, anchor=anchor)


class UIv3:
    def _build_v3(self):
        self.title('AutoFusion Community')
        # Start large enough for both module cards; preserve normal window chrome.
        # Leave a margin on small displays instead of opening partly off-screen.
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        initial_w = min(1390, max(800, int(screen_w * 0.92)))
        initial_h = min(900, max(570, int(screen_h * 0.89)))
        initial_w = min(initial_w, screen_w)
        initial_h = min(initial_h, screen_h)
        self.geometry(f'{initial_w}x{initial_h}')
        self.minsize(min(960, screen_w), min(630, screen_h))
        self.configure(bg=BG)
        shell = tk.Frame(self,bg=BG,padx=26,pady=17)
        shell.pack(fill='both',expand=True)

        header = tk.Frame(shell,bg=BG)
        header.pack(fill='x')
        tk.Label(header,text='AutoFusion',font=(FONT,25,'bold'),bg=BG,fg=TEXT).pack(side='left')
        tk.Label(header,text=f'COMMUNITY   ·   V{APP_VERSION}   ·   WINDOWS',font=(FONT,10),bg=BG,fg=SUB).pack(side='right',pady=13)
        tk.Label(shell,text='管理你的开源工具 · 尊重原版安装与使用方式',
                 font=(FONT,10),bg=BG,fg=SUB).pack(anchor='w',pady=(0,12))
        nav = tk.Frame(shell,bg=BG)
        nav.pack(fill='x',pady=(0,12))
        self.nav_buttons={}
        for key,label in [('home','操作台'),('manage','模块管理'),('settings','设置'),('logs','运行日志')]:
            btn=Pill(nav,label,lambda p=key:self._switch_page(p),width=128,height=43,font_size=11,primary=False)
            btn.pack(side='left',padx=(0,9))
            self.nav_buttons[key]=btn
        tk.Frame(shell,bg=BORDER,height=1).pack(fill='x',pady=(0,13))

        stage = tk.Frame(shell,bg=BG)
        stage.pack(fill='both',expand=True)
        self.home_page = tk.Frame(stage,bg=BG)
        self.manage_page = tk.Frame(stage,bg=BG)
        self.settings_page = tk.Frame(stage,bg=BG)
        self.logs_page = tk.Frame(stage,bg=BG)
        for p in (self.home_page,self.manage_page,self.settings_page,self.logs_page):
            p.place(relx=0,rely=0,relwidth=1,relheight=1)

        self._discovery_active = False
        self._discovery_generation = 0
        self._discovery_status = ''
        self._discovery_results = []
        self._discovery_query = ''
        # Local Adobe diagnostics are advisory; never part of the install/uninstall catalog.
        self._local_diag_results = {key: None for key in DIAG_APPS}
        self._local_diag_started = False
        self._local_diag_generation = 0
        self._build_home_v3()
        self._build_manage_v3()
        self._build_settings_v3()
        self._build_logs_v3()

        footer=tk.Frame(shell,bg=CARD,highlightbackground=BORDER,highlightthickness=1,padx=14,pady=8)
        footer.pack(fill='x',pady=(10,0))
        tk.Label(footer,text='状态',font=(FONT,10),bg=CARD,fg=SUB).pack(side='left')
        tk.Label(footer,textvariable=self.status,font=(FONT,10,'bold'),bg=CARD,fg=TEXT).pack(side='left',padx=(12,0))
        tk.Button(footer,text='日志 ›',font=(FONT,10),bg=CARD,fg=SUB,relief='flat',bd=0,cursor='hand2',
                  command=lambda:self._switch_page('logs')).pack(side='right')
        self._current_page = None
        self._home_dirty = False
        self._manage_dirty = False
        self._switch_page('home')
        # Hashes and registry inspection never run inside Tk startup or page switching.
        self.after(90, lambda: self._schedule_status_scan(quiet=True))

    def _page_title(self,page,title,hint=None):
        frame=tk.Frame(page,bg=BG)
        frame.pack(fill='x',pady=(0,14))
        tk.Label(frame,text=title,font=(FONT,17,'bold'),bg=BG,fg=TEXT).pack(anchor='w')
        if hint:
            tk.Label(frame,text=hint,font=(FONT,10),bg=BG,fg=SUB).pack(anchor='w',pady=(3,0))
        return frame

    def _build_home_v3(self):
        self._page_title(self.home_page,'操作台','搜索已收录模块；需要发现其他 GitHub 项目时，请点击「在线搜索 GitHub」。')
        find=SoftCard(self.home_page,padx=17,pady=12)
        find.pack(fill='x',pady=(0,12))
        tk.Label(find,text='搜索项目',bg=CARD,fg=TEXT,font=(FONT,11,'bold')).pack(side='left',padx=(0,12))
        self.home_query=tk.StringVar(value='')
        ttk.Entry(find,textvariable=self.home_query).pack(side='left',fill='x',expand=True,padx=(0,13))
        Pill(find,'浏览已收录模块',lambda:self._switch_page('manage'),width=157).pack(side='right')
        Pill(find,'在线搜索 GitHub',lambda:self._search_github('home'),width=165,primary=True).pack(side='right',padx=(0,9))
        find.bind('<Return>',lambda _e:self._search_github('home'))
        self.home_query.trace_add('write',lambda *_:self._debounced_cards('home'))
        # A normal local query does not contact the internet.
        for entry in find.winfo_children():
            if isinstance(entry,ttk.Entry):
                entry.bind('<Return>',lambda _e:self._search_github('home'))

        head=tk.Frame(self.home_page,bg=BG)
        head.pack(fill='x',pady=(3,10))
        tk.Label(head,text='我的模块',font=(FONT,14,'bold'),bg=BG,fg=TEXT).pack(side='left')
        self.home_count=tk.Label(head,text='',font=(FONT,10),bg=BG,fg=SUB)
        self.home_count.pack(side='left',padx=(10,0))
        self.home_cards=tk.Frame(self.home_page,bg=BG)
        self.home_cards.pack(fill='both',expand=True)
        self.home_cards.bind('<Configure>',lambda e:self._home_responsive(e.width))

        actions=SoftCard(self.home_page,padx=16,pady=12)
        actions.pack(fill='x',pady=(11,0))
        line=tk.Frame(actions,bg=CARD)
        line.pack(fill='x')
        tk.Label(line,text='快捷执行',font=(FONT,11,'bold'),bg=CARD,fg=TEXT).pack(side='left')
        tk.Label(line,text='可选：按已有命令行接口顺序执行，不替代原版界面',font=(FONT,9),bg=CARD,fg=SUB).pack(side='right')
        buttons=tk.Frame(actions,bg=CARD)
        buttons.pack(fill='x',pady=(9,0))
        self.run_button=Pill(buttons,'开始队列',self._start,width=126,primary=True)
        self.run_button.pack(side='left')
        self.stop_button=Pill(buttons,'停止任务',self._stop,width=112)
        self.stop_button.pack(side='left',padx=(9,0))
        ttk.Checkbutton(buttons,text='模拟运行',variable=self.dry_run).pack(side='left',padx=(16,0))
        Pill(buttons,'配置执行任务',lambda:self._open_module_detail('ww'),width=140).pack(side='right')

    def _build_manage_v3(self):
        h=self._page_title(self.manage_page,'模块管理','统一查看模块、安装、打开原版界面及安全卸载。')
        toolbar=tk.Frame(self.manage_page,bg=BG)
        toolbar.pack(fill='x',pady=(0,11))
        self.manage_query=tk.StringVar(value='')
        self.manage_query.trace_add('write',lambda *_:self._on_manage_query_edit())
        tk.Label(toolbar,text='查找',bg=BG,fg=SUB,font=(FONT,10)).pack(side='left',padx=(0,9))
        ttk.Entry(toolbar,textvariable=self.manage_query,width=35).pack(side='left',fill='x',expand=True,padx=(0,10))
        self.all_install_button=Pill(toolbar,'安装已选模块',self._install_selected,width=160,primary=True)
        self.all_install_button.pack(side='left',padx=(0,9))
        Pill(toolbar,'重新扫描',lambda:self._schedule_status_scan(),width=116).pack(side='left')
        Pill(toolbar,'在线搜索 GitHub',lambda:self._search_github('manage'),width=162).pack(side='left',padx=(9,0))
        Pill(toolbar,'离线软件库',self._open_offline_library,width=125).pack(side='left',padx=(9,0))
        for entry in toolbar.winfo_children():
            if isinstance(entry,ttk.Entry):
                entry.bind('<Return>',lambda _e:self._search_github('manage'))
        self.install_buttons=[]

        detailbar=tk.Frame(self.manage_page,bg=BG)
        detailbar.pack(fill='x',pady=(0,8))
        self.manage_back=Pill(detailbar,'← 返回全部模块',self._manage_all,width=165,primary=True)
        self.manage_back.pack(side='left')
        self.manage_title=tk.Label(detailbar,text='受支持的模块',font=(FONT,11,'bold'),bg=BG,fg=TEXT)
        self.manage_title.pack(side='left',padx=(12,0))
        self._manage_details_key=None
        manage_region=tk.Frame(self.manage_page,bg=BG)
        manage_region.pack(fill='both',expand=True)
        self.manage_canvas=tk.Canvas(manage_region,bg=BG,highlightthickness=0,bd=0)
        self.manage_canvas.pack(side='left',fill='both',expand=True)
        manage_scroll=ttk.Scrollbar(manage_region,orient='vertical',command=self.manage_canvas.yview)
        manage_scroll.pack(side='right',fill='y')
        self.manage_canvas.configure(yscrollcommand=manage_scroll.set)
        self.manage_host=tk.Frame(self.manage_canvas,bg=BG)
        self.manage_window_id=self.manage_canvas.create_window((0,0),window=self.manage_host,anchor='nw')
        self.manage_canvas.bind('<Configure>',lambda e:(self.manage_canvas.itemconfigure(self.manage_window_id,width=e.width),self._manage_responsive(e.width)))
        self.manage_host.bind('<Configure>',lambda e:self.manage_canvas.configure(scrollregion=self.manage_canvas.bbox('all')))
        def managed_wheel(event):
            current=self.winfo_containing(event.x_root,event.y_root)
            while current is not None:
                if current is self.manage_canvas or current is self.manage_host:
                    if event.delta: self.manage_canvas.yview_scroll(-int(event.delta/120),'units')
                    return 'break'
                current=current.master
        self.bind_all('<MouseWheel>',managed_wheel,add='+')
        self._refresh_manage_cards()

    def _build_settings_v3(self):
        self._page_title(self.settings_page,'设置','程序自身设置与模块管理策略，不改动第三方项目。')
        install=SoftCard(self.settings_page)
        install.pack(fill='x',pady=(0,12))
        tk.Label(install,text='文件与安装位置',font=(FONT,12,'bold'),bg=CARD,fg=TEXT).pack(anchor='w',pady=(0,11))
        row=tk.Frame(install,bg=CARD)
        row.pack(fill='x')
        tk.Label(row,text='默认模块目录',font=(FONT,10),bg=CARD,fg=SUB).pack(side='left',padx=(0,12))
        ttk.Entry(row,textvariable=self.install_root).pack(side='left',fill='x',expand=True,padx=(0,9))
        Pill(row,'选择目录',self._choose_folder,width=116).pack(side='left')
        tk.Label(install,text='更改目录不会自动搬迁已经安装的模块。',font=(FONT,9),bg=CARD,fg=SUB).pack(anchor='w',pady=(8,0))

        policy=SoftCard(self.settings_page)
        policy.pack(fill='x',pady=(0,12))
        tk.Label(policy,text='安装包与卸载策略',font=(FONT,12,'bold'),bg=CARD,fg=TEXT).pack(anchor='w',pady=(0,9))
        self._clear_checkbox(policy,self.keep_install_packages,'保留安装包以便离线重装',CARD).pack(anchor='w')
        tk.Label(policy,text='关闭时，卸载完成后默认清理该模块受管安装包缓存；个人配置和共享运行环境仍会保留。',
                 font=(FONT,9),bg=CARD,fg=SUB,wraplength=850,justify='left').pack(anchor='w',pady=(5,0))
        a=tk.Frame(policy,bg=CARD)
        a.pack(fill='x',pady=(12,0))
        Pill(a,'清理已卸载模块缓存',self._clean_orphan_caches,width=188).pack(side='left')
        Pill(a,'从缓存离线安装 SRA',self._offline_install_sra,width=174).pack(side='left',padx=(8,0))
        Pill(a,'打开 OK-WW 缓存安装包',self._open_cached_ww,width=204).pack(side='left',padx=(8,0))
        Pill(a,'管理离线软件包库',self._open_offline_library,width=184,primary=True).pack(side='left',padx=(8,0))

        runtime=SoftCard(self.settings_page)
        runtime.pack(fill='x',pady=(0,12))
        tk.Label(runtime,text='任务与更新',font=(FONT,12,'bold'),bg=CARD,fg=TEXT).pack(anchor='w',pady=(0,9))
        c=tk.Frame(runtime,bg=CARD)
        c.pack(fill='x')
        tk.Label(c,text='单模块超时（分钟）',font=(FONT,10),bg=CARD,fg=SUB).pack(side='left')
        ttk.Entry(c,textvariable=self.timeout,width=8).pack(side='left',padx=(10,17))
        self.update_button=Pill(c,'检查 AutoFusion 更新',self._check_self_update,width=195)
        self.update_button.pack(side='left')
        Pill(c,'打开本地配置目录',self._open_config,width=175).pack(side='left',padx=(9,0))
        Pill(c,'软件目录诊断（只读）',self._open_directory_diagnostics,width=196,primary=True).pack(side='left',padx=(9,0))
        end=tk.Frame(self.settings_page,bg=BG)
        end.pack(fill='x',pady=(5,0))
        Pill(end,'保存设置',self._save,width=133,primary=True).pack(side='left')
        tk.Label(end,text='当前界面语言：简体中文；English 界面将在翻译完整后提供。',font=(FONT,9),bg=BG,fg=SUB).pack(side='right')




    def _build_logs_v3(self):
        self._page_title(self.logs_page,'运行日志','安装、下载、操作和卸载记录；原版程序可能不向 AutoFusion 输出日志。')
        controls=tk.Frame(self.logs_page,bg=BG)
        controls.pack(fill='x',pady=(0,10))
        tk.Label(controls,text='筛选',font=(FONT,10),bg=BG,fg=SUB).pack(side='left',padx=(0,8))
        self.log_filter=tk.StringVar(value='全部')
        filt=ttk.Combobox(controls,textvariable=self.log_filter,values=['全部','信息','成功','警告','错误'],
                          state='readonly',width=9)
        filt.pack(side='left')
        filt.bind('<<ComboboxSelected>>',lambda _:self._rerender_logs())
        Pill(controls,'清空显示',self._clear_visible_log,width=116).pack(side='right')
        Pill(controls,'复制日志',self._copy_log,width=112).pack(side='right',padx=(0,8))
        Pill(controls,'打开日志目录',self._open_config,width=138).pack(side='right',padx=(0,8))
        textwrap=tk.Frame(self.logs_page,bg=CARD,highlightbackground=BORDER,highlightthickness=1)
        textwrap.pack(fill='both',expand=True)
        self.log=tk.Text(textwrap,bg=CARD,fg=TEXT,insertbackground=TEXT,bd=0,
                         relief='flat',wrap='word',font=('Consolas',10),padx=16,pady=14,state='disabled',spacing3=3)
        self.log.pack(side='left',fill='both',expand=True)
        scroll=ttk.Scrollbar(textwrap,command=self.log.yview)
        scroll.pack(side='right',fill='y')
        self.log.configure(yscrollcommand=scroll.set)
        for key,color in [('success',GREEN),('error','#ba3333'),('warning',ORANGE),('command','#43698b')]:
            self.log.tag_configure(key,foreground=color)
        self.log_records=[]
        tk.Label(self.logs_page,text='清空显示不会删除保存在本地的 session.log。',font=(FONT,9),bg=BG,fg=SUB).pack(anchor='w',pady=(8,0))

    def _open_directory_diagnostics(self, app='ae'):
        """Read-only Adobe lookup; can be launched from Settings or Module Management."""
        app = app if app in DIAG_APPS else 'ae'
        existing=getattr(self,'_diag_window',None)
        if existing is not None and existing.winfo_exists():
            current=getattr(self,'_diag_selected',None)
            if current is not None and current.get()!=DIAG_APPS[app]:
                current.set(DIAG_APPS[app])
                refresh=getattr(self,'_diag_refresh',None)
                if refresh: refresh()
            existing.lift(); existing.focus_force(); return
        window=tk.Toplevel(self)
        self._diag_window=window
        window.title('软件目录诊断 · 只读')
        window.geometry('1100x650')
        window.minsize(760,460)
        window.configure(bg=BG)
        panel=tk.Frame(window,bg=BG,padx=22,pady=18)
        panel.pack(fill='both',expand=True)
        tk.Label(panel,text='软件目录诊断',font=(FONT,18,'bold'),fg=TEXT,bg=BG).pack(anchor='w')
        tk.Label(panel,text='只检查 Adobe / AutoCAD 常见路径和 Windows 安装登记；目录存在 ≠ 软件已安装。不会删除或修改文件。',
                 font=(FONT,10),fg=SUB,bg=BG,wraplength=1000,justify='left').pack(anchor='w',pady=(4,12))
        bar=tk.Frame(panel,bg=BG)
        bar.pack(fill='x',pady=(0,12))
        tk.Label(bar,text='选择软件',bg=BG,fg=TEXT,font=(FONT,10)).pack(side='left',padx=(0,9))
        selected=tk.StringVar(value=DIAG_APPS[app])
        self._diag_selected=selected
        combobox=ttk.Combobox(bar,values=list(DIAG_APPS.values()),textvariable=selected,width=20,state='readonly')
        combobox.pack(side='left')
        counter=tk.StringVar(value='准备检查…')
        tk.Label(bar,textvariable=counter,bg=BG,fg=SUB,font=(FONT,10)).pack(side='left',padx=18)

        frame=tk.Frame(panel,bg=CARD,highlightbackground=BORDER,highlightthickness=1)
        frame.pack(fill='both',expand=True)
        columns=('role','path','source','status')
        tree=ttk.Treeview(frame,columns=columns,show='headings',selectmode='browse')
        for key,title,width,stretch in [('role','分类',165,False),('path','已知 / 候选目录',540,True),
                                         ('source','位置依据',190,False),('status','状态',90,False)]:
            tree.heading(key,text=title)
            tree.column(key,width=width,minwidth=80,stretch=stretch)
        tree.pack(side='left',fill='both',expand=True)
        scroll=ttk.Scrollbar(frame,orient='vertical',command=tree.yview)
        scroll.pack(side='right',fill='y')
        tree.configure(yscrollcommand=scroll.set)
        tree.tag_configure('shared',foreground='#945d0a')
        note=tk.StringVar(value='请选择一项查看说明。共享插件及用户设置不可当作残留自动删除。')
        tk.Label(panel,textvariable=note,bg=BG,fg=SUB,wraplength=1010,justify='left',font=(FONT,9)).pack(fill='x',anchor='w',pady=(11,7))
        bottom=tk.Frame(panel,bg=BG)
        bottom.pack(fill='x')
        results=[]
        state={'generation':0}
        responses=queue.Queue()

        def current_item():
            chosen=tree.selection()
            if not chosen: return None
            try: return results[int(chosen[0])]
            except (ValueError,IndexError): return None

        def open_selected():
            item=current_item()
            if not item: messagebox.showinfo('请先选择','请先在表格中选择一项。',parent=window); return
            if not item.exists:
                messagebox.showinfo('目录不存在','该候选位置当前不存在；不会自动创建它。',parent=window); return
            try:
                if os.name != 'nt': raise OSError('只支持 Windows 文件资源管理器')
                os.startfile(str(item.path))
            except OSError as exc:
                messagebox.showerror('打开失败',str(exc),parent=window)

        def copy_selected():
            item=current_item()
            if not item: messagebox.showinfo('请先选择','请先在表格中选择一项。',parent=window); return
            window.clipboard_clear(); window.clipboard_append(str(item.path))
            note.set('已复制目录路径（仅复制文本，未修改文件）：'+str(item.path))

        def copy_all():
            if not results:
                messagebox.showinfo('没有结果','当前没有可复制的已知目录；不能据此认定软件没有关联文件。',parent=window); return
            text='AutoFusion 软件目录诊断（只读）\n' + '\n'.join(
                f'{i.role} | {"存在" if i.exists else "候选未发现"} | {i.path} | {i.source}'+
                (' | 共享目录' if i.shared else '') for i in results)
            window.clipboard_clear(); window.clipboard_append(text)
            note.set(f'已复制 {len(results)} 项位置摘要。不会包含文件内容。')

        def docs():
            webbrowser.open(DIAG_DOCS[next((key for key,label in DIAG_APPS.items() if label==selected.get()), 'ae')])

        def on_select(_evt=None):
            item=current_item()
            if item:
                note.set((('【共享目录：切勿直接删除】' if item.shared else '【只读】')+' '+(item.note or item.source))+
                         '；目录存在不等于软件仍已安装。')
        tree.bind('<<TreeviewSelect>>',on_select)

        def refresh(_evt=None):
            state['generation']+=1
            generation=state['generation']
            app=next((key for key,label in DIAG_APPS.items() if label==selected.get()), 'ae')
            counter.set('检查中…（不扫描整盘）')
            note.set('仅检查官方记录的常见目录；磁盘缓存可能由用户自行设置在其他位置。')
            tree.delete(*tree.get_children()); results.clear()
            def worker():
                try: responses.put((generation,scan_known_locations(app),None))
                except Exception as exc: responses.put((generation,[],str(exc)))
            threading.Thread(target=worker,daemon=True).start()

        def poll():
            if not window.winfo_exists(): return
            try:
                while True:
                    generation,items,error=responses.get_nowait()
                    if generation!=state['generation']: continue
                    if error:
                        counter.set('检查失败'); note.set(error); continue
                    results[:]=items
                    for index,item in enumerate(items):
                        tree.insert('', 'end',iid=str(index),values=(item.role,str(item.path),item.source,
                                    '已找到' if item.exists else '待确认'),tags=('shared',) if item.shared else ())
                    counter.set(f'找到 {sum(i.exists for i in items)} 个目录 / {len(items)} 项候选')
                    if not items:
                        note.set('未找到常见目录；不等于未安装或没有配置。软件可能安装到自定义位置。')
            except queue.Empty:
                pass
            self.after(120,poll)

        Pill(bottom,'打开所选目录',open_selected,width=145,primary=True).pack(side='left')
        Pill(bottom,'复制路径',copy_selected,width=116).pack(side='left',padx=(9,0))
        Pill(bottom,'复制诊断摘要',copy_all,width=151).pack(side='left',padx=(9,0))
        Pill(bottom,'官方目录说明',docs,width=143).pack(side='right')
        Pill(bottom,'重新检查',refresh,width=115).pack(side='right',padx=(0,9))
        combobox.bind('<<ComboboxSelected>>',refresh)
        self._diag_refresh=refresh
        self.after(120,poll)
        refresh()

    def _debounced_cards(self,page):
        attr='_search_after_'+page
        pending=getattr(self,attr,None)
        if pending is not None:
            self.after_cancel(pending)
        callback=self._refresh_home_cards if page=='home' else self._refresh_manage_cards
        setattr(self,attr,self.after(110,callback))

    def _switch_v3(self,name):
        pages={'home':self.home_page,'manage':self.manage_page,'settings':self.settings_page,'logs':self.logs_page}
        if name not in pages:
            return
        # Keep the visual tab switch strictly constant-time. No disk reads,
        # registry queries or file hashing on a navigation click.
        if getattr(self,'_current_page',None) != name:
            self._current_page = name
            pages[name].lift()
            for key,button in self.nav_buttons.items():
                button.set_active(key==name)
        if name == 'home' and getattr(self,'_home_dirty',False):
            self._home_dirty = False
            self._refresh_home_cards()
        elif name == 'manage' and getattr(self,'_manage_dirty',False):
            self._manage_dirty = False
            self._refresh_manage_cards()
        if name == 'manage' and not self._local_diag_started:
            # First visit only: run read-only folder lookups off the Tk event thread.
            self._local_diag_started=True
            self.after(80, self._ensure_local_diag_scan)

    def _ensure_local_diag_scan(self):
        """Check AE/PS folder hints only, without starting apps or enumerating drives."""
        self._local_diag_generation += 1
        generation = self._local_diag_generation
        self._local_diag_started = True

        def worker():
            summary = {}
            for key in DIAG_APPS:
                try:
                    hints = scan_known_locations(key)
                    found = sum(bool(h.exists) for h in hints)
                    program = sum(bool(h.exists) for h in hints if h.role in
                                  ('程序目录（候选）', 'Windows 安装登记目录'))
                    summary[key] = (found, program, '')
                except (OSError, ValueError, PermissionError) as exc:
                    summary[key] = (0, 0, str(exc))
            self.events.put(('local_diag_result', (generation, summary)))

        threading.Thread(target=worker, daemon=True,
                         name='AutoFusionLocalDiagnostics').start()

    def _build_local_diag_section(self, query):
        """Advisory Windows software entries, *not* controlled install modules."""
        search_names = {'ae': 'Adobe After Effects AE 特效',
                        'ps': 'Adobe Photoshop PS 图像处理',
                        'cad': 'Autodesk AutoCAD CAD 图纸'}
        matched = [key for key, title in search_names.items()
                   if not query or query in title.casefold()]
        self._local_diag_section = None
        self._local_diag_cards = []
        if not matched:
            return False
        section = tk.Frame(self.manage_host, bg=BG)
        self._local_diag_section = section
        line = tk.Frame(section, bg=BG)
        line.pack(fill='x', pady=(4, 8))
        tk.Label(line, text='本机软件 · 目录诊断（只读）', font=(FONT, 13, 'bold'),
                 fg=TEXT, bg=BG).pack(side='left')
        Pill(line, '重新检查目录', self._ensure_local_diag_scan, width=132).pack(side='right')
        tk.Label(section,
                 text='只读识别 Adobe / AutoCAD 的部分已知目录，不代表软件已安装；不接管安装、插件或卸载。',
                 font=(FONT, 9), bg=BG, fg=SUB, anchor='w', wraplength=900,
                 justify='left').pack(fill='x', pady=(0, 10))
        cards = tk.Frame(section, bg=BG)
        cards.pack(fill='x')
        for key in matched:
            item = SoftCard(cards, padx=16, pady=14)
            title = DIAG_APPS[key]
            tk.Label(item, text=title, font=(FONT, 12, 'bold'), bg=CARD,
                     fg=TEXT).pack(anchor='w')
            data = self._local_diag_results.get(key)
            if data is None:
                status = '常见目录待检查'
            elif data[2]:
                status = '检查未完成：' + data[2][:60]
            elif data[0] > 0:
                status = f'发现 {data[0]} 项目录线索（含 {data[1]} 项程序位置线索）'
            else:
                status = '未找到常见目录；不代表软件未安装'
            tk.Label(item, text=status, font=(FONT, 9), bg=CARD,
                     fg=SUB, anchor='w').pack(anchor='w', pady=(8, 12))
            Pill(item, '查看关联目录', lambda k=key: self._open_directory_diagnostics(k),
                 width=146, primary=True).pack(anchor='w')
            self._local_diag_cards.append(item)
        self._local_diag_card_parent = cards
        return True

    def _position_local_diag_section(self, count):
        section = getattr(self, '_local_diag_section', None)
        if section is None or not section.winfo_exists():
            return
        total = len(getattr(self, 'manage_cards', []))
        row = (total + count - 1) // count
        section.grid(row=row, column=0, columnspan=count, sticky='ew', pady=(12, 5))
        for col in range(2):
            self._local_diag_card_parent.columnconfigure(col,
                weight=1 if col < count else 0)
        for i, card in enumerate(self._local_diag_cards):
            card.grid(row=i // count, column=i % count, sticky='nsew',
                      padx=(0, 8) if count == 2 and i % 2 == 0 else 0,
                      pady=(0, 8))

    def _initial_status_cache(self):
        """Lightweight startup hint, not proof of a complete installation."""
        result = {'ww':('absent','未安装'), 'sra':('absent','未安装'), 'sevenzip':('unknown','待检查')}
        try:
            root = self._validated_root()
            if (root / 'sra' / 'SRA-cli.exe').is_file():
                result['sra'] = ('unknown','正在检查')
            typed=self.ww_path.get().strip()
            if ((typed and Path(typed).is_file()) or (root / 'ok-ww' / 'ok-ww.exe').is_file()):
                result['ww'] = ('unknown','正在检查')
        except (OSError, ValueError):
            pass
        return result

    def _module_status(self,key):
        return self._cached_module_states.get(key,('unknown','待检查'))

    def _status_map(self):
        return {key:self._module_status(key) for key in CATALOG}

    def _schedule_status_scan(self,quiet=False):
        """Read-only status verification off the Tk event loop."""
        try:
            root=self._validated_root()
        except ValueError as exc:
            if not quiet:
                self._log('warning',f'整合目录未配置，仍只读识别整机软件：{exc}')
            root=data_dir()/'unconfigured-modules'
        self._scan_generation += 1
        generation=self._scan_generation
        typed_ww=self.ww_path.get().strip()
        typed_sra=self.sra_path.get().strip()
        def worker():
            try:
                sra=inspect_sra(root,selected=typed_sra,discover_external=True)
                found=Path(typed_ww) if typed_ww and Path(typed_ww).is_file() else detect_ww_exe(root)
                ww=inspect_ww(found)
                seven=detect_sevenzip()
                self.events.put(('status_scan_result',(generation,root,sra,ww,quiet,seven)))
            except Exception as exc:
                self.events.put(('status_scan_error',(generation,str(exc))))
        threading.Thread(target=worker,daemon=True,name='AutoFusionStatusScan').start()

    def _apply_status_scan(self, result):
        if len(result)==5:  # Retain compatibility with existing local regression tests.
            generation, root, sra, ww, quiet=result
            seven=None
        else:
            generation, root, sra, ww, quiet, seven=result
        if generation != self._scan_generation:
            return
        sra_labels={'managed':'已部署','absent':'未安装','unmanaged':'已发现（只读）','unsafe':'存在异常'}
        ww_labels={'installed':'已安装','absent':'未安装','stale':'需要修复','unregistered':'仅检测到程序'}
        if seven is not None:
            self._sevenzip_record = seven
        self._cached_module_states={'sra':(sra.state,sra_labels.get(sra.state,sra.state)),
                                    'ww':(ww.state,ww_labels.get(ww.state,ww.state)),
                                    'sevenzip':((seven.state, {'installed':'已安装','absent':'未安装','stale':'需要修复','unregistered':'发现程序（未登记）'}.get(seven.state,seven.state)) if seven else self._module_status('sevenzip'))}
        self.manage_sra_state.set({'managed':f'已识别受管理的便携版安装：{sra.removable} 个原版文件可安全清理',
            'absent':'未安装 / 未检测到受管理的模块',
            'unmanaged':sra.message,
            'unsafe':'模块目录异常，拒绝自动卸载'}.get(sra.state,sra.state))
        self.manage_ww_state.set({'installed':'已识别程序及 Windows 官方安装登记',
            'stale':'仅有官方安装记录、程序缺失；建议原版修复或官方卸载',
            'unregistered':'发现程序，但未找到 Windows 官方卸载登记',
            'absent':'未检测到程序或官方安装登记'}.get(ww.state,ww.state))
        if sra.state in ('managed','unmanaged'):
            exe=discover_sra_executable(root,self.sra_path.get().strip())
            if exe and not self.sra_path.get().strip():
                self.sra_path.set(str(exe))
        if ww.executable and ww.executable.is_file() and not self.ww_path.get().strip():
            self.ww_path.set(str(ww.executable))
        if getattr(self,'_ww_uninstall_pending',False) and ww.state=='absent':
            self._ww_uninstall_pending=False
            if not self.keep_install_packages.get():
                try:
                    n, sz=clean_module_cache(root,'ww')
                    self._log('success',f'OK-WW 官方卸载已确认：清理 {n} 个缓存（{sz/1024**2:.1f} MiB）。')
                except (OSError, ValueError) as exc:
                    self._log('warning',f'OK-WW 缓存未能清理：{exc}')
        if not quiet:
            self._log('info',f'模块状态扫描：SRA={sra.state}，OK-WW={ww.state}，7-Zip={seven.state if seven else "待检测"}。')
        if getattr(self,'_current_page',None)=='home':
            self._refresh_home_cards()
            self._manage_dirty=True
        elif getattr(self,'_current_page',None)=='manage':
            self._refresh_manage_cards()
            self._home_dirty=True
        else:
            self._home_dirty=True
            self._manage_dirty=True

    def _all_modules(self):
        # UI is generated from catalog metadata and state, not copied per-game UI.
        # Future new modules require an actual safe Core adapter and registry entry.
        return list(CATALOG.items())

    def _refresh_home_cards(self):
        if not hasattr(self,'home_cards'):
            return
        for widget in self.home_cards.winfo_children():
            widget.destroy()
        query=self.home_query.get().strip().casefold()
        all_items=self._all_modules()
        state=self._status_map()
        if query:
            items=[(k,d) for k,d in all_items if query in (k+' '+d['title']+' '+d['subtitle']).casefold()]
        else:
            # Existing third-party Windows applications are not automatically adopted.
            # Only the two managed/tested game modules belong on the personal homepage.
            items=[(k,d) for k,d in all_items if k in ('ww','sra') and state[k][0] not in ('absent','unknown')]
        number=len(items)
        if number>4:
            items=items[:4]
        self.home_count.configure(text=f'{number} 个项目'+(' · 搜索结果' if query else ' · 已发现')+((' · 首页显示前 4 项' if number>4 else '')))
        if not items:
            panel=SoftCard(self.home_cards,padx=28,pady=24)
            panel.pack(fill='both',expand=True)
            symbol='⌕' if query else '＋'
            tk.Label(panel,text=symbol,font=(FONT,29),bg=CARD,fg='#a4abb4').pack(pady=(10,8))
            tk.Label(panel,text=('没有找到匹配的模块' if query else '暂未安装任何模块'),font=(FONT,14,'bold'),
                     bg=CARD,fg=TEXT).pack(pady=(0,7))
            tk.Label(panel,text=('试试其他关键词，或在模块管理中浏览已收录项目。' if query else '从模块管理中选择项目进行安装；不会自动安装任何第三方程序。'),
                     font=(FONT,10),bg=CARD,fg=SUB).pack(pady=(0,12))
            Pill(panel,'浏览可用模块',lambda:self._switch_page('manage'),width=162,primary=True).pack()
            self._home_layout_cards=[]
            return
        self._home_layout_cards=[]
        for key,item in items:
            card=SoftCard(self.home_cards,padx=16,pady=15)
            self._home_layout_cards.append(card)
            self._make_summary_card(card,key,item,state[key],home=True)
        self._home_responsive(self.home_cards.winfo_width())

    def _home_responsive(self,width):
        cards=getattr(self,'_home_layout_cards',[])
        if not cards:
            return
        count=2 if width>=820 else 1
        for col in range(2):
            self.home_cards.columnconfigure(col,weight=1 if col<count else 0,uniform='summary' if col<count else '')
        for i,card in enumerate(cards):
            card.grid(row=i//count,column=i%count,sticky='nsew',padx=(0,8) if i%count==0 and count==2 else (0,0),pady=(0,10))

    def _make_summary_card(self,parent,key,item,state,home=False):
        row=tk.Frame(parent,bg=CARD)
        row.pack(fill='x')
        tk.Label(row,text=item['title'],font=(FONT,13,'bold'),bg=CARD,fg=TEXT).pack(side='left')
        tk.Label(row,text='  '+state[1],font=(FONT,9,'bold'),fg=(GREEN if state[0] in ('installed','managed') else ORANGE if state[0] in ('stale','unsafe') else SUB),bg=CARD).pack(side='right')
        tk.Label(parent,text=item['subtitle']+'  ·  '+item['type'],font=(FONT,9),bg=CARD,fg=SUB).pack(anchor='w',pady=(7,14))
        buttons=tk.Frame(parent,bg=CARD)
        buttons.pack(fill='x')
        installed=state[0] in ('installed','managed','unregistered','unmanaged')
        Pill(buttons,'打开原版' if installed else '下载安装',
             (lambda m=key:self._open_original(m)) if installed else (lambda m=key:self._install_catalog(m)),
             width=133,primary=True).pack(side='left')
        Pill(buttons,'管理详情',lambda m=key:self._open_module_detail(m),width=124).pack(side='left',padx=(9,0))

    def _manage_all(self):
        self._discovery_active=False
        self._discovery_generation+=1
        self._manage_details_key=None
        self._refresh_manage_cards()

    def _refresh_manage_cards(self):
        if not hasattr(self,'manage_host'):
            return
        for w in self.manage_host.winfo_children():
            w.destroy()
        self.install_buttons=[]
        self.all_install_button.configure(state='disabled' if self._discovery_active else 'normal')
        key=self._manage_details_key
        self.manage_back.configure(state='normal' if (key or self._discovery_active) else 'disabled')
        self.manage_title.configure(text='项目详情' if key else ('GitHub 在线搜索 · 未经审核' if self._discovery_active else '已收录项目'))
        if hasattr(self, 'manage_canvas'):
            self.manage_canvas.yview_moveto(0.0)
        if key:
            self._build_detail(key)
            return
        if self._discovery_active:
            self._render_github_results()
            return
        self.manage_cards=[]
        statuses=self._status_map()
        query=self.manage_query.get().strip().casefold()
        for module,info in self._all_modules():
            if query and query not in (module+' '+info['title']+' '+info['subtitle']).casefold():
                continue
            card=SoftCard(self.manage_host,padx=18,pady=17)
            self.manage_cards.append(card)
            self._make_summary_card(card,module,info,statuses[module])
        has_local = self._build_local_diag_section(query)
        if not self.manage_cards and not has_local:
            tk.Label(self.manage_host,text='没有匹配的模块或已支持的目录诊断项目',
                     bg=BG,fg=SUB,font=(FONT,11)).grid(row=0,column=0,pady=50)
        else:
            self._manage_responsive(max(self.manage_host.winfo_width(), self.manage_canvas.winfo_width()))

    def _manage_responsive(self,width):
        if not self.manage_host.winfo_exists():
            return
        if self._manage_details_key or not hasattr(self,'manage_cards'):
            return
        count=2 if width>=820 else 1
        for col in range(2):
            self.manage_host.columnconfigure(col,weight=1 if col<count else 0,uniform='modules' if col<count else '')
        for i,card in enumerate(self.manage_cards):
            card.grid(row=i//count,column=i%count,sticky='nsew',padx=(0,8) if i%count==0 and count==2 else 0,pady=(0,10))
        self._position_local_diag_section(count)

    def _open_module_detail(self,module):
        if module not in CATALOG:
            return
        self._manage_details_key=module
        self._manage_dirty=True
        self._switch_page('manage')

    def _build_detail(self,module):
        if module == 'sevenzip':
            return self._build_sevenzip_detail()
        data=CATALOG[module]
        state=self._module_status(module)
        card=SoftCard(self.manage_host,padx=20,pady=19)
        card.pack(fill='both',expand=True)
        header=tk.Frame(card,bg=CARD)
        header.pack(fill='x',pady=(0,17))
        tk.Label(header,text=f"{data['title']}  ·  {data['subtitle']}",font=(FONT,15,'bold'),bg=CARD,fg=TEXT).pack(side='left')
        tk.Label(header,text=state[1],font=(FONT,10,'bold'),bg=CARD,fg=GREEN if state[0] in ('managed','installed') else SUB).pack(side='right')
        tk.Label(card,text=data['description'],font=(FONT,10),fg=SUB,bg=CARD).pack(anchor='w',pady=(0,18))
        actions=tk.Frame(card,bg=CARD)
        actions.pack(fill='x',pady=(0,18))
        install_button=Pill(actions,'获取 / 安装',lambda m=module:self._install([m]),width=145,primary=True)
        install_button.pack(side='left')
        self.install_buttons.append(install_button)
        Pill(actions,'打开原版',lambda m=module:self._open_original(m),width=136).pack(side='left',padx=(9,0))
        Pill(actions,'官方项目主页',lambda m=module:self._open_repo(m),width=158).pack(side='left',padx=(9,0))
        tk.Frame(card,bg=BORDER,height=1).pack(fill='x',pady=(0,16))
        path=self.ww_path if module=='ww' else self.sra_path
        tk.Label(card,text='识别到的程序路径',font=(FONT,10,'bold'),bg=CARD,fg=TEXT).pack(anchor='w',pady=(0,7))
        line=tk.Frame(card,bg=CARD)
        line.pack(fill='x')
        ttk.Entry(line,textvariable=path).pack(side='left',fill='x',expand=True,padx=(0,9))
        Pill(line,'浏览',lambda m=module,p=path:self._browse(p,m),width=88).pack(side='left')
        task=tk.Frame(card,bg=CARD)
        task.pack(fill='x',pady=(16,14))
        enabled=self.ww_enabled if module=='ww' else self.sra_enabled
        opt=self.ww_task if module=='ww' else self.sra_config
        label='任务序号' if module=='ww' else '配置名称'
        self._clear_checkbox(task,enabled,'加入执行队列',CARD).pack(side='left',padx=(0,18))
        tk.Label(task,text=label,bg=CARD,fg=SUB,font=(FONT,10)).pack(side='left',padx=(0,10))
        ttk.Entry(task,textvariable=opt,width=15).pack(side='left')
        Pill(task,'预览调用参数',self._preview,width=140).pack(side='left',padx=(12,0))
        tk.Frame(card,bg=BORDER,height=1).pack(fill='x',pady=(4,16))
        tk.Label(card,text='安装管理',font=(FONT,11,'bold'),bg=CARD,fg=TEXT).pack(anchor='w',pady=(0,9))
        action=tk.Frame(card,bg=CARD)
        action.pack(fill='x')
        Pill(action,'预览卸载',self._preview_ww if module=='ww' else self._preview_sra,width=133).pack(side='left')
        Pill(action,'官方卸载程序' if module=='ww' else '安全卸载',
             self._uninstall_ww if module=='ww' else self._uninstall_sra,width=150).pack(side='left',padx=(10,0))
        Pill(action,'重新识别',lambda:self._scan_and_refresh(),width=122).pack(side='left',padx=(10,0))
        self._detail_state_label=tk.Label(card,text='',font=(FONT,9),bg=CARD,fg=SUB,wraplength=920,justify='left')
        self._detail_state_label.pack(anchor='w',pady=(14,0))
        self._detail_state_label.configure(text='官方安装版优先使用原版卸载器，不会直接删除软件目录。' if module=='ww' else
                                            '便携版只删除可信清单内未改变的原版文件，个人配置默认保留。')

    def _scan_and_refresh(self):
        self._schedule_status_scan(quiet=False)

    def _open_repo(self,module):
        import webbrowser
        webbrowser.open(CATALOG[module]['website'])

    def _on_manage_query_edit(self):
        # Editing a query returns to local results. Searching GitHub requires a click/Enter.
        if getattr(self,'_discovery_active',False):
            self._discovery_active=False
            self._discovery_generation+=1
        self._debounced_cards('manage')

    def _search_github(self, page):
        query=(self.home_query.get() if page=='home' else self.manage_query.get()).strip()
        if len(query)<2:
            messagebox.showinfo('输入项目名称','请输入至少两个字符，也可以粘贴 GitHub 仓库地址。',parent=self)
            return
        if len(query)>100:
            messagebox.showwarning('搜索词太长','请将搜索词控制在 100 个字符以内。',parent=self)
            return
        if page=='home':
            self.manage_query.set(query)
        self._discovery_generation+=1
        generation=self._discovery_generation
        self._discovery_active=True
        self._discovery_query=query
        self._discovery_results=[]
        self._discovery_status='正在查询 GitHub 公开仓库……'
        self._manage_details_key=None
        self._manage_dirty=True
        self._switch_page('manage')
        self._refresh_manage_cards()
        self.status.set('正在搜索 GitHub，原有模块不受影响')
        def task():
            try:
                found,source=search_cached(query)
                self.events.put(('discovery_results',(generation,found,source)))
            except Exception as e:
                self.events.put(('discovery_error',(generation,str(e))))
        threading.Thread(target=task,daemon=True,name='AutoFusionGithubDiscovery').start()

    def _render_github_results(self):
        self.manage_cards=[]
        fallback=tk.Frame(self.manage_host,bg=BG);fallback.pack(fill='x',pady=(0,8))
        Pill(fallback,'浏览器搜索 GitHub',lambda:webbrowser.open('https://github.com/search?q='+urllib.parse.quote(self._discovery_query)+'&type=repositories'),width=176).pack(side='right')
        tk.Label(self.manage_host,text=f'搜索：{self._discovery_query}  ·  {self._discovery_status}',
                 bg=BG,fg=SUB,font=(FONT,10)).pack(anchor='w',pady=(0,9))
        tk.Label(self.manage_host,text='搜索结果不代表 AutoFusion 已审核。只有内置已审核模块支持安装、接入和安全卸载；其他项目仅可访问仓库或自主保存 Release 文件。',
                 bg=BG,fg='#a66e19',font=(FONT,9),wraplength=980,justify='left').pack(anchor='w',pady=(0,12))
        for repo in self._discovery_results:
            card=SoftCard(self.manage_host,padx=17,pady=13)
            card.pack(fill='x',pady=(0,9))
            tk.Label(card,text=repo.full_name,font=(FONT,12,'bold'),bg=CARD,fg=TEXT).pack(anchor='w')
            desc=(repo.description or '项目未提供描述')
            if len(desc)>150: desc=desc[:147]+'…'
            tk.Label(card,text=desc,font=(FONT,10),bg=CARD,fg=SUB,wraplength=950,
                     justify='left').pack(anchor='w',pady=(5,2))
            tk.Label(card,text=f'许可证：{repo.license_id}  ·  GitHub 仓库（未验证官方身份）'+('  ·  已归档' if repo.archived else ''),
                     font=(FONT,9),bg=CARD,fg=SUB).pack(anchor='w',pady=(0,9))
            line=tk.Frame(card,bg=CARD);line.pack(fill='x')
            Pill(line,'访问 GitHub 仓库',lambda r=repo:webbrowser.open(r.url),width=166).pack(side='left')
            Pill(line,'查看 Release 文件',lambda r=repo:self._inspect_github_release(r),width=182,primary=True).pack(side='left',padx=(9,0))
        if not self._discovery_results and self._discovery_status != '正在查询 GitHub 公开仓库……':
            tk.Label(self.manage_host,text='没有可显示的仓库。你仍可以通过浏览器访问 GitHub 搜索。',
                     bg=BG,fg=SUB,font=(FONT,10)).pack(pady=25)

    def _inspect_github_release(self, repo: Repo):
        self.status.set(f'正在查询 {repo.full_name} 的公开 Release')
        def task():
            try:
                page,assets=latest_release_assets(repo)
                self.events.put(('discovery_assets',(repo,page,assets)))
            except Exception as e:
                self.events.put(('discovery_assets_error',(repo,str(e))))
        threading.Thread(target=task,daemon=True,name='AutoFusionReleaseLookup').start()

    def _show_github_release_dialog(self, repo: Repo, release_page: str, assets: list[Asset]):
        win=tk.Toplevel(self)
        win.title(f'{repo.full_name} · GitHub Release（未审核）')
        win.geometry('780x490');win.configure(bg=BG);win.transient(self)
        win.grab_set()
        tk.Label(win,text=f'{repo.full_name} · 发布包',font=(FONT,15,'bold'),bg=BG,fg=TEXT).pack(anchor='w',padx=20,pady=(18,6))
        tk.Label(win,text='重要：此仓库没有经过 AutoFusion 身份或安全审核。文件可能包含恶意程序；下载仅保存，不自动执行、安装或加入模块。',
                 font=(FONT,10),bg=BG,fg='#a66e19',wraplength=720,justify='left').pack(anchor='w',padx=20,pady=(0,12))
        box=tk.Listbox(win,font=(FONT,10),height=13,selectmode='browse',activestyle='dotbox')
        box.pack(fill='both',expand=True,padx=20)
        for a in assets:
            box.insert('end',f'{a.name}  ·  {a.size/1024**2:.1f} MiB'+('  ·  有 SHA-256 摘要' if a.sha256 else '  ·  无摘要'))
        if assets: box.selection_set(0)
        else: box.insert('end','该 Release 没有符合筛选规则的 Windows 安装附件；请去官网判断项目是否支持 Windows。')
        line=tk.Frame(win,bg=BG)
        line.pack(fill='x',padx=20,pady=18)
        Pill(line,'打开 GitHub 发布页',lambda:webbrowser.open(release_page),width=185).pack(side='left')
        def save_selected():
            selection=box.curselection()
            if not assets or not selection or selection[0]>=len(assets): return
            asset=assets[selection[0]]
            if not messagebox.askyesno('未审核文件 · 下载风险确认',
                f'仓库：{repo.full_name}\n附件：{asset.name}\n\n'
                'AutoFusion 尚未核实开发者身份、许可证和软件安全性。GitHub 上的项目可能并非官方，下载或运行第三方程序存在风险。\n\n'
                '此操作只保存原始文件，绝不执行、安装、解压或接入管理。下载后请自行核验官方身份、数字签名和安全性。\n\n仍要下载吗？',parent=win,icon='warning'):
                return
            initial=Path.home()/'Downloads'
            if not initial.is_dir(): initial=Path.home()
            dest=filedialog.asksaveasfilename(parent=win,title='仅保存未经审核的原始 Release 文件',
                    initialdir=str(initial),initialfile=asset.name)
            if not dest:return
            if Path(dest).exists():
                messagebox.showerror('拒绝覆盖','此文件已存在，请另选新文件名。',parent=win)
                return
            win.destroy()
            self._download_unreviewed_asset(asset,Path(dest))
        Pill(line,'仅下载选中附件',save_selected,width=177,primary=True).pack(side='right')
        if not assets:
            for child in line.winfo_children():
                if isinstance(child,Pill) and child.label=='仅下载选中附件': child.configure(state='disabled')

    def _download_unreviewed_asset(self, asset: Asset, dest: Path):
        self.status.set('正在下载未审核附件（仅保存）')
        def task():
            try:
                last=[-1]
                def report(read,total):
                    step=read//(4*1024*1024)
                    if step!=last[0]:
                        last[0]=step
                        self.events.put(('discovery_progress',(asset.name,read,total)))
                digest, matched=download_release_asset(asset,dest,progress=report)
                self.events.put(('discovery_downloaded',(asset.name,str(dest),digest,matched)))
            except Exception as e:
                self.events.put(('discovery_download_error',str(e)))
        threading.Thread(target=task,daemon=True,name='AutoFusionUnreviewedDownload').start()

    def _rerender_logs(self):
        if not hasattr(self,'log_records'):
            return
        filt=self.log_filter.get()
        mapping={'全部':None,'信息':{'info','start','stop','complete','command'},'成功':{'success'},'警告':{'warning'},'错误':{'error'}}
        allowed=mapping.get(filt)
        self.log.configure(state='normal')
        self.log.delete('1.0','end')
        for level,msg in self.log_records:
            if allowed is None or level in allowed:
                self.log.insert('end',msg+'\n',level)
        self.log.see('end')
        self.log.configure(state='disabled')


    def _toggle_update_reminder(self, module: str):
        """Persistent module-scoped UI preference, not a download trust signal."""
        try:
            set_reminder_muted(module, not is_reminder_muted(module))
            if self._manage_details_key == module:
                self._refresh_manage_cards()
        except (OSError, ValueError) as exc:
            messagebox.showerror('保存提醒偏好失败', str(exc), parent=self)

    def _open_offline_library(self):
        """User-controlled offline archive vault, no auto execution or installation."""
        existing=getattr(self,'_offline_window',None)
        if existing and existing.winfo_exists():
            existing.lift(); existing.focus_set(); return
        try:
            managed=self._validated_root()
            vault=OfflineLibrary(default_library_root(managed),managed_root=managed)
            entries=vault.list_items()
        except (ValueError,OSError) as exc:
            messagebox.showerror('离线库不可用', str(exc), parent=self); return
        window=tk.Toplevel(self)
        self._offline_window=window
        window.title('AutoFusion · 离线软件包库（仅保管，不执行）')
        window.geometry('1030x610'); window.minsize(800,480)
        window.configure(bg=BG)
        body=tk.Frame(window,bg=BG,padx=20,pady=16);body.pack(fill='both',expand=True)
        tk.Label(body,text='离线软件包库',font=(FONT,18,'bold'),bg=BG,fg=TEXT).pack(anchor='w')
        tk.Label(body,text='保存本地安装包副本，检查 SHA-256，导入或导出离线备份；不会执行、安装、清理原始文件。',
                 font=(FONT,10),bg=BG,fg=SUB,wraplength=970,justify='left').pack(anchor='w',pady=(4,8))
        path_var=tk.StringVar(value='保存位置：'+str(vault.root))
        tk.Label(body,textvariable=path_var,font=(FONT,9),bg=BG,fg=SUB,anchor='w').pack(fill='x',pady=(0,10))
        container=tk.Frame(body,bg=CARD,highlightbackground=BORDER,highlightthickness=1)
        container.pack(fill='both',expand=True)
        tree=ttk.Treeview(container,columns=('name','size','hash','added'),show='headings',selectmode='extended')
        for col,title,width in [('name','文件名',350),('size','大小',100),('hash','SHA-256（前 18 位）',190),('added','导入时间',185)]:
            tree.heading(col,text=title);tree.column(col,width=width,minwidth=65,stretch=(col=='name'))
        tree.pack(side='left',fill='both',expand=True)
        bar=ttk.Scrollbar(container,command=tree.yview)
        bar.pack(side='right',fill='y');tree.configure(yscrollcommand=bar.set)
        status=tk.StringVar(value='待命：离线文件仅为用户个人副本，不等于安全认证。')
        tk.Label(body,textvariable=status,font=(FONT,9),bg=BG,fg=SUB,wraplength=920,justify='left').pack(anchor='w',pady=(9,10))
        actions=tk.Frame(body,bg=BG);actions.pack(fill='x')
        jobs=queue.Queue()
        state={'busy':False}
        controls=[]

        def refresh():
            for kid in tree.get_children(): tree.delete(kid)
            records=vault.list_items()
            for e in records:
                tree.insert('','end',iid=e['id'],values=(e['name'],f"{e['size']/1024**2:.1f} MiB",e['sha256'][:18]+'…',e['added'][:19]))
            status.set(f'共 {len(records)} 个离线文件；安装文件与 AutoFusion 程序、fusion 模块相互独立。')

        def selected():
            ids=list(tree.selection())
            if not ids: messagebox.showinfo('请先选择','先选择一个或多个文件。',parent=window)
            return ids

        def launch(action, func):
            if state['busy']: return
            state['busy']=True
            status.set(action+'（后台处理中，不执行安装程序）')
            for control in controls: control.configure(state='disabled')
            def worker():
                try: jobs.put((True,action,func()))
                except Exception as err: jobs.put((False,action,str(err)))
            threading.Thread(target=worker,daemon=True,name='AutoFusionOfflineLibrary').start()

        def import_file():
            filename=filedialog.askopenfilename(parent=window,title='选择要备份的安装包（不会执行）',
                         filetypes=[('安装包或便携压缩包','*.exe *.msi *.zip *.7z *.msix *.msixbundle *.appx *.appxbundle'),('所有文件','*.*')])
            if filename: launch('导入安装包',lambda:vault.import_file(Path(filename)))

        def import_bundle():
            filename=filedialog.askopenfilename(parent=window,title='选择 AutoFusion 离线备份 ZIP',filetypes=[('ZIP 备份','*.zip')])
            if filename: launch('导入离线备份',lambda:vault.import_bundle(Path(filename)))

        def verify_selected():
            ids=selected()
            if ids: launch('正在校验',lambda:[(uid,vault.verify(uid)) for uid in ids])

        def export_selected():
            ids=selected()
            if not ids:return
            if not messagebox.askyesno('个人备份与版权提示',
                '导出包包含所选软件安装文件的完整副本。请仅用于个人迁移，或在获得相应软件分发许可时分享；AutoFusion 不保证第三方软件允许重新分发。\n\n继续导出吗？',parent=window):
                return
            path=filedialog.asksaveasfilename(parent=window,title='新建离线备份 ZIP',defaultextension='.zip',
                         initialfile='AutoFusion_Offline_Backup.zip',filetypes=[('ZIP 文件','*.zip')])
            if path: launch('导出离线备份',lambda:vault.export_bundle(ids,Path(path)))

        def remove_selected():
            ids=selected()
            if not ids:return
            if not messagebox.askyesno('删除库内副本？',
                f'仅删除离线库自己保存的 {len(ids)} 个副本及相应登记；绝不删除最初导入的原文件、已安装模块或用户工程。\n\n是否删除？',parent=window):
                return
            launch('删除库内副本',lambda:[vault.remove(uid) for uid in ids])

        def open_folder():
            vault.root.mkdir(parents=True,exist_ok=True)
            if os.name=='nt': os.startfile(str(vault.root))
            else: messagebox.showinfo('离线库存储位置',str(vault.root),parent=window)

        def add(text,fn,width=118,primary=False):
            button=Pill(actions,text,fn,width=width,primary=primary,height=38,font_size=9)
            button.pack(side='left',padx=(0,7)); controls.append(button)
        add('导入安装包',import_file,122,True)
        add('导入备份 ZIP',import_bundle,121)
        add('校验选中',verify_selected,112)
        add('导出选中',export_selected,112)
        add('删除库内副本',remove_selected,135)
        add('打开存储目录',open_folder,130)
        tk.Label(body,text='SHA-256 仅验证文件和导入时一致，不能证明它来自官方或没有恶意代码。',
                 font=(FONT,9),bg=BG,fg=ORANGE).pack(anchor='w',pady=(12,0))

        def poll():
            if not window.winfo_exists(): return
            try:
                while True:
                    success,action,outcome=jobs.get_nowait()
                    state['busy']=False
                    for c in controls: c.configure(state='normal')
                    if success:
                        try: refresh()
                        except (OSError,ValueError) as exc: status.set(str(exc))
                        if action=='正在校验':
                            failures=[x for x in outcome if not x[1][0]]
                            status.set(f'校验 {len(outcome)} 项，失败 {len(failures)} 项。'+
                                       (' 不一致：'+', '.join(uid[:8] for uid,_ in failures) if failures else ' 所选副本与入库记录一致。'))
                        else:
                            status.set(action+'完成。不会自动运行任何文件。')
                    else:
                        status.set(action+'失败：'+str(outcome))
                        messagebox.showerror('操作未完成',str(outcome),parent=window)
            except queue.Empty: pass
            self.after(130,poll)
        refresh();self.after(130,poll)


# V0.2.3.2: dedicated official release cache cleanup and offline reuse.
BG = "#f6f7f8"
CARD = "#ffffff"
CARD_ALT = "#f2f3f5"
TEXT = "#111318"
SUB = "#505862"
ACCENT = "#111318"
BORDER = "#d9dde2"
FONT = "Microsoft YaHei UI"


class App(UIv3, tk.Tk):
    def __init__(self):
        boot_started = time.perf_counter()
        super().__init__()
        self._pending_after_ids = set()
        self.title("AutoFusion Community")
        self.configure(bg=BG)
        self.settings = load_settings()
        self.events: queue.SimpleQueue[tuple[str, str]] = queue.SimpleQueue()
        self.runner = Runner(self._queue_log)
        self.install_thread = None
        self.install_buttons = []
        self._close_when_finished = False
        self._ww_uninstall_pending = False
        self.update_busy = False
        self._style()
        self._vars()
        self._scan_generation = 0
        self._cached_module_states = self._initial_status_cache()
        self._build()
        self._poll()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<F8>", lambda _e: self._stop())
        self._log("info", f"程序已启动 V{APP_VERSION}；界面初始化 {time.perf_counter()-boot_started:.2f} 秒（不含 Python 启动）。模块扫描在后台进行。")
        self._log("warning", "注意：F8 仅在 AutoFusion 窗口有焦点时生效，不是全局热键。")

    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("TFrame", background=BG)
        s.configure("Card.TFrame", background=CARD)
        s.configure("TLabel", background=BG, foreground=TEXT, font=(FONT, 11))
        s.configure("Muted.TLabel", background=BG, foreground=SUB, font=(FONT, 10))
        s.configure("Title.TLabel", background=BG, foreground=TEXT, font=(FONT, 23, "bold"))
        s.configure("Section.TLabel", background=BG, foreground=TEXT, font=(FONT, 14, "bold"))
        s.configure("Card.TLabel", background=CARD, foreground=TEXT, font=(FONT, 11))
        s.configure("CardMuted.TLabel", background=CARD, foreground=SUB, font=(FONT, 10))
        s.configure("CardTitle.TLabel", background=CARD, foreground=TEXT, font=(FONT, 13, "bold"))
        s.configure("TEntry", fieldbackground=CARD, foreground=TEXT,
                    insertcolor=TEXT, padding=(10, 9), borderwidth=1,
                    lightcolor=BORDER, darkcolor=BORDER)
        s.map("TEntry", bordercolor=[("focus", "#7a7e85")])
        s.configure("TButton", font=(FONT, 11), padding=(14, 10),
                    background=CARD, foreground=TEXT, borderwidth=1,
                    lightcolor=BORDER, darkcolor=BORDER)
        s.map("TButton", background=[("active", CARD_ALT), ("disabled", CARD_ALT)],
              foreground=[("disabled", "#80858c")])
        s.configure("Primary.TButton", font=(FONT, 11, "bold"), padding=(16, 12),
                    background=ACCENT, foreground="#ffffff", borderwidth=1,
                    lightcolor=ACCENT, darkcolor=ACCENT)
        s.map("Primary.TButton", background=[("active", "#33363d"), ("disabled", "#aaaeb5")],
              foreground=[("active", "#ffffff"), ("disabled", "#ffffff")])
        s.configure("TScrollbar", background="#d4d8de", troughcolor=BG,
                    arrowcolor=SUB, borderwidth=0)

    def _vars(self):
        c = self.settings
        try:
            initial_root=c.install_root or str(default_root())
        except ValueError:
            initial_root=""
        self.install_root = tk.StringVar(value=initial_root)
        self.ww_path = tk.StringVar(value=c.ww_exe)
        self.sra_path = tk.StringVar(value=c.sra_exe)
        self.ww_task = tk.StringVar(value=str(c.ww_task))
        self.sra_config = tk.StringVar(value=c.sra_config)
        self.ww_enabled = tk.BooleanVar(value=c.enable_ww)
        self.sra_enabled = tk.BooleanVar(value=c.enable_sra)
        self.dry_run = tk.BooleanVar(value=c.dry_run)
        self.timeout = tk.StringVar(value=str(c.job_timeout_minutes))
        self.keep_install_packages = tk.BooleanVar(value=bool(c.keep_install_packages))
        self.keep_install_packages.trace_add("write", lambda *_: self._store_cache_preference())
        self.manage_ww_state = tk.StringVar(value="尚未扫描")
        self.manage_sra_state = tk.StringVar(value="尚未扫描")
        self.status = tk.StringVar(value="待命 · 模拟模式" if c.dry_run else "待命")
        self.dry_run.trace_add("write", lambda *_: self.status.set("待命 · 模拟模式" if self.dry_run.get() else "待命 · 真实执行"))

    def _build(self):
        """V0.3 four-page dashboard; lifecycle and installer logic remains unchanged."""
        self._build_v3()

    def _switch_page(self, name):
        self._switch_v3(name)

    def _clear_checkbox(self, parent, enabled: tk.BooleanVar, label: str, background: str, large=False):
        text_value = tk.StringVar()
        checkbox = tk.Checkbutton(
            parent, textvariable=text_value, variable=enabled, indicatoron=False,
            relief="flat", bd=0, padx=2, pady=2, highlightthickness=0,
            bg=background, fg=TEXT, selectcolor=background,
            activebackground=background, activeforeground=TEXT,
            font=(FONT, 13 if large else 11, "bold" if large else "normal"),
            cursor="hand2", anchor="w", takefocus=True)

        def refresh(*_):
            text_value.set(("☑  " if enabled.get() else "☐  ") + label)
            checkbox.configure(fg=TEXT if enabled.get() else SUB)

        enabled.trace_add("write", refresh)
        refresh()
        return checkbox

    def _copy_log(self):
        text = self.log.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()

    def _clear_visible_log(self):
        if hasattr(self, "log_records"):
            self.log_records.clear()
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _choose_folder(self):
        chosen = filedialog.askdirectory(title="选择 AutoFusion 模块存储目录")
        if chosen:
            self.install_root.set(chosen)

    def _validated_root(self) -> Path:
        value = self.install_root.get().strip().strip('"')
        if not value:
            raise ValueError('请在设置中选择可写的整合目录，例如 D:\\AutoFusion\\fusion')
        folder = Path(value).expanduser()
        if not folder.is_absolute():
            raise ValueError('整合目录必须是绝对路径')
        if folder == Path(folder.anchor):
            raise ValueError('请不要直接使用磁盘根目录；建议使用 Community 独立模块目录')
        # Avoid installing in protected system directories.
        if sys.platform == 'win32':
            systarget = os.environ.get('WINDIR', 'C:\\Windows').lower().rstrip('\\/')
            if str(folder).lower().startswith(systarget + os.sep):
                raise ValueError('不能将整合目录设在 Windows 系统目录下')
        return folder

    def _install_selected(self):
        selected = [m for m, var in [('ww', self.ww_enabled), ('sra', self.sra_enabled)] if var.get()]
        self._install(selected)

    def _install(self, modules: list[str]):
        if self.install_thread is not None and self.install_thread.is_alive():
            messagebox.showinfo('安装中', '已有下载任务正在进行，请完成后再试。')
            return
        if self.runner.running:
            messagebox.showwarning('任务运行中', '请先结束正在执行的游戏任务。')
            return
        if not modules:
            messagebox.showinfo('未选择模块', '请先勾选至少一个模块。')
            return
        try:
            root = self._validated_root()
        except ValueError as exc:
            messagebox.showerror('路径错误', str(exc))
            return
        labels = '、'.join(MODULE_INFO[m]['label'] for m in modules)
        text = (f'即将从 GitHub 官方 Release 下载：{labels}\n\n'
                f'整合目录：{root}\n\n'
                'SRA 会自动解压并接入 AutoFusion。\n'
                'OK-WW 采用官方安装向导，下载后仍需你确认安装位置并完成安装。\n\n'
                '不会运行源码安装脚本；下载文件来源于第三方作者，运行前请确认信任及游戏账号风险。\n'
                '部分网络可能无法访问 GitHub；本版暂不提供代理/镜像下载。\n\n确认开始下载吗？')
        if not messagebox.askyesno('官方项目下载安装', text, icon='warning'):
            return
        self._log('info', f'开始下载安装：{labels}；存储位置：{root}')
        self.all_install_button.configure(state='disabled')
        for b in self.install_buttons:
            if b.winfo_exists():
                b.configure(state='disabled')
        self.status.set('正在下载 / 安装...')
        self.install_thread = threading.Thread(target=self._install_worker, args=(list(modules), root), daemon=True)
        self.install_thread.start()

    def _install_worker(self, modules: list[str], root: Path):
        for module in modules:
            self.events.put(('install_log', ('info', f'开始获取 {MODULE_INFO[module]["label"]} 的官方发布包'))) 
            try:
                kind, result, asset, digest = download_official(
                    module, root, lambda line, m=module: self.events.put(('install_log', ('info', f'[{m}] {line}'))))
                self.events.put(('install_result', (module, kind, str(result), asset.version, digest)))
            except Exception as exc:
                self.events.put(('install_log', ('error', f'{MODULE_INFO[module]["label"]} 下载/安装失败：{exc}')))
        self.events.put(('install_done', None))

    def _detect_all(self, silent=False):
        try:
            root = self._validated_root()
        except ValueError:
            if not silent: messagebox.showerror('路径错误', '请先设置有效的整合目录')
            return
        found = []
        ww = detect_ww_exe(root)
        if ww:
            self.ww_path.set(str(ww))
            found.append('OK-WW：' + str(ww))
        sra = discover_sra_executable(root, self.sra_path.get().strip())
        if sra:
            self.sra_path.set(str(sra))
            found.append('SRA：' + str(sra))
        if found:
            try: save_settings(self._read_settings())
            except (ValueError, OSError): pass
            for item in found:
                self._log('success', '已检测：' + item)
        elif not silent:
            self._log('warning', '未发现可识别的安装。OK-WW 可尝试点击「浏览」手动选中 ok-ww.exe。')

    def _open_original(self, module):
        if module == 'sevenzip':
            return self._open_sevenzip()
        try:
            if module == 'ww':
                path = Path(self.ww_path.get().strip())
            else:
                root = self._validated_root()
                path = sra_desktop_executable(root)
                if not path and self.sra_path.get().strip():
                    path = Path(self.sra_path.get().strip()).parent / 'SRA.exe'
            if not path or not path.is_file():
                raise ValueError('未找到原版程序。请先从 AutoFusion 下载安装，或通过「浏览」选择已安装程序。')
            if sys.platform != 'win32':
                raise ValueError('此操作仅支持 Windows')
            os.startfile(str(path))
            self._log('info', '已请求启动原版界面：' + str(path))
        except (OSError, ValueError) as exc:
            messagebox.showerror('无法打开原版程序', str(exc))

    def _install_catalog(self, module):
        if module == 'sevenzip':
            return self._install_sevenzip()
        return self._install([module])

    def _build_sevenzip_detail(self):
        info=CATALOG['sevenzip']
        card=SoftCard(self.manage_host,padx=20,pady=19)
        card.pack(fill='both',expand=True)
        record=getattr(self,'_sevenzip_record',None)
        if record is None:
            record=detect_sevenzip(windows=False)  # Keep UI responsive; background scan will update.
        update_state, update_label=sevenzip_update_message(record,info['version'])
        head=tk.Frame(card,bg=CARD); head.pack(fill='x',pady=(0,12))
        tk.Label(head,text='7-Zip · 官方安装版',font=(FONT,15,'bold'),bg=CARD,fg=TEXT).pack(side='left')
        tk.Label(head,text='已安装' if record.state in ('installed','unregistered') else self._module_status('sevenzip')[1],
                 font=(FONT,10,'bold'),bg=CARD,fg=GREEN if record.state in ('installed','unregistered') else SUB).pack(side='right')
        tk.Label(card,text=f"已审核发布包：{info['version']} · Windows x64 · SourceForge 官方项目发布源",font=(FONT,10),bg=CARD,fg=SUB).pack(anchor='w',pady=(0,7))
        muted=is_reminder_muted('sevenzip')
        if update_state=='update' and muted:
            tk.Label(card,text='7-Zip 自动更新提醒已关闭；手动获取更新仍可用。',font=(FONT,10),bg=CARD,fg=SUB).pack(anchor='w',pady=(0,10))
        else:
            tk.Label(card,text=update_label,font=(FONT,11,'bold'),bg=CARD,fg=ACTION if update_state=='update' else SUB).pack(anchor='w',pady=(0,10))
        remind=tk.Frame(card,bg=CARD);remind.pack(fill='x',pady=(0,9))
        tk.Label(remind,text='提醒设置：只影响此模块，不停止手动更新检查',font=(FONT,9),bg=CARD,fg=SUB).pack(side='left')
        Pill(remind,'恢复更新提醒' if muted else '不再提示更新',lambda:self._toggle_update_reminder('sevenzip'),width=139).pack(side='right')
        line=tk.Frame(card,bg=CARD); line.pack(fill='x',pady=(0,12))
        main_label='下载可用更新' if update_state=='update' else '官方下载并安装'
        install=Pill(line,main_label,self._install_sevenzip,width=171,primary=True)
        install.pack(side='left'); self.install_buttons.append(install)
        Pill(line,'打开 7-Zip',self._open_sevenzip,width=130).pack(side='left',padx=(9,0))
        Pill(line,'官方网页',lambda:self._open_repo('sevenzip'),width=120).pack(side='left',padx=(9,0))
        tk.Frame(card,bg=BORDER,height=1).pack(fill='x',pady=(9,12))
        tk.Label(card,text='识别到的程序路径',font=(FONT,12,'bold'),bg=CARD,fg=TEXT).pack(anchor='w',pady=(0,8))
        pathrow=tk.Frame(card,bg=CARD);pathrow.pack(fill='x',pady=(0,13))
        self.sevenzip_path=tk.StringVar(value=str(record.exe) if record.exe else '')
        pathentry=ttk.Entry(pathrow,textvariable=self.sevenzip_path,state='readonly')
        pathentry.pack(side='left',fill='x',expand=True,padx=(0,8))
        Pill(pathrow,'浏览位置',self._open_sevenzip_location,width=112).pack(side='left')
        Pill(pathrow,'复制路径',self._copy_sevenzip_path,width=112).pack(side='left',padx=(8,0))
        tk.Frame(card,bg=BORDER,height=1).pack(fill='x',pady=(0,12))
        tk.Label(card,text='Windows 安装登记与官方卸载',font=(FONT,12,'bold'),bg=CARD,fg=TEXT).pack(anchor='w',pady=(0,9))
        text=(f"状态：{record.state}  |  已安装版本：{record.version or '未知'}  |  已收录版本：{info['version']}\n"
              f"安装位置：{record.location or '未检测到'}\n"
              f"官方卸载命令：{record.uninstall or '未找到'}")
        tk.Label(card,text=text,bg=CARD,fg=SUB,font=(FONT,10),wraplength=940,justify='left').pack(anchor='w',pady=(0,14))
        controls=tk.Frame(card,bg=CARD); controls.pack(fill='x')
        Pill(controls,'调用官方卸载器',self._uninstall_sevenzip,width=170).pack(side='left')
        Pill(controls,'重新识别',self._scan_and_refresh,width=122).pack(side='left',padx=(9,0))
        tk.Label(card,text='下载更新仅获取并校验安装包；是否运行原版安装器由用户决定。不会直接删除既有 7-Zip，也不会扫描全盘文件。',
                 bg=CARD,fg=SUB,font=(FONT,9),wraplength=940,justify='left').pack(anchor='w',pady=(13,0))

    def _open_sevenzip_location(self):
        record=detect_sevenzip()
        if not record.exe or not record.exe.is_file():
            messagebox.showwarning('未找到程序','无法确认 7-Zip 的程序路径。',parent=self)
            return
        try:
            if sys.platform!='win32': raise ValueError('只支持 Windows')
            os.startfile(str(record.exe.parent))
        except (OSError,ValueError) as exc:
            messagebox.showerror('无法打开位置',str(exc),parent=self)

    def _copy_sevenzip_path(self):
        record=detect_sevenzip()
        if not record.exe or not record.exe.is_file():
            messagebox.showwarning('未找到程序','当前没有可复制的已验证程序路径。',parent=self)
            return
        self.clipboard_clear()
        self.clipboard_append(str(record.exe))
        self._log('info','已复制 7-Zip 程序路径。')

    def _install_sevenzip(self):
        if self.install_thread and self.install_thread.is_alive():
            messagebox.showinfo('正在下载','请先完成当前下载任务。',parent=self)
            return
        current=detect_sevenzip()
        info=CATALOG['sevenzip']
        update_state, update_label=sevenzip_update_message(current, info['version'])
        if update_state in ('current','newer'):
            messagebox.showinfo('无需重复下载',update_label+'。\n已经安装了该版本或更新的版本。',parent=self)
            return
        if update_state=='repair':
            messagebox.showwarning('安装记录异常','Windows 中存在 7-Zip 安装登记，但程序文件未找到。建议先通过官方安装器修复或官方卸载，不自动覆盖。',parent=self)
            return
        if current.state in ('installed','unregistered') and update_state=='unknown':
            messagebox.showwarning('无法确定可用更新',update_label+'。\n请先核实版本，避免误升级或降级。',parent=self)
            return
        if not sevenzip_arch_supported():
            messagebox.showwarning('架构不支持','本轮测试只适配 Windows x64。请从 7-Zip 官网选择适合的版本。',parent=self)
            return
        if self.runner.running:
            messagebox.showwarning('任务正在执行','请先停止当前任务。',parent=self)
            return
        try: root=self._validated_root()
        except ValueError as e:
            messagebox.showerror('目录无效',str(e),parent=self); return
        item=CATALOG['sevenzip']
        heading='下载 7-Zip 官方更新包' if update_state=='update' else '从官方来源获取 7-Zip'
        if not messagebox.askyesno(heading,
                f"当前版本：{current.version or '未安装'}  →  已审核可用版本：{item['version']}\n"
                '发布来源：7-Zip 作者的 SourceForge 项目（非 GitHub）\n'
                f"官方安装包：{item['asset']}\n\n"
                '将下载并验证 SHA-256，下载完成后可以选择只保留包、不运行安装器。\n'
                '安装时可按官方向导选择路径；不静默安装。是否继续？',parent=self):
            return
        self.status.set('正在下载 7-Zip 官方安装包...')
        self._log('info',f'准备从 SourceForge 下载 7-Zip {item["version"]}（当前检测版本：{current.version or "未安装"}；不会直接执行）。')
        def work():
            try:
                path,h=download_sevenzip(root,item,lambda n:self.events.put(('sevenzip_progress',n)))
                self.events.put(('sevenzip_downloaded',(str(path),h)))
            except Exception as e:
                self.events.put(('sevenzip_error',str(e)))
            finally:
                self.events.put(('sevenzip_done',None))
        self.install_thread=threading.Thread(target=work,daemon=True,name='SevenzipDownload')
        self.install_thread.start()

    def _open_sevenzip(self):
        rec=detect_sevenzip()
        if not rec.exe or not rec.exe.is_file():
            messagebox.showwarning('未找到 7-Zip','没有找到可启动的 7-Zip 原版程序。',parent=self)
            return
        try:
            if sys.platform != 'win32': raise ValueError('仅支持 Windows')
            os.startfile(str(rec.exe)); self._log('info','请求打开 7-Zip：'+str(rec.exe))
        except (OSError,ValueError) as e:
            messagebox.showerror('启动失败',str(e),parent=self)

    def _uninstall_sevenzip(self):
        rec=detect_sevenzip()
        executable=uninstall_path_from_record(rec)
        if not executable:
            messagebox.showwarning('需要使用官方卸载方式',
                '未找到可安全核验的 7-Zip 原版卸载程序。请到 Windows「已安装的应用」选择 7-Zip 卸载。\nAutoFusion 不会强行删除文件夹或注册表。',parent=self)
            return
        if not messagebox.askyesno('是否卸载 7-Zip？',
                '将启动 7-Zip 原版卸载程序，后续步骤以官方界面为准。\n'
                'AutoFusion 不会直接删除其程序目录或系统记录。\n\n是否继续？',parent=self):
            return
        try:
            os.startfile(str(executable))
            self._log('info','已请求打开 7-Zip 官方卸载程序：'+str(executable))
            self.after(2500, lambda:self._schedule_status_scan(quiet=True))
        except OSError as e:
            messagebox.showerror('无法启动卸载器',str(e),parent=self)

    def _manage_scan(self, quiet=False):
        try:
            root = self._validated_root()
            sra = inspect_sra(root, selected=self.sra_path.get().strip(),discover_external=True)
            labels = {
                'managed': f'已识别受管理的便携版安装：{sra.removable} 个原版文件可安全清理',
                'absent': '未安装 / 未检测到受管理的模块',
                'unmanaged': sra.message,
                'unsafe': '模块目录异常，拒绝自动卸载',
            }
            self.manage_sra_state.set(labels.get(sra.state, sra.state))
            path = Path(self.ww_path.get().strip()) if self.ww_path.get().strip() and Path(self.ww_path.get().strip()).is_file() else detect_ww_exe(root)
            ww = inspect_ww(path)
            wwlabels = {'installed': '已识别程序及 Windows 官方安装登记',
                        'stale': '仅有官方安装记录、程序缺失；建议原版修复或官方卸载',
                        'unregistered': '发现程序，但未找到 Windows 官方卸载登记',
                        'absent': '未检测到程序或官方安装登记'}
            self.manage_ww_state.set(wwlabels.get(ww.state, ww.state))
            # An official installer may spawn child processes; only a subsequent
            # scan showing no EXE *and* no Windows registration completes the flow.
            if getattr(self, '_ww_uninstall_pending', False) and ww.state == 'absent':
                self._ww_uninstall_pending = False
                if not self.keep_install_packages.get():
                    try:
                        n, size = clean_module_cache(root, 'ww')
                        self._log('success', f'OK-WW 官方卸载已通过状态扫描：清理 {n} 个专属安装包缓存（{size / 1024**2:.1f} MiB）。')
                    except (OSError, ValueError) as exc:
                        self._log('warning', f'OK-WW 已卸载，但安装包缓存未能清理：{exc}')
                else:
                    self._log('info', 'OK-WW 官方卸载已通过状态扫描，按设置保留安装包用于离线重装。')
            if not quiet:
                self._log('info', f'模块状态扫描：SRA={sra.state}，OK-WW={ww.state}；未修改文件。')
            sra_labels={'managed':'已部署','absent':'未安装','unmanaged':'已发现（只读）','unsafe':'存在异常'}
            ww_labels={'installed':'已安装','absent':'未安装','stale':'需要修复','unregistered':'仅检测到程序'}
            seven = detect_sevenzip()
            self._cached_module_states={'sra':(sra.state,sra_labels.get(sra.state,sra.state)),
                                        'ww':(ww.state,ww_labels.get(ww.state,ww.state)),
                                        'sevenzip':(seven.state, {'installed':'已安装','absent':'未安装','stale':'需要修复','unregistered':'发现程序（未登记）'}.get(seven.state,seven.state))}
            self._refresh_home_cards()
            self._manage_dirty=True
            return sra, ww
        except (OSError, ValueError) as exc:
            messagebox.showerror('状态扫描失败', str(exc))
            return None

    def _show_uninstall_preview(self, title, content):
        window = tk.Toplevel(self)
        window.title(title)
        window.geometry('760x550')
        window.minsize(550, 370)
        window.configure(bg=BG)
        window.transient(self)
        tk.Label(window, text=title, bg=BG, fg=TEXT,
                 font=(FONT, 15, 'bold')).pack(anchor='w', padx=20, pady=(18, 10))
        area = tk.Frame(window, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        area.pack(fill='both', expand=True, padx=20)
        sc = ttk.Scrollbar(area)
        sc.pack(side='right', fill='y')
        body = tk.Text(area, font=(FONT, 11), bg=CARD, fg=TEXT, bd=0,
                       wrap='word', padx=13, pady=13, yscrollcommand=sc.set)
        body.pack(fill='both', expand=True)
        body.insert('1.0', content)
        body.configure(state='disabled')
        sc.configure(command=body.yview)
        footer = tk.Frame(window, bg=BG)
        footer.pack(fill='x', padx=20, pady=16)
        def copy():
            self.clipboard_clear()
            self.clipboard_append(content)
            self.update_idletasks()
        ttk.Button(footer, text='复制预览', command=copy).pack(side='left')
        ttk.Button(footer, text='关闭', command=window.destroy).pack(side='right')

    def _preview_sra(self):
        try:
            root = self._validated_root()
            plan = inspect_sra(root)
            cached = cache_plan(root, 'sra')
            extras = ('\n\n【安装缓存】\n'
                      f'可识别 ZIP：{len(cached.files)} 个（{cached.total_bytes/1024**2:.1f} MiB）\n'
                      f'卸载时：{"保留用于离线重装" if self.keep_install_packages.get() else "清理此模块专属安装包"}\n'
                      f'其他文件：{cached.unrecognized_files} 个（始终保留）')
            self._show_uninstall_preview('SRA · 安全卸载预览（不会删除）', plan_text_sra(plan) + extras)
            self._log('info', '已预览 SRA 安全卸载清单，没有修改文件。')
        except (OSError, ValueError) as exc:
            messagebox.showerror('预览失败', str(exc))

    def _preview_ww(self):
        try:
            root = self._validated_root()
            path = Path(self.ww_path.get().strip()) if self.ww_path.get().strip() and Path(self.ww_path.get().strip()).is_file() else detect_ww_exe(root)
            state = inspect_ww(path)
            cache = cache_plan(root, 'ww')
            cache_info = (f'\n\n【安装缓存】识别到 {len(cache.files)} 个官方安装包，'
                          f'{cache.total_bytes/1024**2:.1f} MiB。'
                          f'\n安装成功卸载并复查后：{"保留" if self.keep_install_packages.get() else "清理"}。')
            self._show_uninstall_preview('OK-WW · 官方卸载信息（不会删除）', plan_text_ww(state) + cache_info)
            self._log('info', f'已预览 OK-WW 官方卸载信息，安装状态：{state.state}；没有修改文件。')
        except (OSError, ValueError) as exc:
            messagebox.showerror('预览失败', str(exc))

    def _busy_uninstall(self):
        if self.runner.running or (self.install_thread and self.install_thread.is_alive()):
            messagebox.showwarning('操作冲突', '请先停止 AutoFusion 的执行队列及下载任务。')
            return True
        return False

    def _running_sra_processes(self):
        if sys.platform != 'win32':
            return []
        try:
            import csv
            import io
            result = subprocess.run(['tasklist', '/NH', '/FO', 'CSV'], capture_output=True,
                                    timeout=12, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                return ['无法检测系统进程']
            names = []
            for encoding in ('utf-8-sig', 'mbcs'):
                try:
                    rows = csv.reader(io.StringIO(result.stdout.decode(encoding)))
                    names = [row[0].casefold() for row in rows if row]
                    break
                except (UnicodeError, csv.Error):
                    continue
            return [name for name in names if name in ('sra.exe', 'sra-cli.exe')]
        except (OSError, subprocess.TimeoutExpired):
            return ['无法检测系统进程']

    def _uninstall_sra(self):
        if self._busy_uninstall():
            return
        try:
            root = self._validated_root()
            plan = inspect_sra(root)
            if plan.state != 'managed' or plan.unsafe:
                messagebox.showwarning('不允许自动删除', plan_text_sra(plan))
                return
            if plan.removable == 0:
                messagebox.showinfo('无需卸载', '没有可安全删除的原版文件。')
                return
            processes = self._running_sra_processes()
            if processes:
                messagebox.showwarning('请先关闭原版程序',
                    '检测到 SRA 正在运行或无法确认运行状态：\n' +
                    '\n'.join(processes) + '\n请先结束，再重新检查。')
                return
            cache = cache_plan(root, 'sra')
            if cache.unsafe:
                raise ValueError('SRA 缓存目录异常，暂停自动清理：' + cache.reason)
            keep_cache = bool(getattr(self, 'keep_install_packages', None) and self.keep_install_packages.get())
            if not messagebox.askyesno('是否卸载 SRA？',
                f'是否卸载崩铁助手 SRA？\n\n'
                f'将删除 {plan.removable} 个可确认归属的未修改原版文件，约 {plan.bytes_reclaimable/1024**2:.1f} MiB。\n'
                f'保留 {plan.changed} 个修改文件、{plan.user_files} 个新增/用户文件，以及用户配置和共享运行环境。\n'
                f'安装缓存：{len(cache.files)} 个（{cache.total_bytes/1024**2:.1f} MiB），{"保留用于离线重装" if keep_cache else "一并清理"}。\n\n'
                '选择「是」执行安全卸载；选择「否」取消。', icon='warning', parent=self):
                return
            # Re-inspect immediately before changing any files.
            check = inspect_sra(root)
            if check.state != 'managed' or check.unsafe or check.removable == 0:
                raise ValueError('预览后安装状态发生变化，已取消卸载。')
            removed, kept = uninstall_managed_sra(root)
            cache_message = '已按选择保留安装包。'
            if not keep_cache:
                try:
                    cache_count, cache_bytes = clean_module_cache(root, 'sra')
                    cache_message = f'清理 {cache_count} 个 SRA 安装包缓存，释放 {cache_bytes/1024**2:.1f} MiB。'
                except (OSError, ValueError) as exc:
                    cache_message = f'注意：程序已卸载，但缓存清理失败：{exc}'
                    self._log('warning', cache_message)
            self._log('success', f'SRA 清理结束：删除 {removed} 个原版文件，保留 {kept} 个修改/不安全项。{cache_message}')
            if not module_executable(root, 'sra'):
                self.sra_path.set('')
                save_settings(self._read_settings())
            remaining = inspect_sra(root)
            messagebox.showinfo('SRA 安全卸载报告',
                f'已移除原版文件：{removed}\n保留记录内的修改/不安全项：{kept}\n'
                f'安装缓存：{cache_message}\n补充扫描：{remaining.message}\n\n'
                '个人配置和未登记文件默认保留，不能宣称完全无残留。')
            self._manage_scan(quiet=True)
        except (OSError, ValueError) as exc:
            messagebox.showerror('SRA 卸载失败', str(exc))
            self._log('error', 'SRA 卸载失败：' + str(exc))

    def _uninstall_ww(self):
        if self._busy_uninstall():
            return
        if sys.platform != 'win32':
            messagebox.showwarning('系统不支持', '官方卸载入口仅可在 Windows 上运行。')
            return
        try:
            root = self._validated_root()
            file = Path(self.ww_path.get().strip()) if self.ww_path.get().strip() and Path(self.ww_path.get().strip()).is_file() else detect_ww_exe(root)
            state = inspect_ww(file)
            possibilities = [record for record in state.records if record.uninstall_command]
            if not possibilities:
                messagebox.showinfo('未找到官方卸载器',
                    '未找到 OK-WW 的官方卸载命令。可以在 Windows「设置 → 应用 → 已安装的应用」中查找。\n'
                    'AutoFusion 不会通过直接删除目录或注册表来代替官方卸载。')
                return
            if len(possibilities) == 1:
                selected = possibilities[0]
            else:
                index = simpledialog.askinteger('选择安装登记',
                    '检测到多个 OK-WW 卸载记录，请选择需要卸载的编号：\n\n' +
                    '\n'.join(f'{i}. {item.name} {item.version}  {item.location}'
                              for i, item in enumerate(possibilities, 1)),
                    minvalue=1, maxvalue=len(possibilities), parent=self)
                if index is None:
                    return
                selected = possibilities[index-1]
            if not messagebox.askyesno('是否卸载 OK-WW？',
                f'是否卸载鸣潮助手 OK-WW？\n\n'
                f'名称：{selected.name}\n版本：{selected.version or "未知"}\n'
                f'安装目录：{selected.location or "未登记"}\n\n'
                '选择「是」打开官方卸载器；实际卸载过程由 OK-WW 官方程序完成。\n'
                '选择「否」取消，AutoFusion 不会直接删除程序目录或注册表。',
                icon='warning', parent=self):
                return
            # Raw string is passed to Windows CreateProcess, never through a shell.
            subprocess.Popen(selected.uninstall_command, shell=False,
                             creationflags=getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
            self._ww_uninstall_pending = True
            self._log('info', f'已打开 OK-WW 官方卸载器：{selected.name}；完成原版卸载后请点击重新扫描，成功后按缓存策略处理。')
            messagebox.showinfo('请完成官方卸载向导',
                '已尝试启动 OK-WW 官方卸载器，请在其原版窗口完成卸载。\n\n'
                '完成后返回本页面点击「重新扫描安装状态」；如果需要管理员权限或无法打开，请使用 Windows 已安装的应用。')
        except (OSError, ValueError) as exc:
            messagebox.showerror('无法启动官方卸载器', str(exc))
            self._log('error', '启动 OK-WW 官方卸载器失败：' + str(exc))

    def _store_cache_preference(self):
        # Save only this simple bool; an invalid game task field must not make the
        # cache preference silently disappear between launches.
        try:
            settings = load_settings()
            settings.keep_install_packages = self.keep_install_packages.get()
            save_settings(settings)
        except OSError as exc:
            self._log('warning', '无法保存安装包保留选项：' + str(exc))

    def _clean_orphan_caches(self):
        """Manual cleanup for caches left by older versions, only when uninstalled."""
        if self._busy_uninstall():
            return
        try:
            root = self._validated_root()
            sra = inspect_sra(root)
            wwpath = Path(self.ww_path.get().strip()) if self.ww_path.get().strip() and Path(self.ww_path.get().strip()).is_file() else detect_ww_exe(root)
            ww = inspect_ww(wwpath)
            absent = (["sra"] if sra.state == 'absent' else []) + (["ww"] if ww.state == 'absent' else [])
            plans = [cache_plan(root, m) for m in absent]
            if any(p.unsafe for p in plans):
                raise ValueError('缓存目录存在异常链接或路径，不执行自动清理。')
            targets = [p for p in plans if p.files]
            if not targets:
                messagebox.showinfo('没有可清理的缓存', '未发现已卸载模块的专属官方安装包缓存；正在使用的软件缓存不会清理。')
                return
            labels = '、'.join(('崩铁 SRA' if p.module == 'sra' else '鸣潮 OK-WW') for p in targets)
            size = sum(p.total_bytes for p in targets) / 1024**2
            if not messagebox.askyesno('清理已卸载模块缓存？',
                                      f'{labels} 当前未检测到安装。\n将清理 {sum(len(p.files) for p in targets)} 个已识别的模块专属安装包（约 {size:.1f} MiB）。\n未知文件不会删除。\n\n是否继续？'):
                return
            details = []
            for p in targets:
                if p.module == 'sra' and inspect_sra(root).state != 'absent':
                    continue
                if p.module == 'ww':
                    checkpath = Path(self.ww_path.get().strip()) if self.ww_path.get().strip() and Path(self.ww_path.get().strip()).is_file() else detect_ww_exe(root)
                    if inspect_ww(checkpath).state != 'absent':
                        continue
                n, bytes_removed = clean_module_cache(root, p.module)
                details.append(f'{p.module}: {n} 个，{bytes_removed/1024**2:.1f} MiB')
            self._log('success', '已清理卸载模块缓存：' + '；'.join(details))
            messagebox.showinfo('缓存清理报告', '已清理：\n' + '\n'.join(details))
        except (OSError, ValueError) as exc:
            messagebox.showerror('缓存清理失败', str(exc))

    def _offline_install_sra(self):
        if self._busy_uninstall():
            return
        try:
            root = self._validated_root()
            if (root/'sra').exists() or (root/'sra').is_symlink():
                messagebox.showinfo('已存在 SRA', 'SRA 目录已存在，不会覆盖。请先确认当前安装状态。')
                return
            archive = offline_package(root, 'sra')
            if not archive:
                messagebox.showinfo('没有可用缓存', '没有可用的官方 SRA ZIP 缓存，需要联网下载。')
                return
            if not messagebox.askyesno('使用本地缓存安装 SRA？',
                                      f'离线使用现有安装包：\n{archive}\n\n无需访问 GitHub，仍将校验 ZIP 路径与内容。是否安装？'):
                return
            extract_sra_safely(archive, root / 'sra',
                               lambda line: self._log('info', '[离线 SRA] ' + line))
            exe = module_executable(root, 'sra')
            if not exe:
                raise ValueError('离线解压后未发现有效 SRA CLI')
            self.sra_path.set(str(exe))
            save_settings(self._read_settings())
            self._manage_scan(quiet=True)
            self._log('success', 'SRA 已使用本地 ZIP 重新部署：' + str(exe))
            messagebox.showinfo('离线安装完成', 'SRA 已从本地缓存解压部署。打开原版界面确认可运行。')
        except (OSError, ValueError, RuntimeError) as exc:
            messagebox.showerror('离线安装失败', str(exc))

    def _open_cached_ww(self):
        try:
            root = self._validated_root()
            archive = offline_package(root, 'ww')
            if not archive:
                messagebox.showinfo('没有可用缓存', '未找到已缓存的 OK-WW 官方安装包。')
                return
            if not messagebox.askyesno('打开官方安装器？',
                                      f'准备离线打开 OK-WW 的原版安装器：\n{archive}\n\n是否继续？'):
                return
            if sys.platform == 'win32':
                os.startfile(archive)
                self._log('info', '使用本地缓存打开了 OK-WW 官方安装器：' + str(archive))
            else:
                messagebox.showinfo('仅限 Windows', '官方 EXE 安装器只能在 Windows 上启动。')
        except (OSError, ValueError) as exc:
            messagebox.showerror('无法打开官方安装包', str(exc))

    def _open_windows_apps(self):
        if sys.platform != 'win32':
            messagebox.showinfo('仅限 Windows', '此入口仅适用于 Windows。')
            return
        try:
            os.startfile('ms-settings:appsfeatures')
        except OSError as exc:
            messagebox.showerror('无法打开 Windows 设置', str(exc))

    def _browse(self, variable, module):
        value = filedialog.askopenfilename(title=f"请选择 {MODULE_INFO[module]['label']} 可执行程序", filetypes=[("Windows 程序", "*.exe"), ("全部文件", "*.*")])
        if value:
            variable.set(value)

    def _read_settings(self) -> Settings:
        try:
            number = int(self.ww_task.get().strip())
            timeout = int(self.timeout.get().strip())
        except ValueError:
            raise ValueError("任务序号和超时必须为整数") from None
        return Settings(
            keep_install_packages=self.keep_install_packages.get(),
            install_root=self.install_root.get().strip(), ww_exe=self.ww_path.get().strip(), sra_exe=self.sra_path.get().strip(),
            ww_task=number, sra_config=self.sra_config.get().strip(),
            enable_ww=self.ww_enabled.get(), enable_sra=self.sra_enabled.get(),
            dry_run=self.dry_run.get(), job_timeout_minutes=timeout
        )

    def _save(self):
        try:
            values = self._read_settings()
            path = save_settings(values)
            self._log("success", f"配置已保存：{path}")
        except (ValueError, OSError) as exc:
            messagebox.showerror("配置错误", str(exc))

    def _preview(self):
        try:
            import subprocess
            values = self._read_settings()
            lines = []
            for module in selected_modules(values):
                validate_module_parameters(module, values)
                filename = values.ww_exe if module == "ww" else values.sra_exe
                if not filename.strip():
                    lines.append(MODULE_INFO[module]["label"] + "\n尚未选择程序路径；模拟运行无需指定，真实执行前必须选择。")
                else:
                    args, _ = build_command(module, values, check_exe=False)
                    lines.append(MODULE_INFO[module]["label"]+"\n"+subprocess.list2cmdline(args))
            messagebox.showinfo("命令预览（不执行）", "\n\n".join(lines) if lines else "请先勾选模块")
        except ValueError as exc:
            messagebox.showerror("参数错误", str(exc))

    def _start(self):
        if self.runner.running:
            messagebox.showinfo("提示", "任务正在运行，请先停止")
            return
        try:
            values = self._read_settings()
            if not values.dry_run:
                ok = messagebox.askyesno(
                    "确认真实执行",
                    "这将启动所选第三方自动化软件，并可能操作游戏。\n\n"
                    "请事先确认对应账号风险及各软件配置。确定继续吗？",
                    icon="warning"
                )
                if not ok:
                    return
            save_settings(values)
            self.runner.start(values)
            self.status.set("正在模拟..." if values.dry_run else "正在执行...")
            self.run_button.configure(state="disabled")
        except (ValueError, RuntimeError, OSError) as exc:
            messagebox.showerror("无法运行", str(exc))
            self._log("error", str(exc))

    def _stop(self):
        if not self.runner.running:
            self._log("info", "当前没有运行中的任务。")
            return
        if not messagebox.askyesno("确认停止", "停止当前队列？如果是实际运行，可能会中断游戏中的任务。"):
            return
        self.runner.stop()
        self.status.set("正在停止...")

    def _queue_log(self, level: str, text: str):
        self.events.put((level, text))

    def _log(self, level: str, message: str):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        if hasattr(self, 'log_records'):
            self.log_records.append((level, line))
            trimmed = len(self.log_records) > 15000
            if trimmed:
                del self.log_records[:3000]
            levels = {'全部': None, '信息': {'info', 'start', 'stop', 'complete', 'command'},
                      '成功': {'success'}, '警告': {'warning'}, '错误': {'error'}}
            allowed = levels.get(self.log_filter.get())
            if trimmed:
                self._rerender_logs()
            elif allowed is None or level in allowed:
                self.log.configure(state='normal')
                self.log.insert('end', line + '\n', level)
                self.log.see('end')
                self.log.configure(state='disabled')
        try:
            append_session_log(data_dir(),
                f"[{datetime.datetime.now().isoformat(timespec='seconds')}] [{level}] {message}")
        except OSError:
            pass

    def _poll(self):
        while True:
            try:
                level, content = self.events.get_nowait()
            except queue.Empty:
                break
            if level == 'discovery_results':
                generation,repos,source=content
                if generation == self._discovery_generation and self._discovery_active:
                    self._discovery_results=repos
                    self._discovery_status=(f'找到 {len(repos)} 个仓库（实时结果）' if source=='live' else
                        f'本地缓存：{len(repos)} 个仓库（15 分钟内）' if source=='cached' else
                        f'网络/限额异常，展示过去 24 小时内的历史搜索：{len(repos)} 个；需要重新核实仓库状态')
                    self.status.set('GitHub 搜索已结束')
                    self._refresh_manage_cards()
                continue
            if level == 'discovery_error':
                generation,error=content
                if generation == self._discovery_generation and self._discovery_active:
                    self._discovery_results=[]
                    self._discovery_status='搜索失败：'+error
                    self.status.set('GitHub 搜索失败')
                    self._refresh_manage_cards()
                    self._log('warning','GitHub 在线搜索失败：'+error)
                continue
            if level == 'discovery_assets':
                repo,page,assets=content
                self._show_github_release_dialog(repo,page,assets)
                continue
            if level == 'discovery_assets_error':
                repo,error=content
                self._log('warning',f'{repo.full_name} 发布文件查询失败：{error}')
                if messagebox.askyesno('无法读取 Release',f'{error}\n\n需要直接打开 GitHub 仓库吗？',parent=self):
                    webbrowser.open(repo.url)
                continue
            if level == 'discovery_progress':
                name,done,total=content
                self.status.set(f'仅下载文件 {name}：{done/1024**2:.1f}/{total/1024**2:.1f} MiB')
                continue
            if level == 'discovery_downloaded':
                name,path,digest,matched=content
                self.status.set('文件已下载（未审核、未执行）')
                self._log('info',f'未经审核的 GitHub Release 文件已仅保存：{path} SHA256={digest}')
                messagebox.showinfo('附件已保存 · 未运行',
                    f'文件：{path}\nSHA-256：{digest}\n\n'+
                    ('与 GitHub 发布附件提供的摘要一致，但这不证明发布者可信。' if matched else 'GitHub 未提供附件摘要，只计算了本地哈希；来源身份未验证。')+
                    '\n\nAutoFusion 没有打开、安装或管理此文件。',parent=self)
                continue
            if level == 'discovery_download_error':
                self.status.set('附件下载失败')
                self._log('error','GitHub Release 下载失败：'+str(content))
                messagebox.showerror('下载失败',str(content),parent=self)
                continue
            if level == 'local_diag_result':
                generation, summary = content
                if generation == self._local_diag_generation:
                    self._local_diag_results.update(summary)
                    if getattr(self, '_current_page', None) == 'manage' and not \
                            self._manage_details_key and not self._discovery_active:
                        self._refresh_manage_cards()
                    else:
                        self._manage_dirty = True
                continue
            if level == 'status_scan_result':
                self._apply_status_scan(content)
                continue
            if level == 'status_scan_error':
                generation, error = content
                if generation == self._scan_generation:
                    self._log('warning', '安装状态扫描失败：' + error)
                continue
            if level == 'af_release':
                if content is None:
                    self._finish_update_ui()
                    self._log('info', f'AutoFusion V{APP_VERSION} 已是发布源上的最新版。')
                    messagebox.showinfo('已经是最新版本', f'当前 AutoFusion 版本：V{APP_VERSION}', parent=self)
                else:
                    version=content.version
                    text=f'发现 AutoFusion V{version}\n\n更新说明：\n{content.notes[:1000] or "功能和稳定性更新"}\n\n是否现在下载安装并自动重启？\n\n注意：仅替换 AutoFusion 程序文件，不会删除 fusion 模块和用户配置。'
                    if messagebox.askyesno('发现新版本',text,parent=self):
                        self._log('info',f'你已确认更新到 V{version}。正在临时下载和校验。')
                        self._download_self_update(content)
                    else:
                        self._finish_update_ui()
                continue
            if level == 'af_progress':
                self._log('info',content)
                continue
            if level == 'af_update_error':
                self._finish_update_ui()
                self._log('error','AutoFusion 更新失败：'+str(content))
                messagebox.showerror('无法更新 AutoFusion',str(content)+'\n\n旧版本保持不变。',parent=self)
                continue
            if level == 'af_update_ready':
                try:
                    start_update_worker(content)
                    self._log('info','新版已下载、哈希校验和安全解压；即将退出，由独立更新器替换并重启。')
                    self._finish_update_ui()
                    self.destroy()
                    return
                except Exception as exc:
                    self._finish_update_ui()
                    messagebox.showerror('无法启动更新器',str(exc),parent=self)
                continue
            if level == 'sevenzip_progress':
                n=int(content)
                if n // (256*1024) != getattr(self,'_sevenzip_last_step',-1):
                    self._sevenzip_last_step = n // (256*1024)
                    self.status.set(f'正在下载 7-Zip：{n / 1024**2:.1f} MiB')
                continue
            if level == 'sevenzip_error':
                self._log('error','7-Zip 下载失败：'+str(content))
                messagebox.showerror('7-Zip 下载失败',str(content),parent=self)
                continue
            if level == 'sevenzip_downloaded':
                file,h=content
                self._log('success','7-Zip 已完成官方包 SHA-256 校验：'+h)
                if messagebox.askyesno('打开 7-Zip 官方安装器？',
                        '已从官方 SourceForge 发布源获取并通过 SHA-256 校验。\n\n'
                        f'安装包：{file}\n\n'
                        '点击「是」才会打开官方安装向导执行安装/升级；点击「否」只保留已校验的缓存，不改动现有安装。',parent=self):
                    try:
                        if sys.platform!='win32': raise ValueError('此操作只支持 Windows')
                        os.startfile(file)
                        self._log('info','已请求启动 7-Zip 官方安装器：'+file)
                    except (OSError,ValueError) as e:
                        self._log('error','7-Zip 安装器启动失败：'+str(e))
                continue
            if level == 'sevenzip_done':
                self.status.set('7-Zip 下载阶段结束')
                self._schedule_status_scan(quiet=True)
                continue
            if level == 'install_log':
                sublevel, line = content
                self._log(sublevel, line)
                continue
            if level == 'install_result':
                module, kind, file, version, digest = content
                if module == 'sra':
                    self.sra_path.set(file)
                    self._log('success', f'SRA {version} 已自动接入！CLI：{file}')
                    try: save_settings(self._read_settings())
                    except (ValueError, OSError) as exc: self._log('error', '保存配置失败：' + str(exc))
                    self._log('warning', '初次执行任务前，请点击「打开原版界面」设置 SRA 的 Default 任务。可能需要安装 .NET 10 桌面运行时。')
                else:
                    self._log('success', f'OK-WW {version} 官方安装包已下载：{file}')
                    if messagebox.askyesno('启动 OK-WW 官方安装程序',
                            'OK-WW 安装包已下载。需要运行原作者提供的安装向导。\n\n'
                            '请在安装器中选择适合的目录；如果支持，建议使用当前整合目录下的 ok-ww 子文件夹。\n'
                            '安装结束后点击「自动识别现有安装」。若识别失败，可点击「浏览」指定 ok-ww.exe。\n\n'
                            f'准备启动：\n{file}\n\n确定吗？', icon='warning'):
                        try:
                            os.startfile(file) if sys.platform == 'win32' else None
                            self._log('info', '已启动 OK-WW 官方安装程序；完成向导后请点击「自动识别现有安装」。')
                        except OSError as exc:
                            self._log('error', '无法启动安装程序：' + str(exc))
                    else:
                        self._log('warning', '你没有启动 OK-WW 安装程序；安装包已保留，可稍后在下载目录中手动打开。')
                continue
            if level == 'install_done':
                self.all_install_button.configure(state='normal')
                for b in self.install_buttons:
                    if b.winfo_exists():
                        b.configure(state='normal')
                self.status.set('下载 / 安装阶段结束')
                self._schedule_status_scan(quiet=True)
                continue
            self._log(level, content)
            if level in {"error", "stop", "complete"}:
                self.status.set({"error": "任务失败 / 已停止", "stop": "已停止", "complete": "执行结束"}[level])
        if not self.runner.running:
            self.run_button.configure(state="normal")
            if self._close_when_finished:
                self.destroy()
                return
        self.after(125, self._poll)

    def _check_self_update(self):
        """Community uses manual releases; never replace source from an unsigned feed."""
        messagebox.showinfo("Community 更新", "请从经确认的 Community 发布渠道获取新版本。当前未配置自动更新源；不会自动下载或替换源码。", parent=self)

    def _download_self_update(self, release):
        """Runs on a worker thread; no running program files are touched."""
        last_percent=[-1]
        def worker():
            try:
                def progress(received,total):
                    if total:
                        pct=int(received*100/total)
                        if pct//10!=last_percent[0]//10:
                            last_percent[0]=pct
                            self.events.put(('af_progress',f'更新包下载中：{min(pct,100)}%'))
                workdir,plan=prepare_release(release,Path(__file__).resolve().parent,progress)
                self.events.put(('af_update_ready',plan))
            except Exception as exc:
                self.events.put(('af_update_error',str(exc)))
        threading.Thread(target=worker,daemon=True).start()

    def _finish_update_ui(self):
        self.update_busy=False
        try: self.update_button.configure(state='normal')
        except (tk.TclError,AttributeError): pass

    def _open_config(self):
        path = data_dir()
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            import os
            os.startfile(path)
        else:
            messagebox.showinfo("配置位置", str(path))

    def _on_close(self):
        if self.update_busy:
            messagebox.showinfo("更新准备中", "正在下载并验证新版程序。请等检查完成后再关闭，避免中断更新。")
            return
        if self.install_thread and self.install_thread.is_alive():
            messagebox.showinfo('下载安装中', '正在下载安装文件，请完成后再退出，避免留下不完整下载。')
            return
        if self.runner.running:
            if not messagebox.askyesno("任务运行中", "是否停止当前任务并退出？"): return
            self._close_when_finished = True
            self.runner.stop()
            self.status.set("正在停止并退出...")
        else:
            self.destroy()

    def after(self, ms, func=None, *args):
        if func is None:
            return super().after(ms)
        pending = self._pending_after_ids
        token = [None]
        def invoke():
            pending.discard(token[0])
            func(*args)
        token[0] = super().after(ms, invoke)
        pending.add(token[0])
        return token[0]

    def after_cancel(self, identifier):
        self._pending_after_ids.discard(identifier)
        return super().after_cancel(identifier)

    def destroy(self):
        for identifier in tuple(self._pending_after_ids):
            try:
                self.after_cancel(identifier)
            except tk.TclError:
                pass
        super().destroy()


if __name__ == "__main__":
    # On Windows, avoid blurry bitmap scaling of the entire Tk window.
    # Set before creating the Tk root; failure is harmless on older systems.
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except (AttributeError, OSError):
                pass
    if sys.platform != "win32":
        print("提示：程序主要针对 Windows 设计，当前环境仅用于开发或检查。", file=sys.stderr)
    app = App()
    # A health marker confirms successful UI construction after an in-place upgrade.
    if "--af-update-health" in sys.argv:
        try:
            at=sys.argv.index("--af-update-health")
            health=Path(sys.argv[at+1]).resolve()
            if health.parent.name.startswith("af-self-update-") and health.name == "health.json":
                health.write_text(json.dumps({"version": APP_VERSION, "ready": True}), encoding="utf-8")
        except (IndexError, OSError, ValueError):
            pass
    app.mainloop()
