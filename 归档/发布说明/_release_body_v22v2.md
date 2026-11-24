# 多多朗读 v2.2v2

新增繁体中文界面（第五种语言）。

## 更新说明

- 新增繁体中文界面（第五种语言）：台湾 / 香港 / 澳门系统自动检测启用（Windows 按区域子语言、macOS/Linux 按 locale 区分简繁），界面语言下拉新增「繁體中文」，原「中文」更名为「简体中文」

## 下载与安装

**Windows（单文件版）**：`DDNovelReader_v2.2v2_Windows.exe`

1. 下载后双击即可运行（自解压式，启动稍慢但便携，无需安装）
2. 若系统提示「Windows 已保护你的电脑」，点击「更多信息」→「仍要运行」

**Windows（onedir 版，推荐低配 / 机械硬盘用户）**：`DDNovelReader_v2.2v2_Windows_onedir.zip`

1. 解压得到 `多多朗读` 文件夹
2. 进入文件夹，双击 `多多朗读.exe` 运行（运行库随包存放，启动秒开，不向临时目录解压）

**macOS**：`DDNovelReader-macOS_v2.2v2.dmg`

1. 双击挂载 → 把 `DDNovelReader.app` 拖入「应用程序」文件夹
2. 首次打开若提示「无法验证开发者」：右键 app → 打开 → 再次点击「打开」；或在 系统设置 → 隐私与安全性 中点「仍要打开」
3. 若提示「已损坏，无法打开」：打开终端执行 `sudo xattr -dr com.apple.quarantine /Applications/DDNovelReader.app` 后重试

**Linux**：`DDNovelReader_v2.2v2_Linux.zip`

1. 解压得到可执行文件后，在终端进入文件所在目录
2. 加执行权限：`chmod +x DDNovelReader`
3. 运行：`./DDNovelReader`
4. 本地语音朗读需安装 espeak：Ubuntu/Debian 执行 `sudo apt install espeak`，Fedora 执行 `sudo dnf install espeak-ng`（Edge 在线语音无需安装）

## 数据存放位置

- **Windows**：`%APPDATA%\DDNovelReader\`
- **macOS / Linux**：`~/DDNovelReader/`

主要数据：`library.json`（书架 / 阅读进度 / 设置）、`cache/`（书籍解析缓存与源文件备份）、`tts_cache/`（整本语音缓存，可在「关于 → 缓存管理」自定义位置）。
