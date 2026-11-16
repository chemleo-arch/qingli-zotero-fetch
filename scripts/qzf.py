#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qzf — Qingli Zotero Fetch：青梨AI文献传递 + Zotero 全文挂载 CLI

流程: scan(筛选非开源无PDF条目) → submit(提交DOI到青梨) → sync(轮询下载)
      → attach(重命名 PDF 并以 linked_file 挂回 Zotero 条目)

依赖: pip install requests playwright && python -m playwright install chromium

前置条件:
  1. Zotero 10+ 运行中，设置→高级 勾选"允许本机上的其他应用程序与 Zotero 通信"
  2. 首次写授权: Zotero 弹窗时点一次「始终允许」→ 持久 key 落盘 localAPIKeys.json，
     之后零弹窗。若你只点过「允许」(一次性 key)，每次批量写入会弹一次窗，
     建议去 Zotero 撤销后重新点「始终允许」(设置→高级→Clear Write Authorizations)
  3. 青梨网页版登录态: python qzf.py login --profile DIR 打开浏览器扫码一次
"""
import argparse
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path

import requests

API = "http://127.0.0.1:23119"
QINGLI_BASE = "https://qingli.newacademic.net"
APP_NAME = "WorkBuddy"


# ---------------------------------------------------------------- Zotero 授权

def find_keys_file(explicit=None):
    """定位 Zotero profile 下的 localAPIKeys.json（持久授权 key 存储）"""
    if explicit:
        p = Path(explicit)
        return p if p.exists() else None
    home = Path.home()
    cands = []
    appdata = home / "AppData" / "Roaming" / "Zotero" / "Zotero" / "Profiles"
    if appdata.exists():  # Windows
        cands += list(appdata.glob("*/localAPIKeys.json"))
    for pat in ("~/Zotero/*.default*/localAPIKeys.json",       # 自定义数据目录
                "~/.zotero/zotero/*/localAPIKeys.json"):       # Linux/macOS 默认
        cands += list(Path(pat).expanduser().glob("*"))
    cands = [c for c in cands if c.exists()]
    if not cands:
        return None
    return max(cands, key=lambda p: p.stat().st_mtime)


def persistent_key(keys_file):
    """读取 localAPIKeys.json 中最新的 remember:true key（用户点过「始终允许」）"""
    if not keys_file or not keys_file.exists():
        return None
    try:
        keys = json.loads(keys_file.read_text(encoding="utf-8")).get("keys", [])
    except Exception:
        return None
    mem = [k for k in keys if k.get("remember")]
    return mem[-1]["key"] if mem else None


def server_id():
    """每个 Zotero API 响应头都带 Zotero-Server-ID，写请求必须回传"""
    r = requests.get(f"{API}/api/users/0/collections", timeout=10)
    sid = r.headers.get("Zotero-Server-ID", "")
    if not sid:
        r2 = requests.post(f"{API}/api/local/authorize", json={"appName": APP_NAME},
                           headers={"Zotero-Server-ID": "probe"}, timeout=30)
        sid = r2.headers.get("Zotero-Server-ID", "")
    return sid


def authorize(sid, timeout=90):
    """请求一次性授权。Zotero 前台会弹窗: 允许 / 始终允许 / 拒绝。
    返回 (key, remember)；点「始终允许」则 remember=True，key 永久有效。"""
    print("  [authorize] 等待 Zotero 授权弹窗（请到 Zotero 窗口点『始终允许』）…", flush=True)
    try:
        r = requests.post(f"{API}/api/local/authorize", json={"appName": APP_NAME},
                          headers={"Zotero-Server-ID": sid}, timeout=timeout)
    except requests.exceptions.ReadTimeout:
        print("  [authorize] 超时——弹窗只在 Zotero 前台显示，请把 Zotero 窗口置前台", flush=True)
        return None, False
    try:
        j = r.json()
        return j.get("key"), bool(j.get("remember"))
    except Exception:
        print(f"  [authorize] 失败: {r.status_code} {r.text[:120]}", flush=True)
        return None, False


class ZoteroWriter:
    """带持久 key 与 401 自愈的写客户端"""

    def __init__(self, keys_file=None):
        self.sid = server_id()
        self.keys_file = find_keys_file(keys_file)
        if self.keys_file:
            print(f"[auth] localAPIKeys.json: {self.keys_file}", flush=True)
        else:
            print("[auth] 未找到 localAPIKeys.json（可能 Zotero <10 或未授权过）", flush=True)
        self.key = persistent_key(self.keys_file)
        if self.key:
            print(f"[auth] 使用持久 key {self.key[:8]}…（无需弹窗）", flush=True)

    def _headers(self, extra=None):
        h = {"Zotero-Server-ID": self.sid, "Zotero-API-Key": self.key or "",
             "Content-Type": "application/json"}
        if extra:
            h.update(extra)
        return h

    def _refresh(self):
        key, remember = authorize(self.sid)
        self.key = key
        if key and remember:
            print("  [auth] 已记住持久授权（remember=true），之后不再弹窗", flush=True)
            self.keys_file = find_keys_file()  # Zotero 已把新 key 落盘
            self.key = persistent_key(self.keys_file) or key
        return key

    def post_items(self, items, timeout=60):
        r = requests.post(f"{API}/api/users/0/items", json=items,
                          headers=self._headers(), timeout=timeout)
        if r.status_code == 401:  # key 失效/被撤销 → 重新授权并重试
            if self._refresh():
                r = requests.post(f"{API}/api/users/0/items", json=items,
                                  headers=self._headers(), timeout=timeout)
        return r

    def delete_item(self, key, version):
        """注意: 本地 API 的 DELETE 是彻底删除（不进回收站）"""
        h = self._headers({"If-Unmodified-Since-Version": str(version)})
        r = requests.delete(f"{API}/api/users/0/items/{key}", headers=h, timeout=30)
        if r.status_code == 401 and self._refresh():
            h = self._headers({"If-Unmodified-Since-Version": str(version)})
            r = requests.delete(f"{API}/api/users/0/items/{key}", headers=h, timeout=30)
        return r


# ---------------------------------------------------------------- Zotero 读取

def norm_doi(s):
    if not s:
        return ""
    m = re.search(r"10\.\d{4,9}/\S+", s, re.I)
    return m.group(0).strip().rstrip(".,;").lower() if m else ""


def load_all_items():
    r = requests.get(f"{API}/api/users/0/items", params={"format": "json", "limit": 400},
                     timeout=30)
    r.raise_for_status()
    return r.json()


def collection_items(coll):
    r = requests.get(f"{API}/api/users/0/collections/{coll}/items",
                     params={"format": "json", "limit": 300}, timeout=30)
    r.raise_for_status()
    return r.json()


def doi_from_filename(fn):
    m = re.match(r"(10\.\d{4,9})[_-](.+)\.(pdf|caj)", fn, re.I)
    if not m:
        return None
    return f"{m.group(1)}/{m.group(2).replace('_', '/').lower()}"


def zotero_style_name(creators, date, title):
    """复刻 Zotero 默认重命名惯例: 第一作者姓 等 - 年份 - 标题.pdf"""
    ln, n_authors = "", 0
    for c in creators or []:
        if c.get("creatorType") == "author":
            n_authors += 1
            if not ln:
                ln = c.get("lastName") or c.get("name") or ""
    ln = ln or "Unknown"
    author_part = ln if n_authors <= 1 else f"{ln} 等"
    m = re.search(r"\d{4}", date or "")
    year = m.group(0) if m else "nd"
    t = re.sub(r'[\\/:*?"<>|]', "-", (title or "").strip())
    t = re.sub(r"\s+", " ", t).strip().rstrip(".")
    return f"{author_part} - {year} - {t[:150]}.pdf"


# ---------------------------------------------------------------- 青梨

def qingli_capture(profile_dir):
    """无头浏览器截获青梨列表接口的 uc 令牌 / token 头 / cookies"""
    from playwright.sync_api import sync_playwright
    profile_dir = Path(profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(profile_dir), headless=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        cap = {}

        def on_request(req):
            if "new_get_user_demand" in req.url and "uc" not in cap:
                cap["uc"] = req.url
                cap["token"] = (req.headers or {}).get("token") or ""
                cap["ua"] = (req.headers or {}).get("user-agent") or "Mozilla/5.0"

        page.on("request", on_request)
        page.goto(QINGLI_BASE + "/#/search?webPage=2", wait_until="domcontentloaded",
                  timeout=60000)
        page.wait_for_timeout(10000)
        cap["cookies"] = {c["name"]: c["value"]
                          for c in ctx.cookies("https://xcx.newacademic.net")}
        if not cap.get("ua"):
            cap["ua"] = page.evaluate("navigator.userAgent") or "Mozilla/5.0"
        ctx.close()
    if not cap.get("uc"):
        raise RuntimeError("未截获青梨列表接口（登录态可能失效）——"
                           "运行 python qzf.py login --profile <DIR> 重新扫码")
    return cap


def qingli_login(profile_dir, timeout_sec=300):
    """有头浏览器打开青梨首页，等待微信扫码登录（登录态存 profile）"""
    from playwright.sync_api import sync_playwright
    profile_dir = Path(profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(profile_dir), headless=False,
                                                   args=["--start-maximized"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(QINGLI_BASE, wait_until="domcontentloaded", timeout=60000)
        print(f"请在弹出的浏览器中用微信扫码登录（{timeout_sec}s 超时）…", flush=True)
        deadline = time.time() + timeout_sec
        logged = False
        while time.time() < deadline:
            # localStorage 出现 code（uc 参数所用令牌）即视为登录成功
            code = page.evaluate("() => localStorage.getItem('code') || ''")
            if code:
                logged = True
                break
            page.wait_for_timeout(3000)
        ctx.close()
    print("✅ 登录态已保存" if logged else "登录超时，请重试", flush=True)
    return logged


def qingli_records(cap, days=30):
    """直接 POST 列表接口分页拉全量（页面默认只显示最近几天）"""
    out, seen, page_n = [], set(), 1
    end = dt.date.today()
    start = end - dt.timedelta(days=days - 1)
    while True:
        body = {"page": page_n, "size": 50, "sort_by": "time_desc", "con_type": ["all"],
                "status": [], "start_date": start.isoformat(), "end_date": end.isoformat(),
                "content": "", "read": 0, "source": 0}
        resp = requests.post(cap["uc"], json=body, timeout=60,
                             headers={"Content-Type": "application/json",
                                      "token": cap.get("token", ""),
                                      "User-Agent": cap.get("ua", "Mozilla/5.0"),
                                      "Referer": QINGLI_BASE + "/"},
                             cookies=cap.get("cookies", {}))
        j = resp.json()
        data = (j.get("data") or {}).get("data") or []
        total = (j.get("data") or {}).get("total") or 0
        for r in data:
            if r.get("id") not in seen:
                seen.add(r["id"])
                out.append(r)
        pc = (j.get("data") or {}).get("page_count") or 1
        print(f"  page {page_n}: +{len(data)} (累计 {len(out)}/{total})", flush=True)
        if page_n >= pc or not data:
            break
        page_n += 1
    return out


def doi_file_name(doi):
    return doi.strip().lower().replace("/", "_")


def cmd_submit(args):
    from playwright.sync_api import sync_playwright
    if args.file:
        lines = [ln.strip() for ln in Path(args.file).read_text(encoding="utf-8").splitlines()
                 if ln.strip()]
    else:
        lines = [x.strip() for x in args.dois.split(",") if x.strip()]
    lines = lines[args.offset:]
    if not lines:
        print("没有可提交的 DOI")
        return
    if len(lines) > args.limit:
        print(f"警告：剩余 {len(lines)} 条，本次只提交前 {args.limit} 条", flush=True)
        lines = lines[:args.limit]
    if len(lines) > 10:
        print("平台单次上限 10 条，请减小 --limit")
        return
    payload = "\n".join(lines)
    print(f"待提交 {len(lines)} 条: {lines}", flush=True)

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(Path(args.profile)), headless=not args.headed, args=["--start-maximized"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(QINGLI_BASE + "/#/home", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(8000)
        page.locator("textarea").first.fill(payload)
        print("已填入输入框", flush=True)
        page.wait_for_timeout(800)
        if args.dry:
            print("DRY — 只填入不提交", flush=True)
            ctx.close()
            return
        # 注意: 网页版无中间确认步骤，点「提交」即真实进入 AI 处理管线
        page.locator("button", has_text="提交").first.click(timeout=8000)
        print("已点击 提交（真实提交），等待 AI 处理…", flush=True)
        page.wait_for_timeout(9000)
        box_btn = page.locator(".el-message-box__btns button", has_text="确定")
        if box_btn.count():
            box_btn.first.click()
            print("已确认弹窗", flush=True)
        ctx.close()
    print("DONE — 运行 qzf.py sync 轮询进度并下载", flush=True)


def cmd_status(args):
    cap = qingli_capture(args.profile)
    recs = qingli_records(cap, args.days)
    print(f"共 {len(recs)} 条记录", flush=True)
    for r in recs:
        st = r.get("status")
        done = "✅完成" if st == 4 else "⏳处理中" if st else f"状态{st}"
        has_link = "有链接" if r.get("down_url") else "无链接"
        title = (r.get("result_info") or {}).get("title") or r.get("content")
        print(f"  [{done}|{has_link}] {r.get('create_date')} {str(title)[:80]}", flush=True)


def cmd_sync(args):
    cap = qingli_capture(args.profile)
    recs = qingli_records(cap, args.days)
    print(f"共 {len(recs)} 条记录", flush=True)
    man = json.loads(Path(args.manifest).read_text(encoding="utf-8")) \
        if Path(args.manifest).exists() else {}
    doi_map = man.setdefault("doi_map", {})
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    n_new = 0
    for r in recs:
        st = r.get("status")
        rid = r.get("id")
        doi = (r.get("content") or "").strip()
        if st == 4 and r.get("down_url"):
            for url in r["down_url"]:
                low = url.lower()
                ext = ".pdf" if ".pdf" in low else (".caj" if ".caj" in low else ".bin")
                if args.doi_names and doi.lower().startswith("10."):
                    dl = doi.lower()
                    mapped = doi_map.get(dl)
                    if mapped and (dest / mapped).exists():
                        continue
                    name = doi_file_name(doi) + ext
                    if (dest / name).exists():
                        doi_map.setdefault(dl, name)  # 顺手补 map
                        continue
                else:
                    title = (r.get("result_info") or {}).get("title") or r.get("content", "x")
                    slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "-",
                                  (title or "").replace("\n", " ")).strip("-")[:80]
                    name = slug or f"record_{rid}"
                    legacy = doi_file_name(doi) + ext
                    if doi.lower().startswith("10.") and (dest / legacy).exists():
                        doi_map.setdefault(doi.lower(), legacy)
                        continue
                key = f"{rid}_{Path(url).name}"
                if key in man and (dest / man[key]).exists():
                    continue
                try:
                    # 下载服务器（*.newacademic.net:7014）拦截无浏览器 UA 的请求（403, 153B HTML），
                    # 必须携带登录态浏览器同款 UA + Referer
                    resp = requests.get(url, timeout=180, headers={
                        "User-Agent": cap.get("ua", "Mozilla/5.0"),
                        "Referer": QINGLI_BASE + "/",
                    }, cookies=cap.get("cookies", {}))
                except Exception as e:
                    print(f"  FAIL 请求异常 {doi}: {e}", flush=True)
                    continue
                if resp.ok and len(resp.content) > 10000 and b"%PDF" in resp.content[:1024]:
                    (dest / name).write_bytes(resp.content)
                    man[key] = name
                    if doi.lower().startswith("10."):
                        doi_map[doi.lower()] = name
                    n_new += 1
                    print(f"  OK  {doi} -> {name} ({len(resp.content)//1024} KB)", flush=True)
                else:
                    print(f"  FAIL {doi} http={resp.status_code} "
                          f"size={len(resp.content)} head={resp.content[:8]!r}", flush=True)
        elif st == 4:
            print(f"  [完成但无链接] id={rid} {doi}", flush=True)
        else:
            print(f"  [处理中] id={rid} status={st} {doi}", flush=True)
    # 每条都即时落盘，中断不丢 map
    Path(args.manifest).write_text(json.dumps(man, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    print(f"本次新下载: {n_new} 个 -> {dest}", flush=True)


def cmd_scan(args):
    items = collection_items(args.collection)
    tops, pdf_parents = {}, set()
    for it in items:
        d = it["data"]
        if d.get("itemType") == "attachment" and d.get("parentItem"):
            ct = (d.get("contentType") or "").lower()
            if "pdf" in ct or (d.get("filename") or "").lower().endswith(".pdf"):
                pdf_parents.add(d["parentItem"])
        elif d.get("itemType") not in ("note", "annotation"):
            tops[d["key"]] = d
    print(f"分类 {args.collection} 顶级条目 {len(tops)}，其中已有 PDF 附件 {len(pdf_parents)}",
          flush=True)

    # pdf_dir 里已有文件（DOI 命名 + manifest 反查双通道）
    have_files = set()
    for f in Path(args.pdf_dir).glob("*.pdf"):
        doi = doi_from_filename(f.name)
        if doi:
            have_files.add(doi)
    man = json.loads(Path(args.manifest).read_text(encoding="utf-8")) \
        if Path(args.manifest).exists() else {}
    have_files |= set((man.get("doi_map") or {}).keys())

    prio = set()
    if args.priorities and Path(args.priorities).exists():
        prio = {ln.strip().lower() for ln in Path(args.priorities).read_text(
            encoding="utf-8").splitlines() if ln.strip()}

    records = []
    for key, d in tops.items():
        doi = norm_doi(d.get("DOI") or (d.get("extra") or ""))
        records.append({
            "zotero_key": key, "doi": doi,
            "title": (d.get("title") or "")[:120], "itemType": d.get("itemType"),
            "year": (d.get("date") or "")[:10],
            "has_pdf_att": key in pdf_parents,
            "has_pdf_file": doi in have_files if doi else False,
            "priority": doi in prio if doi else False,
        })

    need = [r for r in records if not r["has_pdf_att"] and not r["has_pdf_file"] and r["doi"]]
    no_doi = [r for r in records if not r["doi"]]
    print(f"\n无 PDF 且有 DOI: {len(need)}；缺 DOI: {len(no_doi)}", flush=True)

    print("查询 Unpaywall…", flush=True)
    for r in need:
        try:
            resp = requests.get(f"https://api.unpaywall.org/v2/{r['doi']}",
                                params={"email": args.email}, timeout=20)
            if resp.status_code == 200:
                j = resp.json()
                r["is_oa"] = bool(j.get("is_oa"))
                r["oa_status"] = j.get("oa_status") or ""
            else:
                r["is_oa"] = None
                r["oa_status"] = f"http_{resp.status_code}"
        except Exception as e:
            r["is_oa"] = None
            r["oa_status"] = f"err:{str(e)[:40]}"
        time.sleep(0.15)

    cand = [r for r in need if r["is_oa"] is False]
    cand.sort(key=lambda r: (not r["priority"], r["doi"]))
    ch = [r for r in cand if r["priority"]]
    print(f"\n=== 青梨候选（无 PDF 且非开源）: {len(cand)}（优先 {len(ch)}）===", flush=True)
    for r in cand:
        tag = "[优先]" if r["priority"] else "      "
        print(f"  {tag} {r['doi']:<44} {r['oa_status']:<10} {r['title'][:55]}", flush=True)

    Path(args.out).write_text(json.dumps(
        {"records": records, "candidates": cand}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"\n明细 -> {args.out}", flush=True)


def cmd_attach(args):
    scan = json.loads(Path(args.scan).read_text(encoding="utf-8"))
    pdf_dir = Path(args.pdf_dir)
    man = json.loads(Path(args.manifest).read_text(encoding="utf-8")) \
        if Path(args.manifest).exists() else {}
    doi_map = man.setdefault("doi_map", {})

    all_items = load_all_items()
    meta = {it["data"]["key"]: it["data"] for it in all_items}
    att_parents = set()
    for it in all_items:
        d = it["data"]
        if d.get("itemType") != "attachment" or not d.get("parentItem"):
            continue
        ct = (d.get("contentType") or "").lower()
        p = (d.get("path") or "")
        fn = (d.get("filename") or "")
        if "pdf" in ct or p.lower().endswith(".pdf") or fn.lower().endswith(".pdf"):
            att_parents.add(d["parentItem"])
            # Zotero 已有附件且指向 pdf_dir → 回填 doi_map（对账用户手动操作）
            real = p.replace("attachments:", "") if p.startswith("attachments:") else p
            try:
                if Path(real).resolve().parent == pdf_dir.resolve():
                    rec = next((x for x in scan["records"]
                                if x["zotero_key"] == d["parentItem"]), None)
                    if rec and rec.get("doi"):
                        doi_map.setdefault(rec["doi"], Path(real).name)
            except Exception:
                pass

    todo = []
    for f in sorted(pdf_dir.glob("*.pdf")):
        doi = doi_from_filename(f.name) or \
            next((k for k, v in doi_map.items() if v == f.name), "")
        if not doi:
            print(f"[跳过] {f.name} — 无法对应 DOI")
            continue
        rec = next((x for x in scan["records"] if x.get("doi") == doi), None)
        if not rec:
            print(f"[跳过] {f.name} — 分类内无条目 {doi}")
            continue
        if rec["zotero_key"] in att_parents:
            doi_map.setdefault(doi, f.name)
            continue
        todo.append((rec, f, doi))
    print(f"\n待挂载 {len(todo)} 个", flush=True)

    w = ZoteroWriter(args.keys_file)
    ok = fail = 0
    for rec, f, doi in todo:
        if args.limit and ok >= args.limit:
            break
        d = meta.get(rec["zotero_key"], {})
        if args.rename:
            new_name = zotero_style_name(d.get("creators"), d.get("date"), d.get("title"))
            target = pdf_dir / new_name
            i = 2
            while target.exists() and target != f:
                target = pdf_dir / new_name.replace(".pdf", f" ({i}).pdf")
                i += 1
            if target != f:
                f.rename(target)
                print(f"  重命名: {f.name} -> {target.name}", flush=True)
        else:
            target = f
        doi_map[doi] = target.name
        Path(args.manifest).write_text(json.dumps(man, ensure_ascii=False, indent=1),
                                       encoding="utf-8")

        if args.dry:
            print(f"  [dry] 将挂载 {target.name} -> {rec['zotero_key']}", flush=True)
            ok += 1
            continue
        item = {"itemType": "attachment", "linkMode": "linked_file",
                "title": target.name, "path": str(target),
                "contentType": "application/pdf", "parentItem": rec["zotero_key"],
                "tags": [], "collections": [], "version": 0}
        try:
            r2 = w.post_items([item])
        except requests.exceptions.ReadTimeout:
            print(f"  FAIL(超时) {target.name[:60]}", flush=True)
            fail += 1
            continue
        if r2.status_code == 200 and r2.json().get("success"):
            ok += 1
            print(f"  OK  {target.name[:70]} -> {rec['zotero_key']}", flush=True)
        elif r2.status_code == 429:
            print("  [429] 授权限流，冷却 46s", flush=True)
            time.sleep(46)
            r3 = w.post_items([item])
            if r3.status_code == 200 and r3.json().get("success"):
                ok += 1
                print(f"  OK(重试) {target.name[:70]}", flush=True)
            else:
                fail += 1
                print(f"  FAIL(重试): {r3.status_code} {r3.text[:150]}", flush=True)
        else:
            fail += 1
            print(f"  FAIL {target.name[:60]}: {r2.status_code} {r2.text[:200]}", flush=True)

    Path(args.manifest).write_text(json.dumps(man, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    print(f"\n完成: 成功 {ok}，失败 {fail}；doi_map 已更新", flush=True)


def cmd_auth_test(args):
    """验证写通道: 用持久 key 创建+删除一条临时 note（零弹窗即通过）"""
    w = ZoteroWriter(args.keys_file)
    note = {"itemType": "note", "note": "qzf auth test (auto-removed)",
            "tags": [], "collections": [], "version": 0}
    r = w.post_items([note])
    if r.status_code == 200 and r.json().get("success"):
        nk = list(r.json()["success"].values())[0]
        print(f"✅ POST 成功（key 复用，无弹窗）；清理测试条目 {nk[:8]}…", flush=True)
        g = requests.get(f"{API}/api/users/0/items/{nk}", timeout=15)
        ver = g.json().get("version", 0) if g.status_code == 200 else 0
        rd = w.delete_item(nk, ver)
        print(f"{'✅' if rd.status_code == 204 else '⚠️'} DELETE: {rd.status_code}"
              f"（本地 API 为彻底删除，不进回收站）", flush=True)
        print("auth-test 通过——Zotero 写通道就绪", flush=True)
    else:
        print(f"POST 失败: {r.status_code} {r.text[:200]}", flush=True)
        print("提示: 检查 Zotero 是否运行、是否勾选'允许本机上的其他应用程序通信'", flush=True)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, profile=True):
        if profile:
            p.add_argument("--profile", default=str(Path.home() / ".qzf" / "qingli-profile"),
                           help="青梨 Playwright 登录态目录")
        p.add_argument("--keys-file", default=None,
                       help="Zotero localAPIKeys.json 路径（默认自动发现）")

    p = sub.add_parser("login", help="有头浏览器打开青梨，微信扫码登录一次")
    common(p)
    p = sub.add_parser("scan", help="扫描 Zotero 分类，筛无 PDF 且非开源条目")
    p.add_argument("--collection", required=True, help="Zotero 分类 key")
    p.add_argument("--email", required=True, help="Unpaywall 邮箱")
    p.add_argument("--pdf-dir", required=True)
    p.add_argument("--manifest", default=str(Path.home() / ".qzf" / "manifest.json"))
    p.add_argument("--priorities", help="优先 DOI 清单文件（每行一个）")
    p.add_argument("--out", default=str(Path.home() / ".qzf" / "scan.json"))
    p = sub.add_parser("submit", help="提交 DOI 到青梨（单批 ≤10）")
    p.add_argument("--dois", default="", help="逗号分隔 DOI")
    p.add_argument("--file", help="每行一个 DOI 的文件")
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--headed", action="store_true")
    p.add_argument("--dry", action="store_true", help="只填入不点击提交")
    common(p)
    p = sub.add_parser("status", help="查询青梨记录状态")
    p.add_argument("--days", type=int, default=30, help="查询最近 N 天")
    common(p)
    p = sub.add_parser("sync", help="轮询下载已完成的文献")
    p.add_argument("--dest", required=True, help="PDF 下载目录")
    p.add_argument("--doi-names", action="store_true", help="按 DOI 命名文件")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--manifest", default=str(Path.home() / ".qzf" / "manifest.json"))
    common(p)
    p = sub.add_parser("attach", help="重命名 PDF 并挂 linked_file 到 Zotero 条目")
    p.add_argument("--scan", required=True, help="scan 子命令输出的 JSON")
    p.add_argument("--pdf-dir", required=True)
    p.add_argument("--manifest", default=str(Path.home() / ".qzf" / "manifest.json"))
    p.add_argument("--rename", action="store_true", help="按 Zotero 惯例重命名")
    p.add_argument("--limit", type=int, default=0, help="最多挂载 N 个（0=全部）")
    p.add_argument("--dry", action="store_true")
    common(p, profile=False)
    p = sub.add_parser("auth-test", help="验证 Zotero 写通道（持久 key 零弹窗）")
    common(p, profile=False)

    args = ap.parse_args()
    {"login": lambda a: qingli_login(a.profile),
     "submit": cmd_submit, "status": cmd_status, "sync": cmd_sync,
     "scan": cmd_scan, "attach": cmd_attach, "auth-test": cmd_auth_test}[args.cmd](args)


if __name__ == "__main__":
    main()
