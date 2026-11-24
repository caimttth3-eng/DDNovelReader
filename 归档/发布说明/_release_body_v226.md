# 多多朗读 v2.2.6

跳句与朗读控制链路整体重写：按键瞬间停音、高亮与阅读位置立即跟手，再从新位置开读；连续快按与点按都不再卡住，跨章处也不再空档。

## 更新说明

- 自动跨章不再卡顿：正文末尾的纯装饰分隔行不再被当成一句送去语音合成，跨章衔接处不再出现 1~2 秒空档
- 跳句与朗读控制链路整体重写：按键瞬间同步停音 → 高亮 / 滚动 / 阅读位置与进度条立即跟手 → 再从新位置开读，三层各管一段，互不依赖时序
- 连续快速按到章末会自动进入下一章首句（反向同理回到上一章末句），不再停在最后一句不动；只有全书首尾才真的没有下一句
- 修复点按跳句越跳越卡：每次跳句释放旧的预取合成线程，同一时刻只保留一个；朗读位置只前进，不会被上一句迟到的事件拽回
- 跳句目标与朗读引擎的句子边界完全对齐，跳到的就是接下来要读的那一句
- 修复章节末尾最后几句高亮不显示的问题（取词窗口越界导致上色失败）；快捷键说明改为「长按 ← / →：上一句 / 下一句（朗读中跳转）」

## 下载与安装

**Windows（单文件版）**：`DDNovelReader_v2.2.6_Windows.exe`

1. 下载后双击即可运行（自解压式，启动稍慢但便携，无需安装）
2. 若系统提示「Windows 已保护你的电脑」，点击「更多信息」→「仍要运行」

**Windows（onedir 版，推荐低配 / 机械硬盘用户）**：`DDNovelReader_v2.2.6_Windows_onedir.zip`

1. 解压得到 `多多朗读` 文件夹
2. 进入文件夹，双击 `多多朗读.exe` 运行（运行库随包存放，启动秒开，不向临时目录解压）

**macOS**：`DDNovelReader-macOS_v2.2.6.dmg`

1. 双击挂载 → 把 `DDNovelReader.app` 拖入「应用程序」文件夹
2. 首次打开若提示「无法验证开发者」：右键 app → 打开 → 再次点击「打开」；或在 系统设置 → 隐私与安全性 中点「仍要打开」
3. 若提示「已损坏，无法打开」：打开终端执行 `sudo xattr -dr com.apple.quarantine /Applications/DDNovelReader.app` 后重试

**Linux**：`DDNovelReader_v2.2.6_Linux.zip`

1. 解压得到可执行文件后，在终端进入文件所在目录
2. 加执行权限：`chmod +x DDNovelReader`
3. 运行：`./DDNovelReader`
4. 本地语音朗读需安装 espeak：Ubuntu/Debian 执行 `sudo apt install espeak`，Fedora 执行 `sudo dnf install espeak-ng`（Edge 在线语音无需安装）

## 数据存放位置

- **Windows**：`%APPDATA%\DDNovelReader\`
- **macOS / Linux**：`~/DDNovelReader/`

主要数据：`library.json`（书架 / 阅读进度 / 设置）、`cache/`（书籍解析缓存与源文件备份）、`tts_cache/`（整本语音缓存，可在「关于 → 缓存管理」自定义位置）。
