# Team education engine

This package ports the personal project's current UTXO engine and services into the independent team Flask application. The source working tree, including uncommitted service changes, was read only. `PROVENANCE.json` records original and copied file hashes and the source commit.

The package retains UTXO selection, change, fees, coinbase maturity, real signatures and proof of work, branch selection, mining races, persistent archives and classroom roles. Transport is implemented separately by the team Flask adapter; importing this package does not start a server or open a database.

The archive defaults to this Flask project's `instance/education.sqlite3`. Set `TEAM_EDUCATION_DB` to an explicitly selected team archive. The original account-model archive remains separate. Personal project environment variables and data files are never used. Team classroom teacher credentials are supplied by the Flask factory.

`service/backup.py` provides the same backup/restore CLI through `python -m education_engine.service.backup`; its default database follows the team-only rule above.
