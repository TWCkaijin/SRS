# SSH 匯入與批次分散式訓練設定

2026-11-15 本機功能更新。

使用者後續選擇 Slurm：主要入口已改為「Slurm 叢集設定」，詳見 [Slurm 批次設定](SLURM_BATCH_SETUP.md)。本文件的 PyTorch 頁保留於「進階：PyTorch 環境」。

## 使用方式

- 主機監控 → 匯入 SSH 設定：讀取執行 SRS 使用者的 `~/.ssh/config`，也可選擇檔案或貼上內容。預覽後勾選主機、分別輸入密碼，沿用加密儲存與 SSH 指紋確認流程。
- 解析具名 Host、HostName、User、Port，遵循先出現的值及 wildcard／否定 pattern。Include 不展開、Match 不執行；ProxyJump／ProxyCommand 標示不支援，不能直接匯入連線。只提供密碼驗證。
- 主機監控選取變更會保留未變動主機的監控工作與讀值。個別主機 token 防止移除再加入時接收上一輪結果。
- 卡片保留固定 260px GPU 區域，多卡使用捲動；側欄支援持久化收合、hover／鍵盤焦點展開及手機抽屜。
- 分散式訓練設定：先選節點，第一台是 Rank 0；填各節點可到達的 Rank 0 位址、連接埠、每台程序數、Python 絕對路徑與選用介面。
- 使用既有 PyTorch，或輸入明確版本並選官方 CPU／CUDA wheel 來源，建立獨立 venv。套用會新增 `~/.local/share/srs/training/<plan-id>/config.json`、`launch.py` 與選用 venv，不覆寫既有訓練設定。
- 檢查並預覽 → 確認變更 → 套用，逐台顯示成功／未確認完成。每台執行顯示的 `python3 .../launch.py /path/to/train.py` 啟動同一 DDP 程式。

## 實作界線

- 目前為 PyTorch DDP／torchrun，沒有新增 Slurm 叢集管理。設定不會啟動訓練或同步程式／資料。
- 預檢確認 SSH 指紋、Linux、指定 Python、既有 PyTorch 版本與 GPU 數；安裝後再驗證 PyTorch 與 GPU。尚未自動驗證節點間網路、防火牆、NCCL 或實際多節點訓練。
- 使用 `hosts.terminal` 權限與 CSRF 保護；預覽綁定登入 session、SSH revision 及主機身分。套用前再次核對所有節點。
- 預覽保存在單一 API 程序記憶體，10 分鐘到期；重啟或多 worker 不共用預覽。長批次逾時後需要重新預覽。每台預覽只可套用一次；失敗不自動重跑，可能留下部分安裝目錄。
- 每台遠端操作最多約 11 分鐘。請保持頁面開啟；逐台結果在目前頁面，另有後端 audit 紀錄。

## 驗證

- 後端完整測試：236 passed、1 skipped；前端：23 passed；TypeScript／Vite production build 通過。
- 新測試涵蓋保留其他主機讀值、移除／重新加入競態、匯入解析與權限、預覽唯讀、套用 revision／一次性限制、錯誤去敏、暫存目錄內實際執行檔案產生腳本。
- 瀏覽器 fixture 驗證：1／4／8 GPU 等高、側欄收合／hover／鍵盤、匯入與密碼、兩節點批次套用順序、雙主題、320／390／768／1024／1440 寬度、手機 Escape。
- `work/setup-browser-evidence.json` 與 `work/setup-*.png` 為隔離 UI 測試資料，非實體節點套用證據。
- 已重啟本機服務，確認新增 API 已註冊；以真實登入驗證匯入預覽與本機 SSH 設定讀取（6 個候選項）。未替實體主機安裝 PyTorch 或寫入訓練設定。

## 參考

- [PyTorch torchrun 官方文件](https://docs.pytorch.org/docs/stable/elastic/run.html)：節點數、每節點程序數、rank 與 master 位址參數。
- [UIverse](https://uiverse.io/)：互動元件視覺參考；此次側欄與表單為專案內自訂實作，未直接複製第三方元件。
