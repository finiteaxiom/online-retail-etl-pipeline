# Online Retail ETL Pipeline

从 `online-retail-analysis` 迁移的全量批处理项目。读取根目录 `data.csv`，使用 Python 清洗并完成经营、商品和客户分析，将同一批结果写入 MySQL 并导出 CSV。

## 首次运行

要求 Python 3.10+、MySQL 8.0+，并已创建 `online_retail_etl` 数据库。本机已配置项目账号 `retail_etl`（在本地 `.env` 中设置）；示例配置默认账号为 root。SQLyog 用于查看 MySQL 表，不需要在 Python 运行期间保持打开。

在项目目录的 PowerShell 中：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe run_etl.py --check-connection
.\.venv\Scripts\python.exe run_etl.py
```

如果 `.venv` 已准备好，跳过前两步；如果 `.env` 已存在，不要覆盖它。默认连接 `127.0.0.1:3306`，数据库 `online_retail_etl`，用户 `root`。未设置 `MYSQL_PASSWORD` 时，终端会隐藏输入密码，密码不进入命令行、日志或输出文件。也可在本地 `.env` 中设置密码；该文件已列入 `.gitignore`。不要把密码发到聊天或提交 Git。

本次开发环境的 `.venv` 使用 `--system-site-packages`，复用已有 pandas/numpy，并在项目虚拟环境内安装数据库依赖；新机器可按上面的独立虚拟环境方式安装。

只计算和导出，不连接数据库：

```powershell
.\.venv\Scripts\python.exe run_etl.py --csv-only
```

## 数据流与结果

`data.csv → 字段与类型校验 → 完全重复行去重 → 销售候选 → 普通商品销售 / 客户销售 → 分析 → CSV 核对 → MySQL 暂存与核对 → 统一切换`

| MySQL 表 / 同名 CSV | 内容 |
| --- | --- |
| quality_audit | 取消、数量/价格异常、特殊代码、缺失与重复统计；各问题可能重叠 |
| pipeline_counts | 原始、去重、候选、商品、客户各阶段行数 |
| product_sales | 普通商品销售明细，包含缺失客户编号的销售 |
| customer_sales | 可识别客户销售明细 |
| core_metrics | 销售额、销量、订单数、客户数、客单价和客户覆盖 |
| monthly_sales | 全部月份的销售额、订单数、交易天数、是否参与比较和环比 |
| country_sales | 国家销售额、销量、订单数及占比 |
| product_sales_summary | 商品销售额、销量、订单数及加权平均单价 |
| customer_summary | 客户累计消费、订单数、最近购买时间、客单价和复购类型 |
| customer_metrics | 复购率、均值、中位数、频次与消费相关性 |
| repeat_customers | 一次性与复购客户人数、消费及占比 |
| customer_concentration | 按消费排名的客户十分组及销售贡献 |
| customer_rfm | 客户 RFM、评分、分群和参考日期 |
| rfm_segments | 各群体人数、消费、购买间隔和贡献占比 |

每次结果位于 `output/<run_id>/`，包含 CSV、`run.log` 和 `manifest.json`。清单记录源文件 SHA256、规则、行数、核心指标、排除月份和完成状态。每张表和 CSV 都带相同 `run_id`；MySQL 保留最近一次成功批次，CSV 保留各批次。`csv_only_complete` 表示只完成文件验证，`complete` 且 `mysql_published=true` 才表示数据库也已发布。

## 沿用的业务规则

- 分析副本删除八个业务字段完全重复的行，原始 CSV 不改动。没有明细唯一编号，无法证明所有相同行都是重复录入。
- 数量和单价均大于零，且订单号不以大写 `C` 开头，作为有效销售候选。
- 特殊代码通过 `strip + upper` 匹配，排除 `AMAZONFEE, B, POST, DOT, M, BANK CHARGES`；保留原始商品代码以维持旧分组口径。
- 销售额为数量乘单价；未匹配退货，因此不是扣除退货后的净销售额。
- 缺失 CustomerID 的销售保留在经营分析中，客户分析单独排除。
- 商品按 `StockCode + Description` 分组，沿用原 Notebook 默认不纳入缺失描述的行为。不同描述会拆分同一码商品，商品榜占比分母需理解为有描述的商品销售。
- RFM 参考日期为普通商品销售最大日期的次日，不使用系统当天日期。频次按不同订单数；同值按 CustomerID 升序稳定分组。分群名称是规则标签，不证明因果或实际营销效果。
- 当前数据的 `2011-12` 是部分月份，默认保留数据但不参加环比。可重复使用 `--exclude-month YYYY-MM` 指定其他排除月份。相邻日历月缺失或被排除时不跨月计算环比；活跃交易天数不代表数据一定覆盖完整月份。
- 字段缺失、无效日期/数值或无可分析客户时明确失败，不静默丢弃异常记录。缺失客户和描述允许存在，业务键/国家缺失则拒绝批次。

## 重复运行与失败处理

首版为全量重算。数据库连接先检查；计算和 CSV 核对通过后，加载 `_stage_` 表，核对每表行数、主要金额汇总和明细订单/客户/销量，再通过一条 `RENAME TABLE` 切换整批表。失败批次不能发布半套结果。通过 MySQL 命名锁阻止两个本项目加载器同时发布。成功切换后删除该批次生成的旧表；若清理失败，会记录需清理的表名。

数据库和文件系统没有跨系统事务：CSV 可能已生成但 MySQL 写入失败，必须以清单状态和数据库 `run_id` 判断。运行期间 MySQL 客户端如需连续查询多个表的同一批次，应核对 `run_id`；外部手工改表不受本项目锁保护。金额沿用原 pandas 浮点算法，数据库为 DOUBLE，按 £0.01 容差核对；不作为财务记账系统。数据库限定为 `online_retail_etl`，不写旧库。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

首次成功入库后，在 SQLyog 执行 `sql/verify_results.sql`。对于当前相同源文件，旧项目的回归基准为：普通商品明细 522,714 行、订单 19,778、客户 4,334、销量 5,561,564、销售额 £10,255,019.18。这些值用于当前数据核对，没有写成所有未来输入都必须满足的条件。

代码迁移来源：旧项目的 `cleaning.py`、`rfm.py` 及主 Notebook。绘图、格式化和报告叙述继续留在报告层；此项目输出完整分析数据供后续报告使用。暂未实现调度、增量更新、利润/库存分析或退货匹配。

## 本机实际验证记录（2026-10-16）

MySQL 8.0.46 上已通过项目账号连续运行两次完整 ETL：`202610160535587a0514`、`2026101605364348bbaa`。第二次完成后，独立查询核对全部 14 张表的行数和批次编号、主要金额汇总以及明细订单数/客户数/销量；两批 CSV 去掉 run_id 后业务结果一致，数据库无重复累加，也没有遗留本次暂存表/备份表。验证证据保存在第二批输出目录的 `mysql_verification.json`。

8 项单元测试通过；迁移后四个清洗数据集与旧函数逐行对照一致，全部客户 RFM 指标、评分和分群一致，RFM 群体汇总与旧导出文件一致。旧项目 Git 工作区保持干净。未模拟服务断电、磁盘故障或验证跨系统事务恢复；本机成功运行不等同于这些故障场景验收。
