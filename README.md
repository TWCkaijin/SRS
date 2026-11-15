# Server Remote System

研究運算操作台：React／TypeScript 繁體中文網頁、FastAPI、獨立 executor、唯讀 scheduler、MariaDB schema，以及 Slurm／Apptainer 的部署範本與 PyTorch CPU、單卡、DDP、FSDP2 範例。

**目前網頁聚焦「主機監控」與「觀測排程」兩個入口，登入後直接進入主機監控。** 專案、草稿、計畫、工作與結果等舊流程已從網頁路由移除，原有後端 API 與歷史資料保留。工作派送與排程預設為 `demo`；主機監控為明確啟動的真實唯讀 SSH 觀測。重新整理只更新已保存的清單，不會啟動遠端觀測。

## 本機啟動

需求：Python 3.12、uv、Node.js／npm。以下命令在本目錄執行：

```bash
uv sync --locked
npm --prefix frontend ci
npm --prefix frontend run build
cp .env.web.example .env.web
chmod 600 .env.web
```

編輯 `.env.web`，將 `WEB_ADMIN_PASSWORD` 換成你自己的至少 12 字元密碼，保留 `SRS_MODE=demo`，再啟動：

```bash
.venv/bin/python scripts/dev.py
```

開啟 <http://127.0.0.1:8000>，帳號 `admin`。這個啟動器啟動 API、demo executor、scheduler 與獨立監控程序，結束時一併停止。工作派送仍為 demo，使用 `var/srs-demo.sqlite3`；新增／更新 SSH 主機會觸發首次硬體盤點；持續使用率監控需在「主機監控」頁按下「開始實際監控」。憑證由網頁管理，既有 `.env.hosts` 僅用於一次性匯入。

前端開發可另用 `npm --prefix frontend run dev`，Vite 將 `/api` 代理至 `127.0.0.1:8000`。

## SSH 主機設定、硬體 profile 與實際監控

在「主機監控」按「新增 SSH 主機」，輸入顯示名稱、IP／名稱、SSH 連接埠、帳號與密碼。按「儲存並連線」後，獨立監控程序會首次連線並盤點：

- CPU 型號、實體核心／執行緒、RAM、GPU 型號／UUID／顯存。
- SSD／HDD 型號、容量、介面與掛載點。
- 網卡 MAC、IP、MTU，以及 OS、核心、架構、主機名稱。

Profile 持續保存，顯示首次建檔與最後盤點時間；「重新盤點硬體」可更新。既有主機按卡片底部「設定」，以同一個對話框編輯，密碼留白會保留原密碼。所有主機採相同觀測規則，包含 226；不再以工作派送 inventory 的 `enabled` 決定能否監控。

密碼加密保存於資料庫，本機金鑰為 `var/ssh-credentials.key`（0600）。備份／還原時須一起保留此金鑰；API 不回傳密碼或密文。首次升級會將已有的有效 `.env.hosts` 設定匯入一次，之後 GUI 設定優先，新增／編輯主機不需調整環境檔。

已在本機 `known_hosts` 信任或已有指紋的主機可直接登入。未知／變更的 SSH 主機金鑰會在介面顯示，核對指紋後按「確認並建立 profile」才會使用帳密登入。

勾選任意主機或「全選主機」，選擇 1／2／5／10 秒間隔，再按「開始實際監控」。監控中勾選／取消或調整間隔會立即套用，全部取消會停止。每台有獨立採樣循環，重用已驗證的 SSH 連線；一台離線不會阻擋其他主機。卡片包含 CPU／RAM／GPU 使用率、磁碟掛載點容量／已用／可用、Ethernet 收送速率、連線狀態與開機時間。網路速率使用兩次 counter 差分，首筆不假造為 0。採樣頻率受網路與主機回應時間影響。

按停止、離開頁面或登出會停止採樣；未收到停止通知時，45 秒租約到期。結果不混用 demo 快照。SSH 設定／硬體盤點與工作部署授權分開，新登錄的觀測主機不會自動取得變更或工作派送資格。

本機開發啟動器已包含監控程序。非 demo 部署先執行 Alembic migration，再以相同資料目錄／憑證金鑰啟動：

```bash
.venv/bin/python -m srs.cli init-db
.venv/bin/python -m srs.cli monitor
```

自訂 `SRS_PERMISSIONS` 時，設定主機需 `hosts.manage`，開始／停止監控需 `monitor.manage`。詳細驗證見 [SSH 與監控驗證](docs/LIVE_MONITOR_VERIFICATION.md)。

## 目前的操作流程

1. 在「主機監控」新增 SSH 主機；按「設定」可更新連線資料，按「刪除」移除本機登錄。
2. 勾選主機並按「開始實際監控」。綠色代表正常讀取、紅色代表讀取失敗、灰色代表未監控，均有文字狀態。
3. CPU／RAM／GPU 常駐顯示；「磁碟與網路」與「硬體資訊」預設折疊。超過四張 GPU 的清單可用滑鼠或鍵盤捲動。
4. 監控時調整勾選或更新間隔即時套用；按「停止監控」或離開主機頁停止採樣。
5. 「觀測排程」提供新增、編輯、停用與刪除；排程仍須產生計畫、審查核准，再啟用。模擬環境會顯示提示，避免與真實主機監控混淆。

桌面與手機使用相同功能；主機表單支援 Tab 焦點限制、Escape 關閉與返回原操作按鈕。詳細驗證見 [介面整理驗證](docs/UI_REFINEMENT_VERIFICATION.md)。

## 資料與憑證

- `.env.web`：網頁帳密與公開應用設定；登入密碼與遠端密碼獨立。
- `.env.hosts`：既有工作 executor 專用（網頁觀測不需新增此檔的設定），檔案必須由 executor 帳號持有且權限 `0600`。逐台設定 `HOST_AI_LAB_225_*`／`HOST_AI_LAB_250_*`；SSH、sudo 密碼不互相後備。
- `config/inventory.yaml`：公開主機位址、SSH 身分、GPU UUID 與金鑰指紋。修改後重新啟動服務會同步資料庫並增加 inventory revision，使舊計畫失效。
- `var/`：本機資料。真實部署使用 MariaDB 及受控的 `SRS_DATA_DIR`；`.env*`、資料庫、日誌、成果與測試暫存均排除版本控制。

計畫保存憑證參照，不保存明文密碼。已知憑證在日誌持久化前遮罩，含跨輸出片段的情況；研究程式自行輸出的未知或刻意轉碼秘密無法保證辨識。

## 實機階段

| 階段 | 目的 | 目前證據 |
|---|---|---|
| Mac demo | 主機管理、排程與既有後端工作流程 | 本機 HTTP／DB／worker 及瀏覽器測試；主機監控另有真實 SSH 證據 |
| Mac 原生 CPU | 小型訓練、checkpoint／resume、兩程序 DDP 正確性 | 真正執行 PyTorch；不是 Slurm 或跨主機結果 |
| 本機 Linux VM | MariaDB、Slurm、CPU 排程與兩 worker | 已提供 Lima 與部署範本；尚未啟動驗收 |
| 225 | admin／sudo、GPU 容器、排隊與取消、cgroup 隔離 | 待實機核准計畫與驗收 |
| 225＋250 | 每台一張 GPU、NCCL、DDP、FSDP2、共享 checkpoint | 待實機驗收；不預先承諾異構 GPU／1Gbps 加速 |

既有工作派送試點以 225 為控制節點與第一個運算節點，250 使用指定的一張 GPU。這些部署範本限制不套用於主機觀測；226 與新增主機皆可經同一套 SSH 設定與勾選流程監控。既有驅動、UID、SSH／PAM 與其他使用者程序不在部署範本的變更範圍。

`remote-slurm` 模式啟動前必須完成 MariaDB migration、可信管道核對 SSH fingerprint、逐台 admin 憑證及預檢。部署計畫缺少套件校驗值、原始檔案內容／不存在證據、完整差異或其他前提時會阻擋核准。這是待補足的真實資料，不能用 demo 成功替代。

參閱 [部署步驟](docs/DEPLOYMENT.md)、[操作與復原](docs/OPERATIONS.md)、[測試矩陣](docs/TEST_MATRIX.md)、[本次驗證報告](docs/VERIFICATION.md) 與 [API 契約](docs/IMPLEMENTATION_CONTRACT.md)。API 文件在 `/api/docs`。

## 驗證與獨立服務

```bash
.venv/bin/pytest -q
.venv/bin/ruff check backend workloads infra tests scripts migrations
npm --prefix frontend run build
npm --prefix frontend test -- --run
```

真實模式使用獨立服務，不使用 demo 啟動器。先在各服務設定檔將 `SRS_MODE` 設為 `local-slurm` 或 `remote-slurm`，並設定獨立 MariaDB `SRS_DATABASE_URL`、`SRS_DATA_DIR` 與已核對的 inventory；沿用 quickstart 設定仍會是 demo。

```bash
.venv/bin/python -m srs.cli init-db
.venv/bin/python -m srs.cli api --host 127.0.0.1 --port 8000
.venv/bin/python -m srs.cli worker --credentials-file /etc/server-remote-system/hosts.env
.venv/bin/python -m srs.cli scheduler
```

這些是分別執行的命令。`init-db` 會遷移指定資料庫、同步公開 inventory 並建立預設專案；inventory 變更會增加版本，使舊計畫失效。資料庫備份／遷移／還原目前須另行人工審核，第一版沒有這些操作的網頁計畫產生器。服務檔、資料庫權限、TLS／反向代理及主機金鑰設定見部署文件。本次沒有部署到遠端主機，也沒有建立 Git commit 或 push。

## 網頁終端與 Linux 使用者帳號

主機卡片「設定」旁的「終端」會以保存的 SSH 帳號開啟互動式 PTY。必須先完成 SSH 設定與主機指紋確認。支援鍵盤輸入、Ctrl+C、Tab、終端縮放與重新連線；Esc 保留給終端程式。關閉視窗會中斷連線，長時間工作請用 tmux。單次連線上限 30 分鐘，每個網站帳號最多同時四個終端。登出、網站登入過期、主機停用／刪除或 SSH 設定版本改變後會關閉連線。

主機「設定 → 使用者帳號」可讀取 Linux 一般使用者（UID 1000–65533）、新增帳號並建立家目錄、修改顯示名稱、設定／重設密碼。編輯時密碼留白表示保留。帳號建立使用 `/bin/bash`，需要 Linux 的 Python 3、useradd、usermod、chpasswd。root 或具 sudo 權限的 SSH 帳號才能修改；sudo 密碼每次提供且不保存。此介面不修改系統帳號、root 或目前 SSH 登入帳號。

權限分別為 `hosts.terminal` 與 `hosts.accounts`（預設管理者包含）。自訂 `SRS_PERMISSIONS` 時需明確加入。這兩個入口由使用者直接發起，會操作真實 SSH 主機，包括網站處於 demo 時；既有排程／工作派送模式不改變。API 現在承接這兩種明確請求的 SSH 連線，監控仍使用獨立 worker。WebSocket 使用同源檢查、網站登入 cookie 與首訊息 CSRF 驗證；反向代理須支援 WebSocket upgrade。

驗證：`tests/test_host_access.py`、`work/host-access-evidence.json`。225 實際驗證終端唯讀指令與帳號清單；帳號變更在隔離測試檢查命令、stdin 密碼傳遞與保護條件，沒有為測試更動實機帳號。帳號操作中斷可能只完成部分步驟，介面會提示重新讀取清單後再編輯，不會自動重送。

## 外觀模式

右上角可選「淺色模式」、「深色模式」或「跟隨系統」。登入頁也提供相同切換器，選擇會保存在目前瀏覽器；預設跟隨作業系統。現行介面以 Apple HIG 的內容優先、清楚層級、浮動功能材質、即時回饋與無障礙原則重新整理，仍維持 Web 平台、既有功能與原路由。設計範圍、測試與畫面證據詳見 [Apple UI 驗證](docs/APPLE_UI_VERIFICATION.md)；先前的 [Argon 介面驗證](docs/ARGON_DESIGN_VERIFICATION.md) 保留為歷史紀錄。

## 總覽、監控歷史與帳號審核（2026-11-15）

登入首頁新增 Dashboard，主機監控顯示登入工作階段與 GPU 計算程序，
另提供最近 1 小時／24 小時／7 天歷史和站內告警。監控需明確啟動，
切換頁面仍持續採樣，停止、登出或關閉分頁後結束。

「帳號管理」依主機列出所有 Linux 帳號；新增、修改、密碼鎖定／解鎖
與刪除均先產生 10 分鐘有效的命令計畫，審查後才執行。
刪除保留家目錄，系統帳號和目前 SSH 帳號受到保護。
網站本身仍使用既有環境設定中的管理者帳號。

詳見 [驗證與邊界](docs/OBSERVABILITY_ACCOUNTS_VERIFICATION.md) 及
[設計決策](DESIGN.md)。實機模式需先套用 Alembic 0005，並重啟 API 與監控程序。
