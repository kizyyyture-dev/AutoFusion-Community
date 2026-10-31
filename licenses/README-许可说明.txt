AutoFusion Personal 1.0.0（Windows x64）第三方组件与许可原文说明

本目录保存发行包 licenses\ 目录内随包分发的许可原文，以及本文件的组件对应表。
发行包内文件名与本目录一一对应；第三方许可核对.txt 中列出的组件必须有对应原文，缺少任一项时构建脚本会拒绝打包。

1) Python 3.13.12 运行时（python313.dll、base_library.zip、标准库、内置扩展）
   许可：Python Software Foundation License Version 2（PSF-2.0）
   原文：Python-LICENSE.txt
   来源：https://docs.python.org/3/license.html

2) Tcl/Tk 8.6（tcl86t.dll、tk86t.dll、_tcl_data、_tk_data）
   许可：Tcl/Tk License（BSD 风格，University of California/Sun/Scriptics 等版权声明）
   原文：Tcl-Tk-license.terms
   来源：https://www.tcl.tk/software/tcltk/license.html
   说明：Tcl 与 Tk 使用同一份 license.terms 文本；Python 安装目录内仅 tk8.6 附带原文，故此处以该原文同时覆盖 tcl86t.dll 与 tk86t.dll。

3) OpenSSL 3.0.18（libcrypto-3.dll、libssl-3.dll，同时被 _ssl.pyd / _hashlib.pyd 使用）
   许可：Apache License 2.0
   原文：OpenSSL-Apache-2.0.txt
   来源：https://www.openssl.org/source/license.html
   说明：OpenSSL 3.x 起统一采用 Apache-2.0；本文件为 Apache-2.0 完整正文（含 TERMS AND CONDITIONS 与 END OF TERMS AND CONDITIONS）。

4) zlib 1.3.1（zlib1.dll）
   许可：zlib License
   原文：zlib-LICENSE.txt
   来源：https://www.zlib.net/zlib_license.html

5) libffi 3.4.6（libffi-8.dll，供 _ctypes.pyd 使用）
   许可：libffi License（MIT 风格）
   原文：libffi-LICENSE.txt
   来源：https://github.com/libffi/libffi/blob/v3.4.6/LICENSE

6) PyInstaller 6.22.3（打包工具与其 bootloader，已编入 AutoFusionPersonal.exe）
   许可：GPL 2.0 及 bootloader 例外（允许打包专有程序）
   原文：PyInstaller-COPYING.txt
   来源：https://pyinstaller.org/en/stable/license.html

7) Microsoft Visual C++ 运行库与通用 C 运行库（VCRUNTIME140.dll、VCRUNTIME140_1.dll、ucrtbase.dll、api-ms-win-crt-*.dll）
   许可：Microsoft 可再发行组件条款（随 Visual Studio / Windows SDK 分发），不是开源许可证，因此没有开源原文可随包。
   说明：这些文件属于 Microsoft Visual C++ 可再发行组件，按 Microsoft 的再发行许可随本程序一起分发；本包未修改这些文件。
   参考：https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist

8) 其他随 Python 静态编入 python313.dll 的组件（如 expat、bzip2、lzma/xz、SQLite、libmpdec 等）
   说明：这些组件编入 CPython 官方 Windows 构建产物，其版权与许可声明随 CPython 源码树的 Doc/license.rst 发布；
   本发行包未单独修改或重新链接这些组件。若需逐条全文，可对照 CPython 3.13.12 源码树中的 Doc/license.rst。
   参考：https://docs.python.org/3/license.html

本目录只包含许可原文与对应关系说明，不包含任何 AutoFusion App/Core 源码、构建脚本或用户数据。
