# SSH 主機設定、profile 與實際監控驗證

日期：2026-11-15（Asia/Taipei）。專案：`/Users/kaijin/TWCKaijin/Server-Remote-system`。

## 已交付行為

- 所有已登錄主機使用同一套 SSH 設定、首次盤點、勾選／全選與監控流程。226 沒有觀測例外，不再依工作試點的 enabled 設定排除。
- 網頁新增／編輯顯示名稱、主機位址、SSH port、帳號與密碼。密碼加密保存，API 不回傳明文或密文；編輯留白保留原密碼。有效舊 `.env.hosts` 可一次匯入，GUI 設定不被舊檔覆寫。
- 首次成功登入自動保存 CPU、核心／執行緒、RAM、GPU、SSD／HDD、掛載點、網卡 MAC／IP／MTU、OS／kernel／architecture。保存首次與最近盤點時間，支援重新盤點。
- SSH 金鑰採既有可信 known_hosts 或保存指紋；未知／變更金鑰需透過頁面核對確認，不能直接登入。
- 1／2／5／10 秒可選採樣。各主機獨立循環並重用已驗證的 SSH 連線；一台慢速／離線不阻擋其他主機。顯示 CPU／RAM／GPU 使用率、磁碟總量／已用／可用、網卡連線狀態／link speed／收送速率及 uptime。
- 網路速率使用兩筆 monotonic 時間與 counter 差分；首筆、counter reset 不假造為 0。停止／離頁／登出／租約到期會停止採樣，過期與錯誤不混用 demo 數值。

## 真實驗證

1. 225 與 250 真正唯讀 SSH：CPU、RAM、GPU、磁碟及網卡資料皆成功取得。225 回傳 1 張 GPU、2 顆實體磁碟、3 個掛載點；250 回傳 2 張 GPU、1 顆磁碟、2 個掛載點。此為測試當時觀測。
2. 真 Chrome → 本機 API → 加密儲存 → 獨立 monitor → 真實 225：從網頁新增一筆隔離測試主機、首次盤點成功；沒有修改 `.env`。
3. 網頁勾選 225／250，以 1 秒設定連續採樣，已看到真實磁碟使用量與網路 counter 差分。全選也包含 226，測試中未為 226 填寫或推測帳密，沒有宣稱已實測 226。
4. 停止後超過採樣週期沒有新結果。重啟測試 API／monitor，保留同一個暫存 DB 與加密金鑰，profile／憑證仍在。第二階段 monitor 不提供 `.env.hosts`，透過網頁編輯且密碼留白後仍可實際重新登入及盤點。
5. 桌面與 390px 手機版已渲染／檢查，無水平溢出，瀏覽器 page errors 為空。

證據：

- `work/host-profile-live-validation.json`：225／250 真實硬體盤點。
- `work/ssh-profile-browser-evidence.json`：網頁新增、實機採樣、停止與重啟後保存的完整檢查結果。
- `work/ssh-profile-desktop.png`、`work/ssh-profile-mobile.png`：實際 UI。
- `work/run-ssh-profile-browser.py`、`work/ssh-profile-browser.mjs`：可重跑的隔離真實 SSH 瀏覽器測試。會使用現有 225／250 帳密、建立暫存 DB，測試結束關閉子程序；不修改使用者主機登錄。

## 自動檢查

- Python 全套：217 passed、1 skipped；既有 Starlette/httpx deprecation warning 1 項。
- 前端：18 passed。含主機統一勾選、設定表單、密碼留白、SSH 指紋確認、停止／離頁與不顯示 demo 指標。
- 前端型別／正式建置、Ruff：通過。
- 新 DB Alembic 升級至 0004 通過；舊 SQLite demo DB 加入監控間隔欄位後，原有資料保留。未進行 MariaDB 實機測試。
- 監控／SSH 測試涵蓋加密保存與秘密不回傳、CSRF／權限、226 與新主機同流程、過期／登出／inventory 或設定變更、停止後舊結果不發布、未知指紋需明確確認、profile 重啟／reseed 保存、counter reset、慢主機不阻擋其他主機。Loopback SSH 測試使用合成 telemetry，與上述真實 225／250 證據分開。

## 使用方式與範圍

現有服務需要重啟以載入新 API、資料表及 monitor：原啟動終端 Ctrl+C，重新執行 `.venv/bin/python scripts/dev.py`，刷新網頁即可。新增主機按「新增 SSH 主機」；原清單主機按卡片上的「SSH 主機設定」。

本次測試未終止使用者既有服務，未執行部署、sudo、Slurm 工作或遠端檔案寫入。Linux Python 3 與 NVIDIA nvidia-smi 為目前探測基礎；缺少工具時會顯示該項讀取失敗。主機觀測登錄不等於工作變更授權。加密金鑰 `var/ssh-credentials.key` 須隨 DB 一起備份。

## 管理操作與狀態精簡更新（2026-11-15）

排程清單與詳細頁均提供刪除；刪除會停用排程、撤銷後續派送，保留原執行紀錄。主機卡提供新增、編輯 SSH 設定與刪除；刪除清除加密憑證與 profile、移出監控、停用包含該主機的排程，並留下避免舊 inventory／環境檔自動匯回的刪除標記。

主機頁移除大型「實際監控」橫幅、模式橫幅及重複提示，保留精簡選取、間隔與啟停控制。主機卡以當前讀取行為呈現：成功且未過期為綠色，失敗／逾時為紅色，未監控與停止為灰色。停止後不再以舊錯誤顯示紅色。

驗證：後端 224 passed、1 skipped；前端 21 passed；Ruff 與正式建置通過。Chrome 實際驗證 225 SSH 成功為綠色、226 未設定憑證的失敗為紅色、未選取 250 為灰色；驗證已設定與未設定主機皆可刪除、停止後轉灰、排程清單／詳細頁皆可刪除，以及重啟、reseed 與 legacy import 不會恢復已刪除項目。全部刪除操作在隔離暫存資料庫進行。

證據：`work/management-browser-evidence.json`、`work/management-status-desktop.png`、`work/management-mobile.png`；可重跑入口為 `work/run-management-browser.py`。
