# Argon 介面與雙主題驗證

日期：2026-11-15。

## 設計來源與實作

使用 Antigravity CLI 的 `gemini-4-argon-high`，明確指定僅對提供的前端程式片段提出設計，不讀環境檔、不接觸憑證、不修改檔案。完整建議在 `work/argon-design-response.md`；實作採納海軍藍／石板灰底色、青色操作重點、GPU 分組區域、頂部狀態線、卡片底部工具列、設定底線分頁、終端整合狀態列。

`frontend/src/argon.css` 定義雙主題語意色票與目前所有頁面的外觀；`theme.tsx` 提供淺色／深色／跟隨系統三種選擇，`public/theme-init.js` 在首次繪製前讀取偏好。選擇儲存在瀏覽器 `srs-theme`；預設跟隨 OS，系統主題變更即時套用，跨分頁 storage 事件同步。瀏覽器禁用儲存時仍可在當前頁面切換。

淺色主按鈕採較深的 #0f766e，將白字對比提升到 5.47:1。終端在兩種外觀均維持深色畫布，避免切換網站主題時中斷 SSH。

## 驗證結果

- TypeScript／Vite production build 通過。
- Vitest 8 個檔案、23 項測試通過；新增 OS 主題變更、手動選擇與偏好恢復驗證。
- Chrome 實際驗證：系統預設深色、手動切換、重新載入保留設定、跟隨系統即時變更。
- 實際 225 唯讀監控、SSH 終端與 Linux 帳號清單皆正常。
- 320、390、768、1024、1440 px 無整頁水平溢出。
- 登入、主機卡片、展開的磁碟／網路與硬體資訊、SSH 設定、帳號清單、終端、排程清單／表單／審查內容已檢查深色表面覆蓋。
- 排程審查視覺驗證以攔截回應的 fixture 呈現，不曾建立、核准或執行真實排程；帳號也未被修改。
- 主要語意色對比：淺色最低 4.57:1、深色最低 6.15:1（檢查正文、次要文字、按鈕、成功／錯誤／警告文字對各自底色；不等同完整無障礙稽核）。

瀏覽器腳本：`work/theme-browser.mjs`、`work/run-theme-browser.py`。
機器可讀紀錄：`work/argon-theme-evidence.json`。
畫面：`work/argon-hosts-light.png`、`work/argon-hosts-dark.png`、`work/argon-mobile-dark.png`，以及 `work/argon-*.png` 中其他頁面截圖。
