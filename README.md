# qzf — 青梨AI文献批量下载 + Zotero 全文挂载

把「机构文献传递服务（青梨AI）」和「Zotero 文献库」连起来的命令行工具：

```
Zotero 分类 ──scan──► 无PDF且非开源清单 ──submit──► 青梨AI批量传递
                                                        │ (AI处理)
Zotero ◄──attach── 重命名后的 PDF ◄──sync── 下载完成记录
(linked_file 挂载)      (作者-年份-标题.pdf)
```

- **scan**：扫描 Zotero 分类，Unpaywall 判定 OA，筛出「无全文且非开源」条目（开源的直接抓 OA 直链，不占额度）
- **submit**：把 DOI 清单批量提交到青梨AI网页版（与微信小程序同账号，登录态持久化）
- **sync**：轮询青梨记录接口，自动下载完成的 PDF（校验文件头，防重复）
- **attach**：按 Zotero 惯例重命名 PDF（`第一作者 等 - 年份 - 标题.pdf`），以 linked_file 挂回对应条目——在 Zotero 里点击即打开全文

## 特性

- **零弹窗 Zotero 写入**：利用 Zotero 10 本地 API 的「始终允许」持久 key（`localAPIKeys.json`），点一次授权后永久免弹窗；401 自愈重新授权
- **登录态持久化**：青梨微信扫码一次，Playwright persistent profile 长期有效
- **对账能力**：人工手动挂载/重命名的文件会被自动识别并回填映射，不重复下载、不丢工作
- **中断安全**：映射清单逐条即时落盘，任何时刻中断都能续跑

## 安装

```bash
pip install -r scripts/requirements.txt
python -m playwright install chromium
```

前置条件：
1. **Zotero 10+** 运行中，设置 → 高级 → 勾选「允许本机上的其他应用程序与 Zotero 通信」
2. 机构已开通青梨AI服务（盈科千信文献传递，很多高校图书馆有）
3. 首次写授权时，在 Zotero 弹窗点「**始终允许**」（关键一步，之后零弹窗）

## 快速开始

```bash
QZF="python scripts/qzf.py"

# 0) 首次：微信扫码登录青梨网页版
$QZF login --profile ./qzf-state/profile

# 1) 扫描 Zotero 分类 → 生成下载候选清单
$QZF scan --collection WDV5JVQZ --email you@university.edu.cn \
    --pdf-dir ./参考文献/pdfs --manifest ./qzf-state/manifest.json

# 2) 提交 DOI（单批 ≤10 条；网页版点击即真实提交）
$QZF submit --file dois.txt --profile ./qzf-state/profile

# 3) 轮询下载（提交后随时可跑）
$QZF sync --dest ./参考文献/pdfs --doi-names \
    --manifest ./qzf-state/manifest.json --profile ./qzf-state/profile

# 4) 重命名 + 挂回 Zotero
$QZF attach --scan ./qzf-state/scan.json --pdf-dir ./参考文献/pdfs \
    --manifest ./qzf-state/manifest.json --rename

# 辅助
$QZF status     # 看青梨记录状态
$QZF auth-test  # 验证 Zotero 写通道（临时条目创建+删除）
```

## 平台限制与注意事项

| 事项 | 说明 |
|---|---|
| 单次提交 | ≤10 条（脚本强制） |
| 每日额度 | 约 50 篇（各机构配置不同） |
| 链接有效期 | 下载链接约 **1 天失效**，提交后当天 sync |
| 人工队列 | 状态「完成」但 403 = 平台人工补传，隔天重跑 sync |
| AI 处理耗时 | 几分钟到几十分钟不等 |
| Zotero DELETE | 本地 API 为**彻底删除**（不进回收站），谨慎使用 |
| 授权撤销 | Zotero 设置 → 高级 → Clear Write Authorizations |

## 隐私与安全

- `localAPIKeys.json`（Zotero 持久写授权）等同改库权限，本工具只读不外传；**切勿提交到公开仓库**
- 青梨登录态 profile 含会话 cookie，同样不要入库（见 `.gitignore`）
- 请遵守所在机构的文献传递服务条款，仅用于个人科研用途

## 适用环境

Windows / macOS / Linux；Python 3.10+；Zotero 10+（本地 API 写能力）。
已在 Windows 11 + Zotero 10.0.7 + 102 条真实文献库上闭环验证。

## License

MIT
