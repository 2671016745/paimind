# 截图 / Screenshots

| 文件 / File | 内容 / Content | 分辨率 / Size |
|---|---|---|
| `screenshot-decision.png` | 桌面版决策页 — 浅色主题 / desktop decision page, light theme | 1280×800 |
| `screenshot-decision-dark.png` | 桌面版决策页 — 深色主题 / desktop decision page, dark theme | 1280×800 |
| `screenshot-mobile.png` | 手机版 PWA — 浅色主题 / mobile PWA, light theme | 780×1980 |
| `screenshot-mobile-dark.png` | 手机版 PWA — 深色主题 / mobile PWA, dark theme | 780×1980 |

已用真实运行界面抓取（`desktop` 用 Qt `grab()`，手机版用浏览器 390×990 视口）。
> *Captured from the running UI — Qt `grab()` for desktop, a 390×990 viewport for the PWA.*

桌面版和 PWA 都支持自选深浅色。重新抓图：
> *Both the desktop app and the PWA let the user choose light or dark. Re-capture with:*

```bash
python .tools/shot2.py --theme light --out docs/assets/screenshot-decision.png
python .tools/shot2.py --theme dark  --out docs/assets/screenshot-decision-dark.png
```

设置页截图按需补抓即可，规格同上（≥1280×800，深浅色皆可，主色 `#4F7CFF` / `#3B63E8` 可见）。
> *Capture the settings page the same way when needed (≥1280×800, either theme, primary `#4F7CFF` / `#3B63E8` visible).*
