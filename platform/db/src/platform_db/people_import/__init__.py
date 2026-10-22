"""Import recruiting-form exports and the member roster into the `people` schema.

Three pieces, each usable on its own:

* :mod:`.forms` reads a Microsoft Forms ``.xlsx`` export into
  :class:`~platform_db.people_import.model.Submission` records (pure, no DB).
* :mod:`.members` reads the roster CSV into
  :class:`~platform_db.people_import.model.MemberRow` records (pure, no DB).
* :mod:`.importer` writes either into Postgres, one ``import_batch`` per call,
  idempotently, with ``dry_run`` running the whole thing and rolling back.

``python -m platform_db.people_import --help`` is the command-line front.
"""
