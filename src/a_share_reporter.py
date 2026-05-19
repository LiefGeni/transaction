from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import time
import http.client
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports"
CACHE_DIR = ROOT / "cache"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# Some desktop environments set this to C:\nss_ssl_sfagent.log. The sandbox cannot
# write there, and Python's SSL layer fails before it even opens the HTTPS request.
os.environ.pop("SSLKEYLOGFILE", None)


@dataclass
class ReportConfig:
    title_prefix: str = "A股日报"
    top_n_candidates: int = 5
    top_n_sectors: int = 8
    top_n_risks: int = 5
    min_price: float = 3
    max_price: float = 120
    min_turnover_rate: float = 1
    max_turnover_rate: float = 25
    min_market_cap_cny: float = 3_000_000_000
    exclude_name_keywords: list[str] = field(default_factory=lambda: ["ST", "退", "N", "C"])


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_config() -> ReportConfig:
    cfg = ReportConfig()
    # Keep config dependency-free. If PyYAML exists, read the YAML; otherwise defaults still work.
    try:
        import yaml  # type: ignore

        path = ROOT / "config.yaml"
        if not path.exists():
            path = ROOT / "config.example.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        report = data.get("report", {})
        filters = data.get("filters", {})
        cfg.title_prefix = report.get("title_prefix", cfg.title_prefix)
        cfg.top_n_candidates = int(report.get("top_n_candidates", cfg.top_n_candidates))
        cfg.top_n_sectors = int(report.get("top_n_sectors", cfg.top_n_sectors))
        cfg.top_n_risks = int(report.get("top_n_risks", cfg.top_n_risks))
        cfg.min_price = float(filters.get("min_price", cfg.min_price))
        cfg.max_price = float(filters.get("max_price", cfg.max_price))
        cfg.min_turnover_rate = float(filters.get("min_turnover_rate", cfg.min_turnover_rate))
        cfg.max_turnover_rate = float(filters.get("max_turnover_rate", cfg.max_turnover_rate))
        cfg.min_market_cap_cny = float(filters.get("min_market_cap_cny", cfg.min_market_cap_cny))
        cfg.exclude_name_keywords = list(filters.get("exclude_name_keywords", cfg.exclude_name_keywords))
    except Exception:
        pass
    return cfg


def today_yyyymmdd() -> str:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y%m%d")


def previous_calendar_days(date_str: str, days: int = 10) -> list[str]:
    base = dt.datetime.strptime(date_str, "%Y%m%d").date()
    return [(base - dt.timedelta(days=i)).strftime("%Y%m%d") for i in range(days)]


def http_json(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"})
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                text = resp.read().decode("utf-8", errors="ignore")
            return json.loads(text)
        except (TimeoutError, OSError, http.client.HTTPException) as exc:
            last_error = exc
            time.sleep(0.3 * (attempt + 1))
    if last_error:
        raise last_error
    text = "{}"
    return json.loads(text)


def num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, "-", ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def percentile_scores(rows: list[dict[str, Any]], key: str, inverse: bool = False) -> dict[int, float]:
    values = [(idx, num(row.get(key))) for idx, row in enumerate(rows)]
    if inverse:
        values = [(idx, -value) for idx, value in values]
    values.sort(key=lambda item: item[1])
    if len(values) <= 1:
        return {idx: 0.0 for idx, _ in values}
    return {idx: rank / (len(values) - 1) for rank, (idx, _) in enumerate(values)}


def eastmoney_clist(fs: str, fields: str, fid: str = "f3", pz: int = 5000) -> list[dict[str, Any]]:
    cache_key = hashlib.sha1(f"{fs}|{fields}|{fid}|{pz}".encode("utf-8")).hexdigest()
    cache_path = CACHE_DIR / f"clist-{cache_key}.json"
    params = {
        "pn": 1,
        "pz": pz,
        "po": 1,
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": fid,
        "fs": fs,
        "fields": fields,
        "_": int(time.time() * 1000),
    }
    hosts = [
        "https://82.push2.eastmoney.com",
        "https://push2.eastmoney.com",
        "http://push2.eastmoney.com",
    ]
    for host in hosts:
        try:
            payload = http_json(f"{host}/api/qt/clist/get", params)
            rows = (((payload.get("data") or {}).get("diff")) or [])
            if rows:
                CACHE_DIR.mkdir(exist_ok=True)
                cache_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
                return rows
        except Exception as exc:
            print(f"[warn] clist host {host} failed: {exc}", file=sys.stderr)
    if cache_path.exists():
        print(f"[warn] using cached clist data: {cache_path.name}", file=sys.stderr)
        return json.loads(cache_path.read_text(encoding="utf-8"))
    return []


def spot_rows() -> list[dict[str, Any]]:
    fields = "f12,f14,f2,f3,f5,f6,f7,f8,f9,f20,f21,f62,f184"
    raw = eastmoney_clist("m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23", fields, fid="f3", pz=6000)
    out = []
    for row in raw:
        out.append(
            {
                "代码": row.get("f12"),
                "名称": row.get("f14"),
                "最新价": num(row.get("f2")),
                "涨跌幅": num(row.get("f3")),
                "成交量": num(row.get("f5")),
                "成交额": num(row.get("f6")),
                "振幅": num(row.get("f7")),
                "换手率": num(row.get("f8")),
                "市盈率": num(row.get("f9")),
                "总市值": num(row.get("f20")),
                "流通市值": num(row.get("f21")),
                "主力净流入": num(row.get("f62")),
                "主力净占比": num(row.get("f184")),
            }
        )
    return out


def sector_rows(kind: str) -> list[dict[str, Any]]:
    fs = "m:90+t:2" if kind == "industry" else "m:90+t:3"
    fields = "f12,f14,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87"
    raw = eastmoney_clist(fs, fields, fid="f62", pz=80)
    return [
        {
            "代码": row.get("f12"),
            "名称": row.get("f14"),
            "今日涨跌幅": num(row.get("f3")),
            "主力净流入": num(row.get("f62")),
            "主力净占比": num(row.get("f184")),
        }
        for row in raw
    ]


def topic_pool(date_str: str, pool: str) -> list[dict[str, Any]]:
    endpoint = "getTopicZTPool" if pool == "up" else "getTopicDTPool"
    payload = http_json(
        f"https://push2ex.eastmoney.com/{endpoint}",
        {
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
            "d": date_str,
            "Pageindex": 0,
            "pagesize": 10000,
            "sort": "fbt:asc" if pool == "up" else "fund:desc",
            "_": int(time.time() * 1000),
        },
    )
    rows = (((payload.get("data") or {}).get("pool")) or [])
    out = []
    for row in rows:
        out.append(
            {
                "代码": row.get("c"),
                "名称": row.get("n"),
                "涨跌幅": num(row.get("zdp")),
                "最新价": num(row.get("p")) / 1000 if row.get("p") else 0,
                "成交额": num(row.get("amount")),
                "流通市值": num(row.get("ltsz")),
                "换手率": num(row.get("hs")),
                "所属行业": row.get("hybk", "-"),
                "首次封板时间": row.get("fbt", "-"),
                "最后封板时间": row.get("lbt", "-"),
                "连板数": row.get("lbc", "-"),
                "原因": row.get("reason", "-"),
                "封单资金": num(row.get("fund")),
            }
        )
    return out


def latest_pool(date_str: str, pool: str) -> tuple[str, list[dict[str, Any]]]:
    for day in previous_calendar_days(date_str):
        try:
            rows = topic_pool(day, pool)
            if rows:
                return day, rows
        except Exception as exc:
            print(f"[warn] {pool} pool {day} failed: {exc}", file=sys.stderr)
    return date_str, []


def get_market_data(date_str: str) -> dict[str, Any]:
    up_date, limit_up = latest_pool(date_str, "up")
    down_date, limit_down = latest_pool(date_str, "down")
    spot = spot_rows()
    if not limit_up and spot:
        limit_up = derived_limit_rows(spot, "up")
        up_date = f"{date_str} 实时近似"
    if not limit_down and spot:
        limit_down = derived_limit_rows(spot, "down")
        down_date = f"{date_str} 实时近似"
    return {
        "date": date_str,
        "limit_up_date": up_date,
        "limit_down_date": down_date,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "spot": spot,
        "industry_flow": sector_rows("industry"),
        "concept_flow": sector_rows("concept"),
    }


def limit_threshold(code: str) -> float:
    if code.startswith(("300", "301", "688")):
        return 19.5
    if code.startswith(("8", "4", "920")):
        return 29.5
    return 9.5


def derived_limit_rows(rows: list[dict[str, Any]], direction: str) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        code = str(row.get("代码") or "")
        change = num(row.get("涨跌幅"))
        threshold = limit_threshold(code)
        matched = change >= threshold if direction == "up" else change <= -threshold
        if not matched:
            continue
        copied = dict(row)
        copied.setdefault("所属行业", "-")
        copied["原因"] = "由实时涨跌幅近似判断"
        copied["封单资金"] = 0
        out.append(copied)
    return sorted(out, key=lambda item: abs(num(item.get("涨跌幅"))), reverse=True)


def filtered_universe(rows: list[dict[str, Any]], cfg: ReportConfig) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        name = str(row.get("名称") or "")
        if any(keyword in name for keyword in cfg.exclude_name_keywords):
            continue
        price = num(row.get("最新价"))
        turnover = num(row.get("换手率"))
        market_cap = num(row.get("总市值"))
        if not (cfg.min_price <= price <= cfg.max_price):
            continue
        if not (cfg.min_turnover_rate <= turnover <= cfg.max_turnover_rate):
            continue
        if market_cap < cfg.min_market_cap_cny:
            continue
        out.append(row)
    return out


def top_sector_names(data: dict[str, Any], cfg: ReportConfig) -> set[str]:
    rows = data["industry_flow"] + data["concept_flow"]
    ranked = sorted(rows, key=lambda row: (num(row.get("主力净流入")), num(row.get("主力净占比"))), reverse=True)
    return {str(row.get("名称")) for row in ranked[: cfg.top_n_sectors]}


def score_candidates(data: dict[str, Any], cfg: ReportConfig) -> list[dict[str, Any]]:
    rows = filtered_universe(data["spot"], cfg)
    if not rows:
        return []
    flow_score = percentile_scores(rows, "主力净占比")
    momentum_score = percentile_scores(rows, "涨跌幅")
    liquidity_score = percentile_scores(rows, "成交额")
    hot_names = top_sector_names(data, cfg)
    for idx, row in enumerate(rows):
        sector_score = 0.0
        text = f"{row.get('名称', '')} {row.get('代码', '')}"
        # Eastmoney stock list does not always expose sector here; use flow/momentum when sector match is absent.
        if any(name and name in text for name in hot_names):
            sector_score = 1.0
        change = num(row.get("涨跌幅"))
        if change >= 9.5:
            row["_score"] = -1
            continue
        row["_score"] = flow_score[idx] * 0.4 + momentum_score[idx] * 0.25 + liquidity_score[idx] * 0.25 + sector_score * 0.1
        row["_reason"] = explain_candidate(row)
    return sorted(rows, key=lambda row: row.get("_score", 0), reverse=True)[: cfg.top_n_candidates]


def score_risks(data: dict[str, Any], cfg: ReportConfig) -> list[dict[str, Any]]:
    rows = filtered_universe(data["spot"], cfg)
    risk_score = percentile_scores(rows, "涨跌幅", inverse=True)
    turnover_score = percentile_scores(rows, "换手率")
    for idx, row in enumerate(rows):
        row["_risk_score"] = risk_score[idx] * 0.75 + turnover_score[idx] * 0.25
    return sorted(rows, key=lambda row: row.get("_risk_score", 0), reverse=True)[: cfg.top_n_risks]


def explain_candidate(row: dict[str, Any]) -> str:
    parts = []
    if num(row.get("主力净占比")) > 5:
        parts.append("主力净占比偏强")
    if 2 <= num(row.get("涨跌幅")) < 9.5:
        parts.append("短线动量较强且未涨停")
    if num(row.get("成交额")) > 500_000_000:
        parts.append("成交额活跃")
    if 3 <= num(row.get("换手率")) <= 15:
        parts.append("换手充分")
    return "，".join(parts) or "综合资金、动量和流动性评分靠前"


def money(value: Any) -> str:
    amount = num(value)
    if abs(amount) >= 100_000_000:
        return f"{amount / 100_000_000:.2f}亿"
    if abs(amount) >= 10_000:
        return f"{amount / 10_000:.2f}万"
    return f"{amount:.2f}"


def table_lines(rows: list[dict[str, Any]], columns: list[str], limit: int) -> list[str]:
    if not rows:
        return ["暂无可用数据"]
    lines = []
    for row in rows[:limit]:
        cells = []
        for col in columns:
            value = row.get(col, "-")
            if col in {"主力净流入", "成交额", "总市值", "流通市值", "封单资金"}:
                value = money(value)
            cells.append(f"{col}:{value}")
        lines.append("- " + " | ".join(cells))
    return lines


def count_by(rows: list[dict[str, Any]], key: str, limit: int = 5) -> str:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "-")
        counts[value] = counts.get(value, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:limit]
    return "、".join(f"{name}({count})" for name, count in ranked) if ranked else "暂无"


def render_report(mode: str, data: dict[str, Any], cfg: ReportConfig) -> tuple[str, str]:
    generated = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    title = f"{cfg.title_prefix} {data['date']} {'开盘前' if mode == 'morning' else '收盘后'}"
    candidates = score_candidates(data, cfg)
    risks = score_risks(data, cfg)
    lines = [
        f"# {title}",
        "",
        f"生成时间：{generated} Asia/Shanghai",
        "",
        "> 风险提示：以下内容是量化筛选和市场复盘，不构成个性化投资建议；实盘前请结合仓位、止损、流动性和自身风险承受能力。",
        "",
        "## 市场情绪",
        "",
        f"- 涨停：{len(data['limit_up'])} 只（数据日：{data['limit_up_date']}）",
        f"- 跌停：{len(data['limit_down'])} 只（数据日：{data['limit_down_date']}）",
        f"- 涨停集中行业：{count_by(data['limit_up'], '所属行业')}",
        f"- 跌停集中行业：{count_by(data['limit_down'], '所属行业')}",
        "",
        "## 行业资金流入",
        "",
    ]
    lines.extend(table_lines(data["industry_flow"], ["名称", "今日涨跌幅", "主力净流入", "主力净占比"], cfg.top_n_sectors))
    lines.extend(["", "## 概念资金流入", ""])
    lines.extend(table_lines(data["concept_flow"], ["名称", "今日涨跌幅", "主力净流入", "主力净占比"], cfg.top_n_sectors))
    if mode == "morning":
        lines.extend(["", "## 5 只观察候选", ""])
        if candidates:
            for row in candidates:
                lines.append(
                    f"- {row.get('代码')} {row.get('名称')} | 涨跌幅:{row.get('涨跌幅')} | "
                    f"换手率:{row.get('换手率')} | 主力净占比:{row.get('主力净占比')} | 逻辑:{row.get('_reason')}"
                )
        else:
            lines.append("暂无候选：实时行情或资金流数据不可用。")
        lines.extend(
            [
                "",
                "## 卖出/回避信号",
                "",
                "- 持仓股若跌破个人止损线、放量下跌、主力净流出扩大，优先降低仓位。",
                "- 一字涨停、连续加速后放量开板、跌停封单扩大，不纳入追高买入。",
            ]
        )
    else:
        lines.extend(["", "## 涨停样本", ""])
        lines.extend(table_lines(data["limit_up"], ["代码", "名称", "涨跌幅", "换手率", "封单资金", "首次封板时间", "连板数", "所属行业", "原因"], 12))
        lines.extend(["", "## 跌停样本", ""])
        lines.extend(table_lines(data["limit_down"], ["代码", "名称", "涨跌幅", "换手率", "封单资金", "所属行业"], 12))
    lines.extend(["", "## 短线风险排行", ""])
    lines.extend(table_lines(risks, ["代码", "名称", "涨跌幅", "换手率", "成交额", "总市值"], cfg.top_n_risks))
    return title, "\n".join(lines)


def push_report(title: str, body: str) -> None:
    load_env()
    sent = False
    sendkey = os.getenv("SERVERCHAN_SENDKEY", "").strip()
    wecom = os.getenv("WECOM_BOT_WEBHOOK", "").strip()
    pushplus = os.getenv("PUSHPLUS_TOKEN", "").strip()
    if sendkey:
        data = urllib.parse.urlencode({"title": title, "desp": body}).encode("utf-8")
        urllib.request.urlopen(urllib.request.Request(f"https://sctapi.ftqq.com/{sendkey}.send", data=data), timeout=20).read()
        sent = True
    if wecom:
        payload = json.dumps({"msgtype": "markdown", "markdown": {"content": f"## {title}\n\n{body[:3800]}"}}).encode("utf-8")
        req = urllib.request.Request(wecom, data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=20).read()
        sent = True
    if pushplus:
        payload = json.dumps({"token": pushplus, "title": title, "content": body, "template": "markdown"}).encode("utf-8")
        req = urllib.request.Request("https://www.pushplus.plus/send", data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=20).read()
        sent = True
    if not sent:
        print("[info] no push channel configured; skipped push")


def save_report(title: str, body: str, mode: str, date_str: str) -> Path:
    REPORT_DIR.mkdir(exist_ok=True)
    path = REPORT_DIR / f"{date_str}-{mode}.md"
    path.write_text(body, encoding="utf-8")
    meta = {"title": title, "path": str(path), "generated_at": dt.datetime.now().isoformat()}
    (REPORT_DIR / f"{date_str}-{mode}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate A-share morning or close report.")
    parser.add_argument("--mode", choices=["morning", "close"], required=True)
    parser.add_argument("--date", default=today_yyyymmdd(), help="YYYYMMDD, defaults to today in Asia/Shanghai.")
    parser.add_argument("--push", action="store_true", help="Push to configured WeChat-compatible channels.")
    args = parser.parse_args()
    cfg = load_config()
    data = get_market_data(args.date)
    title, body = render_report(args.mode, data, cfg)
    path = save_report(title, body, args.mode, args.date)
    print(f"saved: {path}")
    if args.push:
        push_report(title, body)


if __name__ == "__main__":
    main()
