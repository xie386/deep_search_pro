# -*- coding: utf-8 -*-
"""`python -m cli` 等价于 `python -m cli.main`（即 dspro）。"""

import sys

from cli.main import main

if __name__ == "__main__":
    sys.exit(main())
