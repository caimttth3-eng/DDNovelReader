# 多多朗读 v2.2.3

新增更新提示与手动检查更新功能。

## 更新说明

- 版本号改为三段式（2.2.3）：为自动更新检查功能铺路，后续版本均按 主版本.次版本.修订版本 编号
- 新增更新提示：启动时后台检查 GitHub 最新版本，发现新版本时弹窗显示版本号与更新内容，提供『去下载 / 稍后 / 跳过此版本』三种选择，跳过某版本后不再提示；仅提示不自动下载安装，网络不可用时静默跳过不影响使用
- 关于窗口新增『检查最新版本』按钮：可随时手动检查更新，发现新版本即弹出更新提示窗（手动检查不受跳过记忆限制）；已是最新或网络不可用均有明确提示
- 更新提示弹窗『去下载』点击后打开浏览器下载页、窗口保持打开，可继续选择『稍后』或『跳过此版本』；重复检查时置顶已有弹窗，不重复弹出

## 下载与安装

**Windows（单文件版）**：`DDNovelReader_v2.2.3_Windows.exe`

1. 下载后双击即可运行（自解压式，启动稍慢但便携，无需安装）
2. 若系统提示「Windows 已保护你的电脑」，点击「更多信息」→「仍要运行」

**Windows（onedir 版，推荐低配 / 机械硬盘用户）**：`DDNovelReader_v2.2.3_Windows_onedir.zip`

1. 解压得到 `多多朗读` 文件夹
2. 进入文件夹，双击 `多多朗读.exe` 运行（运行库随包存放，启动秒开，不向临时目录解压）

**macOS**：`DDNovelReader-macOS_v2.2.3.dmg`

1. 双击挂载 → 把 `DDNovelReader.app` 拖入「应用程序」文件夹
2. 首次打开若提示「无法验证开发者」：右键 app → 打开 → 再次点击「打开」；或在 系统设置 → 隐私与安全性 中点「仍要打开」
3. 若提示「已损坏，无法打开」：打开终端执行 `sudo xattr -dr com.apple.quarantine /Applications/DDNovelReader.app` 后重试

**Linux**：`DDNovelReader_v2.2.3_Linux.zip`

1. 解压得到可执行文件后，在终端进入文件所在目录
2. 加执行权限：`chmod +x DDNovelReader`
3. 运行：`./DDNovelReader`
4. 本地语音朗读需安装 espeak：Ubuntu/Debian 执行 `sudo apt install espeak`，Fedora 执行 `sudo dnf install espeak-ng`（Edge 在线语音无需安装）

## 数据存放位置

- **Windows**：`%APPDATA%\DDNovelReader\`
- **macOS / Linux**：`~/DDNovelReader/`

主要数据：`library.json`（书架 / 阅读进度 / 设置）、`cache/`（书籍解析缓存与源文件备份）、`tts_cache/`（整本语音缓存，可在「关于 → 缓存管理」自定义位置）。
