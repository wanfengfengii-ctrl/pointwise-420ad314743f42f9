# 流域研究网页证据归档 V1

本项目供“流域研究资料中心”的资料员归档公告，研究员以原HTML、直接图片和请求来历核对资料，并离线查阅。仅使用 Python 3.11 标准库，无安装步骤、外部服务或隐藏网络访问。HTTP 由调用方注入；run.py 中 FixtureHttp 是记录实际请求顺序的有限响应源。

## 日常流程与数据

资料员提交有序 entry_id/URL 清单；全量校验后规范化外围空白、去除URL fragment，计算有序规范JSON的SHA-256批次身份。每条公告独立取得HTML，严格UTF-8解码，以响应最终地址解释真实 img[src]，取得全部直接图片，核对位置和资源后事务发布。资料员把固定成果关联回准确条目；全部成功才封存目录。研究员重开归档、复核原字节摘要，再导出HTML和本地图片，目录保持原来源顺序。

来源顺序、HTML出现顺序、首次图片请求顺序分别保留。规范JSON为无额外空白的有序二维数组，UTF-8，ensure_ascii=False；字段边界参与摘要。数据库同时保存规范字节以发现身份冲突。不同entry_id即使URL相同也分别采集。封存批次重收返回原关系，不请求来源；失败批次保存原因，重收抛出原失败，不自动续采。空清单是合法稳定批次。

响应证据包含独立证据ID、请求URL、响应最终URL、状态、有序头字段、原body和SHA-256。正文相同或最终URL相同均不合并不同请求。一次页面采集按去fragment的请求URL判重，其余路径、查询和百分号编码保留；图片请求等于已取得的HTML请求时复用响应，但仍输出独立图片角色文件。零字节成功图片是存在的证据。

V1只处理 img 的 src：单双引号、无引号值、字符引用、重复出现。重复src、空值、非HTTP(S)目标、URL空白、控制符及凭据URL拒绝。缺少src不产生引用。注释、文本、script/style中的伪标签不采集。不使用HTML base标签改变基址；直接图片始终按HTML响应最终URL解析。HTML标签及属性使用HTMLParser识别，并对受支持的属性词法进行明确校验；不保证修复畸形HTML。出现位置是解码文本中的字符区间，覆盖完整属性值令牌，包含原引号；不能当作UTF-8字节偏移。

不执行脚本，不采集CSS、stylesheet、iframe、srcset、字体或远程脚本，不保证浏览器视觉一致性。fixtures/lexical.json 公开有限词法材料；其中CSS事实不参与V1业务处理。V1 的这些限制保持不变；CSS 与样式资源由独立的样式成果入口处理，见下节。

## 样式成果（V2 增量）

在 V1 闭环上新增 `CaptureService.capture_styles(entry_url)` 及等价函数
`capture_styles(archive,http,entry_url)`，采集 HTML、直接图片和有限的样式依赖
（`link[rel~=stylesheet]`、`style` 元素、`style` 属性、CSS 的 `url(...)` 与
`@import`）。`export_capture` 自动识别 V1 或样式成果，二者都返回成果内相对
HTML 入口。`read_capture` 与 `verify_capture` 仍不接收 HTTP 端口。

请求身份（去 fragment 的规范绝对 URL）与响应基址（最终地址）分别保存；
一次样式采集里每个请求至多调用 HTTP 一次；同一请求的多重角色（HTML/CSS/二进制）
各写一份本地文件。依赖可循环且必须完整收齐，每次引用保留 fragment。原响应
字节不变，失败不发布半份成果，旧成果不受影响。停用来源重开后仍可核验与导出；
损坏或缺失证据抛原 `IntegrityError`。完整交接说明见 `HANDOFF.md`。

## 公开 Python API

从项目根目录运行，采用命名空间包，无需空 __init__.py。

```python
from webcapture.archive.schema import ArchiveStore
from webcapture.capture.service import CaptureService, capture_page
from webcapture.batches.collection import collect_batch
from webcapture.batches.catalog import BatchCatalog, render_delivery_catalog
from webcapture.delivery.reading import read_capture
from webcapture.delivery.verification import verify_capture
from webcapture.delivery.html_export import export_capture

# http.request(HttpRequest) -> HttpResponse，由调用方实现
with ArchiveStore("archive.sqlite") as archive:
    capture_id = CaptureService(archive, http).capture_page("https://example.test/notice")
    original = read_capture(archive, capture_id)
    verified = verify_capture(archive, capture_id)
    entry = export_capture(archive, capture_id, "new-offline-directory")
```

capture_page(archive,http,url) 等价于服务方法。collect_batch(archive,http,entries) 返回 BatchCollectionResult(batch_id,capture_ids)，后者按来源顺序排列。条目形状为仅含字符串 entry_id、url 的映射或 SourceEntry；输入不被修改。BatchCatalog.read_catalog(batch_id) 只读封存批次，返回含 ordinal、entry_id、url、capture_id 的不可变条目。导出各成果后，用 entry_id -> (capture_id,本地相对入口) 的完整映射调用 render_delivery_catalog；它核对成果身份并转义目录，返回可写入交付目录的HTML。

read_capture 返回固定清单及全部原响应的只读快照；verify_capture 返回含同一读取快照和已解码HTML的 VerifiedCapture，逐项重新计算原body摘要，核对请求身份、目标、出现位置及最终地址解释结果。两接口均不接收HTTP端口。export_capture消费同一已复核快照，以受控编号输出 index.html 和 image-NNNN.bin；按字符位置倒序替换真实src，保留各自fragment，不改原证据。远端basename不参与本地路径。文件名不承诺图片格式。

未知成果抛 KeyError；损坏、缺失证据或清单悬空抛 archive.schema.IntegrityError；请求、编码、图片解释和发布失败抛 CaptureError 并保留原因；来源形状、重复ID及批次冲突抛 ValueError。只接纳2xx且body为bytes的响应，不自动重试。批次第二条失败保留第一条独立成果及其关系，但不交付完整批次。

ArchiveStore只创建新库或打开兼容版本；重复initialize和重开不重灌样例、不清空数据、不修改用户表或正文，损坏不会被自动修复。不兼容版本或表列拒绝。证据、清单摘要和发布记录同事务提交，正常API不覆盖已发布正文。候选状态由created依次进入html_ready、images_pending、complete、published，失败进入rejected；批次为collecting、sealed或failed。

导出父目录必须存在，目标必须不存在，包括同名文件或符号链接；排他创建失败不获得清理权。写入失败清理本次拥有的目标，保留归档和邻近用户文件。export_capture可选writer(path,bytes)为同步文件写入端口；正常实现排他写出，端口须完整写入或抛异常。写入期间不对并发读者承诺原子可见；成功返回前入口及图片必须全部写成，不要求掉电恢复。

## 模块与回归

sources负责输入形状、HTTP身份与响应接纳；html负责UTF-8、字符位置令牌、CSS 有限词法与两类文档解释；capture拥有候选状态、图片/样式依赖图及完整性；archive负责SQLite、证据和固定发布（V1 与样式清单）；delivery负责快照、复核、路径与导出；batches负责规范清单、身份、成果关系及有序交付目录。34个生产模块均由公开流程调用。

```sh
python3.11 -B run.py verify
python3.11 -B run.py demo
python3.11 -B -m unittest discover -s tests -v
```

verify返回完整八个测试文件的真实退出码。demo读取fixtures/http.json，在测试临时目录实际采集两条公告，关闭SQLite、停用来源，再重开复核和导出，核对本地图片与目录顺序；随后按style_examples.json实际创建样式成果，同样关闭、停用来源、重开核验与导出，逐条按角色核对共享二进制、循环 CSS 与 fragment，输出两条POINTWISE_SCENARIO JSON；临时成果随后清理。它没有访问公网。

测试覆盖有序来源、规范化、空批次、身份顺序、受控身份冲突、重收、同URL不同条目、部分失败、重定向基址、最终地址别名、fragment、多次出现、零字节、无图片、根响应复用、候选状态、严格UTF-8、中文位置、伪引用、真实SQLite触发器事务失败、损坏、缺失正文、悬空关系、清单篡改、重复初始化、路径碰撞、精确改写、已有目标和部分写入清理。

测试覆盖有序来源、规范化、空批次、身份顺序、受控身份冲突、重收、同URL不同条目、部分失败、重定向基址、最终地址别名、fragment、多次出现、零字节、无图片、根响应复用、候选状态、严格UTF-8、中文位置、伪引用、真实SQLite触发器事务失败、损坏、缺失正文、悬空关系、清单篡改、重复初始化、路径碰撞、精确改写、已有目标和部分写入清理；样式侧另覆盖 CSS 有限词法正反例、手算请求图与导出、循环导入、重定向基址、最终地址别名、内联上下文与转义、伪引用、同一请求多角色、长链与失败保全。

所有样例和断言有限且确定；成果ID是归档内不透明身份，不承诺跨新归档一致。测试临时目录仅用于测试自身数据库和导出，原始样例文件不会被修改。
