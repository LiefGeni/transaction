# A 股日报机器人

这个项目每天生成两类报告：

- `morning`：开盘前报告，基于最近交易日收盘数据、板块资金流和个股资金流，输出 5 只“观察候选”，不是保证收益的买入指令。
- `close`：收盘后报告，统计涨停/跌停数量、资金流入板块、市场情绪和风险股。

脚本默认不强制依赖第三方包，直接调用东方财富公开行情接口；如果专题涨跌停池不可用，会用实时涨跌幅规则近似统计涨停/跌停。后续也可以接入 AKShare、Wind、同花顺 iFinD 或聚宽等更稳定的数据源。

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
Copy-Item .env.example .env
Copy-Item config.example.yaml config.yaml
```

可选：如果你希望读取 `config.yaml`，可以再执行：

```powershell
pip install -r requirements.txt
```

然后在 `.env` 填一个推送通道：

- Server 酱：`SERVERCHAN_SENDKEY`
- 企业微信群机器人：`WECOM_BOT_WEBHOOK`
- PushPlus：`PUSHPLUS_TOKEN`

## 运行

```powershell
python .\src\a_share_reporter.py --mode morning --push
python .\src\a_share_reporter.py --mode close --push
```

不加 `--push` 会只在本地生成报告，输出在 `reports/`。

当前选股逻辑是“观察候选池”：综合主力净占比、涨跌幅动量、成交额、换手率，并过滤 ST/退市、价格过低、过度换手和涨停追高。它不是自动下单系统，也不是保证收益的买卖指令。

## Windows 定时任务

先确认手动运行成功，再用管理员 PowerShell 执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\register_tasks.ps1
```

默认交易日周一至周五：

- 09:00 发送开盘前报告
- 15:30 发送收盘报告

## 风险提示

报告用于研究和复盘，不构成个性化投资建议。A 股存在 T+1、涨跌停、停牌、流动性和数据延迟等风险，实盘前应结合仓位、止损、交易成本和个人风险承受能力。
