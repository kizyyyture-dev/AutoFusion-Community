# AutoFusion Community

Windows 本地工具统一管理平台的独立社区版本，基于 Personal 1.0.0 的已验证代码整理。已获所有者确认，采用 PolyForm Noncommercial 1.0.0 许可，公开仓库为 https://github.com/kizyyyture-dev/AutoFusion-Community 。

保留 Python/Tkinter App 与 Core 分离架构、已适配模块管理、默认模拟任务队列、离线安装包库、只读软件目录诊断和运行日志。界面只有操作台、模块管理、设置、运行日志四页。

## 运行与测试

Python 3.13（含 Tkinter），运行时无额外 pip 依赖：

    python app.py
    python run_build_tests.py

Community 配置使用独立的 AutoFusionCommunity 用户目录，模块默认放在其 Modules 子目录；离线库位于同级 OfflinePackages。不会自动迁移或读取 Personal 配置。测试只使用本项目 .test-tmp 内的隔离用户目录。

## Windows 构建

在自行准备的构建环境安装 requirements-build.txt 所列工具后执行：

    python scripts/build_windows.py --output dist

脚本不下载依赖，不上传文件；拒绝覆盖已有输出。构建失败见 build/build.log。当前尚未执行 Community EXE 构建或安全扫描。不要把本地测试结果当成正式发行验收。

## 功能边界

仅支持已适配模块；官方安装器项目遵循原版安装/卸载流程。导入离线库不会自动执行任意程序。源码版与 EXE 均不使用未经签名的源码替换自动更新。在线搜索和下载需要第三方来源，哈希不能证明发布者身份。请保持安全防护开启。

## 授权状态

采用 PolyForm Noncommercial 1.0.0 许可：允许非商业的查看、修改与分发，禁止商业用途。该定位属于源码可用，而非 OSI 定义的开源。完整条款见 LICENSE，政策说明见 LICENSE_POLICY.md；本项目不使用 MIT/Apache 授权。

第三方程序及运行时仍遵守各自许可证，见 THIRD_PARTY_NOTICES.md。Personal 的闭源许可与发布仓库不受此副本影响。

## 参与开发

阅读 CONTRIBUTING.md 与 ARCHITECTURE.md。Community 官方仓库为 https://github.com/kizyyyture-dev/AutoFusion-Community ；请勿将 Personal 仓库（闭源发行，全权保留）当作 Community 的源码仓库。

## 源码发布检查

    python scripts/release_preflight.py
    python scripts/release_preflight.py --require-license

第一条只读检查源码文件白名单和敏感内容；第二条额外要求正式 LICENSE，当前 LICENSE 已就位，两条均应通过。源码发布与二进制发行分开：本次只发布源码，未构建 EXE，不得声称已经完成 Community EXE 验收。
