# 部署與叢集證據契約

## 部署產生器

`infra.catalog.render_deployment(component, node, evidence, max_age_seconds=...)` 不執行任何動作。回傳 `status=ready|blocked`、`issues`、可實際執行的固定 `steps[].argv`、完整 `file_changes[].before/after/diff`。`ready` 只表示可供審查，仍須正常產生不可變計畫及批准。`blocked` 即使包含部分審閱用步驟，也禁止批准或派發。

`node` 是目前公開 inventory binding，含 `id`、`inventory_revision`、`host_key_sha256`、`ssh_username` 與 GPU scope。`evidence` 來自 `node.facts.deploy`，至少含：

- `schema_version:1`、`component`、`execution_id`、含時區的 `observed_at`、相同的 `host` binding；預設最長 900 秒，呼叫者可套用平台批准時限。
- `os:{id:"ubuntu",version_id:"24.04",architecture:"amd64"|"arm64"}`。
- `packages:[{path,sha256,name,version,architecture,source_url,source_sha256,non_setuid}]`。每個檔案必須事先置於 `/srv/srs/staging/`，來源為已核對的 HTTPS 參照、來源 SHA256 與實際 bytes 相符。版本字串不能代替 hash；此工具不下載檔案，也不捏造來源 hash。
- `files:{目的地:{exists,content,sha256}}` 為唯讀探測得到的完整原始內容或明確不存在；`staged_files:{目的地:{path,sha256}}` 必須對應固定範本產生的精確 after bytes。未知原始內容、不同 staged bytes、任意路徑、symlink 或未解 token 都會阻擋。
- `requirements:{需求名稱:{status:"verified",execution_id,detail}}` 只可由固定探測的判斷器建立；不能接受瀏覽器自填的 verified 布林值。`configuration` 的 Slurm 節點／CPU／RAM／單張 GPU mapping 使用結構化數值，由產生器建立指令，不能傳入任意 Slurm 文字或 shell。

主 planner 必須另外驗證 source execution 屬於目前使用者、狀態 succeeded、模式一致、批准 document 的 SHA256 正確、inventory revision 與 pin 相符。純 renderer 不能替代資料庫來源驗證。任何草稿中出現的自製 evidence 都不能直接寫入 node facts。

## 唯讀部署探測

`infra.evidence.deployment_probe_script(component,node,spec)` 產生固定唯讀 Python，應在已批准的 sudo/root 預檢步驟執行。spec 包含：

```json
{
  "packages": [{"path": "/srv/srs/staging/apptainer_1.5.5_amd64.deb", "source_sha256": "<independently verified SHA256>", "source_url": "<verified HTTPS package source>"}],
  "staged_files": {},
  "maintenance_window": {"starts_at": "<ISO8601 with timezone>", "expires_at": "<ISO8601, at most four hours later>"},
  "use_cuda": false,
  "configuration": {}
}
```

以上是未填證據的示意，不能直接通過驗證。`/srv/srs/staging`、套件與其祖先路徑必須 root 所有、不可讓 group／other 寫入，也不可使用 symlink，防止摘要核對後被非管理者替換。探測只讀取白名單內的 staged package／目的地，取得 OS、dpkg metadata／contents，使用 `apt-get --simulate --no-download` 核對已安裝與 staged dependencies；用已核對 SSH 帳號執行短暫 user namespace 與 FUSE 權限檢查，並查詢該節點的 Slurm 工作。它不安裝、下載、建立 shared witness、重啟服務或修改主機檔案。

`parse_deployment_evidence(observation,component,node,spec,execution_id=...,observed_at=...)` 只接受與同一份 spec hash／host binding 相符的 `kind=deployment_probe` 輸出，且忽略輸出中自行夾帶的 requirements。主 worker 只能在該已批准 execution 成功後存入 facts。每個實際安裝步驟在執行時重新檢查 staged bytes；直接管理的設定檔也重新檢查原始 hash／不存在狀態，防止批准後被替換。

既有設定檔的 `file_change.backup_path` 固定為 `destination + '.srs-backup-' + before_sha256[:16]`。安裝器先以 `O_EXCL` 建立備份、寫入原始 bytes 並 fsync；既有檔案或 symlink 一律拒絕覆寫。新內容在同一目錄暫存、fsync，再重驗原檔後原子 replace。原先不存在的目的地不建立備份，使用原子 create-if-absent 發布，若同時有人新增則失敗。計畫列出備份路徑、暫存檔模式與發布策略；發生部分失敗可能留下備份，必須檢視既有證據後另作處理，不盲目重送。

目前自動判斷器能在所有檢查成立時產生 **CPU-only Apptainer** 的 ready review：固定 1.5.5、非 setuid、完整 staged dependency set、不刪除既有套件、維護時段內無該節點 Slurm 工作。這不表示沒有任何 unmanaged process，也不證明 CUDA／驅動相容。Slurm 與 platform 所需的 MUNGE／一致 UID／GID、管理帳號、release／migration／服務變更等證據尚不能由這份 probe 完整證明，因此會保留 blocked；不能手動補幾個 verified=true 就視為已驗證。

## 跨節點唯讀證據

在兩節點 `preflight` 草稿的 `parameters.cluster_probe` 提供下列 spec，可產生逐節點的固定 probe。它必須指向目前選定兩台 inventory 主機的已核對 IP／介面；不可藉此掃描其他主機。

```json
{
  "schema_version": 1,
  "probe_id": "<fresh 32 hex characters>",
  "storage": {"root": "/srv/srs", "source": "<NFS IPv4>:/export", "witness_path": "/srv/srs/.srs-probes/<probe_id>.json", "witness_sha256": "<verified 64 hex characters>"},
  "peers": [
    {"id": "225", "address": "<verified IPv4>", "interface": "<verified interface>", "port": 6818},
    {"id": "250", "address": "<verified IPv4>", "interface": "<verified interface>", "port": 6818}
  ]
}
```

Witness 必須先經另行批准的儲存準備操作建立，內容為 `{probe_id,peer_ids:["225","250"],source,nonce}`，nonce 是 64 hex 隨機值，SHA256 必須對實際檔案 bytes 計算。唯讀 probe 不建立 witness。它拒絕 symlink、同名本機資料夾、不同 NFS export、不同 probe ID/hash，以及缺少任一方向 TCP/6818 的結果。

`parse_cluster_evidence(observations_by_node,spec,execution_id=...,validated_at=...)` 會在兩個固定 probe 都成功時，產生可供 planner 使用的逐節點 `cluster` facts。`network.verified` 的範圍是指定來源介面與雙向 TCP/6818；`shared_storage.verified` 的範圍是同一 NFS export 與既有 witness。輸出保留 `nccl_collective_tested:false`、`write_visibility_tested:false`。實際 NCCL collective、訓練及 checkpoint 寫入／恢復仍要另外跑已批准的工作，不能把唯讀證據當成訓練成功。

Linux probe 的 JSON 掛載欄位依 [util-linux findmnt 官方文件](https://kernel.googlesource.com/pub/scm/utils/util-linux/util-linux/+/refs/heads/master/misc-utils/findmnt.8.adoc) 指定，不依預設輸出欄位猜測。
