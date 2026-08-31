# 多多朗读（DDNovelReader）

一款 Windows 本地运行的中文小说阅读器，支持 **TTS 语音朗读**、主流电子书格式、书架管理、
阅读进度自动保存与自动续读、整本语音缓存。

## 功能一览

| 功能 | 说明 |
| --- | --- |
| 主流格式 | txt / epub / mobi / azw3 / pdf / docx / html |
| TTS 语音朗读 | 默认本地语音（SAPI5，离线），可选 Edge 神经语音（晓晓/云希等 9 种中文音色，需联网）；开始/结束两个按钮；语速可调；逐句高亮跟随（高亮固定窗口首行）；Edge 断网自动回退本地语音 |
| 整本语音缓存 | 提前缓存整本书语音，网络不稳定时减少卡顿；支持按章节选择、暂停/继续、进度持久化（重启后从上次位置续传）、缓存完成后自动关机 |
| 字体设置 | 任意系统字体、字号（10–48）、行距（1.0–3.0）可调，实时生效 |
| 总进度 | 底部进度滑块 + 百分比 + 「第 X 章 / 共 Y 章」+ 总字数，实时更新；拖动滑块跳转；百分比可点击手动输入 |
| 自动保存 | 滚动、翻章、朗读、退出时自动保存阅读进度 |
| 自动续读 | 每次打开自动回到上次阅读位置，并自动打开最近阅读的书 |
| 书架 | Windows 详细信息视图（进度/书名/大小/时间，可排序带箭头）；支持多选；右键菜单（打开/复制原文件/复制书名/删除/删除文件）；书架与阅读区可拖拽调节宽度 |
| 章节切分 | 三重检测：严格匹配（第X章）/ 分隔线匹配 / 书名前缀匹配（如"书名 第1章"）；支持楔子/序章/番外等；第一章前文字归入"简介"章 |
| 阅读区右键 | 复制 / 百度搜索 / 谷歌搜索 / 必应搜索 / 翻译 / 从该段开始朗读 |
| 全屏模式 | F11 或 Alt+Enter 进入；右上角显示当前时间、阅读时间、章节名、总进度；自动隐藏书架 |
| 定时停止 | 可设置分钟数，到时自动停止朗读 |
| 空行压缩 | 三种模式：不压缩 / 合并为一行 / 清理所有行；段落首行缩进二字 |
| 缓存管理 | 正文缓存 + 音频缓存均可自定义文件夹、一键转移、一键清除、打开文件夹、实时统计大小 |
| 阅读主题 | 白天 / 护眼 / 夜间 三种配色 |

## 下载

在 [Releases](https://github.com/你的用户名/DDNovelReader/releases) 页面下载最新版 `多多朗读_vX.XX.exe`，双击即可运行，绿色免安装。

## 使用步骤

1. 打开程序 → 点击「添加书籍」选择小说文件（支持多选批量导入）
2. 加入书架后自动打开；书架双击书名切换书籍
3. 顶部工具条：上一章 / 下一章 / 目录 / 字体 / 字号 / 行距 / 主题 / 定时 / 缓存 / 关于
4. 「开始朗读」从当前位置朗读，朗读时当前句高亮
5. F11 进入全屏模式

## 数据存放位置

- 书架与阅读进度：`%APPDATA%\DDNovelReader\library.json`
- 书籍解析缓存：`%APPDATA%\DDNovelReader\cache\`
- 语音缓存：`%APPDATA%\DDNovelReader\tts_cache\`（可在"关于→缓存管理"中自定义位置）

## 常见问题

- **没有中文语音？** 在 Windows「设置 → 时间和语言 → 语音」中添加中文语音包后重启程序。
- **Edge 语音无声？** 确认网络正常；Edge 语音需联网，断网时自动回退本地语音。
- **朗读卡顿？** 可使用"整本语音缓存"提前缓存，或切换到本地语音。
- **分章不正确？** v1.95 已重构分章系统，支持"书名 第N章"格式；如仍有问题可删除书籍重新导入。

## 开发说明

### 环境要求
- Python 3.10+
- Windows 10/11

### 安装依赖
```bash
pip install -r requirements.txt
```

### 运行
```bash
python -m novelreader.main
```

### 打包 exe
```bash
pyinstaller --noconfirm 多多朗读.spec
```
产物在 `dist\多多朗读.exe`。

### 运行测试
```bash
python tests\test_core.py
python tests\test_gui.py
python tests\test_features.py
```

## 目录结构

```
novelreader/            # 源码包
  __init__.py           # 版本号
  main.py               # 入口
  gui.py                # 主界面
  book_loader.py        # 各格式解析
  chapterizer.py        # 章节切分（三重检测）
  storage.py            # 书架/进度/设置/缓存
  tts_engine.py         # 语音朗读引擎（本地+Edge+整本缓存）
  version_info.py       # 版本历史与快捷键说明
tests/                  # 自动化测试
assets/                 # 资源文件（图标等）
sample/                 # 测试样书
requirements.txt        # Python 依赖
多多朗读.spec            # PyInstaller 打包配置
```

## 技术栈

- 界面：Python 标准库 tkinter（Tcl/Tk 8.6）
- 朗读：pyttsx3（Windows SAPI5）+ edge-tts（微软 Edge 神经语音）
- 解析：ebooklib（epub）、mobi（mobi/azw3）、pdfplumber（pdf）、python-docx（docx）
- 打包：PyInstaller onefile（内置 Tcl/Tk 与全部依赖，绿色免安装）

## 作者

- 邮箱：230468896@qq.com

## 许可证

MIT License

## v1.96 更新（2026-09-01）

- 修复启动慢与硬盘持续被读取：书架「大小」列改为持久化索引（library.json），启动/刷新零扫描
- 音频播放器改为 Windows 原生 MCI，移除 pygame 依赖
- 安装包瘦身：49.6MB → 35.5MB（UPX 压缩 + 排除无用模块）
