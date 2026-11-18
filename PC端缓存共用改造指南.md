# PC / 安卓 缓存共用改造指南（ck2 终版 · 已落地）

> 目标：同一本书在 PC 与安卓上算出**同一个目录键**，整本 TTS 音频缓存可以互相命中；
> 两边书架仍各自统计占用、各自清理，不共享任何"运行状态"。
>
> 本指南对应已经落地的实现（PC 端 `novelreader/`，安卓端 `duoduo_langdu/`），
> 不是设计方案。两端一致性由 `tests/parity_dump.py` + `test/cache_parity_test.dart`
> 逐项对拍验证，验收结果见第 6 节。

---

## 1. 为什么不能直接用 PC 的 `book_id`

PC 书架主键是

```
book_id = md5(normcase(abspath(file_path)))[:16]
```

输入是**本机绝对路径**。安卓没有 `C:\Users\...\xxx.txt` 这个路径，两台 PC 之间也不通，
所以它是"机器绑定键"，不是"内容键"，不能跨端，也不能作为缓存目录名。

安卓侧因此保留自己的 `book.id`（用于书架/进度），另外引入 **`content_key`** 作为
唯一的跨端缓存目录键。两端目录一律用 `content_key`。

---

## 2. 指纹契约 ck2（两端唯一口径，勿改）

```
raw = "ck2|" + norm(书名) + "|" + 章节数 + "|" + norm(全文正文)[:2000]
key = sha1(raw).hexdigest()[:16]
```

- **书名**：PC 取书架 `title`；安卓取导入时的书名。两端必须是同一个字符串
  （导入同一个文件名即可，见 7.1）。
- **章节数**：`len(chapters)`，由分章结果决定，所以分章必须两端一致（见 6.4）。
- **全文正文**：各章 `content` 首尾相接，**不含标题行**。
- **norm**：删除所有空白字符。
  - Python：`re.sub(r"\s+", "", s)`
  - Dart：`RegExp(r'[\s\u001c\u001d\u001e\u001f]+')`
    —— 必须补 `\u001c-\u001f`，Python 的 `\s` 认这四个字符、Dart 不认，
    不补会让某些书 norm 长度差 5、key 漂移。
- **样本截断按码点**（不是 UTF-16 code unit）：
  Python `len()` 是码点；Dart 用 `String.runes.take(limit)` 再 `String.fromCharCodes`。
  含 emoji 的书用 `String.length` 会差 1。

历史兼容：`ck1 = "ck1|" + norm(书名) + "|" + 含换行总字数 + "|" + norm(第0章正文)[:1000]`，
只用于匹配旧的 WebDAV 记录，新写入一律 ck2。

对应实现：
- PC：`novelreader/content_key.py`（`compute_content_key`、`content_key_of_book`、`content_key_of_chapters`）
- 安卓：`lib/services/content_key.dart`（`computeContentKey`、`bookFullText`）

---

## 3. 语速目录：用整数 pct，不用倍率浮点

音频文件按语速分目录。旧实现用 `1.00`、`1.50` 这种倍率字符串做目录名，
两端浮点打印格式不一致（`1.0` / `1.00`），也无法覆盖所有语速。

统一改为 **`pct = round((rate - 1) * 100)`，clamp 到 [-100, 200]**，目录名就是整数：

| rate | speechRate | 目录 |
|---|---|---|
| 50 | 0.25 | `-75` |
| 100 | 0.50 | `-50` |
| 200 | 1.00 | `0` |
| 250 | 1.25 | `25` |
| 300 | 1.50 | `50` |
| 400 | 2.00 | `100` |

PC：`content_key.py` 的 `rate_dir_key` / `rate_pct_of_speech_rate` / `rate_from_pct`
安卓：`tts_cache_store.dart` 的 `ratePctOfSpeechRate`

---

## 4. 目录结构与进度文件

```
<cache_root>/<content_key>/<pct>/<chapter_index>-<char_offset>.mp3
```

`char_offset` 是**章内原文偏移（码点口径）**，即该句在
`preprocess_for_tts(chapter.content)` 结果里的起始下标反查回原章的下标。
两端必须算出同一个偏移，否则文件名不同 → 全部 miss 且不报错。

进度文件 `progress.json` 在 `<content_key>/` 根下，两端兼容写法：

```json
{ "bookId": "<content_key>", "book_id": "<content_key>", "rate": "0", ... }
```

- 读：`bookId` 接受 `content_key` 或旧 `book.id`；只校验 voice，不校验别的。
- 写：同时写 `bookId` 与 `book_id`，都写 `content_key`；`rate` 写 pct 整数。

---

## 5. 旧目录懒迁移（不做全局重命名）

不启动时批量改名（用户可能有十几本、数 GB 缓存，改名期间被杀进程会留下半残目录）。
改为**按书懒迁移**：打开某本书时，如果 `<旧 book_id>/` 存在而 `<content_key>/` 不存在，
把前者 rename 过去；两者都存在则把旧目录里的文件合并进去再删旧目录；
完成后写 `.tts_layout_v2` 标记，之后不再检查。语速子目录的旧倍率名（`1.00`）也在同一步改名为 pct。

PC：`tts_engine.py` 的迁移；安卓：`tts_cache_store.dart` 的 `_migrateLegacyLayout` / `_migrateRateDirs`。

---

## 6. 验收（对拍工具链 + 实测结果）

工具链：

| 文件 | 作用 |
|---|---|
| `tests/parity_dump.py` | PC 侧解析 + 导出 + 逐项比对（走 `parse_book` 真路径，不做换行归一化） |
| `duoduo_langdu/test/cache_parity_test.dart` | 安卓侧解析并导出 JSON（`--dart-define=PARITY_SRC/PARITY_TITLE/PARITY_OUT/PARITY_CH`） |
| `tests/parity_one.ps1` | 单本对拍，结果写到 `build/parity_one.txt`（UTF-8） |
| `tests/run_parity_all.ps1` | 15 本书库批量，日志 `build/parity_all.log` |

比对项共 9 条：content_key、开头 2000 字样本、全文 norm 字数、章节总数、前 N 章标题、
句子数、句子坐标与文本逐项、逐章 norm 字数、全书标题逐项。

**实测（2026-11-18，用户 15 本书库）：14 本 9/9 全过，1 本 5 项不同（见 7.4）。**
安卓 `flutter test` 全套 51 项全过，`flutter analyze` 无 error。

排查过程中修掉的真实缺口（都已修，写在这里供后续回归参考）：

1. **正文极短且无句末标点的章被清空**：`_add_linebreaks` / `_addLinebreaks` 对"不含换行、
   无句末标点"的文本会把内容丢掉（PC 与安卓同款缺陷，但 PC 侧 `_absorb_inline_title`
   只 `strip("\n")`，安卓多做了一次 `_strip`，于是安卓多丢一章）。安卓已改为只 `strip("\n")`。
2. **装饰符号集合**：PC 用 `unicodedata.category ∈ {Sk, So, Pc}` 判颜文字，
   Dart 无该 API。已由 Python 全量枚举生成 `lib/services/unicode_symbols.dart`
   （212 段区间，二分查找），`°`、`╯╰` 这类符号两端判定一致。
3. **GBK / GB18030 解码**：安卓原来只有 UTF-8，GBK 书（15 本里 6 本）解析出来是乱码，
   norm 字数暴涨、章数塌成 1。已新增 `lib/services/text_decoder.dart`：
   BOM → 严格 UTF-8 → GB18030/Big5 → **按 PC 同一公式打分择优**（CJK占比×2 − 替换符×5 − 控制符×3，阈值 0.6）
   → 宽松 UTF-8 兜底。双字节映射表 `enc_tables.dart` 与四字节分段表 `gb4_table.dart`
   均由 Python codec 全量枚举生成、逐码位一致（含 `♫` 这类落在 GB18030 四字节区的符号）。
4. **分章逻辑必须整份移植**：安卓原来的三重检测与 PC 的
   `内层标题优先 / 数字标题候选重切 / 合并重切` 不等价，trxs.cc 类书会差几十章。
   现已整份复刻 `split_chapters` 全链路。
5. **行级装饰过滤要作用在原始章文本上**：把段落 `\n` 折成空格再清洗，会让
   PC 的整行丢弃规则失效（`------ / 正文卷 / ------` 三行被折成一行后只剩中间文字）。
   安卓改为以保留行结构的原文做章级清洗，段落块只负责显示归属（块内 `\n`→空格，等长，偏移不变）。

---

## 7. 已知边界（决定"能不能共用"）

1. **书名参与指纹**：同名文件才算同一本。PC 用文件名派生标题、安卓用导入名，
   两端导入同一个文件名即可一致；手动改名会造成 key 不同（设计取舍，非 bug）。
2. **PC 端人工重切过的书**：PC 有"分章异常诊断 + 人工重切"，人工结果安卓无法复现，
   这类书两端章数不同 → 不共用。判断特征：书名带 `_分章结果` 后缀。
   （本轮实测：带该后缀的 6 本经解码修复后已全部 9/9 全过，其源 txt 本身可自动切分，不属此列。）
3. **与 PC 对齐优先于"更聪明"**：安卓不再使用自己的更宽松规则。因此：
   - `第三十三章 大结局` 这种行，两端标题都截为 `大结局`；
   - `序章 引子` 两端标题都是 `引子`；
   - "短标题 + 分隔线" 不识别为章节，走 fallback 单节。
   这些是 PC 的既有行为，改动会让两边 key 分裂。
4. **唯一未对齐的一本**：`柯学：黄毛侦探(trxs.cc)`（`5ccaa0d73dc71a34`），
   PC 191 章 / 安卓 192 章，安卓多切 1 章（把一句含"第一回合"的正文行当成标题，
   标题即该正文句）。两端句子文本与偏移其余全同，仅章数差 1 导致 key 不同。
5. **GB18030 四字节**：已支持（`gb4_table.dart`，207 段线性映射）。若日后 Python 侧
   更换解码器，此表需重新生成。
6. **跨段合并句的高亮退化**：句子清洗后若跨段合并，安卓高亮只覆盖前半段（偏移正确、显示略短）。

---

## 8. PC 侧改造清单（已落地，供核对/复刻）

1. `content_key.py`：ck2 指纹、`chapters_text`、`sample_text_for_book`、`book_full_text`、
   `book_parts`、`content_key_of_book`、`content_key_of_chapters`、`rate_dir_key`、`rate_pct_of_speech_rate`。
2. `tts_engine.py`：缓存根目录改 `content_key`；语速目录改 pct；进度双键；懒迁移；按书大小统计按 content_key 返回。
3. `sync_engine.py`：`content_key()` 走 `content_key_of_book`；新增 `legacy_key(book)`；记录里多写 `content_key_version`、`legacy_key`。
4. `import_ui.py` / `reader_ui.py`：导入与打开书时用 `content_key_of_chapters` / `content_key_of_book`。
5. `tests/test_cache_layout.py`、`test_cache_layout_flow.py`、`test_gui_cache_wiring.py`：改为 ck2 断言与三参签名。

安卓侧对应文件：
`content_key.dart`、`text_decoder.dart`、`enc_tables.dart`、`gb4_table.dart`、`unicode_symbols.dart`、
`tts_text.dart`、`chapterizer.dart`、`book_parser.dart`、`tts_cache_store.dart`、
`models/book.dart`（`contentKey`）、`models/progress.dart`（`cacheDirName`）、
`screens/reader_screen.dart`、`screens/shelf_screen.dart`、`services/book_cache_task.dart`。

---

## 9. 怎么验证一本书能不能共用

```powershell
# PC 项目根
cd C:\Users\lyuga\Doubao\chats\2026-08-27\new-chat-1
pwsh -NoProfile -ExecutionPolicy Bypass -File .\tests\parity_one.ps1 `
  -Src "F:\DDNovelReader\cache\sources\<book_id>.txt" `
  -Title "<两端一致的书名>" -Ch 6
Get-Content .\build\parity_one.txt -Encoding utf8
```

看到 `==== 0 FAIL / 9 ====` 且两端 key 相同 → 该书缓存可跨端共用。

> 注意：`parity_one.json` 每次覆盖；`run_parity_all.ps1` 每本跑前会先删除
> `build/parity_cur.json`，否则 flutter test 失败时会拿旧 JSON 比出假结果（已修）。
