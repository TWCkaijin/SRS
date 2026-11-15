# Slurm 批次叢集設定

使用者選擇「Slurm 管理訓練工作」後，`#/training` 已改為 Slurm 設定頁。原 torchrun 設定保留在 `#/torchrun`，Slurm 與 PyTorch 分別處理資源排程和訓練程序。

## 操作

1. 勾選 SSH 主機、選擇控制節點（同時作為運算節點）、填入既有訓練帳號。
2. 檢查環境後調整節點名稱、節點間可互連的 IP、CPU 與 RAM。預留 1 GiB 系統記憶體；GPU 預設不選取，必須明確勾選納管的卡。
3. 產生預覽，展開各主機的設定檔、Ubuntu 套件版本與 SHA256。修正前置條件後重新檢查。
4. 填各主機的 sudo 密碼（root／免密 sudo 可留白），勾選審查確認，再批次套用。需要 `hosts.terminal` 和 `plan.approve`。
5. 所有節點先驗證 sudo，再依序執行全體安裝、全體設定、全體啟動。最後由控制節點讀取 `sinfo`。

套用會真正操作 SSH 主機，與平台的模擬工作模式無關。這次開發驗證沒有對實體主機呼叫套用。

## 新叢集範圍

- Ubuntu 24.04、systemd、cgroup v2、`/usr/bin/python3` 與 python3-apt。
- 使用主機 APT 索引中可信任 Ubuntu 來源的套件；不是舊基礎設施模板固定的 Slurm 26.05.5。節點須有一致 Slurm 版本。
- APT 預覽只讀取既有索引。缺索引或相依套件需要升級／移除時阻擋，不會自動 `apt update` 或更新既有系統。
- 下載套件後核對已審查的 SHA256，再以 dpkg 安裝完整新增相依套件。排除驅動、核心、PAM、SSH、防火牆相關套件。
- 新建服務帳號（若不存在）：slurm UID/GID 64030、munge UID/GID 64031；衝突會阻擋。訓練使用者必須預先存在且 UID/GID 相同。
- 偵測既有 Slurm 設定、活躍服務、MUNGE 金鑰或 policy-rc.d 時阻擋，這不是既有叢集擴充／升級工具。
- 安裝暫時建立 `policy-rc.d` 阻止套件自動啟動服務，正常完成或 Python 例外時移除。主機當機／強制終止後可能需管理員處理殘留檔案。
- 配置 `/etc/slurm/slurm.conf`、`gres.conf`、`cgroup.conf` 及 slurmd systemd drop-in；每個檔案的完整內容列入審查摘要。
- 新叢集使用共用隨機 MUNGE 金鑰；原套件在本次安裝產生的金鑰會備份，絕不覆寫預先存在的叢集金鑰。
- 啟用 MUNGE、控制節點 slurmctld、所有節點 slurmd。Slurm TCP 6817／6818、節點名稱／IP、時鐘與工作通訊網路須由管理員安排；流程不變更防火牆或同步訓練資料。

## 紀錄與限制

預覽綁定登入 session、SSH 身分／revision、套件清單、設定內容與 bootstrap 程式 SHA256。核准需要 CSRF 與精確 content hash；每個計畫只執行一次。各步驟重新檢查登入、權限與 SSH 設定。密碼只經 stdin 傳送，使用後清除，不寫入計畫／audit；MUNGE key 不回傳瀏覽器。

同一 API 程序禁止兩個計畫同時操作相同主機。預覽與即時進度在記憶體，30 分鐘內可核准；服務重啟後不可恢復進度或自動續跑。完整非敏感計畫與各階段狀態記錄於 audit；遠端 `/var/lib/srs-slurm/<plan-id>.json` 保存階段與設定。此版應以單一 API worker 執行。

失敗保留已完成變更，停止後續步驟，狀態為「未確認完成」，不會自動回滾／重跑。重試前由管理員檢查 `journalctl -u munge -u slurmctld -u slurmd`、APT/dpkg 與上述標記。

`sinfo` 顯示所有預期節點 idle／allocated／mixed 時只代表節點已註冊。未建立 Slurm accounting／slurmdbd、配額與 fair-share，也未將本流程自動綁到舊版固定試點工作執行器。可在控制節點使用 `sbatch`、`squeue`、`scancel`；分散式訓練仍需相同程式／資料、PyTorch 環境與實際 NCCL/DDP 驗收。

## 驗證（2026-11-15）

- 後端 244 passed、1 skipped；前端 25 passed；production build 與新增 Python 檔案 lint 通過。
- Slurm 測試驗證 GPU 範圍與資源預留、輸入注入拒絕、套件來源／SHA／升級阻擋、批次階段順序、整批 sudo 預檢、失敗停止、revision 與權限、密碼／共用 key 不回傳。
- 後端使用隔離 DB 與模擬 SSH；固定遠端程式通過語法檢查，尚未在 Ubuntu VM 或實體主機完成 dpkg／systemd 端到端安裝。
- `work/slurm-browser-evidence.json` 是 API fixture 瀏覽器測試，涵蓋雙主題、資源選擇、預覽、核准與狀態，以及 320／390／768／1024／1440 寬度。`work/slurm-*.png` 為測試資料截圖。
- 沒有對實體主機安裝套件、寫入 Slurm/MUNGE 設定、啟動服務或提交訓練工作。

參考：[Slurm 管理員指南](https://slurm.schedmd.com/quickstart_admin.html)、[slurm.conf](https://slurm.schedmd.com/slurm.conf.html)、[python-apt package API](https://apt-team.pages.debian.net/python-apt/library/apt.package.html)。
