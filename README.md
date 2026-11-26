# 避雷查 Taiwan-Taiwan-check

輸入產品、企業、品牌、人名，就能查到曾發生的食安問題、犯罪與貪污判決、性騷擾、性別歧視、涉中爭議（政府調查、處分、國安判決）、資安或詐騙列管，以及每件事的政府或新聞來源。

- **網站**：https://cxzzsf0896753245657-hue.github.io/Taiwan-Taiwan-check/
- **Android APP**：[下載最新版 bilei.apk](https://github.com/cxzzsf0896753245657-hue/Taiwan-Taiwan-check/releases/latest/download/bilei.apk)
- **iPhone**：用 Safari 開啟網站 →「分享」→「加入主畫面」

## APP 為什麼不用重新安裝

APP 是一個很薄的外殼，打開後直接載入上面的網站。資料或網頁功能更新時，只要更新網站，APP 下次開啟就是新版，不需要重新下載 APK。只有外殼本身（圖示、名稱、權限）改變時才會產生新的 APK。

## 目錄

| 路徑 | 內容 |
| --- | --- |
| `site/` | 網站（PWA），由 GitHub Pages 部署 |
| `site/data/entries.json` | 全部紀錄，每筆含分類、議題、政黨（犯案當時）、司法狀態與來源 |
| `site/data/history.json` | 修訂紀錄：每次更正前的原文都保留在這裡 |
| `app/` | Android APP 外殼（Capacitor），由 GitHub Actions 打包並發佈到 Releases |
| `tools/` | 資料維護腳本 |

## 資料原則

- 來源優先順序：政府機關公告、法院判決 → 多家主流媒體一致報導的事實。每個事件都列出來源。
- 狀態分開標示：判決確定、有罪未確定、起訴／審理中、行政處分、民事判賠、官方調查、調查中、媒體報導、受影響（非責任方）、無罪／不起訴。
- 政治人物標示「犯案或事件當時所屬政黨」，各黨使用同一套收錄標準。
- 更正不刪除原文：原本的文字與來源保留並標示「原始紀錄」，修改前內容存在 `history.json`，git 版本歷史也完整保留。

## 回報新增或更正

在網站「新增紀錄」貼上新聞網址，或直接[開一則 Issue](https://github.com/cxzzsf0896753245657-hue/Taiwan-Taiwan-check/issues/new?template=report.md)。排程會讀取網址、查證後自動建檔或更正。

## （選用）固定 APK 簽章

目前 APK 用 debug 簽章打包，日常使用沒有影響。若之後想讓新版 APK 能直接覆蓋安裝舊版，請在 repo 的 Settings → Secrets and variables → Actions 新增：

- `ANDROID_KEYSTORE_B64`：keystore 檔的 base64 內容（別名需為 `bilei`）
- `ANDROID_KEYSTORE_PASSWORD`：keystore 與金鑰密碼

產生方式：

```bash
keytool -genkeypair -keystore bilei.jks -alias bilei -keyalg RSA -keysize 2048 -validity 36500
base64 -w0 bilei.jks   # 複製輸出貼到 ANDROID_KEYSTORE_B64
```

設定後，下一次 APK 會改用這把金鑰簽章；請把 bilei.jks 自行妥善保存，不要上傳到 repo。

本站為資訊整理，非法律意見。

## 外部彙整資料（site/data/ext.json）

`ext.json` 是從 council2026.taiwangogo.tw（時代力量管理、台灣前進製作）與 guilty.policy2026.workers.dev（匿名個人專案）匯入的 222 位 2026 年參選人司法紀錄。匯入規則：

- 只收附司法院裁判書連結，或至少兩家媒體報導的紀錄；只有臉書來源的不收。
- 不收：家屬紀錄、行政罰、guilty 網站標示「待查證」的項目；「中配」身分不是犯罪，也不匯入。
- 兩站重複的案件以判決字號去重。
- 政黨欄是 2026 年參選登記政黨，**犯案當時政黨未逐案核實**，頁面標為「所屬（未核實當時）」。
- 每筆事件文字結尾註明彙整自哪個網站。

這個檔案與 `entries.json` 分開存放；同步資料庫到 `entries.json` 時不要覆寫 `ext.json`。要更正 ext.json 的紀錄，把整筆搬進資料庫（先寫 history），網頁會優先顯示資料庫版本。
