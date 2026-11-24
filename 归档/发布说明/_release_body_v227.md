# 多多朗读 v2.2.7

这一版是性能与包体：滚动停顿后的保存不再卡顿、书架缓存大小计算不再占用主线程、启动时不再为用不到的解析器付代价；同时把安装包瘦了一圈（Windows onedir 压缩包 43.05MB → 36.02MB，单文件版 42.09MB → 35.99MB）。

## 更新说明

- 阅读落盘不再卡顿：滚动停顿后的保存从「2 次全量重写 library.json + 整表重建书架」改为「1 次合并落盘 + 只更新本书那一行进度」，主线程实测 63.3ms → 5.9ms，一轮书架的写盘次数 58 → 6 次
- 书架缓存大小计算改为常驻单线程 + 队列回传：不再每次刷新都 join 等旧线程（原来主线程被吃掉 50~60ms），且只在大小真有变化时落盘一次；顺带修复了跨线程直接调 Tk 导致大小偶尔算不完的问题
- 启动导入链瘦身：python-docx / mobi 解析库、edge-tts、pyttsx3、pycaw、asyncio 改为用到才导入，启动导入耗时 1651ms → 313ms（源码口径），只读 txt 不再为这些重依赖付启动代价
- 给 library.json 加写锁，杜绝主线程与后台线程同时重写同一临时文件触发 120ms 重试等待
- 打包配置显式收集 mobi，避免惰性导入后漏收导致 mobi/azw3 书籍无法解析
- 安装包体积瘦身：裁掉程序用不到的图片编解码插件（AVIF / WebP / 色彩管理）与重复打包的 Tcl/Tk 脚本库，Windows onedir 目录 87.1MB → 73.8MB、onedir 压缩包 43.05MB → 36.02MB、单文件版 42.09MB → 35.99MB；同轮实测主窗口出现 1135ms → 1010ms、空闲内存 106.7MB → 87.5MB

## 下载与安装

**Windows（单文件版）**：`DDNovelReader_v2.2.7_Windows.exe`

1. 下载后双击即可运行（自解压式，启动稍慢但便携，无需安装）
2. 若系统提示「Windows 已保护你的电脑」，点击「更多信息」→「仍要运行」

**Windows（onedir 版，推荐低配 / 机械硬盘用户）**：`DDNovelReader_v2.2.7_Windows_onedir.zip`

1. 解压得到 `多多朗读` 文件夹
2. 进入文件夹，双击 `多多朗读.exe` 运行（运行库随包存放，启动秒开，不向临时目录解压）

**macOS**：`DDNovelReader-macOS_v2.2.7.dmg`

1. 双击挂载 → 把 `DDNovelReader.app` 拖入「应用程序」文件夹
2. 首次打开若提示「无法验证开发者」：右键 app → 打开 → 再次点击「打开」；或在 系统设置 → 隐私与安全性 中点「仍要打开」
3. 若提示「已损坏，无法打开」：打开终端执行 `sudo xattr -dr com.apple.quarantine /Applications/DDNovelReader.app` 后重试

**Linux**：`DDNovelReader_v2.2.7_Linux.zip`

1. 解压得到可执行文件后，在终端进入文件所在目录
2. 加执行权限：`chmod +x DDNovelReader`
3. 运行：`./DDNovelReader`
4. 本地语音朗读需安装 espeak：Ubuntu/Debian 执行 `sudo apt install espeak`，Fedora 执行 `sudo dnf install espeak-ng`（Edge 在线语音无需安装）

## 数据存放位置

- **Windows**：`%APPDATA%\DDNovelReader\`
- **macOS / Linux**：`~/DDNovelReader/`

主要数据：`library.json`（书架 / 阅读进度 / 设置）、`cache/`（书籍解析缓存与源文件备份）、`tts_cache/`（整本语音缓存，可在「关于 → 缓存管理」自定义位置）。
