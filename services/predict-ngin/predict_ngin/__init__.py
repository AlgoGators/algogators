"""predict-ngin: prediction-market research core, organised by bounded context.

Contexts (lowest layer first): ``shared`` <- ``markets`` <- ``venues`` <-
``costs`` / ``tapes`` / ``books`` / ``capture`` <- ``evaluation`` <- ``paper``.
The layering is enforced by import-linter (see ``.importlinter``).
"""

__version__ = "0.1.0"
