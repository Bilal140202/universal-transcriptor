"""Enable `python -m transcriptor …` (used by the web bridge and containers)."""
from .cli import main

raise SystemExit(main())
