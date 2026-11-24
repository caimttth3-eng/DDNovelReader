# 多多朗读 v2.2.5

书架支持鼠标框选多选与批量删除，系统托盘新增『打开歌词字幕』快捷入口。

## 更新说明

- 书架支持鼠标框选：按住左键上下拖动即可连续多选（跨越空白区域也能框选，拖到列表边缘自动滚动）；Ctrl / Shift 拖动在原选择上追加，Ctrl+A 全选书架
- 书架右键菜单支持批量删除：选中多本时删除菜单显示数量，确认弹窗列出待删书目前 5 本，一次移除全部选中书籍，并按选项清理缓存 / 源文件备份 / 音频缓存
- 双击书架条目改为按鼠标所在行打开，多选状态下不再固定打开第一本
- 系统托盘菜单新增『打开歌词字幕』：无需回到主窗口即可打开桌面歌词字幕窗，已打开时只置前不重复创建
- 新增界面文案五语言同步翻译（简体 / 繁体 / 英文 / 日文 / 韩文）

## 下载与安装

**Windows（单文件版）**：`DDNovelReader_v2.2.5_Windows.exe`

1. 下载后双击即可运行（自解压式，启动稍慢但便携，无需安装）
2. 若系统提示「Windows 已保护你的电脑」，点击「更多信息」→「仍要运行」

**Windows（onedir 版，推荐低配 / 机械硬盘用户）**：`DDNovelReader_v2.2.5_Windows_onedir.zip`

1. 解压得到 `多多朗读` 文件夹
2. 进入文件夹，双击 `多多朗读.exe` 运行（运行库随包存放，启动秒开，不向临时目录解压）

**macOS**：`DDNovelReader-macOS_v2.2.5.dmg`

1. 双击挂载 → 把 `DDNovelReader.app` 拖入「应用程序」文件夹
2. 首次打开若提示「无法验证开发者」：右键 app → 打开 → 再次点击「打开」；或在 系统设置 → 隐私与安全性 中点「仍要打开」
3. 若提示「已损坏，无法打开」：打开终端执行 `sudo xattr -dr com.apple.quarantine /Applications/DDNovelReader.app` 后重试

**Linux**：`DDNovelReader_v2.2.5_Linux.zip`

1. 解压得到可执行文件后，在终端进入文件所在目录
2. 加执行权限：`chmod +x DDNovelReader`
3. 运行：`./DDNovelReader`
4. 本地语音朗读需安装 espeak：Ubuntu/Debian 执行 `sudo apt install espeak`，Fedora 执行 `sudo dnf install espeak-ng`（Edge 在线语音无需安装）

## 数据存放位置

- **Windows**：`%APPDATA%\DDNovelReader\`
- **macOS / Linux**：`~/DDNovelReader/`

主要数据：`library.json`（书架 / 阅读进度 / 设置）、`cache/`（书籍解析缓存与源文件备份）、`tts_cache/`（整本语音缓存，可在「关于 → 缓存管理」自定义位置）。
