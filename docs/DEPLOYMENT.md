# 建置與部署

這個專案將「編輯草稿 → 產生不可變計畫 → 審核內容與影響 → 批准 → 執行」作為唯一遠端執行入口。開發時不會自動讀取個人 SSH 設定或憑證，也不會因開啟頁面而連線主機。`infra/plan_deployment.py` 只輸出待補證據的 JSON，不會部署。

## 目前交付與限制

- 已提供 API／worker 原型所需的運算範例、Slurm 腳本產生器、部署範本、三台本機 VM 宣告與 MariaDB 測試容器宣告。
- 本機已驗證純 Python 合約、腳本語法、實際 CPU 訓練與 checkpoint 恢復；詳細證據以 `TEST_MATRIX.md` 為準。
- 未啟動 Lima VM、MariaDB 容器或任何 Linux 服務；未在 225／250 安裝套件、送出 GPU 工作、改動驅動或 PAM。
- 套件版本已選定，外部二進位檔／VM 映像的 SHA256 尚須從可信來源核實並納入最終批准內容。版本字串不是供應來源驗證。

## 固定基線

| 項目 | 基線 |
|---|---|
| 執行環境 | Python 3.12、Ubuntu 24.04；Mac 本機為 arm64 |
| 訓練 | PyTorch 2.14.1；本機 CPU／Gloo，遠端 CUDA 12.6 wheel／NCCL |
| 排程 | Slurm 26.05.5；REST API `v0.0.45` |
| 容器 | Apptainer 1.5.5，優先非 setuid 安裝 |
| 選配研究入口 | Open OnDemand 4.2.0 |
| 正式資料庫 | MariaDB 11.4.8；SQLite 僅標示為 demo／特定本機測試 |

版本與官方來源記錄於 `infra/versions.lock.json`。本機 CUDA 以外的 wheel 與遠端 CUDA wheel 分開安裝，不能拿 arm64 成功當作 x86_64 GPU 相容性證明。595.99.02 驅動是先前盤點資料，上線前仍須重新讀取；本專案不會為配合測試自動更新驅動。

## 第一階段：本機實際 CPU 整合

`infra/lima/` 定義三台 Ubuntu arm64 VM：head 2 vCPU／4 GiB，worker-1 與 worker-2 各 1 vCPU／2 GiB，總計 8 GiB。這是 16 GiB Mac 的試點起點，非已實測容量保證。映像 checksum、私有網路位址及開始 VM 的動作需要另行審核；檔案沒有自動 provisioning hook，也沒有開放 home 目錄掛載。

head 承載控制面、Slurm controller、測試資料庫與選配 OOD；兩個 worker 使用 CPU-only partition。保留至少 512 MiB 給各 worker 的 OS，不把整台 VM 記憶體宣告為可排程 RAM。各 VM 使用一致的測試 UID／GID、MUNGE key 及時鐘，並驗證 DNS／主機名稱雙向解析。Slurm 的 CPU／RAM 限制須在 Linux VM 實測；Mac 原生 CPU DDP 不能取代此測試。

本機 `local-slurm` 要使用獨立資料庫、inventory 與憑證檔，不與 `remote-slurm` 共用主機綁定。VM 的顯示名稱必須標明本機測試；不得把 VM 結果記為 225／250 的實機結果。實機階段才使用實際主機的指紋與身份。

`infra/compose.mariadb.yaml` 只將資料庫連到 `127.0.0.1:13306`。啟動前需提供已核對的 `mariadb:11.4.8@sha256:...` 映像與私有密碼檔路徑；密碼透過 Docker secrets 掛載，不寫在命令、YAML 或測試資料。啟動、schema migration、停止及刪除資料卷都是分開列出的操作，不由測試程式自動執行。SQLite 通過不代表 MariaDB migration、交易或鎖定驗證已通過。

## 第二階段：225 單機試點

225 同時擔任初期 head／compute，GPU 僅納入使用者指定的 RTX 3060 Ti。先取得經批准的唯讀預檢：OS／CPU／RAM、cgroup v2、主機指紋、磁碟空間、GPU UUID／裝置檔映射、工作占用及工具版本。只有符合計畫前置條件才可開始安裝。初次只部署 Slurm 與 CPU 作業，確認排程與清理後再驗證 Apptainer 和單卡 CUDA。

部署計畫需明列：可信套件來源與 hash、已存在的檔案摘要與完整差異、專用帳號權限、服務安裝／啟動／重新啟動動作及回復方式。`infra/deploy/*.sh.in` 不下載套件、不連線其他主機、不重啟服務；package installation 可能執行套件維護腳本，這項影響也必須在審核畫面列出。`infra/catalog.py` 的 `deployment_review(component)` 保留未綁定項目並回傳 `issues`，不能直接當成已批准計畫。

Linux 服務範本將 API 與 executor 分為不同帳號。主機登入憑證只供 executor 讀取，API 不應取得 `.env.hosts`。API 預設只聽回送介面，正式對外入口另列 TLS／反向代理計畫。第一版由專用 `srs-runner` 執行運算；API 所有權檢查不等同每位研究者的 OS 身分隔離。

## 第三階段：225＋250 跨節點

250 只加入明確指定的一張 RTX 2080 Ti；第二張 GPU 不得列入 GRES 或排程容量。以 UUID 核對 PCI bus 與 `/dev/nvidiaN`，不要把容易變動的索引 0 當作資產身份。節點 GPU 探測、容器 hash、專案 commit、連線介面及工作時限都進入最終計畫。

兩台以相同路徑讀取核准的程式、容器與 checkpoint。共享儲存必須真的跨節點可見；同名本機資料夾不算。FSDP2 範例在存取 checkpoint 前交換探測檔，確認每個 rank 可見所有探測檔後才呼叫 DCP。若尚未建立共享儲存，該驗收維持待測，不能略過或宣稱恢復成功。

跨節點先跑小型 NCCL collective，再依序跑 DDP 與 FSDP2。每台一個 rank、每 rank 一張保留 GPU，通信逾時 120 秒，Slurm 上限 5 分鐘，失敗中止同一工作其他 rank。Rendezvous 主機與 port、各節點 LAN 或已部署 WireGuard 介面都要經核對，不依 SSH 能連線就推定節點間可直接互通。1 Gbps 連線只作初期功能驗證，不承諾訓練加速。

## 可供主計畫引用的介面

- `infra.catalog.deployment_review("slurm" | "apptainer" | "platform")`：回傳固定來源檔 SHA256、script／argv、檔案目的地、所需證據與未解項目；完全不執行。
- `python infra/plan_deployment.py --component slurm`：只在 stdout 輸出上述 JSON。
- `workloads.slurm.render.render_job(RunSpec(...), ...)`：回傳純字串 sbatch，固定每節點一個 rank、五分鐘上限；GPU 跨機工作要求兩個明確節點與各自的介面。
- `python -m workloads.train`：只執行已配置資源中的工作，不提交 Slurm、不 SSH、不安裝套件。`--profile cpu|single|ddp|fsdp2`、`--device cpu|cuda`、`--output-dir`；恢復用 `--phase resume --resume-from`，新的 `--checkpoint-dir` 必須尚不存在。
- `workloads.compare_resume`／`workloads.compare_ddp`：讀取結果與 tensor 檔，驗證恢復／固定全域 batch 的數值一致性；不接觸遠端。

正式部署需封裝完整 `workloads/`、`infra/` 與後端版本，或將來源與內容摘要明確納入 release；不能只打包 `backend/srs` 就假定範例與範本也存在。

`render_deployment`、部署唯讀探測、跨節點 NFS／網路證據的 JSON 契約與目前 CPU-only Apptainer 可驗證範圍，詳見 [EVIDENCE_CONTRACT.md](EVIDENCE_CONTRACT.md)。服務帳號、共享成果群組與 `SRS_DATA_DIR` 設定詳見 [OPERATIONS.md](OPERATIONS.md)。

## OOD 的授權邊界

Open OnDemand 是選配入口。若直接啟用其 Slurm 提交或互動應用，會形成另一條送工作管道；在本專案的「逐次審核批准」要求下，不得無條件開啟。第一版使用本專案表單與審核流程。未來整合 OOD 時須明定它如何呼叫相同批准服務，或取得使用者另外授權的使用政策，不能以 OOD 登入成功視為所有運算均已批准。
