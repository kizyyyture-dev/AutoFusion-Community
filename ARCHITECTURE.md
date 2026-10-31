# 架构

- app.py：Tkinter 页面、交互与后台事件队列。
- core.py：设置、命令参数验证与任务编排，不依赖 Tkinter。
- install_manager.py / lifecycle.py：来源验证、安装与受管文件清单卸载。
- catalog_adapter.py / modules_catalog.json：模块元数据与适配。
- offline_library.py / cache_manager.py：离线副本与缓存边界。
- discovery.py / discovery_cache.py：第三方公开项目发现。
- directory_diagnostics.py：只读目录诊断。
- update_manager.py / updater_worker.py：保留回归覆盖的历史更新辅助代码，UI 不启用源码自动替换。
- tests/：离线回归，使用临时文件与模拟下载。
- scripts/build_windows.py：全新公开构建入口，不包含私人构建路径。

请保持 UI 与 Core 分离；新增模块应提供清晰来源与安全卸载边界。

- community_storage.py：独立配置中的本地根目录记录与便携安装摘要；只用于管理权限，不限制软件发现。
