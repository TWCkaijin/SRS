# 操作與故障處理

## 日常操作

1. 登入後從快取查看主機、工作與紀錄。頁面重新整理不會直接對主機下指令。
2. 需要新狀態時，建立一次性預檢草稿，或建立有明確主機範圍、起迄時間與間隔的唯讀排程；檢視計畫後批准。
3. 訓練草稿選擇已登錄專案、固定 revision／容器、模式與資源。看完實際 script、主機、資源及影響後批准不可變計畫。
4. 改動主機、參數、內容或排程規則後必須重新產生及批准計畫。過期批准不可沿用。
5. 停止平台 execution 只停止尚未派發的步驟；已送出的 Slurm 工作要另建取消計畫，除非原計畫已明確授權該取消動作。

## 運算規範

先跑 CPU，再跑 225 單 GPU，最後才跑 225＋250。GPU 工作必須在 Slurm 分配內執行；`workloads/train.py` 會要求 `SLURM_JOB_ID`、指定可見設備，並限制每台一個 rank／一張 GPU。這些應用層檢查不能取代 Slurm cgroup。

workload 接受 `RANK/WORLD_SIZE/LOCAL_RANK`；若不存在，改讀 `SLURM_PROCID/SLURM_NTASKS/SLURM_LOCALID`。兩 rank 要有明確 `MASTER_ADDR` 與 `MASTER_PORT`，並能走計畫中核准的網路介面。實際通信 timeout 是 120 秒，Slurm 作業上限五分鐘；遇到 rank 失敗時取消同一工作其餘 rank。

`--steps` 表示本次量測更新次數，另有 `--warmup` 更新不計入速度；兩者都會增加 checkpoint 的 step。恢復數值測試使用 warmup=0。`--checkpoint-dir` 是新輸出路徑，不可預先建立；`--resume-from` 是既有完整 checkpoint。`COMPLETE` 僅在所有 rank 存檔完成後由 rank 0 寫入。沒有完整標記、模型規格不同或 torch 版本不同的 checkpoint 會拒絕恢復。

正確性範例固定 FP32，不要求 2080 Ti 支援 BF16。FSDP2 是模型訓練狀態分片，並不是把 8GB 與 11GB 相加成一張 19GB GPU。每卡仍須容納自己的分片、activation 與 all-gather 暫存。

## 保護既有使用者

- 只使用明確保留的 GPU UUID；「當下利用率為零」不等於資源已可重新分配。
- 250 的另一張 GPU 不列入 GRES、不測負載、不更動 power limit。
- 不用廣泛的 `pkill`、依使用者全殺程序、全卡 reset 或重啟驅動處理測試失敗。只取消本次 Slurm job／step。
- 不在未審核的計畫中更改 SSH、PAM、使用者、驅動、開機設定或防火牆。
- `pam_slurm_adopt` 會改變直接 SSH 的行為，必須先在 VM／專用試點驗證救援登入與回復方式，再另行規劃遷移。
- v1 共用專用運算帳號，只能宣稱 API 所有權與工作資源管理，不宣稱各研究者已具完整 OS／檔案隔離。

## 常見故障與處理

| 現象 | 處理方式 |
|---|---|
| SSH 指紋或登入身份不同 | 停止；由可信管道重新核對 inventory，重新產生計畫，不自動接受新 key |
| 連線中斷，命令是否完成不明 | 記錄 unknown，檢查已批准的 durable journal／Slurm job identity；不可直接重送 |
| Slurm 已收工作但回覆遺失 | 使用計畫綁定的 job comment／ID 做已授權 reconcile，避免重複提交 |
| 目標檔案 hash 在批准後改變 | 中止寫入，重新預檢與比較差異，再由使用者批准 |
| GPU 已有其他工作或指定 UUID 不見 | 中止本次 GPU 計畫；不清掉他人工作，不換到另一張未批准的卡 |
| CUDA kernel／驅動不相容 | 保留版本與錯誤，選擇已核准的相容映像；不自動升級系統驅動 |
| NCCL timeout | 先核對兩 rank、介面、私有位址與 port，再使用小型 collective；清理範圍只限自身 job |
| checkpoint 只在其中一台可見 | 修正共享儲存的計畫與權限，再重新測試；不要以同名目錄掩蓋問題 |
| 日誌空間不足或紀錄不完整 | 停止派發，標記 log_complete=false；另行審核精確範圍的清理計畫 |
| CPU DDP 在 Mac localhost rendezvous 等待 | 使用未占用的明確 loopback IPv4／port、設定短 rendezvous timeout；不延伸成遠端網路修復 |

## 備份與回復

資料庫備份、migration、還原及檔案回復須另行人工審核；第一版未提供這些操作的網頁計畫產生器，`init-db` 是直接執行的本機管理命令。部署前保留版本、原始 hash、目的地與必要的備份證據。密碼、私鑰、MUNGE key 不進入公開計畫、git、fixture 或日誌；計畫只保存 executor 可解析的秘密參照。

首次正式導入應在工作維護時段完成，驗證 CPU 排程／取消後再啟用 GPU partition。平台離線不應觸發無條件取消既有 Slurm 工作；恢復後先同步已授權狀態，再開放新的工作派發。

## 正式服務帳號與檔案權限

三份 systemd 範本使用已存在的專用帳號與 `srs` 群組；建立帳號、群組、目錄及啟動服務需另外列入批准計畫。API 與 scheduler 使用 `srs-api`，worker 使用 `srs-executor`，三者 `Group=srs`、`UMask=0027`。API／scheduler 不提供主機憑證檔；只有 worker 的命令列含 `--credentials-file /etc/server-remote-system/hosts.env`，路徑是公開參照，密碼不在 argv。

所有服務必須在各自 EnvironmentFile 明確設定 `SRS_DATA_DIR=/var/lib/server-remote-system`、相同正式 MariaDB URL、同一份公開 inventory 路徑與執行模式。不可沿用開發時 repo/var 與 SQLite 的預設值。正式 release 需包含 `backend/srs`、`infra`、`workloads`、migration 與前端產物。

| 路徑 | 所有權／權限 | 用途 |
|---|---|---|
| `/var/lib/server-remote-system` | `root:srs`、`02770` | setgid 保留群組；API 與 worker 可建立各自資料 |
| `/var/lib/server-remote-system/artifacts` | `srs-executor:srs`、`02750` | API 可讀取已登錄成果；worker 可寫入 |
| `artifacts/<job_id>` | `srs-executor:srs`、`0750` 或 `02750` | worker 顯式保留群組讀取；其他帳號不可進入 |
| 成果檔 | `srs-executor:srs`、`0640` | API 驗證 DB 所有權與 SHA256 後提供下載 |
| `/etc/server-remote-system/hosts.env` | `srs-executor:srs-executor`、`0600` | executor-only 主機憑證；不能給 `srs` 群組讀取 |
| `api.env`／`executor.env` | 各服務帳號所有、`0600` | 不把任何登入密碼加入公開部署 diff |

目錄要在開服務前依批准計畫建立；程式 `mkdir(exist_ok=True)` 不會修正既有錯誤所有權。若先前曾以單一帳號建立 `0700` 成果目錄，需另行審核精確目錄的群組／權限修正，不能只改 umask 就假設舊資料可讀。API 仍須通過每個 artifact 的使用者所有權檢查；Unix 群組配置不是研究者身分隔離。

API 命令為 `.venv/bin/python -m srs.cli api --host 127.0.0.1 --port 8000`；worker 為 `.venv/bin/python -m srs.cli worker --credentials-file /etc/server-remote-system/hosts.env`；scheduler 為 `.venv/bin/python -m srs.cli scheduler`。三者獨立啟停；安裝 unit 不會自動 daemon-reload、enable、start、migration 或連線遠端。
