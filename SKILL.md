---
name: qingli-zotero-fetch
description: 用青梨AI文献传递平台（网页版，与微信小程序同账号）批量提交 DOI、自动轮询下载非开源付费文献 PDF，并重命名为 Zotero 惯例后以 linked_file 挂回 Zotero 条目；含 Zotero 分类扫描 + Unpaywall 开源判定，自动筛出"无全文且非开源"的条目，开源的直接走 Unpaywall 免费抓取。Zotero 写入用本地 API 持久 key（点一次「始终允许」后零弹窗）。触发词：青梨、文献传递、DOI 下载文献、下载非开源文献、Zotero 挂全文、Zotero 全文链接、文献补全、qzf。
agent_created: true
---

# 青梨AI 文献批量下载 + Zotero 全文挂载（qzf）

一句话流程：**Zotero 分类 → 扫描筛出无 PDF 且非开源的条目 → 青梨批量提交 → 轮询下载到文献文件夹 → 重命名并挂回 Zotero**。全部命令化，无人值守可跑。

本技能在真实项目（102 条综述分类、46 条提交、31 篇 PDF 到手并挂载）上闭环验证过。

## 前置条件（一次性）

1. **Zotero 10+** 运行中，且 设置 → 高级 勾选「允许本机上的其他应用程序与 Zotero 通信」
2. **青梨账号**：用户所在机构已开通青梨服务（盈科千信文献传递）。首次运行 `login` 子命令，弹出浏览器用微信扫码一次，登录态持久化，之后免扫码
3. Python 依赖：
   ```bash
   pip install -r scripts/requirements.txt
   python -m playwright install chromium
   ```

## 关键机制：Zotero 写授权（零弹窗）

Zotero 10 本地 API 写操作需要授权。**弹窗有三个选项：「允许」（一次性 key）/「始终允许」（持久 key）/「拒绝」**。

- 引导用户在**第一次弹窗时点「始终允许」**——持久 key 落盘 `localAPIKeys.json`（profile 目录），之后 qzf 自动读取，**不再弹任何窗**
- qzf 已实现 key 复用 + 401 自愈：持久 key 直接用；失效则自动重新 authorize 并提示用户
- 若用户此前只点过「允许」：设置 → 高级 → **Clear Write Authorizations** 撤销旧授权，重跑一次点「始终允许」
- 弹窗只在 Zotero **前台窗口**显示；批量写入前把 Zotero 窗口置前台（`Start-Process zotero.exe` 激活现有实例）
- Zotero 对弹窗授权限速：每分钟最多 5 次，超限 429 + Retry-After，脚本已处理冷却
- 写请求必须带 `Zotero-Server-ID`（读响应头回传）+ `Zotero-API-Key`；DELETE 带版本头
- **本地 API 的 DELETE 是彻底删除，不进回收站**——删除操作要谨慎

## 工作流（标准顺序）

```bash
S=~/appdata/roaming  # 示例；实际按用户项目路径传入
QZF="python <skill>/scripts/qzf.py"
DATA=<项目>/qzf-state          # manifest.json / scan.json 存放处
PDF=<项目>/参考文献/pdfs        # PDF 归档目录

# 0) 首次：扫码登录（有头浏览器）
$QZF login --profile "$DATA/profile"

# 1) 扫描 Zotero 分类 → 无 PDF 且非开源清单（优先 DOI 清单可选）
$QZF scan --collection <KEY> --email <unpaywall邮箱> \
    --pdf-dir "$PDF" --manifest "$DATA/manifest.json" \
    [--priorities "$DATA/priority_dois.txt"] --out "$DATA/scan.json"

# 2) 提交 DOI 到青梨（单批 ≤10；网页版点「提交」即真实提交，无二次确认！）
$QZF submit --file "$DATA/dois.txt" --profile "$DATA/profile"
#   分批: --offset 10 / --offset 20 …  演练: --dry

# 3) 轮询下载（可反复跑；PDF 校验 %PDF 头 + >10KB）
$QZF sync --dest "$PDF" --doi-names --manifest "$DATA/manifest.json" --profile "$DATA/profile"

# 4) 重命名（作者 等 - 年份 - 标题.pdf）并挂 linked_file 回 Zotero
$QZF attach --scan "$DATA/scan.json" --pdf-dir "$PDF" \
    --manifest "$DATA/manifest.json" --rename
```

- `scan` 只报候选不动库；`submit --dry` 只填不点；`attach --dry` 只重命名预演
- `status` 子命令可随时看青梨记录状态（完成/处理中/有无链接）
- `auth-test` 一条命令验证 Zotero 写通道（临时 note 创建+删除，通过即零弹窗）
- 青梨**下载链接约 1 天失效**——提交后当天务必 sync
- 单次提交 ≤10 条，每日额度约 50 篇（各机构配置不同）；AI 处理需几分钟到几十分钟
- 状态「完成」但链接 403 = 平台人工补传队列，隔天重跑 `sync` 通常能收到

## 工程要点（踩坑记录，agent 必读）

1. **青梨列表接口**：`POST https://xcx.newacademic.net/api/literature/new_get_user_demand/?uc=code_...`，`uc` 在 localStorage `code`；必须带 `token` 请求头（从页面请求截获）+ cookies，body 分页 `{page,size:50,start_date,end_date,...}`；页面 UI 只显示最近几天，API 传宽日期窗拉全量
2. **青梨网页版没有中间确认**：首页输入框点「提交」直接进 AI 管线，`--dry` 只拦在填入后
3. **资源同步去重**：manifest 的 `doi_map`（DOI→当前文件名）必须与磁盘文件名强一致；attach 重命名后**立即逐条落盘** manifest（中断不丢）；Zotero 里已有人工挂的附件会在 attach 时对账回填 doi_map
4. **文件命名**：`--doi-names` 用 `10.1016_j.xxx.pdf`（小写、`/`→`_`）；`attach --rename` 改为 Zotero 惯例「第一作者 等 - 年份 - 标题.pdf」；两套命名靠 doi_map 衔接
5. **重复下载防护**：sync 下载前查 doi_map + 磁盘同名 + manifest 键三重去重；若发现同内容两份文件，以 Zotero 附件引用路径为准，MD5 校验后清理副本
6. OA 判定用 **Unpaywall**（`is_oa=false` 才走青梨）；`is_oa=true` 的可直接抓 OA 直链省额度
