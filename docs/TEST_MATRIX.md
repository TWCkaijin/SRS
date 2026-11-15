# 驗證層級與證據

所有測試報告須帶有環境與日期。`demo`、本機 CPU、Linux VM 與實際 GPU 的證據不可互換。

| 層級 | 可驗證 | 不代表已驗證 |
|---|---|---|
| 本機離線測試 | 草稿／計畫、參數邊界、內容 hash、腳本產生、模擬狀態與錯誤 | SSH、Slurm、GPU 或遠端安全性 |
| Mac 真實 CPU | 小型訓練、Gloo 雙程序 DDP、checkpoint 數值恢復 | Linux cgroup、CUDA、x86_64 映像或實體網路 |
| 三台 Linux VM | 真正 Slurm 排程／取消／作業歷史、CPU／RAM 限制、節點故障 | NVIDIA GPU、PCIe、實機供電／散熱、跨主機網路效能 |
| 225 單機 GPU | 驅動與容器、單卡訓練、GPU GRES／cgroup、同機作業清理 | 跨節點訓練、網路容量或控制面 HA |
| 225＋250 各一 GPU | NCCL、跨機 DDP、FSDP2／DCP、共同資料路徑與故障收斂 | 8GB＋11GB 自動形成單一 19GB 顯存，或大型模型已能運行 |

## 本次已完成的 workload 驗證

日期：2026-11-07（Asia/Taipei）。獨立測試環境為 `work/workload-venv`，Python 3.12.14、PyTorch 2.14.1、Darwin arm64；沒有安裝到應用程式 `.venv`。

- `tests/test_workload_contracts.py`：13 個測試通過，涵蓋兩 rank 資料完整且不重複、無法假造 rendezvous、非法或過大的工作拒絕、恢復合約、GPU 腳本界限、注入字串處理及部署計畫保持 blocked。
- 其中 CPU checkpoint 測試是真實執行：不中斷 3 步，與先跑 2 步存模型／optimizer／step、全新程序恢復再跑 1 步；最終固定輸入 logits 在容差內一致。
- 實際 CPU DDP 兩 rank 跑 3 步，並與相同全域 batch 的單 CPU 基準比較。`work/workload-validation/` 保留 rank JSON 與 probe tensors；最大 logits 絕對差約 `3.58e-7`，通過 `rtol=1e-4, atol=1e-5`。
- 這次微模型的 DDP 單次測量速度比約 0.73，沒有加速；它只驗證通信與數值流程。不能把這個數字外推到 GPU 或不同模型。
- workload／infra 的 Ruff 與已產生 sbatch 的 Bash 語法檢查通過。

首次 `torchrun --standalone` 在此受限環境的 `localhost:0` rendezvous 等待失敗；停止該程序後，改用明確的 `127.0.0.1`、固定未占用 port 及 30 秒 rendezvous timeout，在獲准的本機回送連線測試中通過。這不是遠端 NCCL 證據。

要重跑實際 CPU checkpoint 測試，將 `SRS_TEST_TORCH_PYTHON` 指向獨立安裝 torch 2.14.1 的 Python，再執行該 pytest 檔。未設定時該案例標記 skipped，不會以假輸出冒充訓練通過。CPU DDP 的命令與結果屬本機測試，沒有 SSH、VM 或遠端安裝動作。

## 部署與叢集證據合約測試

`tests/test_infra_evidence.py` 使用明確標示的 synthetic observations，驗證既有檔案／staged SHA 不符、過期／不同 inventory、非法路徑、不同套件版本／來源、setuid、未 staged dependencies、移除套件、不同 NFS／witness／probe ID、缺少雙向介面連線時，計畫維持 blocked。測試也核對全部產生的 shell 語法與實際 CLI entrypoint；沒有把 fixture 當作 Linux 探測結果。CPU-only Apptainer 的正常 fixture 可產生 ready review，但仍不會執行。

本次執行 `test_infra_evidence.py`、`test_workload_contracts.py`、`test_job_planner.py` 的合併結果為 72 passed、1 skipped；skip 是未在這次命令重跑的實際 Torch checkpoint 案例，較早的獨立 CPU 結果如上。新增證據程式的 Ruff 檢查通過。實際 Linux `apt-get --simulate`／user namespace／NFS／TCP6818、systemd 跨帳號成果權限仍須在批准的 VM／實機環境驗收。

## 待完成的 Linux 與實機驗收

| 驗收 | 測試方法 | 通過條件 | 現況 |
|---|---|---|---|
| MariaDB | 實際啟動指定映像後跑 schema、交易與並行工作領取測試 | 無重複領取、授權與執行原子一致、遷移可驗證 | 待測 |
| Linux 排程 | 兩個 CPU worker 同時提交競爭資源的作業 | 指定限制下排隊，取消及超時正確清理 | 待測 |
| CPU／RAM 隔離 | 在專用 VM 測試 cpuset、超限及子程序 | 限制只影響自己的工作，OS 保留資源仍可用 | 待測 |
| GPU 指定 | 只將核准 UUID 映射的裝置加入 GRES | 任務只取得指定 GPU，250 第二張不被配置 | 待測 |
| GPU 隔離 | 同一測試作業移除可見性變數後嘗試未分配裝置 | cgroup 仍阻止使用；非僅環境變數隱藏 | 待測 |
| Apptainer CUDA | 同一已核對 SIF 在兩台執行小型 tensor | 都能 forward／backward，驅動與映像摘要留存 | 待測 |
| 跨節點通信 | 各一 GPU 執行小型 NCCL all-reduce | 值正確，失聯在設定期限內失敗 | 待測 |
| DDP | 同模型與固定全域 batch，比較兩台各自單卡與雙節點 | 數值一致、記錄吞吐；不以必須加速當通過標準 | 待測 |
| FSDP2 | 小型 FP32 Transformer，兩 rank 完成訓練 | 每 rank DTensor placement／local_numel 證明分片，loss 有限 | 待測 |
| DCP 恢復 | 共同路徑存模型／optimizer／step，全新作業恢復 | 接續一步後與不中斷流程在容差內一致 | 待測 |
| 實機失聯 | 專用試點只中斷自己的測試步驟 | 不盲目重送，狀態可標記 unknown，既有工作不受影響 | 待測 |

## 量測規則

正確性 smoke test 使用小模型、少量步驟。效能比較另跑 5 次 warm-up、20 個量測步驟、3 回重複；固定模型、精度、資料與全域 batch，使用各 rank 最慢 step 的時間，排除 checkpoint 與讀檔時間。比較基準使用兩台中較快的單 GPU，並報完整設定，不能只挑較慢的卡讓加速比看起來較好。

FSDP2 記錄每卡 allocated／reserved 訓練峰值，以及每個參數的全域／本地元素數。小模型可能因通信緩衝而峰值更高，所以第一版驗收是「真分片、可訓練、可恢復」，不要求一定節省峰值，更不故意跑到 OOM 來證明容量。控制面重啟恢復與 HA 是不同測試，本版沒有 HA 驗收結論。
