# qzf — Qingli AI Literature Delivery + Zotero Full-Text Attachment

## English

**qzf** (Qingli Zotero Fetch) connects an institutional **document delivery service (Qingli AI)** with your **Zotero library**:

```
Zotero collection ──scan──► non-OA, no-PDF list ──submit──► Qingli AI batch delivery
                                                                │ (AI processing, minutes)
Zotero ◄──attach── Zotero-style renamed PDFs ◄──sync── completed deliveries
(linked_file attachment)   (Author et al. - Year - Title.pdf)
```

- **scan** — audit a Zotero collection, check Unpaywall OA status, and list papers that have **neither a PDF nor open access** (open-access ones are fetched directly, saving delivery quota)
- **submit** — batch-submit DOI lists to the Qingli AI web app (same account as the WeChat mini program; login persisted)
- **sync** — poll the Qingli record API and download completed PDFs (file-header validated, de-duplicated)
- **attach** — rename PDFs to the Zotero convention (`Author et al. - Year - Title.pdf`) and attach them to the corresponding items as **linked_file** — click in Zotero to open the full text

### Highlights

- **Zero-prompt Zotero writes**: uses Zotero 10's local API persistent key ("Always Allow" once → key stored in `localAPIKeys.json`, reused forever; automatic re-authorization on 401)
- **Persistent login**: WeChat QR scan once for Qingli; Playwright persistent profile keeps the session
- **Reconciliation**: files you attached/renamed manually are detected and folded into the DOI→filename map — no duplicate downloads, no lost work
- **Interrupt-safe**: the mapping manifest is written item-by-item; any interruption resumes cleanly

### Install

```bash
pip install -r scripts/requirements.txt
python -m playwright install chromium
```

Prerequisites:
1. **Zotero 10+** running, with *Settings → Advanced → "Allow other applications on this computer to communicate with Zotero"* checked
2. Your institution has Qingli AI service enabled (Yingke Qianxin literature delivery, common in Chinese university libraries)
3. On the **first** Zotero authorization dialog, click "**Always Allow**" — after that, writes are prompt-free forever

### Quick start

```bash
QZF="python scripts/qzf.py"

# 0) First time: WeChat QR login to Qingli web app
$QZF login --profile ./qzf-state/profile

# 1) Scan a Zotero collection -> candidate list
$QZF scan --collection <COLL_KEY> --email you@university.edu.cn \
    --pdf-dir ./references/pdfs --manifest ./qzf-state/manifest.json

# 2) Submit DOIs (≤10 per batch; the web app submits for real on click)
$QZF submit --file dois.txt --profile ./qzf-state/profile

# 3) Poll & download (run any time after submitting)
$QZF sync --dest ./references/pdfs --doi-names \
    --manifest ./qzf-state/manifest.json --profile ./qzf-state/profile

# 4) Rename + attach back to Zotero
$QZF attach --scan ./qzf-state/scan.json --pdf-dir ./references/pdfs \
    --manifest ./qzf-state/manifest.json --rename

# Helpers
$QZF status     # delivery record status
$QZF auth-test  # verify the Zotero write channel (temp note create+delete)
```

### Platform limits & cautions

| Item | Note |
|---|---|
| Per submission | ≤10 DOIs (enforced) |
| Daily quota | ~50 papers (institution-dependent) |
| Link validity | download links expire in **~1 day** — sync the same day |
| Manual queue | "completed" but 403 = platform manual re-upload; re-run sync next day |
| AI processing | minutes to tens of minutes |
| Zotero DELETE | local-API DELETE is **permanent** (no trash) — use carefully |
| Revoke auth | Zotero Settings → Advanced → Clear Write Authorizations |

### Privacy & security

- `localAPIKeys.json` grants full write access to your library; the tool only reads it locally and **must never be committed**
- The Qingli profile contains session cookies — also excluded via `.gitignore`
- Use within your institution's delivery-service terms, for personal research only

Windows / macOS / Linux; Python 3.10+; Zotero 10+. Verified end-to-end on Windows 11 + Zotero 10.0.7 with a 102-item real library.

## 中文

**qzf** 把机构**文献传递服务（青梨AI）**与 **Zotero 文献库**连成一条命令行流水线：

- **scan**：扫描 Zotero 分类，Unpaywall 判定 OA，筛出「无全文且非开源」条目（开源的直接抓 OA 直链，不占额度）
- **submit**：DOI 清单批量提交到青梨AI网页版（与微信小程序同账号，登录态持久化）
- **sync**：轮询青梨记录接口，自动下载完成的 PDF（校验文件头，防重复）
- **attach**：按 Zotero 惯例重命名 PDF（`第一作者 等 - 年份 - 标题.pdf`），以 linked_file 挂回对应条目——在 Zotero 里点击即打开全文

### 特性

- **零弹窗 Zotero 写入**：利用 Zotero 10 本地 API 的「始终允许」持久 key，点一次授权后永久免弹窗，401 自愈
- **登录态持久化**：青梨微信扫码一次，之后免扫码
- **对账能力**：人工手动挂载/重命名的文件会被自动识别并回填映射，不重复下载、不丢工作
- **中断安全**：映射清单逐条即时落盘，任何时刻中断都能续跑

### 安装与前置条件

```bash
pip install -r scripts/requirements.txt
python -m playwright install chromium
```

1. **Zotero 10+** 运行中，设置 → 高级 → 勾选「允许本机上的其他应用程序与 Zotero 通信」
2. 所在机构已开通青梨AI服务（盈科千信文献传递）
3. 首次写授权弹窗点「**始终允许**」（关键一步，之后零弹窗）

### 使用

```bash
QZF="python scripts/qzf.py"
$QZF login --profile ./qzf-state/profile                                  # 首次扫码
$QZF scan --collection <KEY> --email you@univ.edu.cn --pdf-dir ./pdfs     # 筛选
$QZF submit --file dois.txt --profile ./qzf-state/profile                 # 提交
$QZF sync --dest ./pdfs --doi-names --manifest ./qzf-state/manifest.json \
    --profile ./qzf-state/profile                                         # 下载
$QZF attach --scan ./qzf-state/scan.json --pdf-dir ./pdfs \
    --manifest ./qzf-state/manifest.json --rename                         # 挂载
```

### 平台限制与注意事项

| 事项 | 说明 |
|---|---|
| 单次提交 | ≤10 条（脚本强制） |
| 每日额度 | 约 50 篇（各机构配置不同） |
| 链接有效期 | 下载链接约 **1 天失效**，提交后当天 sync |
| 人工队列 | 状态「完成」但 403 = 平台人工补传，隔天重跑 sync |
| AI 处理耗时 | 几分钟到几十分钟不等 |
| Zotero DELETE | 本地 API 为**彻底删除**（不进回收站），谨慎使用 |
| 授权撤销 | Zotero 设置 → 高级 → Clear Write Authorizations |

### 隐私与安全

- `localAPIKeys.json` 等同 Zotero 改库权限，本工具仅本地读取，**切勿提交到仓库**
- 青梨登录态 profile 含会话 cookie，同样不入库（见 `.gitignore`）
- 请遵守所在机构的文献传递服务条款，仅用于个人科研用途

## License

MIT
