# Apple HIG-inspired Web UI verification

驗證日期：2026-11-15

## 範圍

本次只重構 React 網頁的視覺與版面呈現，保留既有 API、權限、登入、
Hash 路由、SSH 主機管理、實際監控、Slurm 設定、定時監控、終端與主題
切換行為。沒有加入 Xcode、SwiftUI 或其他 Apple 原生框架。

主要呈現層位於 `frontend/src/apple.css`，並在既有樣式後載入，避免更動
功能元件的事件處理與資料流。`theme.tsx` 只同步更新瀏覽器主題色。

## 設計決策

- 使用內容優先的寬版工作區、清楚的頁面標題與較少的裝飾邊框。
- 側欄、頂部工具列、對話框與通知使用節制的半透明功能層；內容卡片保持
  較實的表面，避免玻璃材質層層堆疊。
- 主要動作使用單一藍色強調；成功、警告、錯誤仍有文字、圖示或形狀，
  不只依賴顏色。
- 互動元件提供 hover、focus-visible、active 與 disabled 狀態；按下時即時
  回饋。
- 支援淺色、深色、較高對比、減少透明度與減少動態效果媒體偏好。
- 手機版保留原導覽抽屜、焦點限制與 Escape 行為，卡片與主要動作改為單欄。

## 自動驗證

- `npm run build`：通過；TypeScript 與 Vite production build 成功。
- `npm test -- --run`：11 個 test files、31 tests 全部通過。
- Chromium 1440×1000：登入、主機監控、AI_Lab_225 設定對話框、Slurm
  叢集設定、定時監控均可開啟；三個主要頁面沒有水平溢位。
- Chromium 390×844：導覽抽屜可開啟與關閉；主機監控沒有水平溢位。
- 深色模式：實際套用 `data-theme=dark`；主機設定對話框可開啟。
- 減少動態效果：頁面動畫降為近乎即時，沒有保留位移動畫。

## 畫面證據

- `frontend/evidence/apple-ui-desktop.png`
- `frontend/evidence/apple-ui-mobile.png`

畫面來自本機真實前端與 demo 後端執行結果；沒有啟動實際主機監控，也沒有
修改遠端主機、Slurm 設定或排程資料。
