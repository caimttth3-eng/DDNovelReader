# -*- coding: utf-8 -*-
"""界面国际化：以中文文案为 key，按当前语言返回译文（未翻译或中文模式返回原文）。

用法：文件顶部 `from .i18n import T as _T`，把用户可见文案包 `_T("中文")`。
切换：设置界面「界面语言」写入 storage('ui_lang')，重启生效（tkinter 运行中整体换语言
风险高，重启最稳）。中文版行为零变化（T 直接返回原文）。
"""

_LANG = "zh"

_EN = {
    # ---- 主界面：标题栏 / 加载 ----
    "多多朗读": "DDNovelReader",
    "未打开书籍": "No book open",
    "正在加载书籍": "Loading book",
    "正在加载书籍{dots}": "Loading book{dots}",
    # ---- 工具栏第 1 行 ----
    "◀ 上一章": "◀ Prev",
    "下一章 ▶": "Next ▶",
    "书架": "Shelf",
    "目录": "TOC",
    "关于": "About",
    "📚 我的书架": "📚 My Shelf",
    # ---- 工具栏第 2 行：排版 ----
    "排版": "Layout",
    "不压缩": "No Compress",
    "合并为一行": "Merge Lines",
    "清理所有行": "Flatten All",
    "本地": "Local",
    "字体": "Font",
    "字号": "Size",
    "行距": "Spacing",
    "空行": "Blank",
    "书页": "Page",
    "白天": "Day",
    "护眼": "Eye",
    "夜间": "Night",
    "米黄": "Cream",
    "🔍 搜索": "🔍 Search",
    "搜索": "Search",
    # ---- 工具栏第 3 行：朗读 ----
    "朗读": "Read",
    "▶ 开始朗读": "▶ Read",
    "⏹ 结束": "⏹ Stop",
    "语速": "Speed",
    "停顿": "Pause",
    # ---- 工具栏第 4 行：音量/语音/缓存/定时 ----
    "音量": "Volume",
    "语音": "Voice",
    "整本缓存": "Cache All",
    "定时": "Timer",
    # ---- 书架区 ----
    "添加书籍": "Add Book",
    "打开书籍": "Open Book",
    "确认还原": "Confirm Restore",
    "＋ 添加书籍": "＋ Add Book",
    "备份书架": "Backup",
    "还原书架": "Restore",
    "复制书名": "Copy Title",
    "复制原文件": "Copy Source",
    "删除文件": "Delete File",
    "删除文件（清空缓存）": "Delete & Cache",
    "删除（保留缓存）": "Delete (keep cache)",
    "复制": "Copy",
    "确认选中的是小说文件？": "Confirm this is a novel file?",
    "确认导入": "Confirm Import",
    # ---- 阅读区右键菜单 ----
    "🔖 添加书签/划线": "🔖 Bookmark/Highlight",
    "从该段开始朗读": "Read From Here",
    "百度搜索": "Baidu",
    "谷歌搜索": "Google",
    "必应搜索": "Bing",
    "翻译": "Translate",
    # ---- 目录/书签面板 ----
    "书签": "Bookmarks",
    "（无书签）": "(no bookmarks)",
    "（暂无书签，选中文字后右键添加）": "(none - select text and right-click to add)",
    "删除该书签": "Delete Bookmark",
    "书签备注（默认取选中文字，可直接确认）：": "Note (defaults to selected text, press OK):",
    # ---- 关于 ----
    "界面语言": "Language",
    "界面语言已切换，重启软件后生效": "Language changed. Restart to apply.",
    "点击换主题": "Click to change theme",
    "支持 Windows / macOS / Linux / Android 的有声小说阅读器（多多朗读）  ·  当前版本 v{version}": "Audiobook reader for Windows / macOS / Linux / Android (DDNovelReader) · Version v{version}",
    "软件发布：": "Releases:",
    "作者联系方式：": "Contact:",
    "（点击复制）": "(click to copy)",
    "B站空间：": "Bilibili:",
    "（点击打开）": "(click to open)",
    "反馈问题可以在B站动态留言，B站我天天看。": "Leave feedback on Bilibili posts - I read it daily.",
    "抖音号：": "Douyin ID:",
    # 更新记录：tab 标题翻译，内容（版本历史）保持中文不译
    "更新记录": "Changelog",
    "快捷键说明": "Shortcuts",
    "缓存管理": "Cache Manager",
    "选择控件主题风格（点击即切换，即时生效）": "Choose control theme (click to switch, instant)",
    # ---- 通用按钮 ----
    "确定": "OK",
    "取消": "Cancel",
    "关闭": "Close",
    "开始": "Start",
    "停止": "Stop",
    "全选": "All",
    "反选": "Invert",
    "跳转": "Jump",
    "覆盖": "Overwrite",
    "清除": "Clear",
    "清空": "Clear",
    # ---- 提示框标题 ----
    "提示": "Info",
    "无法打开": "Cannot Open",
    "解析失败": "Parse Failed",
    "备份失败": "Backup Failed",
    "备份成功": "Backup Success",
    "还原失败": "Restore Failed",
    "还原成功": "Restore Success",
    "转移失败": "Move Failed",
    "转移完成": "Move Done",
    "已复制": "Copied",
    "已复制 ✓": "Copied ✓",
    "已清除": "Cleared",
    "已取消": "Cancelled",
    "失败": "Failed",
    "取消导入": "Cancel Import",
    "取消定时": "Cancel Timer",
    "拖拽导入失败": "Drop Import Failed",
    # ---- 下载管理器 ----
    "书名": "Title",
    "状态": "Status",
    "进度": "Progress",
    "缓存大小": "Cache Size",
    "时间": "Time",
    "大小": "Size",
    "全部开始/继续": "All Start",
    "全部暂停": "All Pause",
    "▶ 开始/继续": "▶ Start",
    "⏸ 暂停": "⏸ Pause",
    "▶ 继续": "▶ Resume",
    "▶ 开始缓存": "▶ Cache",
    "✔ 已完成": "✔ Done",
    "章节选择…": "Select Chaps…",
    "验证补全": "Verify",
    "删除音频缓存": "Delete Audio",
    "打开该书缓存…": "Open Cache…",
    "立即补全缺失": "Fill Missing",
    "并发下载线程：": "Concurrent threads:",
    "线程越多下载越快；超过 6 易被微软限流，失败重试反而更慢。": "More threads = faster, but above 6 Microsoft may throttle causing retries to slow down.",
    "整本缓存：每本书一条任务，可多本同时缓存；音频体积较大，请慎用。双击或点「章节选择」进入章节。": "One task per book; multiple books can cache simultaneously. Audio is large - use with care. Double-click or 'Select Chaps' to enter.",
    "续传：已缓存过的句子自动跳过，无需重复下载": "Resume: cached sentences are skipped automatically",
    "正在验证缓存完整性，请稍候…\n（百万字书需数秒）": "Verifying cache integrity, please wait…\n(million-word books take a few seconds)",
    "整本语音缓存验证结果（按当前分章 / 音色 / 语速重建任务列表，与磁盘逐句比对）": "Verification result (tasks rebuilt by current chapters / voice / rate and compared with disk)",
    "《%s》 ✔ 完整：%d 句全部存在，无缺失。": "«%s» ✔ Complete: all %d sentences exist.",
    "《%s》 共 %d 句，缺失 %d 句（分布在 %d 章）：%s": "«%s» %d sentences, %d missing (across %d chapters): %s",
    "《%s》 验证失败：%s": "«%s» verify failed: %s",
    "没有需要补全的任务": "Nothing to fill",
    "补全任务已启动（仅下载缺失句），可在下载管理器中查看进度。": "Fill started (missing sentences only); see Download Manager for progress.",
    "补全失败：%s": "Fill failed: %s",
    "缓存失败：%s": "Cache failed: %s",
    "请先在列表中选择至少一本书": "Select at least one book first",
    "请先选择要删除音频缓存的书": "Select a book to delete audio cache",
    "请先选择一本书": "Select a book first",
    "无法读取书籍文件：\n%s": "Cannot read book file:\n%s",
    "已选 {sel} 本 · 缓存中 {caching} 本 · 音频缓存总大小 {size}": "Selected {sel} · caching {caching} · audio cache {size}",
    "缓存已占容量：0 MB": "Cache used: 0 MB",
    "尚未开始": "Not started",
    "正在准备缓存任务…": "Preparing cache tasks…",
    "正在统计…": "Counting…",
    "正在解析并分章，请稍候…": "Parsing & splitting chapters, please wait…",
    "正在重新解析并分章，请稍候…": "Re-parsing & splitting chapters, please wait…",
    "重新预处理文本": "Reprocess Text",
    "选择要缓存的章节（Ctrl/Shift 可多选）：": "Select chapters to cache (Ctrl/Shift for multi-select):",
    "从本章起": "From Here",
    "缓存完成后自动关机（60 秒倒计时，可运行 shutdown /a 取消）": "Auto shutdown after cache (60s countdown, cancel with 'shutdown /a')",
    "整本缓存功能用于网络不稳定时提前缓存减少卡顿；缓存音频体积较大，请慎用。": "Pre-cache voice to reduce stutter on unstable networks. Audio cache is large - use with care.",
    "缓存管理": "Cache Manager",
    "正文解析缓存": "Text Cache",
    "音频缓存（整本语音）": "Audio Cache",
    "一键转移": "Move",
    "打开文件夹": "Open Folder",
    "清除缓存": "Clear Cache",
    "选择正文缓存新位置": "Choose text cache folder",
    "选择音频缓存新位置（建议放在非 C 盘）": "Choose audio cache folder (avoid C: drive)",
    "选择音频缓存文件夹（建议放在非 C 盘）": "Choose audio cache folder (avoid C: drive)",
    "选择正文解析缓存文件夹": "Choose text cache folder",
    "自定义位置": "Custom location",
    "全部覆盖": "Overwrite All",
    "全部重新预处理": "Reprocess All",
    "覆盖：沿用已有分章解析，立即完成；\n重新预处理：丢弃旧解析重新分章（长篇耗时较久）。": "Overwrite: reuse existing parse (instant);\nReprocess: drop old parse and re-split (slow for long books).",
    "全部覆盖：沿用已有分章解析，立即完成；\n全部重新预处理：丢弃旧解析重新分章（长篇耗时较久）。": "Overwrite All: reuse existing parse (instant);\nReprocess All: drop old parse and re-split (slow for long books).",
    # ---- 导入 ----
    "选择要加入书架的小说（可多选）": "Choose novel files to add (multi-select)",
    "选择备份文件": "Choose backup file",
    "输入关键词后点击搜索（搜索前 500 条结果）": "Type keywords and press Search (first 500 results)",
    "关键词：": "Keyword:",
    # ---- 定时 ----
    "设置定时时长（分钟）：": "Timer duration (minutes):",
    "（当前已有定时，重新设置将覆盖）": "(a timer is running - resetting will overwrite)",
    # ---- 其他 ----
    "确定": "OK",
    "正在加载书籍": "Loading book",
    "已添加书签/划线": "Bookmark added",
    "已跳转到书签位置": "Jumped to bookmark",
    "已删除书签": "Bookmark deleted",
    "已设定 {mins} 分钟定时，到点自动停止朗读": "Timer set for {mins} min, reading stops automatically",
    "定时时间到，已停止朗读（本次定时 {mins} 分钟）": "Timer ended, reading stopped ({mins} min)",
    "音频缓存目录已迁移 %d 个到新版（按书分目录）结构": "Audio cache migrated %d to new per-book structure",
}


def T(s):
    """按当前语言返回译文；中文模式或未收录 key 返回原文。"""
    if _LANG == "zh":
        return s
    return _EN.get(s, s)


def set_lang(lang):
    global _LANG
    _LANG = "zh" if lang not in ("en", "zh") else lang


def get_lang():
    return _LANG
