# Entry point of the bundled `retrodisc-die` helper: Detect It Easy only, no PySide6.
import sys

from retrodisc.core.deep_scan import run_cli

sys.exit(run_cli(sys.argv[1:]))
