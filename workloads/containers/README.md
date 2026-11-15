# 受控 Apptainer 映像建置

`pytorch-cu126.def` 是離線建置 recipe，不會下載套件或變更主機 NVIDIA 驅動。它尚未在本次工作中實際 build；目前沒有已驗收的 SIF。

建置前須準備並審核：

- `/srv/srs/staging/python-3.12-rootfs.sif`：可信來源、Python 3.12 的 base SIF 與 SHA256。
- `/srv/srs/staging/wheels-cu126/`：目標 Linux 架構的 torch 2.14.1／CUDA 12.6 及完整相依 wheel；Mac arm64 wheel 不可拿來替代 x86_64。
- `/srv/srs/staging/requirements-cu126.lock`：每個直接及間接相依版本與 `--hash=sha256:...`，可通過 pip `--require-hashes`。
- `.def` 內容 hash、`apptainer build` 版本、輸出路徑、所需磁碟與執行身分。

獨立審核建置後，再對生成的 SIF 取 SHA256，登錄專案 `container` 路徑，透過核准的唯讀預檢讀取同一摘要。第一版部署 renderer 安裝 Apptainer 本身，不會隱藏執行映像 build，也不會自動填入無法核實的 base image 或 wheel hash。
