# twcard

自動從 Gmail 下載台灣各銀行信用卡電子帳單 PDF，解析繳費截止日與應繳金額，並可透過 Apple 提醒事項通知繳費。

## 功能

- **Gmail 自動下載** - 從指定 Gmail 標籤下載所有信用卡帳單 PDF
- **多銀行支援** - 支援 15+ 家台灣銀行的帳單解析
- **自動解密** - 自動嘗試各銀行設定的 PDF 密碼
- **去重機制** - 已下載的郵件不會重複下載
- **繳費提醒** - 透過 CalDAV 建立 Apple 提醒事項
- **排程執行** - 可部署到 Linux Server 用 cron 定時執行

## 支援銀行

| 代碼 | 銀行 | 解析方式 |
|------|------|----------|
| 008 | 華南銀行 | pdfplumber |
| 009 | 彰化銀行 | pdfplumber |
| 011 | 上海商銀 | pdfplumber |
| 012 | 台北富邦 | pdfminer + PyMuPDF CID |
| 053 | 台中銀行 | pdfplumber |
| 081 | 匯豐銀行 | pdfplumber |
| 103 | 新光銀行 | pdfplumber |
| 803 | 聯邦銀行 | pdfplumber |
| 805 | 遠東商銀 | PyMuPDF CID |
| 807 | 永豐銀行 | pdfplumber |
| 808 | 玉山銀行 | pdfplumber |
| 809 | 凱基銀行 | pdfplumber |
| 810 | 星展銀行 | pdfplumber |
| 812 | 台新銀行 | pdfplumber |
| 822 | 中國信託 | pdfplumber |

## 環境需求

- Python 3.10+
- Gmail 帳號（帳單郵件需設定標籤分類）
- （選用）Apple ID，用於 CalDAV 繳費提醒

## 安裝

### 1. 下載專案並安裝套件

```bash
git clone https://github.com/YOUR_USERNAME/twcard.git
cd twcard
pip install -r requirements.txt
```

### 2. 設定 Gmail API

1. 前往 [Google Cloud Console](https://console.cloud.google.com/)
2. 建立新專案（或選擇現有專案）
3. 啟用 **Gmail API**：API 和服務 > 程式庫 > 搜尋「Gmail API」> 啟用
4. 建立 OAuth 憑證：
   - API 和服務 > 憑證 > 建立憑證 > OAuth 用戶端 ID
   - 應用程式類型：**電腦版應用程式**
   - 下載 JSON 檔案，存到專案根目錄並命名為 `credentials.json`
5. 設定 OAuth 同意畫面：
   - API 和服務 > OAuth 同意畫面
   - 使用者類型：外部
   - 填入應用程式名稱和你的 email
   - 在「測試使用者」區塊加入你自己的 Gmail 地址

### 3. 設定 Gmail 標籤

在 Gmail 建立篩選器，將信用卡帳單郵件自動套用標籤。例如：

```
寄件者：bank@example.com
主旨包含：信用卡帳單
動作：套用標籤「銀行/信用卡帳單」
```

在 `.env` 中設定標籤名稱：

```env
GMAIL_LABELS=銀行/信用卡帳單
```

支援多個標籤，用逗號分隔：

```env
GMAIL_LABELS=銀行/信用卡帳單,銀行/對帳單
```

不設定的話預設是 `銀行/信用卡帳單`。跨標籤的同一封郵件會自動去重。

標籤路徑用 `/` 分隔。例如你的標籤結構是：

```
銀行/
  信用卡帳單/
```

就填 `銀行/信用卡帳單`。

如何找到標籤名稱？打開 Gmail 點進該標籤，網址列會顯示：

```
https://mail.google.com/mail/u/0/#label/%E9%8A%80%E8%A1%8C%2F%E4%BF%A1%E7%94%A8%E5%8D%A1%E5%B8%B3%E5%96%AE
```

把 `#label/` 後面的部分做 URL decode 就是標籤名稱（`%E9%8A%80%E8%A1%8C` = `銀行`，`%2F` = `/`）。

### 4. 設定 PDF 密碼

大部分台灣銀行的帳單 PDF 有密碼保護。建立 `passwords.json`：

```json
{
  "008 華南銀行": {
    "passwords": ["身分證字號"]
  },
  "810 星展銀行": {
    "passwords": ["生日MMDD+身分證末四碼"]
  },
  "812 台新銀行": {
    "passwords": ["密碼1", "密碼2"]
  }
}
```

各銀行常見密碼格式：

| 銀行 | 常見密碼格式 |
|------|-------------|
| 多數銀行 | 身分證字號（如 `A123456789`） |
| 部分銀行 | 身分證末四碼 + 生日 |
| 星展銀行 | 生日 MMDD + 身分證末四碼 |
| 台新銀行 | 生日 DDMMYY 或身分證字號 |
| 凱基銀行 | 身分證末四碼 |
| 上海商銀 | 身分證末六碼 |

`passwords` 欄位是一個清單 — 如果銀行改版換了密碼格式，把新密碼加進去，保留舊的即可。系統會依序嘗試每個密碼。

### 5. 首次執行（需要瀏覽器）

首次執行需要開啟瀏覽器完成 Google OAuth 認證：

```bash
python -m src.cli run
```

執行後會：
1. 自動開啟瀏覽器，登入 Google 帳號並授權
2. 儲存 `token.json`，之後不需要再登入
3. 下載所有帳單 PDF
4. 解析帳單並輸出 `statements.csv`

### 6. Apple 提醒事項設定（選用）

建立 `.env` 檔案：

```env
GMAIL_LABELS=銀行/信用卡帳單
APPLE_ID=your_apple_id@example.com
APPLE_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
```

Apple App 專用密碼申請方式：
1. 前往 [appleid.apple.com](https://appleid.apple.com)
2. 登入 > App 專用密碼 > 產生密碼

## 使用方式

```bash
# 完整流程：下載 + 解析
python -m src.cli run

# 只下載（不解析）
python -m src.cli download

# 只解析（不下載）
python -m src.cli parse

# 跳過下載，只解析
python -m src.cli run --skip-download
```

## 輸出格式

解析結果存在 `statements.csv`：

```csv
bank,file,due_date,amount
008 華南銀行,CREDITA2024.pdf,2024/12/08,19308
009 彰化銀行,彰化銀行2026年2月份信用卡對帳單.pdf,2026/03/18,189
```

- `due_date`：繳費截止日（`YYYY/MM/DD` 格式），或 `不需繳款`
- `amount`：應繳金額（新台幣），負數代表溢繳/退款

## 部署到 Linux Server

### 上傳到 Server

```bash
# 複製專案到 server
scp -r . user@server:/path/to/twcard

# 在 server 上安裝套件
ssh user@server
cd /path/to/twcard
pip install -r requirements.txt
```

### 重要：Token 設定

首次認證必須在有瀏覽器的電腦上執行。認證完成後，把 `token.json` 複製到 server：

```bash
scp token.json user@server:/path/to/twcard/
```

Token 會自動更新，之後在 server 上不需要瀏覽器。

### 設定檔案權限

```bash
chmod 600 passwords.json credentials.json token.json .env
```

### 設定 Cron

```bash
crontab -e
```

每週一早上 9 點執行：

```cron
0 9 * * 1 cd /path/to/twcard && /usr/bin/python3 -m src.cli run >> /path/to/twcard/cron.log 2>&1
```

或每天早上 8 點：

```cron
0 8 * * * cd /path/to/twcard && /usr/bin/python3 -m src.cli run >> /path/to/twcard/cron.log 2>&1
```

## 專案結構

```
twcard/
  src/
    cli.py              # CLI 入口
    config.py           # 常數與銀行規則
    gmail_downloader.py # Gmail 下載（含去重）
    pdf_extractor.py    # PDF 文字提取（多種方式）
    parsers.py          # 銀行帳單解析器（15 家）
    pipeline.py         # 流程編排：下載 -> 解析 -> CSV
  pdfs/                 # 下載的 PDF（按銀行分資料夾）
  data/
    downloaded.json     # 下載去重記錄
  credentials.json      # Gmail OAuth 憑證（勿提交）
  token.json            # Gmail OAuth Token（勿提交）
  passwords.json        # 各銀行 PDF 密碼（勿提交）
  .env                  # Apple ID 憑證（勿提交）
  statements.csv        # 解析結果
```

## 安全注意事項

- **絕對不要** 把 `credentials.json`、`token.json`、`passwords.json`、`.env` 提交到 git
- 這些檔案已在 `.gitignore` 中排除
- 在 server 上務必設定 `chmod 600`
- Gmail OAuth 只使用唯讀權限（`gmail.readonly`）
- 如果懷疑憑證外洩，請立即撤銷：
  - Gmail：[Google 帳戶權限](https://myaccount.google.com/permissions)
  - Apple：[Apple ID](https://appleid.apple.com) > App 專用密碼

## 授權

MIT
