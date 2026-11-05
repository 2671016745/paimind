"""Desktop entry point: ``python -m desktop`` or ``python desktop/main.py``."""

from __future__ import annotations

import argparse
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="paimind 桌面版")
    parser.add_argument("--no-api", action="store_true", help="不自动启动内置 API 服务")
    parser.add_argument("--api-port", type=int, default=None)
    parser.add_argument("--theme", choices=("dark", "light"), default=None,
                        help="启动时使用的主题，覆盖已保存的选择")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QSettings

    from . import app as app_module
    from .app import MainWindow, ensure_cjk_font
    from .api_client import ApiClient
    from .server_host import ServerHost

    app = QApplication(sys.argv)
    # 必须在建窗口之前注册，否则中文会渲染成方框。
    ensure_cjk_font()
    app.setApplicationName("paimind")
    app.setApplicationDisplayName("应急食品")

    settings = QSettings("paimind", "paimind")
    app_module.apply_theme(args.theme or str(settings.value("ui/theme", "dark")))

    client = ApiClient()

    if not args.no_api:
        host = ServerHost()
        if host.start(port=args.api_port):
            host.wait_ready(timeout=8.0)

    # 切换主题要重建窗口：控件样式在构造时就被 f-string 固化了，
    # 仅重新绑定颜色常量不会让已有控件换色。
    # *Rebuilding is the price of theming: widget styles are baked in at
    # construction, so rebinding the colour constants alone changes nothing.*
    holder = {"window": None}

    def toggle_theme() -> None:
        target = "light" if app_module.THEME == "dark" else "dark"
        old = holder["window"]
        geometry = old.geometry() if old is not None else None
        tab_index = old.tabs.currentIndex() if old is not None else 0

        app_module.apply_theme(target)
        settings.setValue("ui/theme", target)

        window = MainWindow(client, on_theme_toggle=toggle_theme)
        if geometry is not None:
            window.setGeometry(geometry)
        window.tabs.setCurrentIndex(tab_index)
        window.show()
        holder["window"] = window

        if old is not None:
            old.close()
            old.deleteLater()

    window = MainWindow(client, on_theme_toggle=toggle_theme)
    holder["window"] = window
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
