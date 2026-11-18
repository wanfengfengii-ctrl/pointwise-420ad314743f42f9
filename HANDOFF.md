# 样式成果交接说明（V2 增量）

本文件只说明在既有 V1 闭环上新增的样式采集能力、来源身份约定与发布边界。
业务口径以 `STYLES.md` 为准，运行清单见 `RUNTIME_GUIDE.md` 与
`.pointwise-runtime.json`。

## 新增公开入口

```python
from webcapture.capture.service import CaptureService, capture_styles
from webcapture.delivery.reading import read_capture
from webcapture.delivery.verification import verify_capture
from webcapture.delivery.html_export import export_capture

with ArchiveStore("archive.sqlite") as archive:
    capture_id = CaptureService(archive, http).capture_styles("https://example.test/start")
    # 等价写法：capture_styles(archive, http, "https://example.test/start")
    view = read_capture(archive, capture_id)
    verified = verify_capture(archive, capture_id)
    entry = export_capture(archive, capture_id, "new-offline-directory")
```

`export_capture` 依据固定清单识别 V1 或样式成果，两者都返回成果内相对 HTML
入口（V1 为 `index.html`，样式成果同为 `index.html`，并附带 CSS 与二进制文件）。

`read_capture` 与 `verify_capture` 仍不接收 HTTP 端口；`verify_capture` 对样式成果
返回 `verified.kind == "styles"`，`verified.documents` 给出按解释顺序排列的
`"html"` 与 `"css:<请求身份>"` 严格解码文本，`verified.responses` 给出请求身份到
原响应的只读映射。V1 成果仍返回 `kind == "page"`，`view.images`、
`view.manifest.references` 等原有字段不变。

## 请求身份与响应基址

* 请求身份是去 fragment 后的规范绝对 URL；路径、查询与百分号编码保持不变。
  fragment 不发送，但每次引用都单独保存并在导出时原样保留。
* 每条证据同时保存 `request_url`（请求身份）与 `final_url`（响应基址）。
  外部 CSS 以该 CSS 响应最终地址解析，内联 CSS 以 HTML 响应最终地址解析。
* 去 fragment 后相同的请求在**一次**样式采集中至多调用 HTTP 一次；不同请求
  即使重定向到同一最终 URL、正文相同，也各自保留独立证据。同一请求可同时
  承担 `html`、`css`、`binary` 多个角色，导出时按角色各写一份本地文件。

## 依赖闭包与角色

* HTML 的有效 `link[rel~=stylesheet]`、`style` 元素内容、唯一 `style` 属性，
  以及普通 `img[src]` 都按有限规则解释；CSS 中的真实 `url(...)` 与 `@import`
  按 `fixtures/lexical.json` 的 `css.subset` 识别。
* `css` 角色：`link`/`@import` 目标；`binary` 角色：`img` 与 `url(...)` 目标。
  请求被判定为新角色时一次性补足该角色的解释，因此循环导入既能收齐引用，
  也不会无限采集。`@import` 目标继续作为 CSS 解释，其余二进制目标从不解析正文。
* 伪引用不采集：注释、普通字符串、`script`/`style` 之外的伪标签都不产生引用，
  导出时也保持原样。

## 固定成果与发布边界

* 发布在单个 SQLite 事务内完成：证据、清单摘要与发布记录同事务提交。
  任一依赖失败即整体回滚，不产生半份成果，已发布旧成果保持可读可导出。
* 清单同时记录每个请求的证据与角色、被解释文档的顺序，以及每条引用的
  文档、字符区间、令牌、解码目标和最终请求身份。`verify_capture` 不信任这些
  记录本身：它从固定原字节重算整张依赖图，逐项比对请求顺序、角色、文档顺序、
  引用位置与目标。
* 原响应字节逐字节保存在 `evidence` 表中，导出不修改归档内任何正文。
  导出器按已核验快照工作，只改写派生副本。

## 离线交付

* 交付目录为平铺布局：`index.html`、`style-NNNN.css`、`media-NNNN.bin`。
  平铺可避免 `..` 段，也避免路径歧义。
* 改写按字符区间倒序进行，落在每个引用的**值令牌**上（`img src`/`link href`
  的值、`url(...)` 括号内的值、`@import` 的字符串或 url 值），不重写包围的
  `url(`/`)`，因此未加引号的 `url()` 仍不加引号、加引号的值仍加引号。
  HTML 属性值统一写成 HTML 转义的双引号值；`url()` 值按原引号形态写成
  未加引号（显式反斜杠转义）或 CSS 字符串；`@import` 始终写成 CSS 字符串。
  内联 `style` 属性中的值再叠加一层 HTML 转义。
* 每条支持的引用都不再指向网络；循环 CSS 也会生成闭合的本地链接。
  不支持的目标（如 `data:`）、伪引用与其余正文原样保留。
* 目标必须不存在（含同名文件与符号链接），排他创建；写入失败只清理本次
  创建的目标，不触碰相邻用户文件或旧成果。

## 核验与拒绝

* 关闭归档、停用 HTTP 后重开：`read_capture` 取得完整原响应；
  `verify_capture` 与 `export_capture` 全程零 HTTP 调用。
* 以下情况一律抛 `archive.schema.IntegrityError`，不访问网络补洞：
  正文摘要不符、证据行缺失、资源请求身份不符、清单摘要被改、清单引用悬空、
  请求顺序/角色/文档顺序与重算结果不一致。
* 来源形状、编码、依赖获取与发布失败抛 `CaptureError`；批次语义与 V1 一致。
