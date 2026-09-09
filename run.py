"""PyInstaller 打包入口：以包方式导入，避免相对导入失效。"""

from ctyun_monitor.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
