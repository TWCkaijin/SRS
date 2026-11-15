# 本次實作與驗證報告

日期：2026-11-07（Asia/Taipei）。專案：`/Users/kaijin/TWCKaijin/_lab/Server-Remote-system`。

## 已交付

繁體中文 React／TypeScript 操作台與 FastAPI 已接通。API、executor、唯讀 scheduler 為獨立程序；提供草稿、固定模板計畫、內容 hash 核准、排隊執行、stdout／stderr／audit、失敗與對帳、到期排程、工作狀態及受控成果下載。預設 demo 不載入主機憑證、不連線遠端主機。

實機 adapter 包含嚴格 SSH fingerprint、逐台 SSH／sudo 密碼、固定 Slurm REST 請求、變更步驟的持久化 runner、未知結果不盲目重送。另提供 MariaDB migration、Lima VM 宣告、Slurm／Apptainer／systemd 範本、部署與跨節點唯讀證據 parser、CPU／單卡／DDP／FSDP2 訓練和 checkpoint／resume 程式。

## 已執行的測試

| 驗證 | 結果 | 證據與邊界 |
|---|---|---|
| 後端、執行器、核准、排程、部署／workload 合約 | **201 passed，0 skipped** | [完整輸出](verification/backend-tests.txt)、[JUnit](verification/backend-junit.xml)。HTTP／DB／worker 為真本機整合；SSH／Slurm 通訊用 fake transports；檔案安全案例使用本機暫存目錄 |
| Python lint | 通過 | [Ruff 結果](verification/lint.txt) |
| 鎖定相依與套件入口 | `uv sync --locked --offline` 通過 | Editable 安裝；可從專案外 import `srs`、`infra`、`workloads` |
| 前端型別／正式建置 | 通過 | [建置輸出](../frontend/evidence/frontend-build.txt)，實際產生 `frontend/dist` |
| 前端元件 | **13 passed** | [測試輸出](../frontend/evidence/frontend-tests.txt)；不是遠端部署證據 |
| 瀏覽器完整操作 | 14 項流程＋10 項手機／焦點檢查通過，errors 為空 | [瀏覽器紀錄](../frontend/evidence/browser-report.json)、[桌面總覽](../frontend/evidence/02-overview-desktop.png)、[計畫審核](../frontend/evidence/03-plan-desktop.png)、[執行日誌](../frontend/evidence/04-execution-desktop.png)、[手機總覽](../frontend/evidence/07-overview-mobile.png) |
| 真 CPU checkpoint 恢復 | 通過 | 最終 201 項測試已設定獨立 Torch Python，實際跑不中斷 3 步與 2 步儲存＋新程序恢復 1 步，結果比較通過 |
| 真 CPU 雙程序 DDP | 通過數值比較 | [比較結果](verification/cpu-ddp-comparison.json)、[單 CPU 原始結果](verification/cpu-reference-rank-0.json)、[DDP rank 0](verification/ddp-cpu-rank-0.json)、[rank 1](verification/ddp-cpu-rank-1.json) |
| Database migration | 新 SQLite DB 升級至 head 通過；MariaDB dialect SQL 可離線生成 | 15 張表含 Alembic version；沒有實際 MariaDB 服務驗證 |

瀏覽器使用隔離 headless Chrome 與本機 `127.0.0.1:8765`，實際完成登入、七個資料頁、儲存草稿、產生計畫、確認後核准、執行完成與日誌下載、排程審核／啟停、工作提交、成果收集與下載、390px 手機導覽。瀏覽器中的訓練與主機狀態仍是後端 demo 模擬；下載結果也明確包含 `simulation: true`。真正的 CPU 訓練是表中另外列出的獨立程序測試。

CPU DDP 與相同全域 batch 單 CPU 的最大 logits 絕對差為 `3.5762786865234375e-7`，在 `rtol=1e-4, atol=1e-5` 內。這個 3-step 微模型單次量測速度比約 `0.728`，沒有加速；不能外推到 GPU 或較大模型。Torch 環境為 Python 3.12、PyTorch 2.14.1、Darwin arm64，沒有 CUDA 或 Slurm job。

主要核准與安全案例包括：未核准不派發、修改 hash／inventory／草稿後阻擋、開始期限過期拒絕、開始後依已核准 timeout 完成、併發／重複核准只建立一個執行、排程權限分離／到期／不補跑／不重疊、兩台不同 SSH／sudo 密碼、stdout／stderr／非零退出／timeout、斷線不重送、byte cursor 續讀、秘密跨片段／跨頁／多層 JSON 遮罩、符號連結與檔案摘要變更拒絕、失去租約的舊 worker 不提交結果、磁碟不足不派發、成果擁有權與路徑約束。

測試有一項 Starlette／httpx TestClient deprecation warning，沒有失敗。獨立 Torch 環境沒有 NumPy，會出現初始化 NumPy 的警告；本次 tensor 比較與訓練不依賴 NumPy，相關真實測試已通過。

## 尚未完成的實機驗收

本次沒有啟動 Linux VM、MariaDB、Slurm 或 Apptainer，也沒有連線或部署到 225／250。檢查時 Lima 沒有 instance；Docker CLI 存在，但 daemon socket 未啟動。MariaDB 交易鎖定、真正 Slurm REST schema／CPU 排程／cgroup、逐台 admin／sudo 登入、NVIDIA GPU、NCCL、GPU DDP、FSDP2／DCP 恢復及跨帳號 systemd 成果權限仍待實機驗收。

CPU-only Apptainer 的固定部署 renderer 可在完整實測證據成立時產生可審閱計畫；目前沒有真實主機證據，因此尚未執行。Slurm／platform 缺維護時段、帳號／MUNGE、備份或其他管理前提時維持 blocked；不會填入假的 verified 值。Apptainer 的 [離線 CUDA recipe](../workloads/containers/README.md) 也尚未 build，base image／wheel／SIF hash 必須先核實。

## 第一版的操作限制

- 有副作用 SSH 步驟使用遠端 durable journal；唯讀 SSH 探測只在記憶體中傳輸，不在遠端新增 journal。唯讀連線中斷會保留已接收紀錄並標示不完整，原始輸出無法補回，必須另建核准的預檢。結果待確認時不得宣稱日誌完整。
- 日誌分頁以原始 byte offset 續讀，inode／size 變更會留下 gap；不宣稱不同主機、stdout 與 stderr 的接收序號是原始全域時間順序。
- 成果收集僅限已登錄工作的 `rank-0.json`／`rank-1.json`，每檔最多 64KiB。完整 checkpoint 下載與任意資料檔傳輸不在第一版介面中。
- 清理模板只處理已結束工作、已收集且摘要一致的固定結果 JSON，先保留獨占備份；不自動清除日誌、checkpoint 或任意目錄。
- DB 備份／還原／migration、帳號建立、共享儲存與網路建置須另行人工審核；目前沒有這些操作的網頁計畫產生器。`init-db` 是直接執行的管理命令。

Slurm 提交欄位另已核對 [官方 v0.0.45 job_desc_msg schema](https://slurm.schedmd.com/rest_api.html#v0.0.45_job_desc_msg)；型別符合文件，仍不等於實際 Slurm 服務驗收。

分階段的重現方法與通過條件見 [TEST_MATRIX.md](TEST_MATRIX.md)、[EVIDENCE_CONTRACT.md](EVIDENCE_CONTRACT.md) 與 [OPERATIONS.md](OPERATIONS.md)。API／worker／scheduler 測試程序已停止；本機 8765 連接埠已關閉；本次沒有 Git commit、push 或遠端部署。
